from __future__ import annotations

from pathlib import Path

from llm_wiki.config import load_config


def test_load_config_from_repo_root(repo_root: Path) -> None:
    config, paths = load_config(repo_root)
    assert config.model_provider == "openai"
    assert paths.base_dir == repo_root
    assert paths.raw_inbox == repo_root / "raw" / "inbox"
