from __future__ import annotations

from fastapi.testclient import TestClient

from llm_wiki.config import load_config
from llm_wiki.dashboard import create_dashboard_app
from llm_wiki.ops.common import default_page


def test_dashboard_home_and_search(temp_workspace) -> None:
    (temp_workspace / "wiki" / "concepts" / "frontend-design.md").write_text(
        default_page(
            "concepts/frontend-design.md",
            "Frontend Design",
            "# Frontend Design\n\nDesigning interfaces.\n\n## Sources\n- raw/sources/example.md",
        ),
        encoding="utf-8",
    )
    _, paths = load_config(temp_workspace)
    client = TestClient(create_dashboard_app(paths))

    response = client.get("/")
    assert response.status_code == 200
    assert "oamc" in response.text

    response = client.get("/search", params={"q": "frontend"})
    assert response.status_code == 200
    assert "Frontend Design" in response.text


def test_dashboard_page_view_renders_markdown_and_links(temp_workspace) -> None:
    (temp_workspace / "wiki" / "concepts" / "frontend-design.md").write_text(
        default_page(
            "concepts/frontend-design.md",
            "Frontend Design",
            "# Frontend Design\n\nSee [[entities/gpt-5-4]].\n\n## Sources\n- raw/sources/example.md",
        ),
        encoding="utf-8",
    )
    (temp_workspace / "wiki" / "entities" / "gpt-5-4.md").write_text(
        default_page(
            "entities/gpt-5-4.md",
            "GPT-5.4",
            "# GPT-5.4\n\nA model.\n\n## Sources\n- raw/sources/example.md",
        ),
        encoding="utf-8",
    )
    _, paths = load_config(temp_workspace)
    client = TestClient(create_dashboard_app(paths))

    response = client.get("/page/concepts/frontend-design")
    assert response.status_code == 200
    assert "Frontend Design" in response.text
    assert "/page/entities/gpt-5-4.md" in response.text
