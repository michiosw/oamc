from __future__ import annotations

from pathlib import Path

from llm_wiki.integrations import obsidian


def test_obsidian_url_uses_vault_name_and_relative_path(tmp_path: Path) -> None:
    base_dir = tmp_path / "oamc"
    target = base_dir / "wiki" / "syntheses" / "design patterns.md"
    url = obsidian.obsidian_url(base_dir, target)

    assert url == "obsidian://open?vault=oamc&file=wiki/syntheses/design%20patterns.md"


def test_open_in_obsidian_uses_single_url_launch(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    class Result:
        returncode = 1

    monkeypatch.setattr(obsidian.subprocess, "run", lambda args, check=False: calls.append(args) or Result())

    base_dir = tmp_path / "oamc"
    target = base_dir / "wiki" / "syntheses" / "design patterns.md"

    obsidian.open_in_obsidian(base_dir, target)

    assert calls == [["open", "obsidian://open?vault=oamc&file=wiki/syntheses/design%20patterns.md"]]
