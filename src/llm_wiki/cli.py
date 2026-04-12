from __future__ import annotations

import os
import subprocess
import time
import webbrowser
from pathlib import Path

import typer
import uvicorn

from llm_wiki.config import load_config, write_default_config
from llm_wiki.dashboard import create_dashboard_app
from llm_wiki.llm.base import LLMClient
from llm_wiki.llm.openai_client import OpenAIWikiClient
from llm_wiki.ops.ingest import ingest_sources
from llm_wiki.ops.lint import run_lint
from llm_wiki.ops.query import run_query
from llm_wiki.ops.rebuild_index import rebuild_index
from llm_wiki.ops.search import iter_wiki_pages
from llm_wiki.paths import ensure_structure, repo_relative


app = typer.Typer(help="Maintain a local-first markdown wiki with an LLM.")

MIT_LICENSE = """MIT License

Copyright (c) 2026 Michel Osswald

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def build_client(config) -> LLMClient:
    return OpenAIWikiClient(config)


def build_client_or_exit(config) -> LLMClient:
    try:
        return build_client(config)
    except RuntimeError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc


def _write_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content.rstrip() + "\n", encoding="utf-8")


def initialize_workspace(base_dir: Path) -> None:
    ensure_structure(base_dir)
    _write_if_missing(
        base_dir / "README.md",
        "# oamc\n\nBootstrapped LLM wiki repository.\n",
    )
    _write_if_missing(base_dir / "LICENSE", MIT_LICENSE)
    _write_if_missing(base_dir / ".env.example", "OPENAI_API_KEY=your_api_key_here\n")
    _write_if_missing(
        base_dir / ".gitignore",
        ".env\n.venv/\n__pycache__/\n.pytest_cache/\n",
    )
    _write_if_missing(
        base_dir / "AGENTS.md",
        "# AGENTS.md\n\nMaintain the wiki in `wiki/` and keep `raw/` immutable.\n",
    )
    if not (base_dir / "config" / "config.yaml").exists():
        write_default_config(base_dir)
    _write_if_missing(
        base_dir / "config" / "schema.md",
        "# LLM Wiki Schema\n\nKeep wiki pages structured and linked.\n",
    )
    _write_if_missing(
        base_dir / "wiki" / "index.md",
        "# Wiki Index\n\nThis file is maintained by `llm-wiki rebuild-index`.\n",
    )
    _write_if_missing(
        base_dir / "wiki" / "log.md",
        "# Wiki Log\n",
    )


def _render_list(title: str, items: list[str]) -> None:
    typer.echo(title)
    for item in items:
        typer.echo(f"- {item}")


def _last_log_heading(log_path: Path) -> str | None:
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            return line[3:].strip()
    return None


def _print_query_result(result) -> None:
    if result.page_path:
        typer.echo(f"Saved page: {result.page_path}")
    typer.echo("")
    typer.echo(f"# {result.title}")
    typer.echo("")
    typer.echo(result.answer_preview or "No summary answer section was generated.")
    if result.selected_candidates:
        typer.echo("")
        _render_list("Context pages:", result.selected_candidates)
    if result.touched:
        typer.echo("")
        _render_list("Touched files:", result.touched)


def _open_path(base_dir: Path, relative_path: str) -> None:
    absolute = (base_dir / "wiki" / relative_path.replace("wiki/", "")).resolve()
    subprocess.run(["open", absolute.as_posix()], check=False)


def _run_process_once(config, repo_paths, client, *, lint: bool) -> None:
    inbox_paths = sorted(repo_paths.raw_inbox.glob("*"))
    if not inbox_paths:
        typer.echo("Inbox is empty. Nothing to process.")
        return

    ingest_result = ingest_sources(config, repo_paths, client, inbox_paths)
    typer.echo(f"Processed inbox ({len(ingest_result.processed_sources)} source{'s' if len(ingest_result.processed_sources) != 1 else ''})")
    _render_list("Processed sources:", ingest_result.processed_sources)
    if ingest_result.source_pages:
        _render_list("Source pages:", ingest_result.source_pages)
    if lint:
        lint_result = run_lint(config, repo_paths, client)
        typer.echo(f"Lint complete ({len(lint_result.issues)} issues)")
        if lint_result.normalized_pages:
            _render_list("Normalized pages:", lint_result.normalized_pages)


def _inbox_snapshot(repo_paths) -> tuple[tuple[str, int, int], ...]:
    files = sorted(path for path in repo_paths.raw_inbox.glob("*") if path.is_file())
    return tuple(
        (
            path.name,
            path.stat().st_mtime_ns,
            path.stat().st_size,
        )
        for path in files
    )


@app.command()
def init(
    base_dir: Path = typer.Option(Path.cwd(), "--base-dir", resolve_path=True),
) -> None:
    initialize_workspace(base_dir.resolve())
    typer.echo(f"Initialized LLM wiki workspace at {base_dir.resolve()}")


@app.command()
def ingest(
    source_paths: list[Path] = typer.Argument(None),
    lint: bool = typer.Option(False, "--lint/--no-lint"),
    base_dir: Path | None = typer.Option(None, "--base-dir", resolve_path=True),
) -> None:
    config, repo_paths = load_config(base_dir)
    paths = source_paths or sorted(repo_paths.raw_inbox.glob("*"))
    if not paths:
        typer.echo("Inbox is empty. Drop files into raw/inbox/ first.")
        raise typer.Exit()
    client = build_client_or_exit(config)
    result = ingest_sources(config, repo_paths, client, paths)
    typer.echo(f"Ingest complete ({len(result.processed_sources)} source{'s' if len(result.processed_sources) != 1 else ''})")
    if result.processed_sources:
        _render_list("Processed sources:", result.processed_sources)
    if result.source_pages:
        _render_list("Source pages:", result.source_pages)
    if result.entity_pages:
        _render_list("Entity pages:", result.entity_pages)
    if result.concept_pages:
        _render_list("Concept pages:", result.concept_pages)
    if lint:
        lint_result = run_lint(config, repo_paths, client)
        typer.echo(f"Auto-lint complete ({len(lint_result.issues)} issues reviewed)")
        if lint_result.normalized_pages:
            _render_list("Normalized pages:", lint_result.normalized_pages)
        if lint_result.touched:
            _render_list("Touched files:", lint_result.touched)


@app.command()
def query(
    question: str = typer.Argument(...),
    write_page: bool = typer.Option(True, "--write-page/--no-write-page"),
    show_answer: bool = typer.Option(True, "--show-answer/--no-show-answer"),
    scope: list[str] = typer.Option(None, "--scope"),
    top_k: int = typer.Option(6, "--top-k", min=1),
    open_page: bool = typer.Option(False, "--open"),
    base_dir: Path | None = typer.Option(None, "--base-dir", resolve_path=True),
) -> None:
    config, repo_paths = load_config(base_dir)
    client = build_client_or_exit(config)
    result = run_query(
        config,
        repo_paths,
        client,
        question,
        write_page=write_page,
        top_k=top_k,
        scopes=scope,
    )
    typer.echo("Query complete")
    if show_answer:
        _print_query_result(result)
    if open_page and result.page_path:
        _open_path(repo_paths.base_dir, result.page_path)


@app.command()
def lint(
    base_dir: Path | None = typer.Option(None, "--base-dir", resolve_path=True),
) -> None:
    config, repo_paths = load_config(base_dir)
    client = build_client_or_exit(config)
    result = run_lint(config, repo_paths, client)
    typer.echo(f"Lint complete ({len(result.issues)} issues)")
    if result.normalized_pages:
        _render_list("Normalized pages:", result.normalized_pages)
    if result.touched:
        _render_list("Touched files:", result.touched)


@app.command()
def process(
    lint: bool = typer.Option(True, "--lint/--no-lint"),
    base_dir: Path | None = typer.Option(None, "--base-dir", resolve_path=True),
) -> None:
    config, repo_paths = load_config(base_dir)
    inbox_paths = sorted(repo_paths.raw_inbox.glob("*"))
    if not inbox_paths:
        typer.echo("Inbox is empty. Nothing to process.")
        if lint:
            if os.getenv(config.openai_api_key_env):
                client = build_client_or_exit(config)
                lint_result = run_lint(config, repo_paths, client)
                typer.echo(f"Lint complete ({len(lint_result.issues)} issues)")
            else:
                typer.echo(f"Skipping lint because {config.openai_api_key_env} is not set.")
        raise typer.Exit()

    client = build_client_or_exit(config)
    _run_process_once(config, repo_paths, client, lint=lint)


@app.command()
def status(
    base_dir: Path | None = typer.Option(None, "--base-dir", resolve_path=True),
) -> None:
    _, repo_paths = load_config(base_dir)
    inbox_paths = sorted(repo_paths.raw_inbox.glob("*"))
    total_pages = len(iter_wiki_pages(repo_paths))
    last_log = _last_log_heading(repo_paths.log)

    typer.echo("LLM Wiki Status")
    typer.echo(f"- Inbox files: {len(inbox_paths)}")
    typer.echo(f"- Wiki pages: {total_pages}")
    typer.echo(f"- Last log entry: {last_log or 'none'}")
    if inbox_paths:
        _render_list(
            "Inbox:",
            [repo_relative(path, repo_paths.base_dir) for path in inbox_paths],
        )


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8421, "--port", min=1, max=65535),
    open_browser: bool = typer.Option(True, "--open/--no-open"),
    base_dir: Path | None = typer.Option(None, "--base-dir", resolve_path=True),
) -> None:
    _, repo_paths = load_config(base_dir)
    app_instance = create_dashboard_app(repo_paths)
    url = f"http://{host}:{port}"
    typer.echo(f"Serving wiki dashboard at {url}")
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(app_instance, host=host, port=port, log_level="warning")


@app.command()
def watch(
    lint: bool = typer.Option(True, "--lint/--no-lint"),
    interval: float = typer.Option(2.0, "--interval", min=0.5),
    base_dir: Path | None = typer.Option(None, "--base-dir", resolve_path=True),
) -> None:
    config, repo_paths = load_config(base_dir)
    typer.echo(f"Watching {repo_relative(repo_paths.raw_inbox, repo_paths.base_dir)} every {interval:.1f}s. Press Ctrl+C to stop.")

    last_seen = _inbox_snapshot(repo_paths)
    last_processed: tuple[tuple[str, int, int], ...] | None = None
    pending_snapshot: tuple[tuple[str, int, int], ...] | None = last_seen or None
    client = None

    try:
        while True:
            snapshot = _inbox_snapshot(repo_paths)
            if snapshot and snapshot != last_seen:
                pending_snapshot = snapshot
                typer.echo("Detected inbox change. Waiting for files to settle...")
            elif snapshot and pending_snapshot is not None and snapshot == pending_snapshot and snapshot != last_processed:
                if client is None:
                    client = build_client_or_exit(config)
                typer.echo("Processing inbox...")
                _run_process_once(config, repo_paths, client, lint=lint)
                last_processed = snapshot
                pending_snapshot = None
            last_seen = snapshot
            time.sleep(interval)
    except KeyboardInterrupt:
        typer.echo("Stopped watching.")


@app.command("rebuild-index")
def rebuild_index_command(
    base_dir: Path | None = typer.Option(None, "--base-dir", resolve_path=True),
) -> None:
    _, repo_paths = load_config(base_dir)
    rebuild_index(repo_paths)
    typer.echo(f"Rebuilt {repo_paths.index}")


def main() -> None:
    app()
