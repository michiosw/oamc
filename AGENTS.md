# AGENTS.md

You are maintaining an LLM-owned markdown wiki inside this repository.

## Hard rules

- Never edit files in `raw/` except to move a successfully ingested file from `raw/inbox/` to `raw/sources/`.
- Treat `wiki/` as the maintained knowledge layer.
- Update `wiki/index.md` after every wiki write operation.
- Append a new entry to `wiki/log.md` for every ingest, query, lint, or rebuild operation.
- Use wikilinks only, for example `[[concepts/llm-wiki]]`.
- Every wiki page must include YAML frontmatter.
- Every non-log wiki page must end with a `Sources` section.

## Page types

- `wiki/sources/`: one page per ingested source
- `wiki/entities/`: people, organizations, projects
- `wiki/concepts/`: ideas, methods, topics
- `wiki/syntheses/`: answers, comparisons, analyses

## Retrieval workflow

1. Read `wiki/index.md`
2. Select likely relevant pages
3. Read only the relevant pages
4. Synthesize an answer
5. File durable output back into the wiki when appropriate

## Writing style

- Prefer short paragraphs
- Prefer precise claims over broad summaries
- Cite supporting source pages or raw files in a final `Sources` section
- Keep edits incremental and inspectable
