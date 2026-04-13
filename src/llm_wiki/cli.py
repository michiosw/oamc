from __future__ import annotations

import os
import threading
import webbrowser
from pathlib import Path
from typing import cast

import typer

from llm_wiki.core.config import load_config, write_default_config
from llm_wiki.core.health import build_doctor_report
from llm_wiki.core.models import (
    RESEARCH_TEMPLATES,
    AppConfig,
    DoctorReport,
    LintResult,
    QueryResult,
    RepoPaths,
    ResearchTemplate,
)
from llm_wiki.core.paths import ensure_structure, is_placeholder_artifact, repo_relative
from llm_wiki.core.telemetry import configure_logging, get_logger, log_event
from llm_wiki.integrations.menubar import install_launch_agent, run_menubar, uninstall_launch_agent
from llm_wiki.integrations.obsidian import open_in_obsidian
from llm_wiki.llm.base import LLMClient
from llm_wiki.llm.openai_client import OpenAIWikiClient
from llm_wiki.ops.ingest import ingest_sources
from llm_wiki.ops.lint import run_lint
from llm_wiki.ops.query import run_query
from llm_wiki.ops.rebuild_index import rebuild_index
from llm_wiki.ops.search import iter_wiki_pages
from llm_wiki.runtime.studio import DashboardServer, run_process_once, watch_loop

app = typer.Typer(help="Maintain a local-first markdown wiki with an LLM.")
LOGGER = get_logger(__name__)

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


def build_client(config: AppConfig) -> LLMClient:
    return OpenAIWikiClient(config)


def build_client_or_exit(config: AppConfig) -> LLMClient:
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
        "# LLM Wiki Schema\n\nSchema version: `1`\n\nKeep wiki pages structured and linked.\n",
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


def _print_query_result(result: QueryResult) -> None:
    if result.page_path:
        typer.echo(f"Saved page: {result.page_path}")
    typer.echo(f"Template: {result.template}")
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
    open_in_obsidian(base_dir, absolute)


def _print_doctor_report(report: DoctorReport) -> None:
    typer.echo("Doctor report")
    typer.echo(f"- Overall: {report.overall_status}")
    if report.latest_processed_source:
        typer.echo(f"- Latest source: {report.latest_processed_source}")
    if report.latest_log_heading:
        typer.echo(f"- Latest log: {report.latest_log_heading}")
    typer.echo("")
    for check in report.checks:
        typer.echo(f"- {check.label}: {check.status} — {check.detail}")
    typer.echo("")
    typer.echo(f"Recommended next step: {report.recommended_next_step}")


def _emit(message: str) -> None:
    typer.echo(message)


def _exit_on_runtime_error(exc: RuntimeError) -> None:
    log_event(LOGGER, "command_runtime_error", error=str(exc))
    typer.echo(str(exc))
    raise typer.Exit(code=1) from exc


def load_config_or_exit(base_dir: Path | None) -> tuple[AppConfig, RepoPaths]:
    try:
        return load_config(base_dir)
    except (RuntimeError, FileNotFoundError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc


@app.command()
def init(
    base_dir: Path = typer.Option(Path.cwd(), "--base-dir", resolve_path=True),
) -> None:
    log_event(LOGGER, "command_started", command="init", base_dir=base_dir.resolve())
    initialize_workspace(base_dir.resolve())
    typer.echo(f"Initialized LLM wiki workspace at {base_dir.resolve()}")


@app.command()
def ingest(
    source_paths: list[Path] = typer.Argument(None),
    lint: bool = typer.Option(False, "--lint/--no-lint"),
    base_dir: Path | None = typer.Option(None, "--base-dir", resolve_path=True),
) -> None:
    config, repo_paths = load_config_or_exit(base_dir)
    log_event(LOGGER, "command_started", command="ingest", base_dir=repo_paths.base_dir)
    paths = [path for path in (source_paths or sorted(repo_paths.raw_inbox.glob("*"))) if not is_placeholder_artifact(path)]
    if not paths:
        typer.echo("Inbox is empty. Drop files into raw/inbox/ first.")
        raise typer.Exit()
    client = build_client_or_exit(config)
    try:
        result = ingest_sources(config, repo_paths, client, paths)
    except RuntimeError as exc:
        _exit_on_runtime_error(exc)
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
    template: str = typer.Option("synthesis", "--template"),
    scope: list[str] = typer.Option(None, "--scope"),
    top_k: int = typer.Option(6, "--top-k", min=1),
    open_page: bool = typer.Option(False, "--open"),
    base_dir: Path | None = typer.Option(None, "--base-dir", resolve_path=True),
) -> None:
    if template not in RESEARCH_TEMPLATES:
        typer.echo(f"Unsupported template: {template}. Choose from: {', '.join(RESEARCH_TEMPLATES)}")
        raise typer.Exit(code=1)
    template_name = cast(ResearchTemplate, template)
    config, repo_paths = load_config_or_exit(base_dir)
    log_event(LOGGER, "command_started", command="query", base_dir=repo_paths.base_dir, template=template_name)
    client = build_client_or_exit(config)
    try:
        result = run_query(
            config,
            repo_paths,
            client,
            question,
            write_page=write_page,
            template=template_name,
            top_k=top_k,
            scopes=scope,
        )
    except RuntimeError as exc:
        _exit_on_runtime_error(exc)
    typer.echo("Query complete")
    if show_answer:
        _print_query_result(result)
    if open_page and result.page_path:
        _open_path(repo_paths.base_dir, result.page_path)


