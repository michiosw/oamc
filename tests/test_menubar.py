from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from llm_wiki.core.config import load_config
from llm_wiki.integrations import menubar


def test_build_launch_agent_payload(temp_workspace: Path) -> None:
    payload = menubar.build_launch_agent_payload(Path("/Applications/oamc.app"), base_dir=temp_workspace)

    assert payload["Label"] == menubar.LAUNCH_AGENT_LABEL
    assert payload["ProgramArguments"] == ["/Applications/oamc.app/Contents/MacOS/oamc"]
    assert payload["WorkingDirectory"] == temp_workspace.as_posix()
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] == {"SuccessfulExit": False}
    assert payload["LimitLoadToSessionType"] == "Aqua"


def test_build_app_bundle(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args, check=False, **kwargs):
        calls.append(args)
        if args[2] == "PyInstaller":
            dist_dir = Path(args[args.index("--distpath") + 1])
            bundle_path = dist_dir / menubar.APP_BUNDLE_NAME
            (bundle_path / "Contents").mkdir(parents=True, exist_ok=True)
            (bundle_path / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "oamc"}))

    monkeypatch.setattr(menubar.subprocess, "run", fake_run)

    bundle_path = menubar.build_app_bundle(
        tmp_path,
        target_path=tmp_path / "Applications" / menubar.APP_BUNDLE_NAME,
        python_executable="/tmp/oamc-python",
    )

    assert bundle_path.exists()
    info = plistlib.loads((bundle_path / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == menubar.APP_BUNDLE_ID
    assert info["LSUIElement"] is True
    assert calls[0][2] == "PyInstaller"
    assert "--windowed" in calls[0]
    assert "rumps" in calls[0]


def test_install_and_uninstall_launch_agent(tmp_path: Path, monkeypatch) -> None:
    base_dir = tmp_path / "repo"
    base_dir.mkdir()
    launch_dir = tmp_path / "Library" / "LaunchAgents"
    apps_dir = tmp_path / "Applications"
    commands: list[list[str]] = []
    subprocess_calls: list[list[str]] = []

    monkeypatch.setattr(menubar, "launch_agent_path", lambda home=None: launch_dir / f"{menubar.LAUNCH_AGENT_LABEL}.plist")
    monkeypatch.setattr(menubar, "app_bundle_path", lambda home=None: apps_dir / menubar.APP_BUNDLE_NAME)
    monkeypatch.setattr(menubar, "_run_launchctl", lambda args, check: commands.append(args))

    def fake_run(args, check=False, **kwargs):
        if len(args) > 2 and args[2] == "PyInstaller":
            dist_dir = Path(args[args.index("--distpath") + 1])
            bundle_path = dist_dir / menubar.APP_BUNDLE_NAME
            (bundle_path / "Contents").mkdir(parents=True, exist_ok=True)
            (bundle_path / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "oamc"}))
            return None
        subprocess_calls.append(args)
        return None

    monkeypatch.setattr(menubar.subprocess, "run", fake_run)

    agent_path, app_path = menubar.install_launch_agent(base_dir)
    assert agent_path.exists()
    assert app_path.exists()
    payload = plistlib.loads(agent_path.read_bytes())
    assert payload["Label"] == menubar.LAUNCH_AGENT_LABEL
    assert payload["WorkingDirectory"] == base_dir.as_posix()
    assert commands[0][0] == "bootout"
    assert commands[1][0] == "bootstrap"
    assert commands[2][0] == "kickstart"
    assert ["osascript", "-e", f'tell application id "{menubar.APP_BUNDLE_ID}" to quit'] in [
        call[:3] for call in subprocess_calls if len(call) >= 3
    ]
    assert ["pkill", "-x", menubar.APP_NAME] in [call[:3] for call in subprocess_calls if len(call) >= 3]

    removed_agent_path, removed_app_path = menubar.uninstall_launch_agent()
    assert removed_agent_path == agent_path
    assert removed_app_path == app_path
    assert not agent_path.exists()
    assert not app_path.exists()


