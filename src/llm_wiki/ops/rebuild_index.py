from __future__ import annotations

from llm_wiki.markdown import link_target_for_path, load_markdown, summary_from_content
from llm_wiki.models import RepoPaths
from llm_wiki.ops.search import iter_wiki_pages


SECTION_TITLES = {
    "sources": "Sources",
    "entities": "Entities",
    "concepts": "Concepts",
    "syntheses": "Syntheses",
}


def rebuild_index(repo_paths: RepoPaths) -> str:
    grouped: dict[str, list[str]] = {key: [] for key in SECTION_TITLES}
    for page in iter_wiki_pages(repo_paths):
        metadata, body = load_markdown(page)
        relative_path = page.relative_to(repo_paths.wiki_root).as_posix()
        group = relative_path.split("/", 1)[0]
        title = str(metadata.get("title") or page.stem)
        summary = summary_from_content(body, fallback=title)
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
