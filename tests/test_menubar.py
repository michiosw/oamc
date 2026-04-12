from __future__ import annotations

import plistlib
from pathlib import Path

from llm_wiki import menubar


def test_build_launch_agent_payload(temp_workspace: Path) -> None:
    payload = menubar.build_launch_agent_payload(
        temp_workspace,
        python_executable="/tmp/oamc-python",
    )

    assert payload["Label"] == menubar.LAUNCH_AGENT_LABEL
    assert payload["ProgramArguments"] == [
        "/tmp/oamc-python",
        "-m",
        "llm_wiki.cli",
        "menubar",
        "--base-dir",
        temp_workspace.as_posix(),
    ]
    assert payload["WorkingDirectory"] == temp_workspace.as_posix()
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] is True


def test_install_and_uninstall_launch_agent(tmp_path: Path, monkeypatch) -> None:
    base_dir = tmp_path / "repo"
    base_dir.mkdir()
    launch_dir = tmp_path / "Library" / "LaunchAgents"
    commands: list[list[str]] = []

    monkeypatch.setattr(menubar, "launch_agent_path", lambda home=None: launch_dir / f"{menubar.LAUNCH_AGENT_LABEL}.plist")
    monkeypatch.setattr(menubar, "_run_launchctl", lambda args, check: commands.append(args))

    agent_path = menubar.install_launch_agent(base_dir)
    assert agent_path.exists()
    payload = plistlib.loads(agent_path.read_bytes())
    assert payload["Label"] == menubar.LAUNCH_AGENT_LABEL
    assert payload["WorkingDirectory"] == base_dir.as_posix()
    assert commands[0][0] == "bootout"
    assert commands[1][0] == "bootstrap"

    removed_path = menubar.uninstall_launch_agent()
    assert removed_path == agent_path
    assert not agent_path.exists()