def test_restart_managed_app_prefers_launch_agent(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(menubar, "launch_agent_path", lambda home=None: tmp_path / "dev.oamc.studio.plist")
    (tmp_path / "dev.oamc.studio.plist").write_text("", encoding="utf-8")
    monkeypatch.setattr(menubar.subprocess, "run", lambda args, check=False, **kwargs: calls.append(args))

    menubar.restart_managed_app(tmp_path)

    assert calls == [["launchctl", "kickstart", "-k", f"gui/{menubar.os.getuid()}/{menubar.LAUNCH_AGENT_LABEL}"]]


def test_reveal_installed_app_reveals_bundle_or_repo(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []
    apps_dir = tmp_path / "Applications"
    bundle = apps_dir / menubar.APP_BUNDLE_NAME
    (bundle / "Contents").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(menubar, "app_bundle_path", lambda home=None: bundle)
    monkeypatch.setattr(menubar.subprocess, "run", lambda args, check=False, **kwargs: calls.append(args))

    menubar.reveal_installed_app(tmp_path / "repo")
    assert calls[-1] == ["open", "-R", bundle.as_posix()]

    shutil_target = tmp_path / "repo"
    calls.clear()
    bundle.unlink(missing_ok=True) if bundle.is_file() else None
    import shutil

    shutil.rmtree(bundle.parent, ignore_errors=True)
    menubar.reveal_installed_app(shutil_target)
    assert calls[-1] == ["open", shutil_target.as_posix()]


def test_menu_status_and_activity_labels_are_human_friendly() -> None:
    assert menubar._menu_status_title("ok", 0) == "Status: Healthy · inbox clear"
    assert menubar._menu_status_title("warn", 2) == "Status: Needs attention · 2 in inbox"
    assert menubar._short_activity_label("[2026-04-13] query | What does the wiki currently know about preparing Codex for Xcode and Swift projects?") == "query · What does the wiki currently know about..."
    assert menubar._short_activity_label("No activity yet") == "No activity yet"


def test_background_tasks_propagate_unexpected_errors(temp_workspace: Path, monkeypatch) -> None:
    config, repo_paths = load_config(temp_workspace)

    def fake_capture(*args, **kwargs):
        raise RuntimeError("capture failed")

    def fake_process(*args, **kwargs):
        raise RuntimeError("process failed")

    monkeypatch.setattr(menubar, "capture_clipboard_to_inbox", fake_capture)

    with pytest.raises(RuntimeError, match="capture failed"):
        menubar._capture_clipboard_and_process(
            config=config,
            repo_paths=repo_paths,
            process_lock=menubar.threading.Lock(),
            lint=True,
            notify=lambda *args, **kwargs: None,
        )

    monkeypatch.setattr(menubar, "capture_clipboard_to_inbox", lambda *args, **kwargs: None)
    monkeypatch.setattr(menubar, "run_process_once", fake_process)
    monkeypatch.setattr(menubar, "OpenAIWikiClient", lambda config: object())

    with pytest.raises(RuntimeError, match="process failed"):
        menubar._process_inbox(
            config=config,
            repo_paths=repo_paths,
            process_lock=menubar.threading.Lock(),
            lint=True,
            notify=lambda *args, **kwargs: None,
        )


def test_background_action_notifies_logs_and_refreshes_before_reraising(monkeypatch) -> None:
    notifications: list[tuple[str, str, str]] = []
    log_events: list[dict[str, str]] = []
    refreshed = False

    def fail() -> None:
        raise RuntimeError("background failed")

    def refresh() -> None:
        nonlocal refreshed
        refreshed = True

    def fake_log_event(logger, event: str, **fields: str) -> None:
        log_events.append({"event": event, **fields})

    monkeypatch.setattr(menubar, "log_event", fake_log_event)

    with pytest.raises(RuntimeError, match="background failed"):
        menubar._run_background_action(
            action="capture_clipboard",
            failure_title="Clipboard capture issue",
            task=fail,
            notify_user=lambda title, subtitle, message: notifications.append(
                (title, subtitle, message)
            ),
            refresh=refresh,
        )

    assert notifications == [
        (menubar.APP_NAME, "Clipboard capture issue", "background failed")
    ]
    assert log_events == [
        {
            "event": "menubar_background_action_failed",
            "action": "capture_clipboard",
            "error": "background failed",
        }
    ]
    assert refreshed is True
