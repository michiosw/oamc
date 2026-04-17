from __future__ import annotations

from llm_wiki.core.markdown import link_target_for_path
from llm_wiki.core.models import RepoPaths
from llm_wiki.ops.search import iter_wiki_pages, page_summary

SECTION_TITLES = {
    "sources": "Sources",
    "entities": "Entities",
    "concepts": "Concepts",
    "syntheses": "Syntheses",
}


def rebuild_index(repo_paths: RepoPaths) -> str:
    grouped: dict[str, list[str]] = {key: [] for key in SECTION_TITLES}
    for page in iter_wiki_pages(repo_paths):
        relative_path, _, summary = page_summary(repo_paths, page)
        group = relative_path.split("/", 1)[0]
        grouped[group].append(f"- [[{link_target_for_path(relative_path)}]] - {summary}")

    lines = [
        "# Wiki Index",
        "",
        "This file is maintained by `llm-wiki rebuild-index`.",
        "",
    ]
    for group, heading in SECTION_TITLES.items():
        lines.append(f"## {heading}")
        lines.append("")
        entries = grouped[group]
        if entries:
            lines.extend(entries)
        else:
            lines.append("No pages yet.")
        lines.append("")

    content = "\n".join(lines).rstrip() + "\n"
    repo_paths.index.write_text(content, encoding="utf-8")
    return content
