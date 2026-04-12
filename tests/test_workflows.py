from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from llm_wiki.cli import app
from llm_wiki.llm.base import LLMClient
from llm_wiki.models import (
    IngestRequest,
    IngestResponse,
    LintRequest,
    LintResponse,
    PageDraft,
    QueryRequest,
    QueryResponse,
)


class FakeLLMClient(LLMClient):
    def ingest(self, request: IngestRequest) -> IngestResponse:
        return IngestResponse(
            source_page=PageDraft(
                relative_path="sources/20260412-sample-note.md",
                content="""---
title: Sample Note
tags:
  - llm
---
# Sample Note

## Summary

This source discusses a personal LLM wiki workflow.

## Key Claims

- Persistent markdown works well.

## Entities

- [[entities/obsidian]]

## Concepts

- [[concepts/llm-wiki]]
""",
            ),
            entity_pages=[
                PageDraft(
                    relative_path="entities/obsidian.md",
                    content="""---
title: Obsidian
tags:
  - tool
---
# Obsidian

## Overview

Obsidian is the browsing layer for the vault.
""",
                )
            ],
            concept_pages=[
                PageDraft(
                    relative_path="concepts/llm-wiki.md",
                    content="""---
title: LLM Wiki
tags:
  - pattern
---
# LLM Wiki

## Definition

An LLM-maintained markdown wiki that compounds knowledge over time.
""",
                )
            ],
            notes="Ingested the sample note and updated related pages.",
        )

    def query(self, request: QueryRequest) -> QueryResponse:
        return QueryResponse(
            page=PageDraft(
                relative_path="syntheses/design-patterns.md",
                content="""---
title: Design Patterns
tags:
  - synthesis
---
# Design Patterns

## Question

What are the main design patterns in my notes?

## Summary Answer

Use a compiled wiki, explicit source pages, and durable synthesis pages.
""",
            ),
            notes="Wrote a synthesis page for the question.",
        )

    def lint(self, request: LintRequest) -> LintResponse:
        return LintResponse(
            created_pages=[
                PageDraft(
                    relative_path="concepts/missing-link.md",
                    content="""---
title: Missing Link
tags:
  - concept
---
# Missing Link

## Definition

A placeholder concept page created during lint repair.
""",
                )
            ],
            updated_pages=[],
            notes="Repaired missing concept pages.",
        )


def test_cli_smoke_workflows(temp_workspace: Path, runner: CliRunner, monkeypatch) -> None:
    from llm_wiki import cli

    monkeypatch.setattr(cli, "build_client", lambda config: FakeLLMClient())

    source_path = temp_workspace / "raw" / "inbox" / "sample-note.md"
    source_path.write_text(
        "# Sample Note\n\nThis note references [[concepts/missing-link]].\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["ingest", "--base-dir", str(temp_workspace)])
    assert result.exit_code == 0, result.output
    assert (temp_workspace / "wiki" / "sources" / "20260412-sample-note.md").exists()

    result = runner.invoke(
        app,
        [
            "query",
            "What are the main design patterns in my notes?",
            "--write-page",
            "--base-dir",
            str(temp_workspace),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (temp_workspace / "wiki" / "syntheses" / "design-patterns.md").exists()

    result = runner.invoke(app, ["lint", "--base-dir", str(temp_workspace)])
    assert result.exit_code == 0, result.output
    assert (temp_workspace / "wiki" / "concepts" / "missing-link.md").exists()

    result = runner.invoke(app, ["rebuild-index", "--base-dir", str(temp_workspace)])
    assert result.exit_code == 0, result.output
    assert "[[concepts/missing-link]]" in (temp_workspace / "wiki" / "index.md").read_text(
        encoding="utf-8"
    )


def test_init_command_creates_workspace(tmp_path: Path, runner: CliRunner) -> None:
    result = runner.invoke(app, ["init", "--base-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "config" / "config.yaml").exists()
    assert (tmp_path / "wiki" / "index.md").exists()
