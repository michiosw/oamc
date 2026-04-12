from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from llm_wiki.cli import app
from llm_wiki.health import build_doctor_report
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


def read_fixture(path: Path, name: str) -> str:
    return (path / "tests" / "fixtures" / "golden" / name).read_text(encoding="utf-8")


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


class RuntimeErrorLLMClient(LLMClient):
    def ingest(self, request: IngestRequest) -> IngestResponse:
        raise RuntimeError("OpenAI authentication failed. Update OPENAI_API_KEY in .env, then restart oamc.")

    def query(self, request: QueryRequest) -> QueryResponse:
        raise RuntimeError("OpenAI authentication failed. Update OPENAI_API_KEY in .env, then restart oamc.")

    def lint(self, request: LintRequest) -> LintResponse:
        raise RuntimeError("OpenAI authentication failed. Update OPENAI_API_KEY in .env, then restart oamc.")


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
            "--template",
            "decision-brief",
            "--base-dir",
            str(temp_workspace),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (temp_workspace / "wiki" / "syntheses" / "design-patterns.md").exists()
    assert "# Design Patterns" in result.output
    assert "compiled wiki" in result.output
    assert "Template: decision-brief" in result.output

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
    assert "Health:" in result.output


def test_doctor_reports_missing_api_key_and_clippings(temp_workspace: Path, runner: CliRunner, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    clippings_note = temp_workspace / "Clippings" / "stray.md"
    clippings_note.parent.mkdir(parents=True, exist_ok=True)
    clippings_note.write_text("# Stray\n", encoding="utf-8")

    result = runner.invoke(app, ["doctor", "--base-dir", str(temp_workspace)])
    assert result.exit_code == 0, result.output
    assert "Doctor report" in result.output
    assert "OpenAI API key: error" in result.output
    assert "Clipper destination: warn" in result.output


def test_doctor_reports_placeholder_api_key(temp_workspace: Path, runner: CliRunner, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (temp_workspace / ".env").write_text("OPENAI_API_KEY=your_api_key_here\n", encoding="utf-8")

    result = runner.invoke(app, ["doctor", "--base-dir", str(temp_workspace)])
    assert result.exit_code == 0, result.output
    assert "OpenAI API key: error" in result.output
    assert "placeholder value" in result.output


def test_doctor_report_detects_index_drift_and_malformed_page(temp_workspace: Path, monkeypatch) -> None:
    from llm_wiki.ops.common import default_page

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    good_page = temp_workspace / "wiki" / "concepts" / "frontend-design.md"
    good_page.write_text(
        default_page(
            "concepts/frontend-design.md",
            "Frontend Design",
            "# Frontend Design\n\nBody.\n\n## Sources\n- raw/sources/example.md",
        ),
        encoding="utf-8",
    )
    bad_page = temp_workspace / "wiki" / "concepts" / "bad.md"
    bad_page.write_text("# Bad page\n\nNo frontmatter.\n", encoding="utf-8")
    (temp_workspace / "wiki" / "index.md").write_text("# Wiki Index\n\n## Concepts\n\nNo pages yet.\n", encoding="utf-8")

    config, repo_paths = load_config(temp_workspace)
    report = build_doctor_report(config, repo_paths)

    index_check = next(check for check in report.checks if check.key == "index-drift")
    page_check = next(check for check in report.checks if check.key == "page-metadata")
    assert index_check.status == "warn"
    assert page_check.status == "warn"


def test_status_reports_pending_inbox_next_step(temp_workspace: Path, runner: CliRunner, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    (temp_workspace / "raw" / "inbox" / "queued.md").write_text("# Queued\n", encoding="utf-8")

    result = runner.invoke(app, ["status", "--base-dir", str(temp_workspace)])
    assert result.exit_code == 0, result.output
    assert "Inbox files: 1" in result.output
    assert "Inbox pending. Let the menubar watcher process it, or run llm-wiki process." in result.output


def test_process_command_reports_runtime_error_cleanly(temp_workspace: Path, runner: CliRunner, monkeypatch) -> None:
    from llm_wiki import cli

    monkeypatch.setattr(cli, "build_client", lambda config: RuntimeErrorLLMClient())
    (temp_workspace / "raw" / "inbox" / "sample-note.md").write_text("# Sample\n", encoding="utf-8")

    result = runner.invoke(app, ["process", "--no-lint", "--base-dir", str(temp_workspace)])
    assert result.exit_code == 1, result.output
    assert "OpenAI authentication failed." in result.output
    assert "Traceback" not in result.output


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
        template="compare",
        scopes=["entities/gpt-5-4"],
    )

    assert result.page_path == "syntheses/scoped-answer.md"
    assert result.template == "compare"
    assert client.last_request is not None
    assert client.last_request.template == "compare"
    assert [candidate.relative_path for candidate in client.last_request.candidates] == [
        "entities/gpt-5-4.md"
    ]


def test_ingest_matches_golden_source_page(temp_workspace: Path, repo_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import llm_wiki.ops.common as common
    import llm_wiki.ops.ingest as ingest_module

    monkeypatch.setattr(common, "now_iso", lambda: "2026-04-12T12:00:00+00:00")
    monkeypatch.setattr(
        ingest_module,
        "_planned_storage_destination",
        lambda repo_paths, source_path: repo_paths.raw_sources / "20260412-sample-note.md",
    )

    source_path = temp_workspace / "raw" / "inbox" / "sample-note.md"
    source_path.write_text("# Sample Note\n", encoding="utf-8")
    config, paths = load_config(temp_workspace)

    ingest_sources(config, paths, FakeLLMClient(), [source_path])

    actual = (temp_workspace / "wiki" / "sources" / "20260412-sample-note.md").read_text(encoding="utf-8")
    expected = read_fixture(repo_root, "ingest_source_page.md")
    assert actual == expected


def test_query_matches_golden_synthesis_page(temp_workspace: Path, repo_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import llm_wiki.ops.common as common
    from llm_wiki.ops.query import run_query

    monkeypatch.setattr(common, "now_iso", lambda: "2026-04-12T12:00:00+00:00")

    (temp_workspace / "wiki" / "sources" / "20260412-sample-note.md").write_text(
        read_fixture(repo_root, "ingest_source_page.md"),
        encoding="utf-8",
    )
    config, paths = load_config(temp_workspace)

    run_query(
        config,
        paths,
        FakeLLMClient(),
        "What are the main design patterns in my notes?",
        write_page=True,
    )

    actual = (temp_workspace / "wiki" / "syntheses" / "design-patterns.md").read_text(encoding="utf-8")
    expected = read_fixture(repo_root, "query_synthesis_page.md")
    assert actual == expected


def test_doctor_reports_schema_mismatch(temp_workspace: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    schema_path = temp_workspace / "config" / "schema.md"
    schema_path.write_text(schema_path.read_text(encoding="utf-8").replace("Schema version: `1`", "Schema version: `2`"), encoding="utf-8")

    result = runner.invoke(app, ["doctor", "--base-dir", str(temp_workspace)])
    assert result.exit_code == 0, result.output
    assert "Schema contract: warn" in result.output
    assert "schema.md=2" in result.output
