from __future__ import annotations

from llm_wiki.core.markdown import (
    dump_markdown,
    extract_section,
    extract_wikilinks,
    load_markdown,
    slugify,
)


def test_slugify_normalizes_titles() -> None:
    assert slugify("LLM Wiki: First Pass!") == "llm-wiki-first-pass"


def test_markdown_round_trip(tmp_path) -> None:
    path = tmp_path / "page.md"
    path.write_text(
        dump_markdown(
            {"title": "Example", "type": "concepts", "tags": ["a"], "source_refs": []},
            "# Example\n\nBody text.",
        ),
        encoding="utf-8",
    )
    metadata, body = load_markdown(path)
    assert metadata["title"] == "Example"
    assert "Body text." in body


def test_extract_wikilinks() -> None:
    content = "See [[concepts/llm-wiki]] and [[entities/openai|OpenAI]]."
    assert extract_wikilinks(content) == ["concepts/llm-wiki", "entities/openai"]


def test_extract_section() -> None:
    content = """# Title

## Summary Answer

This is the answer.

## Sources

- one
"""
    assert extract_section(content, "Summary Answer") == "This is the answer."
