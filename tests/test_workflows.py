from __future__ import annotations

from pathlib import Path

import pytest
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
from llm_wiki.ops.ingest import ingest_sources
from llm_wiki.config import load_config


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
                    relative_path="wiki/entities/obsidian.md",
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


class CapturingQueryLLMClient(LLMClient):
    def __init__(self) -> None:
        self.last_request: QueryRequest | None = None

    def ingest(self, request: IngestRequest) -> IngestResponse:
        raise NotImplementedError

    def query(self, request: QueryRequest) -> QueryResponse:
        self.last_request = request
        return QueryResponse(
            page=PageDraft(
                relative_path="syntheses/scoped-answer.md",
                content="""---
title: Scoped Answer
tags:
  - synthesis
---
# Scoped Answer

## Summary Answer

Scoped answer.
""",
            )
        )

    def lint(self, request: LintRequest) -> LintResponse:
        raise NotImplementedError


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
            "--base-dir",
            str(temp_workspace),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (temp_workspace / "wiki" / "syntheses" / "design-patterns.md").exists()
    assert "# Design Patterns" in result.output
    assert "compiled wiki" in result.output

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


def test_ingest_with_empty_inbox_exits_cleanly(temp_workspace: Path, runner: CliRunner, monkeypatch) -> None:
    from llm_wiki import cli

    monkeypatch.setattr(cli, "build_client", lambda config: FakeLLMClient())
    result = runner.invoke(app, ["ingest", "--base-dir", str(temp_workspace)])
    assert result.exit_code == 0, result.output
    assert "Inbox is empty" in result.output


def test_start_command_launches_watch_and_dashboard(temp_workspace: Path, runner: CliRunner, monkeypatch) -> None:
    from llm_wiki import cli

    started: dict[str, object] = {}

    class FakeThread:
        def __init__(self, *, target, kwargs, daemon, name) -> None:
            started["target"] = target
            started["kwargs"] = kwargs
            started["daemon"] = daemon
            started["name"] = name

        def start(self) -> None:
            started["started"] = True

    def fake_serve(*, host, port, open_browser, base_dir) -> None:
        started["serve"] = {
            "host": host,
            "port": port,
            "open_browser": open_browser,
            "base_dir": base_dir,
        }

    monkeypatch.setattr(cli.threading, "Thread", FakeThread)
    monkeypatch.setattr(cli, "serve", fake_serve)

    result = runner.invoke(app, ["start", "--no-open", "--base-dir", str(temp_workspace)])
    assert result.exit_code == 0, result.output
    assert started["started"] is True
    assert started["daemon"] is True
    assert started["name"] == "llm-wiki-watch"
    assert started["serve"] == {
        "host": "127.0.0.1",
        "port": 8421,
        "open_browser": False,
        "base_dir": temp_workspace,
    }


class FailingLLMClient(LLMClient):
    def ingest(self, request: IngestRequest) -> IngestResponse:
        return IngestResponse(
            source_page=PageDraft(
                relative_path="sources/example.md",
                content="# Example\n",
            ),
            entity_pages=[
                PageDraft(
                    relative_path="wiki/not-allowed/example.md",
                    content="# Invalid\n",
                )
            ],
        )

    def query(self, request: QueryRequest) -> QueryResponse:
        raise NotImplementedError

    def lint(self, request: LintRequest) -> LintResponse:
        raise NotImplementedError


def test_ingest_does_not_move_source_on_failure(temp_workspace: Path) -> None:
    source_path = temp_workspace / "raw" / "inbox" / "sample.md"
    source_path.write_text("# Sample\n", encoding="utf-8")
    config, paths = load_config(temp_workspace)

    with pytest.raises(ValueError):
        ingest_sources(config, paths, FailingLLMClient(), [source_path])

    assert source_path.exists()
    assert not any((temp_workspace / "raw" / "sources").iterdir())


