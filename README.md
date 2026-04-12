# oamc

## About

`oamc` is a local-first LLM wiki for research workflows.

Raw sources go into `raw/`, the system compiles them into a maintained markdown wiki in `wiki/`, and Obsidian is the main reading/editing surface. The app layer adds a CLI, a local dashboard, and a macOS menubar runtime.

This repo is for the product code. Your live vault content stays local by default and is intentionally ignored by git.

## Inspirations

- Andrej Karpathy's April 4, 2026 X thread on LLM knowledge bases and the follow-up "LLM Wiki" idea file
- [`wiki-os`](https://github.com/Ansub/wiki-os) for the local wiki-app and dashboard direction

## What It Ships

- `llm-wiki`, a Python CLI
- a strict `raw/ -> wiki/` knowledge pipeline
- ingest, query, lint, status, doctor, watch, and process workflows
- a local dashboard for search, browse, and research prompts
- a macOS menubar app for always-on use

## Repository Shape

```text
oamc/
  config/
  raw/
  wiki/
  src/llm_wiki/
    core/
    integrations/
    llm/
    ops/
    runtime/
    cli.py
  tests/
```

- `core/` holds contracts, config, paths, health, markdown helpers, and telemetry
- `ops/` holds the write-path workflows
- `runtime/` holds the dashboard and studio runtime
- `integrations/` holds Obsidian and macOS bridges
- `llm/` holds provider-facing client code

## Quick Start

```bash
uv sync
cp .env.example .env
export OPENAI_API_KEY=...
uv run llm-wiki init
```

Recommended daily setup on macOS:

```bash
uv run llm-wiki install-menubar
```

That installs `oamc.app`, keeps the watcher and dashboard running, and removes the need to start the tool manually.

## Daily Flow

1. Clip a source into `raw/inbox/`
2. Let the watcher process it, or run `uv run llm-wiki process`
3. Review the generated pages in Obsidian
4. Ask a research question with `uv run llm-wiki query "..." --template synthesis`

Useful commands:

```bash
uv run llm-wiki process
uv run llm-wiki query "What does the wiki currently know about X?"
uv run llm-wiki status
uv run llm-wiki doctor
```

## Local Data Policy

This repository tracks product code, config, tests, and docs.

It does not track your live research corpus by default:

- `raw/inbox/`, `raw/sources/`, and `raw/assets/` stay local
- generated `wiki/` pages stay local
- `wiki/index.md` and `wiki/log.md` stay local

That keeps personal sources, syntheses, and vault activity out of normal open-source commits.

## Quality Gates

```bash
uv run pytest
uv run mypy src
python3 -m compileall src tests
uv run llm-wiki doctor
```

## Notes

- `raw/inbox/` is the only supported clipping destination
- `raw/` is immutable input; successful ingest moves files from `raw/inbox/` to `raw/sources/`
- `wiki/` is the maintained knowledge layer
- `CONTRIBUTING.md` and `SECURITY.md` are the source of truth for contributor workflow and disclosure
