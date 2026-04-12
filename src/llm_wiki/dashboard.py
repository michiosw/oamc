from __future__ import annotations

from datetime import datetime
from html import escape
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from markdown_it import MarkdownIt

from llm_wiki.config import load_config
from llm_wiki.health import build_doctor_report
from llm_wiki.llm.openai_client import OpenAIWikiClient
from llm_wiki.markdown import extract_wikilinks, link_target_for_path, load_markdown, parse_markdown
from llm_wiki.models import RESEARCH_TEMPLATES, RepoPaths
from llm_wiki.obsidian import open_in_obsidian, reveal_in_finder
from llm_wiki.ops.query import run_query
from llm_wiki.ops.search import iter_wiki_pages, search_pages
from llm_wiki.paths import repo_relative


BODY_FONT = '"Avenir Next", "Segoe UI", "Helvetica Neue", sans-serif'
DISPLAY_FONT = '"Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif'
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
MD = MarkdownIt("commonmark", {"html": False, "linkify": True}).enable("table")


def create_dashboard_app(repo_paths: RepoPaths) -> FastAPI:
    app = FastAPI(title="oamc", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def home(q: str = Query("", alias="q")) -> str:
        if q.strip():
            body = render_search(repo_paths, q.strip())
            return render_layout("Search", body, q=q.strip())
        body = render_home(repo_paths)
        return render_layout("Wiki", body)

    @app.get("/search", response_class=HTMLResponse)
    def search(q: str = Query(...)) -> str:
        body = render_search(repo_paths, q)
        return render_layout(f"Search: {q}", body, q=q)

    @app.get("/page/{page_path:path}", response_class=HTMLResponse)
    def page(page_path: str) -> str:
        relative_path = normalize_page_path(page_path)
        target = repo_paths.wiki_root / relative_path
        if not target.exists():
            raise HTTPException(status_code=404, detail="Page not found")
        body = render_page(repo_paths, target)
        return render_layout(relative_path, body)

    @app.get("/ask", response_class=HTMLResponse)
    def ask(q: str = Query(""), scope: str = Query(""), template: str = Query("synthesis")) -> str:
        question = q.strip()
        template = template if template in RESEARCH_TEMPLATES else "synthesis"
        if not question:
            return render_layout("Ask", render_ask_form(template=template))

        config, _ = load_config(repo_paths.base_dir)
        scopes = [item.strip() for item in scope.split(",") if item.strip()]
        try:
            result = run_query(
                config,
                repo_paths,
                OpenAIWikiClient(config),
                question,
                write_page=True,
                template=template,
                top_k=config.search.default_top_k,
                scopes=scopes,
            )
            body = render_ask_result(repo_paths, question, scope, template, result)
        except RuntimeError as exc:
            body = render_ask_error(question, scope, template, str(exc))
        return render_layout(f"Ask: {question}", body, q=question)

    @app.get("/open")
    def open_target(
        request: Request,
        kind: str = Query(...),
        path: str = Query(...),
        target: str = Query("obsidian"),
    ) -> RedirectResponse:
        absolute = resolve_open_target(repo_paths, kind=kind, path=path)
        if target == "finder":
            reveal_in_finder(absolute)
        else:
            open_in_obsidian(repo_paths.base_dir, absolute)
        return RedirectResponse(request.headers.get("referer") or "/", status_code=303)

    return app


def normalize_page_path(page_path: str) -> str:
    normalized = page_path.lstrip("/")
    if normalized.startswith("wiki/"):
        normalized = normalized[5:]
    if not normalized.endswith(".md"):
        normalized += ".md"
    return normalized


def render_home(repo_paths: RepoPaths) -> str:
    config, _ = load_config(repo_paths.base_dir)
    report = build_doctor_report(config, repo_paths, assume_dashboard_serving=True)
    pages = sorted(iter_wiki_pages(repo_paths), key=lambda path: path.stat().st_mtime, reverse=True)
    recent = pages[:8]
    stats = {
        "Sources": len(list((repo_paths.wiki_root / "sources").glob("*.md"))),
        "Entities": len(list((repo_paths.wiki_root / "entities").glob("*.md"))),
        "Concepts": len(list((repo_paths.wiki_root / "concepts").glob("*.md"))),
        "Syntheses": len(list((repo_paths.wiki_root / "syntheses").glob("*.md"))),
    }

    stat_html = "".join(
        f'<div class="stat"><div class="stat-label">{escape(label)}</div><div class="stat-value">{value}</div></div>'
        for label, value in stats.items()
    )
    recent_html = "".join(render_page_list_item(repo_paths, page) for page in recent) or "<li>No pages yet.</li>"
    inbox_count = len(list(repo_paths.raw_inbox.glob("*")))
    latest_ingest_html = render_latest_ingest(repo_paths, report)
    health_html = render_health_surface(repo_paths, report, inbox_count)
    return f"""
    <section class="hero-grid">
      <div class="hero">
        <p class="eyebrow">Local wiki workspace</p>
        <h1>Research against the wiki, not against scattered notes.</h1>
        <p class="lede">Clip into <code>raw/inbox/</code>, let the watcher process it, then ask one bounded research question at a time.</p>
      </div>
      <div class="hero-note">
        <p class="eyebrow">Official workflow</p>
        <p class="meta-line">The supported ingest path is <code>raw/inbox/</code>. Anything left in <code>Clippings/</code> is outside the pipeline.</p>
        <p class="meta-line">On macOS, the canonical runtime is <code>uv run llm-wiki install-menubar</code>.</p>
      </div>
    </section>
    {render_ask_form(compact=True)}
    <section class="stats">{stat_html}</section>
    <section class="split">
      <div>
        <h2>Recent pages</h2>
        <ul class="page-list">{recent_html}</ul>
      </div>
      <div>
        {health_html}
      </div>
    </section>
    {latest_ingest_html}
    """


def render_ask_form(
    question: str = "",
    scope: str = "",
    template: str = "synthesis",
    *,
    compact: bool = False,
) -> str:
    panel_class = "ask-panel ask-panel-compact" if compact else "ask-panel"
    template_options = "".join(
        f'<option value="{escape(option)}"{" selected" if option == template else ""}>{escape(render_template_label(option))}</option>'
        for option in RESEARCH_TEMPLATES
    )
    return f"""
    <section class="{panel_class}">
      <p class="eyebrow">Research mode</p>
      <h2>Ask what the wiki currently knows.</h2>
      <form action="/ask" method="get" class="ask-form">
        <input type="search" name="q" value="{escape(question)}" placeholder="What does the wiki currently know about..." required>
        <input type="text" name="scope" value="{escape(scope)}" placeholder="Optional scope: gpt-5-4, frontend-design">
        <select name="template">{template_options}</select>
        <button type="submit">Ask</button>
      </form>
      <p class="helper">Every serious question writes a synthesis page, updates the index, and is meant to be continued in Obsidian.</p>
    </section>
    """


def render_search(repo_paths: RepoPaths, query: str) -> str:
    candidates = search_pages(repo_paths, query, top_k=20)
    if not candidates:
        return f"<section><h1>No results</h1><p class='lede'>No wiki pages matched <code>{escape(query)}</code>.</p></section>"

    items = []
    for candidate in candidates:
        items.append(
            f"""
            <li class="result">
              <a href="/page/{escape(candidate.relative_path)}" class="result-title">{escape(candidate.title)}</a>
              <div class="result-path">{escape(candidate.relative_path)}</div>
              <p class="result-summary">{escape(candidate.summary)}</p>
            </li>
            """
        )
    return f"""
    <section>
      <p class="eyebrow">Search</p>
      <h1>{escape(query)}</h1>
      <p class="meta-line">Top matches across sources, entities, concepts, and syntheses.</p>
      <ul class="result-list">{''.join(items)}</ul>
    </section>
    """


def render_ask_result(repo_paths: RepoPaths, question: str, scope: str, template: str, result) -> str:
    context_html = "".join(f"<li>{escape(candidate)}</li>" for candidate in result.selected_candidates) or "<li>No context pages were selected.</li>"
    saved_html = (
        f'<p class="meta-line">Saved to <a href="/page/{escape(result.page_path)}">{escape(result.page_path)}</a></p>'
        if result.page_path
        else ""
    )
    action_html = ""
    if result.page_path:
        action_html = f"""
        <div class="action-row">
          <a class="action-link" href="/open?kind=wiki&path={escape(result.page_path)}&target=obsidian">Open in Obsidian</a>
          <a class="action-link action-link-muted" href="/open?kind=wiki&path={escape(result.page_path)}&target=finder">Reveal in Finder</a>
        </div>
        """
    return f"""
    {render_ask_form(question, scope, template)}
    <section class="answer-panel">
      <p class="eyebrow">Saved synthesis</p>
      <h1>{escape(result.title)}</h1>
      {saved_html}
      {action_html}
      <div class="answer-copy">{render_markdown(result.answer_preview)}</div>
    </section>
    <section class="split">
      <div>
        <h2>Context pages</h2>
        <ul class="link-list">{context_html}</ul>
      </div>
      <div>
        <h2>What happened</h2>
        <p class="meta-line">The saved synthesis is the primary artifact. The inline answer is only a preview.</p>
        <p class="meta-line">Keep exploring from the saved page in Obsidian, not from transient chat context.</p>
      </div>
    </section>
    """


def render_ask_error(question: str, scope: str, template: str, message: str) -> str:
    return f"""
    {render_ask_form(question, scope, template)}
    <section class="answer-panel">
      <p class="eyebrow">Query failed</p>
      <h1>Could not ask the wiki yet.</h1>
      <p class="lede">{escape(message)}</p>
      <p class="meta-line">Check your <code>.env</code> file and make sure <code>OPENAI_API_KEY</code> is set.</p>
    </section>
    """


def render_page(repo_paths: RepoPaths, target: Path) -> str:
    metadata, body = load_markdown(target)
    relative_path = target.relative_to(repo_paths.wiki_root).as_posix()
    title = str(metadata.get("title") or target.stem)
    rendered = render_markdown(body)
    backlinks = find_backlinks(repo_paths, relative_path)
    backlinks_html = "".join(
        f'<li><a href="/page/{escape(path)}">{escape(label)}</a></li>'
        for path, label in backlinks
    ) or "<li>No backlinks yet.</li>"
    metadata_html = "".join(
        f'<li><span>{escape(key)}</span><strong>{escape(render_metadata_value(value))}</strong></li>'
        for key, value in metadata.items()
        if key in {"type", "created", "updated", "status"}
    )
    actions = f"""
    <div class="action-row">
      <a class="action-link" href="/open?kind=wiki&path={escape(relative_path)}&target=obsidian">Open in Obsidian</a>
      <a class="action-link action-link-muted" href="/open?kind=wiki&path={escape(relative_path)}&target=finder">Reveal in Finder</a>
    </div>
    """
    return f"""
    <article class="page">
      <p class="eyebrow">{escape(relative_path)}</p>
      <h1>{escape(title)}</h1>
      <ul class="meta-list">{metadata_html}</ul>
      {actions}
      <div class="markdown-body">{rendered}</div>
    </article>
    <aside class="sidebar">
      <h2>Backlinks</h2>
      <ul class="link-list">{backlinks_html}</ul>
    </aside>
    """


def render_markdown(body: str) -> str:
    linked = WIKILINK_RE.sub(_wikilink_replacer, body)
    return MD.render(linked)


def _wikilink_replacer(match: re.Match[str]) -> str:
    raw_target = match.group(1)
    target = raw_target.split("|", 1)[0].strip()
    label = raw_target.split("|", 1)[1].strip() if "|" in raw_target else target.split("/")[-1].replace("-", " ")
    normalized = normalize_page_path(target)
    return f"[{label}](/page/{normalized})"


def find_backlinks(repo_paths: RepoPaths, relative_path: str) -> list[tuple[str, str]]:
    target = link_target_for_path(relative_path)
    backlinks: list[tuple[str, str]] = []
    for page in iter_wiki_pages(repo_paths):
        if page.relative_to(repo_paths.wiki_root).as_posix() == relative_path:
            continue
        content = page.read_text(encoding="utf-8")
        if target in extract_wikilinks(content):
            metadata, _ = load_markdown(page)
            backlinks.append((page.relative_to(repo_paths.wiki_root).as_posix(), str(metadata.get("title") or page.stem)))
    backlinks.sort(key=lambda item: item[1].lower())
    return backlinks


def render_metadata_value(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def render_page_list_item(repo_paths: RepoPaths, page: Path) -> str:
    metadata, body = load_markdown(page)
    relative_path = page.relative_to(repo_paths.wiki_root).as_posix()
    summary = parse_markdown(body).content.splitlines()[0] if body else ""
    date_label = datetime.fromtimestamp(page.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    return (
        f'<li><a href="/page/{escape(relative_path)}">{escape(str(metadata.get("title") or page.stem))}</a>'
        f'<div class="result-path">{escape(relative_path)} · {escape(date_label)}</div>'
        f'<p class="result-summary">{escape(summary)}</p></li>'
    )


def render_template_label(template: str) -> str:
    return template.replace("-", " ").title()


def render_health_surface(repo_paths: RepoPaths, report, inbox_count: int) -> str:
    clippings_note = ""
    if report.clippings_files:
        clippings_note = (
            f'<p class="status-warn">Found {len(report.clippings_files)} markdown file(s) in <code>Clippings/</code>. '
            "Retarget Web Clipper to <code>raw/inbox/</code>.</p>"
        )
    index_check = next((check for check in report.checks if check.key == "index-drift"), None)
    runtime_check = next((check for check in report.checks if check.key == "dashboard"), None)
    return f"""
    <section class="status-card">
      <p class="eyebrow">Inbox health</p>
      <h2>Workspace status</h2>
      <p class="meta-line">Inbox files: <strong>{inbox_count}</strong></p>
      <p class="meta-line">Latest source: <code>{escape(report.latest_processed_source or 'none yet')}</code></p>
      <p class="meta-line">Latest log: {escape(report.latest_log_heading or 'none yet')}</p>
      <p class="meta-line">Index: <strong>{escape(index_check.detail if index_check else 'unknown')}</strong></p>
      <p class="meta-line">Runtime: <strong>{escape(runtime_check.detail if runtime_check else 'unknown')}</strong></p>
      {clippings_note}
      <p class="meta-line">Next step: {escape(report.recommended_next_step)}</p>
    </section>
    """


def render_latest_ingest(repo_paths: RepoPaths, report) -> str:
    entry = report.latest_ingest
    if not entry:
        return ""
    source_page = next((page for page in entry.touched_pages if page.startswith("sources/")), None)
    related_pages = [page for page in entry.touched_pages if page.startswith("entities/") or page.startswith("concepts/")]
    related_html = "".join(f"<li>{escape(page)}</li>" for page in related_pages[:6]) or "<li>No related pages yet.</li>"
    source_action = ""
    if source_page:
        source_action = f"""
        <div class="action-row">
          <a class="action-link" href="/open?kind=wiki&path={escape(source_page)}&target=obsidian">Open source page</a>
          <a class="action-link action-link-muted" href="/open?kind=raw&path={escape(report.latest_processed_source or '')}&target=finder">Reveal raw source</a>
        </div>
        """
    return f"""
    <section class="split split-tight">
      <div class="answer-panel">
        <p class="eyebrow">Latest ingest</p>
        <h2>{escape(entry.title)}</h2>
        <p class="meta-line">Raw source: <code>{escape(report.latest_processed_source or 'unknown')}</code></p>
        <p class="meta-line">{escape(entry.summary)}</p>
        {source_action}
      </div>
      <div class="status-card">
        <p class="eyebrow">Touched pages</p>
        <ul class="link-list">{related_html}</ul>
      </div>
    </section>
    """


def resolve_open_target(repo_paths: RepoPaths, *, kind: str, path: str) -> Path:
    if kind == "wiki":
        relative_path = normalize_page_path(path)
        target = repo_paths.wiki_root / relative_path
    elif kind == "raw":
        relative_path = path.lstrip("/")
        target = repo_paths.base_dir / relative_path
    else:
        raise HTTPException(status_code=400, detail="Unsupported open target")
    target = target.resolve()
    if not target.exists():
        raise HTTPException(status_code=404, detail="Target not found")
    if repo_paths.base_dir.resolve() not in target.parents and target != repo_paths.base_dir.resolve():
        raise HTTPException(status_code=400, detail="Target is outside the repo")
    return target


def render_layout(title: str, body: str, *, q: str = "") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{escape(title)} · oamc</title>
  <style>
    :root {{
      --bg: #f5f1ea;
      --bg-wash: rgba(50, 89, 74, 0.08);
      --panel: rgba(255, 251, 245, 0.78);
      --panel-strong: rgba(255, 251, 245, 0.94);
      --text: #191713;
      --muted: #6f675d;
      --border: rgba(54, 45, 34, 0.10);
      --accent: #295c52;
      --accent-soft: rgba(41, 92, 82, 0.12);
      --shadow: 0 16px 40px rgba(31, 25, 20, 0.06);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: {BODY_FONT};
      line-height: 1.55;
      min-height: 100vh;
      position: relative;
    }}
    body::before {{
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background:
        radial-gradient(circle at top left, var(--bg-wash), transparent 34%),
        radial-gradient(circle at 85% 10%, rgba(186, 150, 103, 0.08), transparent 26%);
      opacity: 1;
    }}
    a {{
      color: inherit;
      text-decoration-color: rgba(41, 92, 82, 0.3);
      text-underline-offset: 0.18em;
    }}
    code {{ font-family: "SF Mono", "JetBrains Mono", monospace; font-size: 0.92em; }}
    .shell {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 28px 24px 56px;
      position: relative;
      z-index: 1;
    }}
    header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 24px;
      padding: 0 0 24px;
      border-bottom: 1px solid var(--border);
    }}
    .brand {{
      text-decoration: none;
    }}
    .brand-mark {{
      display: block;
      font-family: {DISPLAY_FONT};
      font-size: 2rem;
      letter-spacing: -0.03em;
    }}
    .brand-note {{
      display: block;
      color: var(--muted);
      font-size: 0.95rem;
    }}
    form {{
      display: flex;
      gap: 10px;
      min-width: min(460px, 100%);
    }}
    input[type="search"] {{
      flex: 1;
      border: 1px solid var(--border);
      background: var(--panel-strong);
      color: var(--text);
      border-radius: 999px;
      padding: 12px 16px;
      font: inherit;
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.45);
    }}
    button {{
      border: 0;
      border-radius: 999px;
      background: var(--text);
      color: white;
      padding: 12px 18px;
      font: inherit;
      cursor: pointer;
      transition: transform 160ms ease, opacity 160ms ease, background 160ms ease;
    }}
    button:hover {{
      transform: translateY(-1px);
      background: var(--accent);
    }}
    main {{
      padding-top: 28px;
      display: grid;
      gap: 28px;
    }}
    .hero-grid {{
      display: grid;
      grid-template-columns: minmax(0, 1.8fr) minmax(280px, 0.9fr);
      gap: 28px;
      align-items: end;
    }}
    .hero h1, article h1, section h1 {{
      margin: 0 0 12px;
      font-family: {DISPLAY_FONT};
      font-size: clamp(2.2rem, 4vw, 4.2rem);
      line-height: 0.98;
      letter-spacing: -0.04em;
      max-width: 12ch;
    }}
    .hero-note {{
      padding: 18px 20px;
      border-radius: 24px;
      border: 1px solid var(--border);
      background: linear-gradient(180deg, rgba(255,251,245,0.88), rgba(255,251,245,0.58));
      box-shadow: var(--shadow);
      backdrop-filter: blur(12px);
    }}
    h2 {{
      font-family: {DISPLAY_FONT};
      font-size: 1.5rem;
      margin: 0 0 12px;
      letter-spacing: -0.02em;
    }}
    .eyebrow {{
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.12em;
      font-size: 0.75rem;
      margin: 0 0 10px;
    }}
    .lede, .meta-line {{
      color: var(--muted);
      max-width: 70ch;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 14px;
    }}
    .ask-panel,
    .answer-panel,
    .stat {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 24px;
      padding: 20px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(14px);
    }}
    .ask-panel-compact {{
      padding: 22px 22px 20px;
    }}
    .ask-form {{
      display: grid;
      gap: 12px;
      min-width: 0;
      margin-top: 20px;
    }}
    .ask-form input[type="search"],
    .ask-form input[type="text"],
    .ask-form select {{
      width: 100%;
      border: 1px solid var(--border);
      background: rgba(255,255,255,0.72);
      color: var(--text);
      border-radius: 16px;
      padding: 12px 16px;
      font: inherit;
    }}
    .helper {{
      color: var(--muted);
      margin: 12px 0 0;
    }}
    .stat-label {{
      color: var(--muted);
      font-size: 0.9rem;
      text-transform: uppercase;
      letter-spacing: 0.1em;
    }}
    .stat-value {{
      margin-top: 8px;
      font-family: {DISPLAY_FONT};
      font-size: 2.4rem;
      letter-spacing: -0.04em;
    }}
    .split {{
      display: grid;
      grid-template-columns: minmax(0, 1.6fr) minmax(280px, 1fr);
      gap: 28px;
    }}
    .split-tight {{
      gap: 20px;
    }}
    .page-list, .result-list, .link-list {{
      list-style: none;
      padding: 0;
      margin: 0;
    }}
    .page-list li, .result {{
      padding: 18px 0;
      border-top: 1px solid var(--border);
      transition: transform 180ms ease;
    }}
    .page-list li:hover, .result:hover {{
      transform: translateX(4px);
    }}
    .page {{
      max-width: 760px;
      background: var(--panel-strong);
      border: 1px solid var(--border);
      border-radius: 28px;
      padding: 26px 28px 30px;
      box-shadow: var(--shadow);
    }}
    .result-title, .page-list a {{
      font-size: 1.08rem;
      font-weight: 600;
      text-decoration: none;
    }}
    .result-path {{
      color: var(--muted);
      font-size: 0.9rem;
      margin-top: 3px;
    }}
    .result-summary {{
      margin: 6px 0 0;
      color: var(--muted);
    }}
    .meta-list {{
      display: flex;
      flex-wrap: wrap;
      gap: 16px;
      list-style: none;
      padding: 0;
      margin: 0 0 24px;
      color: var(--muted);
      font-size: 0.95rem;
    }}
    .meta-list li {{
      display: flex;
      gap: 8px;
      align-items: baseline;
    }}
    .sidebar {{
      padding: 20px;
      border: 1px solid var(--border);
      border-radius: 24px;
      background: var(--panel);
      box-shadow: var(--shadow);
      align-self: start;
      position: sticky;
      top: 24px;
    }}
    .markdown-body {{
      font-size: 1.05rem;
    }}
    .answer-copy {{
      font-size: 1.05rem;
    }}
    .status-card {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 24px;
      padding: 20px;
      box-shadow: var(--shadow);
    }}
    .status-warn {{
      margin: 12px 0;
      padding: 12px 14px;
      border-radius: 16px;
      background: rgba(150, 90, 56, 0.10);
      color: #7d4a29;
    }}
    .action-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin: 12px 0 16px;
    }}
    .action-link {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border-radius: 999px;
      background: var(--text);
      color: white;
      padding: 10px 14px;
      text-decoration: none;
      font-size: 0.95rem;
    }}
    .action-link:hover {{
      background: var(--accent);
      text-decoration: none;
    }}
    .action-link-muted {{
      background: rgba(25, 23, 19, 0.08);
      color: var(--text);
    }}
    .answer-copy > :first-child {{
      margin-top: 0;
    }}
    .markdown-body h1, .markdown-body h2, .markdown-body h3 {{
      font-family: {DISPLAY_FONT};
      letter-spacing: -0.02em;
      margin-top: 1.6em;
      margin-bottom: 0.45em;
    }}
    .markdown-body p, .markdown-body ul {{
      margin: 0 0 1em;
    }}
    .markdown-body ul {{
      padding-left: 1.2em;
    }}
    .markdown-body blockquote {{
      margin: 1.2em 0;
      padding-left: 1em;
      border-left: 2px solid var(--border);
      color: var(--muted);
    }}
    @media (max-width: 860px) {{
      header, .split, .hero-grid {{ grid-template-columns: 1fr; display: grid; }}
      form {{ min-width: 0; }}
      .ask-form {{ grid-template-columns: 1fr; }}
      .sidebar {{ position: static; }}
      .shell {{ padding: 24px 18px 48px; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <a class="brand" href="/">
        <span class="brand-mark">oamc</span>
        <span class="brand-note">local wiki workspace</span>
      </a>
      <form action="/search" method="get">
        <input type="search" name="q" value="{escape(q)}" placeholder="Search notes, syntheses, entities..." />
        <button type="submit">Search</button>
      </form>
    </header>
    <main>{body}</main>
  </div>
</body>
</html>"""
