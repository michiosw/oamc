from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from llm_wiki.cli import initialize_workspace


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture()
def temp_workspace(tmp_path: Path) -> Path:
    initialize_workspace(tmp_path)
    return tmp_path