@app.command()
def lint(
    base_dir: Path | None = typer.Option(None, "--base-dir", resolve_path=True),
) -> None:
    config, repo_paths = load_config_or_exit(base_dir)
    log_event(LOGGER, "command_started", command="lint", base_dir=repo_paths.base_dir)
    client = build_client_or_exit(config)
    try:
        result = run_lint(config, repo_paths, client)
    except RuntimeError as exc:
        _exit_on_runtime_error(exc)
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
    config, repo_paths = load_config_or_exit(base_dir)
    log_event(LOGGER, "command_started", command="process", base_dir=repo_paths.base_dir, lint=lint)
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
    process_lint_result: LintResult | None
    try:
        ingest_result, process_lint_result = run_process_once(config, repo_paths, client, lint=lint, emit=_emit)
    except RuntimeError as exc:
        _exit_on_runtime_error(exc)
    if ingest_result.source_pages:
        _render_list("Source pages:", ingest_result.source_pages)
    if ingest_result.entity_pages:
        _render_list("Entity pages:", ingest_result.entity_pages)
    if ingest_result.concept_pages:
        _render_list("Concept pages:", ingest_result.concept_pages)
    if process_lint_result and process_lint_result.normalized_pages:
        _render_list("Normalized pages:", process_lint_result.normalized_pages)


@app.command()
def status(
    base_dir: Path | None = typer.Option(None, "--base-dir", resolve_path=True),
) -> None:
    config, repo_paths = load_config_or_exit(base_dir)
    log_event(LOGGER, "command_started", command="status", base_dir=repo_paths.base_dir)
    inbox_paths = sorted(repo_paths.raw_inbox.glob("*"))
    total_pages = len(iter_wiki_pages(repo_paths))
    report = build_doctor_report(config, repo_paths)

    typer.echo("LLM Wiki Status")
    typer.echo(f"- Inbox files: {len(inbox_paths)}")
    typer.echo(f"- Wiki pages: {total_pages}")
    typer.echo(f"- Last log entry: {report.latest_log_heading or 'none'}")
    typer.echo(f"- Health: {report.overall_status}")
    typer.echo(f"- Recommended next step: {report.recommended_next_step}")
    if report.clippings_files:
        typer.echo("- Warning: stray markdown exists in Clippings/. Retarget Web Clipper to raw/inbox/.")
    if inbox_paths:
        _render_list(
            "Inbox:",
            [repo_relative(path, repo_paths.base_dir) for path in inbox_paths],
        )


@app.command()
def doctor(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8421, "--port", min=1, max=65535),
    base_dir: Path | None = typer.Option(None, "--base-dir", resolve_path=True),
) -> None:
    config, repo_paths = load_config_or_exit(base_dir)
    log_event(LOGGER, "command_started", command="doctor", base_dir=repo_paths.base_dir, host=host, port=port)
    report = build_doctor_report(config, repo_paths, host=host, port=port)
    _print_doctor_report(report)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8421, "--port", min=1, max=65535),
    open_browser: bool = typer.Option(True, "--open/--no-open"),
    base_dir: Path | None = typer.Option(None, "--base-dir", resolve_path=True),
) -> None:
    _, repo_paths = load_config_or_exit(base_dir)
    log_event(LOGGER, "command_started", command="serve", base_dir=repo_paths.base_dir, host=host, port=port)
    server = DashboardServer(repo_paths, host=host, port=port)
    url = server.url
    typer.echo(f"Serving wiki dashboard at {url}")
    if open_browser:
        webbrowser.open(url)
    server.start()
    try:
        while True:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        server.stop()


