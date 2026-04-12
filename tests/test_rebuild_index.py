from __future__ import annotations

from pathlib import Path

from llm_wiki.ops.common import default_page
from llm_wiki.ops.rebuild_index import rebuild_index
from llm_wiki.core.config import load_config


def test_rebuild_index_lists_pages(temp_workspace: Path) -> None:
    (temp_workspace / "wiki" / "concepts" / "llm-wiki.md").write_text(
        default_page(
            "concepts/llm-wiki.md",
            "LLM Wiki",
            "# LLM Wiki\n\nA compiled knowledge base pattern.\n\n## Sources\n- [[sources/example]]",
        ),
        encoding="utf-8",
    )
    _, paths = load_config(temp_workspace)
    content = rebuild_index(paths)
    assert "[[concepts/llm-wiki]]" in content
    assert "compiled knowledge base pattern" in content.lower()
