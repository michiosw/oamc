from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, cast

from llm_wiki.health import build_doctor_report
from llm_wiki import __version__
from llm_wiki.config import load_config
from llm_wiki.llm.openai_client import OpenAIWikiClient
from llm_wiki.studio import DashboardServer, inbox_count, latest_log_heading, run_process_once, watch_loop


LAUNCH_AGENT_LABEL = "dev.oamc.studio"
APP_NAME = "oamc"
APP_BUNDLE_NAME = f"{APP_NAME}.app"
APP_BUNDLE_ID = "dev.oamc.studio"


def app_bundle_path(home: Path | None = None) -> Path:
    root = (home or Path.home()).expanduser()
    return root / "Applications" / APP_BUNDLE_NAME


def launch_agent_path(home: Path | None = None) -> Path:
    root = (home or Path.home()).expanduser()
    return root / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"


def build_launch_agent_payload(app_path: Path, *, base_dir: Path) -> dict[str, object]:
    executable_path = (app_path / "Contents" / "MacOS" / APP_NAME).as_posix()
    return {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [executable_path],
        "WorkingDirectory": base_dir.as_posix(),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "LimitLoadToSessionType": "Aqua",
        "ProcessType": "Interactive",
    }


def build_app_bundle(
    base_dir: Path,
    *,
    target_path: Path | None = None,
    python_executable: str | None = None,
) -> Path:
    bundle_path = (target_path or app_bundle_path()).expanduser().resolve()
    build_root = (base_dir / ".oamc" / "pyinstaller").resolve()
    dist_dir = build_root / "dist"
    work_dir = build_root / "build"
    spec_dir = build_root / "spec"
    entry_script = build_root / "oamc_menubar_entry.py"

    if bundle_path.exists():
        shutil.rmtree(bundle_path)

    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    build_root.mkdir(parents=True, exist_ok=True)

    python_path = _resolve_python_executable(base_dir, python_executable)
    entry_script.write_text(_pyinstaller_entry_script(base_dir), encoding="utf-8")
    subprocess.run(
        [
            python_path,
            "-m",
            "PyInstaller",
            "--windowed",
            "--noconfirm",
            "--clean",
            "--name",
            APP_NAME,
            "--distpath",
            dist_dir.as_posix(),
            "--workpath",
            work_dir.as_posix(),
            "--specpath",
            spec_dir.as_posix(),
            "--osx-bundle-identifier",
            APP_BUNDLE_ID,
            "--paths",
            (base_dir / "src").as_posix(),
            "--hidden-import",
            "rumps",
            "--hidden-import",
            "AppKit",
            "--hidden-import",
            "Foundation",
            entry_script.as_posix(),
        ],
        check=True,
    )
    built_app = dist_dir / APP_BUNDLE_NAME
    if bundle_path.exists():
        shutil.rmtree(bundle_path)
    shutil.copytree(built_app, bundle_path)

    contents_dir = bundle_path / "Contents"

    info_path = contents_dir / "Info.plist"
    existing_info = plistlib.loads(info_path.read_bytes()) if info_path.exists() else {}
    existing_info.update(
        {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleDisplayName": APP_NAME,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleIdentifier": APP_BUNDLE_ID,
        "CFBundleName": APP_NAME,
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
        }
    )
    info_path.write_bytes(plistlib.dumps(existing_info))
    (contents_dir / "PkgInfo").write_text("APPL????", encoding="utf-8")
    return bundle_path


def _resolve_python_executable(base_dir: Path, python_executable: str | None) -> str:
    if python_executable:
        return python_executable
    venv_python = base_dir / ".venv" / "bin" / "python3"
    if venv_python.exists():
        return venv_python.as_posix()
    virtual_env = os.environ.get("VIRTUAL_ENV")
    if virtual_env:
        candidate = Path(virtual_env) / "bin" / "python3"
        if candidate.exists():
            return candidate.as_posix()
    return sys.executable


def _pyinstaller_entry_script(base_dir: Path) -> str:
    return "\n".join(
        [
            "from pathlib import Path",
            "from llm_wiki.menubar import run_menubar",
            "",
            "if __name__ == '__main__':",
            f"    run_menubar(base_dir=Path({base_dir.resolve().as_posix()!r}))",
            "",
        ]
    )


def install_launch_agent(base_dir: Path) -> tuple[Path, Path]:
    _terminate_existing_app()
    _run_launchctl(["bootout", f"gui/{os.getuid()}", launch_agent_path().as_posix()], check=False)
    app_path = build_app_bundle(base_dir)
    agent_path = launch_agent_path()
    agent_path.parent.mkdir(parents=True, exist_ok=True)
    (base_dir / ".oamc").mkdir(parents=True, exist_ok=True)
    payload = build_launch_agent_payload(app_path, base_dir=base_dir)
    agent_path.write_bytes(plistlib.dumps(payload))
    _run_launchctl(["bootstrap", f"gui/{os.getuid()}", agent_path.as_posix()], check=True)
    _run_launchctl(["kickstart", "-k", f"gui/{os.getuid()}/{LAUNCH_AGENT_LABEL}"], check=False)
    return agent_path, app_path


def uninstall_launch_agent() -> tuple[Path, Path]:
    agent_path = launch_agent_path()
    app_path = app_bundle_path()
    if agent_path.exists():
        _run_launchctl(["bootout", f"gui/{os.getuid()}", agent_path.as_posix()], check=False)
        agent_path.unlink()
    _terminate_existing_app()
    if app_path.exists():
        shutil.rmtree(app_path)
    return agent_path, app_path