@app.command()
def start(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8421, "--port", min=1, max=65535),
    interval: float = typer.Option(2.0, "--interval", min=0.5),
    lint: bool = typer.Option(True, "--lint/--no-lint"),
    open_browser: bool = typer.Option(True, "--open/--no-open"),
    base_dir: Path | None = typer.Option(None, "--base-dir", resolve_path=True),
) -> None:
    config, repo_paths = load_config_or_exit(base_dir)
    log_event(LOGGER, "command_started", command="start", base_dir=repo_paths.base_dir, host=host, port=port)
    typer.echo("Starting oamc studio")
    typer.echo(f"- Dashboard: http://{host}:{port}")
    typer.echo(f"- Inbox watch: {repo_relative(repo_paths.raw_inbox, repo_paths.base_dir)}")
    watch_thread = threading.Thread(
        target=watch_loop,
        kwargs={
            "config": config,
            "repo_paths": repo_paths,
            "client_factory": lambda: build_client(config),
            "lint": lint,
            "interval": interval,
            "emit": _emit,
        },
        daemon=True,
        name="llm-wiki-watch",
    )
    watch_thread.start()
    serve(
        host=host,
        port=port,
        open_browser=open_browser,
        base_dir=repo_paths.base_dir,
    )


@app.command()
def watch(
    lint: bool = typer.Option(True, "--lint/--no-lint"),
    interval: float = typer.Option(2.0, "--interval", min=0.5),
    base_dir: Path | None = typer.Option(None, "--base-dir", resolve_path=True),
) -> None:
    config, repo_paths = load_config_or_exit(base_dir)
    log_event(LOGGER, "command_started", command="watch", base_dir=repo_paths.base_dir, interval=interval)
    typer.echo(f"Watching {repo_relative(repo_paths.raw_inbox, repo_paths.base_dir)} every {interval:.1f}s. Press Ctrl+C to stop.")

    try:
        watch_loop(
            config,
            repo_paths,
            client_factory=lambda: build_client(config),
            lint=lint,
            interval=interval,
            emit=_emit,
        )
    except RuntimeError as exc:
        _exit_on_runtime_error(exc)
    except KeyboardInterrupt:
        typer.echo("Stopped watching.")


@app.command()
def menubar(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8421, "--port", min=1, max=65535),
    interval: float = typer.Option(2.0, "--interval", min=0.5),
    lint: bool = typer.Option(True, "--lint/--no-lint"),
    open_browser: bool = typer.Option(False, "--open/--no-open"),
    base_dir: Path | None = typer.Option(None, "--base-dir", resolve_path=True),
) -> None:
    try:
        log_event(LOGGER, "command_started", command="menubar", base_dir=base_dir or Path.cwd(), host=host, port=port)
        run_menubar(
            base_dir=base_dir,
            host=host,
            port=port,
            interval=interval,
            lint=lint,
            open_browser=open_browser,
        )
    except ModuleNotFoundError as exc:
        if exc.name == "rumps":
            typer.echo("Missing optional dependency: rumps. Run `uv sync` to install the menubar app dependencies.")
            raise typer.Exit(code=1) from exc
        raise


@app.command("install-menubar")
def install_menubar_command(
    base_dir: Path | None = typer.Option(None, "--base-dir", resolve_path=True),
) -> None:
    _, repo_paths = load_config_or_exit(base_dir)
    log_event(LOGGER, "command_started", command="install-menubar", base_dir=repo_paths.base_dir)
    agent_path, app_path = install_launch_agent(repo_paths.base_dir)
    typer.echo(f"Installed macOS app at {app_path}")
    typer.echo(f"Installed menubar login item at {agent_path}")


@app.command("uninstall-menubar")
def uninstall_menubar_command() -> None:
    log_event(LOGGER, "command_started", command="uninstall-menubar")
    agent_path, app_path = uninstall_launch_agent()
    typer.echo(f"Removed menubar login item at {agent_path}")
    typer.echo(f"Removed macOS app at {app_path}")


@app.command("rebuild-index")
def rebuild_index_command(
    base_dir: Path | None = typer.Option(None, "--base-dir", resolve_path=True),
) -> None:
    _, repo_paths = load_config_or_exit(base_dir)
    log_event(LOGGER, "command_started", command="rebuild-index", base_dir=repo_paths.base_dir)
    rebuild_index(repo_paths)
    typer.echo(f"Rebuilt {repo_paths.index}")


def main() -> None:
    configure_logging()
    app()
