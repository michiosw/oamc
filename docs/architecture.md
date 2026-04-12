# Architecture

## Core model

`oamc` follows the LLM Wiki pattern:

1. raw sources land in `raw/inbox/`
2. successful ingest moves them into `raw/sources/`
3. the application writes structured pages into `wiki/`
4. queries operate on the wiki, not directly on raw documents

## Package layout

- `src/llm_wiki/core/` contains config, models, paths, health checks, markdown helpers, and telemetry
- `src/llm_wiki/ops/` contains ingest, query, lint, rebuild, and search workflows
- `src/llm_wiki/runtime/` contains the dashboard and long-running studio runtime
- `src/llm_wiki/integrations/` contains Obsidian and macOS-specific integration code
- `src/llm_wiki/llm/` contains provider-facing client code

## Runtime model

The repo uses one shared runtime shape:

- the watcher processes new files in `raw/inbox/`
- the dashboard exposes search, browsing, and research prompts
- the macOS menubar app wraps the same runtime for always-on use

## Repository policy

The repository tracks product code, configuration, docs, and tests.

Live vault content in `raw/` and `wiki/` stays local by default and is intentionally ignored by git.
