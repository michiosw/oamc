from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import frontmatter


WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def slugify(value: str) -> str:
    lowered = value.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", lowered)
    return normalized.strip("-") or "untitled"


def load_markdown(path: Path) -> tuple[dict[str, Any], str]:
    post = frontmatter.load(path)
    return dict(post.metadata), post.content.strip()


def dump_markdown(metadata: dict[str, Any], body: str) -> str:
    post = frontmatter.Post(body.strip(), **metadata)
    return str(frontmatter.dumps(post)).strip() + "\n"


def upsert_frontmatter(
    content: str,
    updates: dict[str, Any],
    *,
    created: str | None = None,
) -> str:
    post = parse_markdown(content)
    metadata = dict(post.metadata)
    if created and "created" not in metadata:
        metadata["created"] = created
    metadata.update(updates)
    return dump_markdown(metadata, post.content)


def extract_wikilinks(content: str) -> list[str]:
    matches = []
    for raw_match in WIKILINK_RE.findall(content):
        target = raw_match.split("|", 1)[0].strip()
        matches.append(normalize_link_target(target))
    return matches


def normalize_link_target(target: str) -> str:
    normalized = target.strip()
    if normalized.endswith(".md"):
        normalized = normalized[:-3]
    return normalized.lstrip("/")


def link_target_for_path(relative_path: str) -> str:
    return relative_path[:-3] if relative_path.endswith(".md") else relative_path


def title_from_content(content: str, fallback: str) -> str:
    post = parse_markdown(content)
    title = post.metadata.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    for line in post.content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return str(stripped.lstrip("#").strip())
    return fallback


def summary_from_content(content: str, fallback: str = "") -> str:
    post = parse_markdown(content)
    for paragraph in post.content.split("\n\n"):
        cleaned = paragraph.strip()
        if not cleaned or cleaned.startswith("#") or cleaned.startswith("## Sources"):
            continue
        return re.sub(r"\s+", " ", cleaned)[:220]
    return fallback


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_markdown(content: str) -> frontmatter.Post:
    try:
        return frontmatter.loads(content)
    except Exception:
        stripped = strip_frontmatter_block(content)
        return frontmatter.Post(stripped.strip())


def strip_frontmatter_block(content: str) -> str:
    match = FRONTMATTER_RE.match(content)
    if match:
        return content[match.end() :]
    return content


def extract_section(content: str, heading: str) -> str:
    post = parse_markdown(content)
    lines = post.content.splitlines()
    target = heading.strip().lower()
    collecting = False
    target_level = 0
    collected: list[str] = []

    for line in lines:
        match = HEADING_RE.match(line.strip())
        if match:
            level = len(match.group(1))
            title = match.group(2).strip().lower()
            if collecting and level <= target_level:
                break
            if title == target:
                collecting = True
                target_level = level
                continue
        if collecting:
            collected.append(line)

    return "\n".join(collected).strip()
