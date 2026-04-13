from __future__ import annotations

from pathlib import Path

from llm_wiki.core.config import load_config
from llm_wiki.core.paths import is_placeholder_artifact
from llm_wiki.ops.common import default_page
from llm_wiki.ops.search import filter_candidates, list_candidates, search_pages


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


def test_filter_candidates_by_scope(temp_workspace: Path) -> None:
    (temp_workspace / "wiki" / "concepts" / "llm-wiki.md").write_text(
        default_page(
            "concepts/llm-wiki.md",
            "LLM Wiki",
            "# LLM Wiki\n\nA persistent wiki.\n\n## Sources\n- [[sources/example]]",
        ),
        encoding="utf-8",
    )
    (temp_workspace / "wiki" / "entities" / "obsidian.md").write_text(
        default_page(
            "entities/obsidian.md",
            "Obsidian",
            "# Obsidian\n\nA markdown IDE.\n\n## Sources\n- [[sources/example]]",
        ),
        encoding="utf-8",
    )
    _, paths = load_config(temp_workspace)
    filtered = filter_candidates(list_candidates(paths), ["entities/obsidian"])
    assert [candidate.relative_path for candidate in filtered] == ["entities/obsidian.md"]


def test_placeholder_pages_are_excluded_from_search_and_counts(temp_workspace: Path) -> None:
    (temp_workspace / "wiki" / "sources" / "gitkeep.md").write_text(
        "---\ntitle: Gitkeep\ntags: []\n---\n# Gitkeep\n\nplaceholder\n\n## Sources\n",
        encoding="utf-8",
    )
    (temp_workspace / "wiki" / "sources" / "real-source.md").write_text(
        default_page(
            "sources/real-source.md",
            "Real Source",
            "# Real Source\n\nUseful content.\n\n## Sources\n- raw/sources/real-source.md",
        ),
        encoding="utf-8",
    )
    _, paths = load_config(temp_workspace)
    candidates = list_candidates(paths)
    assert [candidate.relative_path for candidate in candidates] == ["sources/real-source.md"]
    assert is_placeholder_artifact(temp_workspace / "wiki" / "sources" / "gitkeep.md")
