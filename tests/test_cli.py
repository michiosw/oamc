from __future__ import annotations

from llm_wiki.cli import app
from llm_wiki.core.models import QueryResult


def test_query_open_uses_resolved_wiki_path(temp_workspace, runner, monkeypatch) -> None:
    from llm_wiki import cli

    opened: list[str] = []

    monkeypatch.setattr(cli, "build_client_or_exit", lambda config: object())
    monkeypatch.setattr(
        cli,
        "run_query",
        lambda config, repo_paths, client, question, write_page, template, top_k, scopes: QueryResult(
            page_path="syntheses/portable-launch.md",
            title="Portable Launch",
            answer_preview="## Summary Answer\n\nSaved answer.",
            template=template,
            selected_candidates=[],
            content="",
        ),
    )
    monkeypatch.setattr(cli, "open_in_obsidian", lambda base_dir, target: opened.append(target.as_posix()))

    result = runner.invoke(
        app,
        [
            "query",
            "What should be opened?",
            "--open",
            "--no-write-page",
            "--no-show-answer",
            "--base-dir",
            str(temp_workspace),
        ],
    )

    assert result.exit_code == 0
    assert opened == [f"{temp_workspace}/wiki/syntheses/portable-launch.md"]
