from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import re

from llm_wiki.core.markdown import (
    dump_markdown,
    link_target_for_path,
    parse_markdown,
    slugify,
    title_from_content,
    upsert_frontmatter,
)
from llm_wiki.core.models import PageDraft, RepoPaths


ALLOWED_WIKI_PREFIXES = ("sources/", "entities/", "concepts/", "syntheses/")


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def today_stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def ensure_default_sources_section(content: str, source_refs: list[str]) -> str:
    normalized = re.sub(r"\n#{1,6}\s+Sources\s*\n.*$", "", content.strip(), flags=re.DOTALL)
    lines = ["## Sources"]
    for source in source_refs:
        lines.append(f"- {source}")
    return normalized + "\n\n" + "\n".join(lines) + "\n"


def page_type_from_relative_path(relative_path: str) -> str:
    return relative_path.split("/", 1)[0]


def normalize_draft(
    draft: PageDraft,
    *,
    repo_paths: RepoPaths,
    default_relative_path: str | None = None,
    source_refs: list[str] | None = None,
) -> tuple[str, str]:
    relative_path = draft.relative_path.strip() or (default_relative_path or "")
    if not relative_path:
        raise ValueError("Draft is missing a relative path.")
    relative_path = relative_path.lstrip("/")
    if relative_path.startswith("wiki/"):
        relative_path = relative_path[5:]
    if not relative_path.endswith(".md"):
        relative_path += ".md"
    if not relative_path.startswith(ALLOWED_WIKI_PREFIXES):
        if default_relative_path is None:
            raise ValueError(f"Invalid wiki destination: {relative_path}")
        relative_path = default_relative_path

    absolute_path = repo_paths.wiki_root / relative_path.replace("wiki/", "")
    if absolute_path.exists():
        from llm_wiki.core.markdown import load_markdown

        existing_metadata, _ = load_markdown(absolute_path)
        created = existing_metadata.get("created")
    else:
        created = now_iso()

    page_type = page_type_from_relative_path(relative_path)
    title = title_from_content(draft.content, fallback=slugify(Path(relative_path).stem).replace("-", " ").title())
    source_refs = source_refs or []
    content = ensure_default_sources_section(draft.content, source_refs)
    content = upsert_frontmatter(
        content,
        updates={
            "title": title,
            "type": page_type,
            "updated": now_iso(),
            "status": "active",
            "source_refs": source_refs,
        },
        created=created,
    )
    return relative_path, content


def write_wiki_draft(
    draft: PageDraft,
    *,
    repo_paths: RepoPaths,
    default_relative_path: str | None = None,
    source_refs: list[str] | None = None,
) -> str:
    relative_path, content = normalize_draft(
        draft,
        repo_paths=repo_paths,
        default_relative_path=default_relative_path,
        source_refs=source_refs,
    )
    destination = repo_paths.wiki_root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")
    return relative_path


def append_log_entry(
    repo_paths: RepoPaths,
    *,
    operation: str,
    title: str,
    summary: str,
    touched_pages: list[str],
) -> None:
    timestamp = today_stamp()
    heading = f"## [{timestamp}] {operation} | {title}".strip()
    touched_targets = []
    for page in touched_pages:
        if page == "wiki/index.md":
            touched_targets.append("index")
        elif page == "wiki/log.md":
            touched_targets.append("log")
        else:
            touched_targets.append(link_target_for_path(page))
    touched_lines = "\n".join(f"- [[{target}]]" for target in touched_targets)
    new_entry = (
        f"{heading}\n\n"
        f"{summary.strip()}\n\n"
        "Touched pages:\n"
        f"{touched_lines}\n\n"
    )
    existing = repo_paths.log.read_text(encoding="utf-8").strip()
    if existing.startswith("# Wiki Log"):
        body = existing[len("# Wiki Log") :].lstrip()
        updated = "# Wiki Log\n\n" + new_entry + body
    else:
        updated = "# Wiki Log\n\n" + new_entry + existing
    repo_paths.log.write_text(updated.rstrip() + "\n", encoding="utf-8")


def default_page(relative_path: str, title: str, body: str) -> str:
    return dump_markdown(
        {
            "title": title,
            "type": page_type_from_relative_path(relative_path),
            "created": now_iso(),
            "updated": now_iso(),
            "tags": [],
            "source_refs": [],
            "status": "active",
        },
        body,
    )


def normalize_existing_wiki_page(relative_path: str, content: str) -> str:
    post = parse_markdown(content)
    metadata = dict(post.metadata)
    if not metadata:
        return content

    source_refs = metadata.get("source_refs")
    if not isinstance(source_refs, list):
        source_refs = []
    normalized_body = ensure_default_sources_section(post.content, source_refs)
    normalized_content = dump_markdown(metadata, normalized_body)
    return normalized_content
