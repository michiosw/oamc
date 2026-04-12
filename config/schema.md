# LLM Wiki Schema

Schema version: `1`

You are an LLM agent that maintains a markdown wiki in this repository.

## Layer ownership

- `raw/` contains immutable source files.
- `wiki/` contains generated and maintained knowledge pages.

You may create, update, and delete files in `wiki/`.
You may never edit files in `raw/`, except that the ingest workflow may move a file from `raw/inbox/` to `raw/sources/` after successful processing.

## Page types

### Source pages

- Directory: `wiki/sources/`
- One page per ingested source
- Filename pattern: `YYYYMMDD-slug.md`
- Sections: Summary, Key Claims, Entities, Concepts, Evidence Notes, Sources

### Entity pages

- Directory: `wiki/entities/`
- For people, organizations, projects, tools
- Sections: Overview, Roles, Relationships, Timeline, Sources

### Concept pages

- Directory: `wiki/concepts/`
- For ideas, techniques, methods, categories
- Sections: Definition, Key Ideas, Variants, Open Questions, Sources

### Synthesis pages

- Directory: `wiki/syntheses/`
- For query answers, comparisons, analyses
- Sections: Question, Summary Answer, Analysis, Implications, Sources

## Global conventions

- Every wiki page must use YAML frontmatter
- Required frontmatter keys: `title`, `type`, `created`, `updated`, `tags`, `source_refs`, `status`
- Prefer relative wikilinks only, for example `[[concepts/llm-wiki]]`
- Every non-log page must end with a `Sources` section
- Keep edits incremental and inspectable

## Index and log

`wiki/index.md` is the authoritative content catalog. It must list all wiki pages grouped by type, with a one-line description for each page.

`wiki/log.md` is append-only. Use headings in this exact form:

`## [YYYY-MM-DD] operation | short title`

Each entry should briefly explain what changed and list touched pages.

## Operations

### Ingest

When ingesting a source:

1. Read the raw source
2. Create or update a source page
3. Update related entity and concept pages when warranted
4. Update `wiki/index.md`
5. Append `wiki/log.md`

### Query

When answering a question:

1. Read `wiki/index.md` first
2. Select a small relevant page set
3. Read those pages
4. Write a synthesis page when durable output is requested
5. Update `wiki/index.md`
6. Append `wiki/log.md`

### Lint

When linting:

1. Look for orphans, broken concept links, stale summaries, and weak cross-linking
2. Make targeted fixes
3. Update `wiki/index.md`
4. Append `wiki/log.md`
