from __future__ import annotations

from fastapi.testclient import TestClient

from llm_wiki.config import load_config
from llm_wiki.dashboard import create_dashboard_app
from llm_wiki.models import QueryResult
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
    assert "Research mode" in response.text

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


def test_dashboard_ask_route_renders_saved_answer(temp_workspace, monkeypatch) -> None:
    from llm_wiki import dashboard

    (temp_workspace / "wiki" / "concepts" / "frontend-design.md").write_text(
        default_page(
            "concepts/frontend-design.md",
            "Frontend Design",
            "# Frontend Design\n\nDesigning interfaces.\n\n## Sources\n- raw/sources/example.md",
        ),
        encoding="utf-8",
    )

    class FakeOpenAIClient:
        def __init__(self, config) -> None:
            self.config = config

    monkeypatch.setattr(dashboard, "OpenAIWikiClient", FakeOpenAIClient)
    monkeypatch.setattr(
        dashboard,
        "run_query",
        lambda config, repo_paths, client, question, write_page, template, top_k, scopes: QueryResult(
            page_path="syntheses/frontend-summary.md",
            title="Frontend Summary",
            answer_preview="## Summary Answer\n\nSaved answer.",
            template=template,
            selected_candidates=["concepts/frontend-design.md"],
            content="",
        ),
    )

    _, paths = load_config(temp_workspace)
    client = TestClient(create_dashboard_app(paths))

    response = client.get("/ask", params={"q": "Summarize frontend design", "scope": "frontend-design", "template": "compare"})
    assert response.status_code == 200
    assert "Frontend Summary" in response.text
    assert "Saved answer." in response.text
    assert "/page/syntheses/frontend-summary.md" in response.text
    assert "Open in Obsidian" in response.text
    assert 'value="compare" selected' in response.text


def test_dashboard_open_route_uses_obsidian_and_finder_actions(temp_workspace, monkeypatch) -> None:
    from llm_wiki import dashboard

    page = temp_workspace / "wiki" / "concepts" / "frontend-design.md"
    page.write_text(
        default_page(
            "concepts/frontend-design.md",
            "Frontend Design",
            "# Frontend Design\n\nDesigning interfaces.\n\n## Sources\n- raw/sources/example.md",
        ),
        encoding="utf-8",
    )

    opened: list[str] = []
    revealed: list[str] = []
    monkeypatch.setattr(dashboard, "open_in_obsidian", lambda base_dir, target: opened.append(target.as_posix()))
    monkeypatch.setattr(dashboard, "reveal_in_finder", lambda target: revealed.append(target.as_posix()))

    _, paths = load_config(temp_workspace)
    client = TestClient(create_dashboard_app(paths))

    response = client.get("/open", params={"kind": "wiki", "path": "concepts/frontend-design", "target": "obsidian"}, follow_redirects=False)
    assert response.status_code == 303
    assert opened == [page.as_posix()]

    response = client.get("/open", params={"kind": "wiki", "path": "concepts/frontend-design", "target": "finder"}, follow_redirects=False)
    assert response.status_code == 303
    assert revealed == [page.as_posix()]
