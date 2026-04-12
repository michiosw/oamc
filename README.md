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

Drop markdown sources into `raw/inbox/`, then run the one-command daily workflow:

```bash
uv run llm-wiki process
```

For the best day-to-day setup, run the studio command and leave it open:

```bash
uv run llm-wiki start
```

That gives you:

- a local dashboard in the browser
- automatic inbox processing in the background
- one place to browse, search, and ask questions

On macOS, the better setup is the menubar app. Install it once and let it launch at login:

```bash
uv sync
uv run llm-wiki install-menubar
```

That gives you:

- an `oamc` icon in the macOS menu bar
- a real `~/Applications/oamc.app` bundle
- the watcher and dashboard always running under macOS supervision
- a one-click way to open the dashboard, Obsidian, or process the inbox manually

When you update `oamc`, run `uv run llm-wiki install-menubar` again once to refresh the installed app bundle.

If you only want inbox automation without the dashboard:

```bash
uv run llm-wiki watch
```

Ask a question and get both a saved page and a terminal answer preview:

```bash
uv run llm-wiki query "What does the wiki currently know about prompt engineering and frontend design?"
```

Check current state any time:

```bash
uv run llm-wiki status
```

Launch the local dashboard:

```bash
uv run llm-wiki serve
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
3. Run `uv run llm-wiki process`
4. Review the new wiki pages in Obsidian
5. Ask questions with `uv run llm-wiki query "..."`
6. Commit `raw/` and `wiki/` when the changes look good

## Daily commands

`uv run llm-wiki process`

- Processes everything currently in `raw/inbox/`
- Rebuilds the index
- Runs a lint pass
- Leaves you with a clean wiki state

`uv run llm-wiki watch`

- Watches `raw/inbox/`
- Waits for new files to settle
- Auto-runs the same processing flow
- Best option if you want clipping to feel automatic

`uv run llm-wiki start`

- Starts the local dashboard
- Starts the inbox watcher in the same session
- Opens the browser by default
- Best default command if you want the system to feel like one app

`uv run llm-wiki menubar`

- Starts the macOS menubar app directly
- Runs the dashboard and inbox watcher in the background
- Best option if you want `oamc` living in the menu bar instead of a terminal tab

`uv run llm-wiki install-menubar`

- Installs the macOS login item
- Launches the menubar app automatically after login
- Removes the need to start `oamc` manually

`uv run llm-wiki query "..."`

- Searches the wiki
- Writes a synthesis page by default
- Prints the answer preview directly in the terminal
- Supports `--scope` to focus on a source, concept, entity, or path fragment
- Supports `--open` to open the saved synthesis page after writing

`uv run llm-wiki status`

- Shows inbox count
- Shows wiki page count
- Shows the latest log entry

`uv run llm-wiki serve`

- Starts a local dashboard for search and browsing
- Lets you ask the wiki directly from the browser
- Opens the wiki in your browser by default
- Keeps the presentation clean and minimal

`uv run llm-wiki ingest`

- Lower-level command when you only want ingest behavior

`uv run llm-wiki lint`

- Lower-level maintenance command when you want cleanup without new ingest

## Notes

- `raw/` is immutable input. The CLI only moves files from `raw/inbox/` to `raw/sources/` after successful ingest.
- `wiki/` is LLM-maintained output.
- `wiki/index.md` is the first file the agent should consult for retrieval.
- `wiki/log.md` is the append-only operations log.
