from __future__ import annotations

import os
from pathlib import Path

from llm_wiki.core.config import load_config


def test_load_config_from_repo_root(repo_root: Path) -> None:
    config, paths = load_config(repo_root)
    assert config.model_provider == "openai"
    assert paths.base_dir == repo_root
    assert paths.raw_inbox == repo_root / "raw" / "inbox"


def test_load_config_reads_repo_env(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "config.yaml").write_text(
        "model_provider: openai\nmodel_name: gpt-4.1\nopenai_api_key_env: OPENAI_API_KEY\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("OPENAI_API_KEY=test-key\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    load_config(tmp_path)

    assert os.environ["OPENAI_API_KEY"] == "test-key"