class MalformedQueryLLMClient(LLMClient):
    def ingest(self, request: IngestRequest) -> IngestResponse:
        raise NotImplementedError

    def query(self, request: QueryRequest) -> QueryResponse:
        return QueryResponse(
            page=PageDraft(
                relative_path="syntheses/bad-frontmatter.md",
                content="""---
title: Summary: GPT-5.4 and frontend design
tags:
  - synthesis
---
# GPT-5.4 and Frontend Design

## Summary Answer

The wiki currently emphasizes prompt quality, design systems, and tool use.

## Sources
- [[sources/20260412-designing-delightful-frontends-with-gpt-5-4]]
""",
            ),
            notes="Wrote a synthesis page with malformed frontmatter.",
        )

    def lint(self, request: LintRequest) -> LintResponse:
        raise NotImplementedError


def test_query_recovers_from_malformed_frontmatter(temp_workspace: Path) -> None:
    from llm_wiki.ops.common import default_page
    from llm_wiki.ops.query import run_query

    (temp_workspace / "wiki" / "sources" / "20260412-designing-delightful-frontends-with-gpt-5-4.md").write_text(
        default_page(
            "sources/20260412-designing-delightful-frontends-with-gpt-5-4.md",
            "Designing delightful frontends with GPT-5.4",
            "# Summary\n\nA source page.\n\n## Sources\n- raw/sources/example.md",
        ),
        encoding="utf-8",
    )
    config, paths = load_config(temp_workspace)

    result = run_query(
        config,
        paths,
        MalformedQueryLLMClient(),
        "Summarize what the wiki currently knows about GPT-5.4 and frontend design.",
        write_page=True,
    )

    synthesis_path = temp_workspace / "wiki" / "syntheses" / "bad-frontmatter.md"
    content = synthesis_path.read_text(encoding="utf-8")
    assert synthesis_path.exists()
    assert "title: GPT-5.4 and Frontend Design" in content
    assert content.count("## Sources") == 1
    assert "syntheses/bad-frontmatter.md" in result.touched


def test_process_command_runs_ingest_and_lint(temp_workspace: Path, runner: CliRunner, monkeypatch) -> None:
    from llm_wiki import cli

    monkeypatch.setattr(cli, "build_client", lambda config: FakeLLMClient())
    source_path = temp_workspace / "raw" / "inbox" / "sample-note.md"
    source_path.write_text("# Sample Note\n", encoding="utf-8")

    result = runner.invoke(app, ["process", "--base-dir", str(temp_workspace)])
    assert result.exit_code == 0, result.output
    assert "Processed inbox" in result.output
    assert "Lint complete" in result.output


def test_status_command_reports_counts(temp_workspace: Path, runner: CliRunner) -> None:
    result = runner.invoke(app, ["status", "--base-dir", str(temp_workspace)])
    assert result.exit_code == 0, result.output
    assert "LLM Wiki Status" in result.output
    assert "Inbox files: 0" in result.output


def test_query_scope_filters_context(temp_workspace: Path) -> None:
    from llm_wiki.ops.common import default_page
    from llm_wiki.ops.query import run_query

    (temp_workspace / "wiki" / "entities" / "gpt-5-4.md").write_text(
        default_page(
            "entities/gpt-5-4.md",
            "GPT-5.4",
            "# GPT-5.4\n\nA model.\n\n## Sources\n- raw/sources/example.md",
        ),
        encoding="utf-8",
    )
    (temp_workspace / "wiki" / "concepts" / "frontend-design.md").write_text(
        default_page(
            "concepts/frontend-design.md",
            "Frontend Design",
            "# Frontend Design\n\nA concept.\n\n## Sources\n- raw/sources/example.md",
        ),
        encoding="utf-8",
    )
    config, paths = load_config(temp_workspace)
    client = CapturingQueryLLMClient()

    result = run_query(
        config,
        paths,
        client,
        "Summarize GPT-5.4",
        write_page=True,
        scopes=["entities/gpt-5-4"],
    )

    assert result.page_path == "syntheses/scoped-answer.md"
    assert client.last_request is not None
    assert [candidate.relative_path for candidate in client.last_request.candidates] == [
        "entities/gpt-5-4.md"
    ]
