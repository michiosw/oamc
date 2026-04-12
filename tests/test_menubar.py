from __future__ import annotations

import plistlib
from pathlib import Path

from llm_wiki import menubar


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
