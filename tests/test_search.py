from __future__ import annotations

from pathlib import Path

from llm_wiki.config import load_config
from llm_wiki.ops.common import default_page
from llm_wiki.ops.search import search_pages


def test_search_ranks_relevant_pages(temp_workspace: Path) -> None:
    (temp_workspace / "wiki" / "concepts" / "llm-wiki.md").write_text(
        default_page(
            "concepts/llm-wiki.md",
            "LLM Wiki",
            "# LLM Wiki\n\nA persistent wiki for knowledge compilation.\n\n## Sources\n- [[sources/example]]",
        ),
        encoding="utf-8",
    )
    (temp_workspace / "wiki" / "entities" / "obsidian.md").write_text(
        default_page(
            "entities/obsidian.md",
            "Obsidian",
            "# Obsidian\n\nA markdown IDE for browsing the vault.\n\n## Sources\n- [[sources/example]]",
        ),
        encoding="utf-8",
    )
    _, paths = load_config(temp_workspace)
    results = search_pages(paths, "wiki knowledge", top_k=1)
    assert results[0].relative_path == "concepts/llm-wiki.md"