def _terminate_existing_app() -> None:
    subprocess.run(
        [
            "osascript",
            "-e",
            f'tell application id "{APP_BUNDLE_ID}" to quit',
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(["pkill", "-x", APP_NAME], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def restart_managed_app(base_dir: Path) -> None:
    if launch_agent_path().exists():
        subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LAUNCH_AGENT_LABEL}"],
            check=False,
        )
        return
    if sys.executable.endswith(f"/{APP_NAME}"):
        subprocess.Popen([sys.executable])
        return
    subprocess.Popen(
        [
            sys.executable,
            "-m",
            "llm_wiki.cli",
            "menubar",
            "--base-dir",
            base_dir.as_posix(),
        ]
    )


def reveal_installed_app(base_dir: Path) -> None:
    reveal_target = app_bundle_path()
    if reveal_target.exists():
        subprocess.run(["open", "-R", reveal_target.as_posix()], check=False)
        return
    subprocess.run(["open", base_dir.as_posix()], check=False)


def _run_launchctl(args: list[str], *, check: bool) -> None:
    subprocess.run(
        ["launchctl", *args],
        check=check,
        stdout=subprocess.DEVNULL if not check else None,
        stderr=subprocess.DEVNULL if not check else None,
    )


def run_menubar(
    *,
    base_dir: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8421,
    interval: float = 2.0,
    lint: bool = True,
    open_browser: bool = False,
) -> None:
    import rumps

    config, repo_paths = load_config(base_dir)
    dashboard = DashboardServer(repo_paths, host=host, port=port)
    dashboard.start()
    if open_browser:
        subprocess.run(["open", dashboard.url], check=False)

    process_lock = threading.Lock()
    stop_event = threading.Event()

    def notify(message: str) -> None:
        if message.startswith("Processed inbox"):
            rumps.notification(APP_NAME, "Ingest complete", message)
        elif message.startswith("Missing required environment variable"):
            rumps.notification(APP_NAME, "Configuration issue", message)

    watcher = threading.Thread(
        target=watch_loop,
        kwargs={
            "config": config,
            "repo_paths": repo_paths,
            "client_factory": lambda: OpenAIWikiClient(config),
            "lint": lint,
            "interval": interval,
            "emit": notify,
            "stop_event": stop_event,
            "process_lock": process_lock,
        },
        daemon=True,
        name="llm-wiki-menubar-watch",
    )
    watcher.start()

    class OAMCMenuBar(rumps.App):
        def __init__(self) -> None:
            super().__init__(APP_NAME, quit_button=None)
            self.menu = [
                "Status",
                "Runtime",
                None,
                "Open Dashboard",
                "Open Obsidian",
                None,
                "Process Inbox Now",
                "Restart oamc",
                "Reveal oamc.app",
                "Open Repo",
                None,
                "Quit oamc",
            ]
            self.title = APP_NAME
            self._status_item = cast(Any, self.menu)["Status"]
            self._status_item.set_callback(None)
            self._runtime_item = cast(Any, self.menu)["Runtime"]
            self._runtime_item.set_callback(None)
            self._timer = rumps.Timer(self.refresh, 5)
            self._timer.start()
            self.refresh(None)

        def refresh(self, _sender: object) -> None:
            pending = inbox_count(repo_paths)
            heading = latest_log_heading(repo_paths) or "No activity yet"
            report = build_doctor_report(config, repo_paths, host=host, port=port)
            dashboard_check = next((check for check in report.checks if check.key == "dashboard"), None)
            dashboard_state = "dashboard on" if dashboard_check and dashboard_check.status == "ok" else "dashboard off"
            self.title = f"{APP_NAME} · {pending}" if pending else APP_NAME
            self._status_item.title = f"Inbox: {pending} · {heading}"
            self._runtime_item.title = f"Runtime: {dashboard_state} · watcher on · {report.overall_status}"

        @rumps.clicked("Open Dashboard")
        def open_dashboard(self, _sender: object) -> None:
            subprocess.run(["open", dashboard.url], check=False)

        @rumps.clicked("Open Obsidian")
        def open_obsidian(self, _sender: object) -> None:
            subprocess.run(["open", "-a", "Obsidian", repo_paths.base_dir.as_posix()], check=False)

        @rumps.clicked("Process Inbox Now")
        def process_now(self, _sender: object) -> None:
            def _task() -> None:
                try:
                    with process_lock:
                        run_process_once(
                            config,
                            repo_paths,
                            OpenAIWikiClient(config),
                            lint=lint,
                            emit=notify,
                        )
                except Exception as exc:
                    rumps.notification(APP_NAME, "Processing issue", str(exc))
                finally:
                    self.refresh(None)

            threading.Thread(target=_task, daemon=True, name="llm-wiki-process-now").start()

        @rumps.clicked("Open Repo")
        def open_repo(self, _sender: object) -> None:
            subprocess.run(["open", repo_paths.base_dir.as_posix()], check=False)

        @rumps.clicked("Restart oamc")
        def restart_app(self, _sender: object) -> None:
            restart_managed_app(repo_paths.base_dir)
            stop_event.set()
            dashboard.stop()
            rumps.quit_application()

        @rumps.clicked("Reveal oamc.app")
        def reveal_app(self, _sender: object) -> None:
            reveal_installed_app(repo_paths.base_dir)

        @rumps.clicked("Quit oamc")
        def quit_app(self, _sender: object) -> None:
            stop_event.set()
            dashboard.stop()
            rumps.quit_application()

    OAMCMenuBar().run()
