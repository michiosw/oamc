from __future__ import annotations

from pathlib import Path

from llm_wiki.integrations.obsidian import obsidian_url


def test_obsidian_url_uses_vault_name_and_relative_path(tmp_path: Path) -> None:
    base_dir = tmp_path / "oamc"
    target = base_dir / "wiki" / "syntheses" / "design patterns.md"
    url = obsidian_url(base_dir, target)

    assert url == "obsidian://open?vault=oamc&file=wiki/syntheses/design%20patterns.md"
