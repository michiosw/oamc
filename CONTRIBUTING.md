# Contributing to oamc

`oamc` is a local-first research cockpit built around one hard rule: `raw/` is input, `wiki/` is the maintained knowledge product, and `src/llm_wiki/` is the machine that keeps the two in sync.

## Development workflow

1. Sync dependencies.
2. Run the quality gates locally.
3. Keep product-code changes separate from live wiki-content changes.
4. Use conventional commits.

```bash
uv sync --extra dev
uv run pytest
python3 -m compileall src tests
uv run mypy src
uv run llm-wiki doctor
```

## Repository invariants

- Never edit files in `raw/` except to move a successfully ingested file from `raw/inbox/` to `raw/sources/`.
- Treat `wiki/` as the maintained knowledge layer.
- Update `wiki/index.md` after every wiki write operation.
- Append a new entry to `wiki/log.md` for every ingest, query, lint, or rebuild operation.
- Every non-log wiki page must include YAML frontmatter and end with exactly one `Sources` section.
- `raw/inbox/` is the only supported clipping destination.

## Coding standards

- Prefer small, explicit modules over new abstractions.
- Keep shared contracts and deterministic helpers in `src/llm_wiki/core/`.
- Keep workflow logic in `src/llm_wiki/ops/`.
- Keep runtime orchestration in `src/llm_wiki/runtime/`.
- Keep platform integration in `src/llm_wiki/integrations/`.
- Keep the dashboard read-focused. Obsidian remains the editing surface.
- Hard cutover only. Do not add backward compatibility layers.

## Testing expectations

- Add or update tests with every product-code change.
- Prefer deterministic fake clients for ingest/query/lint tests.
- Add golden-style fixture assertions when changing durable markdown outputs.
- Do not rely on live provider calls in the normal test suite.

## Versioning and commits

- Use semver.
- Use conventional commits such as `feat:`, `fix:`, `chore:`, or `docs:`.

## Pull requests

Every PR should explain:

- what changed
- which repo invariant it touches
- how it was verified
- whether any schema/config contract changed
