from __future__ import annotations

from pathlib import Path

import typer

from llm_wiki.config import load_config, write_default_config
from llm_wiki.llm.base import LLMClient
from llm_wiki.llm.openai_client import OpenAIWikiClient
from llm_wiki.ops.ingest import ingest_sources
from llm_wiki.ops.lint import run_lint
from llm_wiki.ops.query import run_query
from llm_wiki.ops.rebuild_index import rebuild_index
from llm_wiki.paths import ensure_structure


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


@app.command()
def init(
    base_dir: Path = typer.Option(Path.cwd(), "--base-dir", resolve_path=True),
) -> None:
    initialize_workspace(base_dir.resolve())
    typer.echo(f"Initialized LLM wiki workspace at {base_dir.resolve()}")


@app.command()
def ingest(
    source_paths: list[Path] = typer.Argument(None),
    base_dir: Path | None = typer.Option(None, "--base-dir", resolve_path=True),
) -> None:
    config, repo_paths = load_config(base_dir)
    client = build_client(config)
    paths = source_paths or sorted(repo_paths.raw_inbox.glob("*"))
    touched = ingest_sources(config, repo_paths, client, paths)
    typer.echo("Ingest complete")
    for path in touched:
        typer.echo(path)


@app.command()
def query(
    question: str = typer.Argument(...),
    write_page: bool = typer.Option(False, "--write-page"),
    base_dir: Path | None = typer.Option(None, "--base-dir", resolve_path=True),
) -> None:
    config, repo_paths = load_config(base_dir)
    client = build_client(config)
    touched = run_query(config, repo_paths, client, question, write_page=write_page)
    typer.echo("Query complete")
    for path in touched:
        typer.echo(path)


@app.command()
def lint(
    base_dir: Path | None = typer.Option(None, "--base-dir", resolve_path=True),
) -> None:
    config, repo_paths = load_config(base_dir)
    client = build_client(config)
    issues, touched = run_lint(config, repo_paths, client)
    typer.echo(f"Lint complete ({len(issues)} issues)")
    for path in touched:
        typer.echo(path)


@app.command("rebuild-index")
def rebuild_index_command(
    base_dir: Path | None = typer.Option(None, "--base-dir", resolve_path=True),
) -> None:
    _, repo_paths = load_config(base_dir)
    rebuild_index(repo_paths)
    typer.echo(f"Rebuilt {repo_paths.index}")


def main() -> None:
    app()
