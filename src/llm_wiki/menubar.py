from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import threading
from pathlib import Path

from llm_wiki.config import load_config
from llm_wiki.llm.openai_client import OpenAIWikiClient
from llm_wiki.studio import DashboardServer, inbox_count, latest_log_heading, run_process_once, watch_loop


LAUNCH_AGENT_LABEL = "dev.oamc.studio"


def launch_agent_path(home: Path | None = None) -> Path:
    root = (home or Path.home()).expanduser()
    return root / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"


def build_launch_agent_payload(base_dir: Path, *, python_executable: str | None = None) -> dict[str, object]:
    python_path = python_executable or sys.executable
    log_dir = base_dir / ".oamc"
    stdout_log = log_dir / "menubar.log"
    stderr_log = log_dir / "menubar.error.log"
    return {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [
            python_path,
            "-m",
            "llm_wiki.cli",
            "menubar",
            "--base-dir",
            base_dir.as_posix(),
        ],
        "WorkingDirectory": base_dir.as_posix(),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Interactive",
        "StandardOutPath": stdout_log.as_posix(),
        "StandardErrorPath": stderr_log.as_posix(),
        "EnvironmentVariables": {
            "PATH": os.environ.get("PATH", ""),
        },
    }


def install_launch_agent(base_dir: Path) -> Path:
    agent_path = launch_agent_path()
    agent_path.parent.mkdir(parents=True, exist_ok=True)
    (base_dir / ".oamc").mkdir(parents=True, exist_ok=True)
    payload = build_launch_agent_payload(base_dir)
    agent_path.write_bytes(plistlib.dumps(payload))
    _run_launchctl(["bootout", f"gui/{os.getuid()}", agent_path.as_posix()], check=False)
    _run_launchctl(["bootstrap", f"gui/{os.getuid()}", agent_path.as_posix()], check=True)
    _run_launchctl(["kickstart", "-k", f"gui/{os.getuid()}/{LAUNCH_AGENT_LABEL}"], check=False)
    return agent_path


def uninstall_launch_agent() -> Path:
    agent_path = launch_agent_path()
    if agent_path.exists():
        _run_launchctl(["bootout", f"gui/{os.getuid()}", agent_path.as_posix()], check=False)
        agent_path.unlink()
    return agent_path


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
            rumps.notification("oamc", "Ingest complete", message)
        elif message.startswith("Missing required environment variable"):
            rumps.notification("oamc", "Configuration issue", message)

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
            super().__init__("oamc", quit_button=None)
            self.menu = [
                "Status",
                None,
                "Open Dashboard",
                "Open Obsidian",
                None,
                "Process Inbox Now",
                "Open Repo",
                None,
                "Quit oamc",
            ]
            self.title = "oamc"
            self._status_item = self.menu["Status"]
            self._status_item.set_callback(None)
            self._timer = rumps.Timer(self.refresh, 5)
            self._timer.start()
            self.refresh(None)

        def refresh(self, _sender) -> None:
            pending = inbox_count(repo_paths)
            heading = latest_log_heading(repo_paths) or "No activity yet"
            self.title = f"oamc · {pending}" if pending else "oamc"
            self._status_item.title = f"Inbox: {pending} · {heading}"

        @rumps.clicked("Open Dashboard")
        def open_dashboard(self, _sender) -> None:
            subprocess.run(["open", dashboard.url], check=False)

        @rumps.clicked("Open Obsidian")
        def open_obsidian(self, _sender) -> None:
            subprocess.run(["open", "-a", "Obsidian", repo_paths.base_dir.as_posix()], check=False)

        @rumps.clicked("Process Inbox Now")
        def process_now(self, _sender) -> None:
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
                except RuntimeError as exc:
                    rumps.notification("oamc", "Configuration issue", str(exc))
                finally:
                    self.refresh(None)

            threading.Thread(target=_task, daemon=True, name="llm-wiki-process-now").start()

        @rumps.clicked("Open Repo")
        def open_repo(self, _sender) -> None:
            subprocess.run(["open", repo_paths.base_dir.as_posix()], check=False)

        @rumps.clicked("Quit oamc")
        def quit_app(self, _sender) -> None:
            stop_event.set()
            dashboard.stop()
            rumps.quit_application()

    OAMCMenuBar().run()
