# oamc

`oamc` is a local-first LLM wiki project: raw sources go in, a maintained markdown wiki comes out, and Obsidian is the UI for browsing the result.

This repo is designed for a single user working in one vault. The wiki is the artifact. The LLM does the bookkeeping: source summaries, concept pages, entity pages, synthesis pages, backlinks, index updates, and log entries.

## What this ships

- A git-backed Obsidian-friendly vault layout
- A Python CLI, `llm-wiki`
- A strict schema for how the wiki is maintained
- Built-in local retrieval based on `wiki/index.md` plus text scoring
- Ingest, query, lint, and index rebuild workflows

## Repository layout

```text
oamc/
  config/
    config.yaml
    schema.md
  raw/
    inbox/
    sources/
    assets/
  wiki/
    index.md
    log.md
    concepts/
    entities/
    sources/
    syntheses/
  src/llm_wiki/
  tests/
```

## Requirements

- Python 3.12+
- `uv`
- An OpenAI API key in `OPENAI_API_KEY`
- Obsidian for browsing the vault

## Quick start

```bash
uv sync
cp .env.example .env
export OPENAI_API_KEY=...
uv run llm-wiki init
```

Drop a markdown web clip into `raw/inbox/`, then run:

```bash
uv run llm-wiki ingest
```

Ask a question and file the answer back into the wiki:

```bash
uv run llm-wiki query "What are the main design patterns in my notes?" --write-page
```

Run periodic maintenance:

```bash
uv run llm-wiki lint
uv run llm-wiki rebuild-index
```

## Obsidian setup

Open the repo root as an Obsidian vault.

Recommended settings:

- Set attachment folder path to `raw/assets/`
- Enable wikilinks
- Keep graph view enabled

Recommended plugins:

- Obsidian Web Clipper
- Dataview
- Marp

Suggested workflow:

1. Clip a source into `raw/inbox/`
2. Download its images into `raw/assets/` if needed
3. Run `uv run llm-wiki ingest`
4. Review the git diff in Obsidian or your editor
5. Ask questions with `uv run llm-wiki query ...`
6. Run `uv run llm-wiki lint` occasionally

## Notes

- `raw/` is immutable input. The CLI only moves files from `raw/inbox/` to `raw/sources/` after successful ingest.
- `wiki/` is LLM-maintained output.
- `wiki/index.md` is the first file the agent should consult for retrieval.
- `wiki/log.md` is the append-only operations log.
