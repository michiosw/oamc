from __future__ import annotations

import re
import threading
from datetime import datetime
from html import escape
from pathlib import Path
from typing import cast

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from markdown_it import MarkdownIt

from llm_wiki.core.config import load_config
from llm_wiki.core.health import build_doctor_report
from llm_wiki.core.markdown import (
    extract_wikilinks,
    link_target_for_path,
    load_markdown,
    parse_markdown,
)
from llm_wiki.core.models import (
    RESEARCH_TEMPLATES,
    DoctorReport,
    QueryResult,
    RepoPaths,
    ResearchTemplate,
)
from llm_wiki.core.paths import is_placeholder_artifact, repo_relative
from llm_wiki.integrations.obsidian import open_in_obsidian, reveal_in_finder
from llm_wiki.llm.openai_client import OpenAIWikiClient
from llm_wiki.ops.capture import capture_text_to_inbox
from llm_wiki.ops.query import run_query
from llm_wiki.ops.search import iter_wiki_pages, search_pages

BODY_FONT = '"Avenir Next", "Segoe UI", "Helvetica Neue", sans-serif'
DISPLAY_FONT = '"Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif'
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
MD = MarkdownIt("commonmark", {"html": False, "linkify": True}).enable("table")


def create_dashboard_app(
    repo_paths: RepoPaths,
    *,
    process_lock: threading.Lock | None = None,
    lint: bool = True,
) -> FastAPI:
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
        template_name = cast(ResearchTemplate, template if template in RESEARCH_TEMPLATES else "synthesis")
        if not question:
            return render_layout("Ask", render_ask_form(template=template_name))

        config, _ = load_config(repo_paths.base_dir)
        scopes = [item.strip() for item in scope.split(",") if item.strip()]
        try:
            result = run_query(
                config,
                repo_paths,
                OpenAIWikiClient(config),
                question,
                write_page=True,
                template=template_name,
                top_k=config.search.default_top_k,
                scopes=scopes,
            )
            body = render_ask_result(repo_paths, question, scope, template_name, result)
        except RuntimeError as exc:
            body = render_ask_error(question, scope, template_name, str(exc))
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

    @app.post("/capture")
    async def capture_note(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Capture request must be valid JSON.") from exc

        text = str(payload.get("text") or "")
        title = str(payload.get("title") or "")
        source_url = str(payload.get("source_url") or "")
        if not text.strip():
            return JSONResponse(
                {"ok": False, "message": "Nothing to capture. Paste some text first."},
                status_code=400,
            )

        from llm_wiki.runtime.studio import run_process_once

        config, _ = load_config(repo_paths.base_dir)
        captured_path: Path | None = None
        source_page_path: str | None = None
        try:
            if process_lock is not None:
                with process_lock:
                    captured_path = capture_text_to_inbox(
                        repo_paths,
                        text,
                        title=title,
                        source_url=source_url,
                        captured_from="dashboard",
                    )
                    queued_paths = sorted(repo_paths.raw_inbox.glob("*"))
                    capture_index = queued_paths.index(captured_path)
                    ingest_result, _ = run_process_once(
                        config,
                        repo_paths,
                        OpenAIWikiClient(config),
                        lint=lint,
                    )
            else:
                captured_path = capture_text_to_inbox(
                    repo_paths,
                    text,
                    title=title,
                    source_url=source_url,
                    captured_from="dashboard",
                )
                queued_paths = sorted(repo_paths.raw_inbox.glob("*"))
                capture_index = queued_paths.index(captured_path)
                ingest_result, _ = run_process_once(
                    config,
                    repo_paths,
                    OpenAIWikiClient(config),
                    lint=lint,
                )
            if capture_index < len(ingest_result.source_pages):
                source_page_path = ingest_result.source_pages[capture_index]
        except ValueError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
        except RuntimeError as exc:
            queued_relative = repo_relative(captured_path, repo_paths.base_dir) if captured_path else "raw/inbox"
            return JSONResponse(
                {
                    "ok": False,
                    "message": f"Captured note to {queued_relative}, but processing could not finish yet: {exc}",
                    "queued_path": queued_relative,
                },
                status_code=500,
            )

        queued_relative = repo_relative(captured_path, repo_paths.base_dir) if captured_path else "raw/inbox"
        response = {
            "ok": True,
            "message": f"Captured note from the dashboard and processed {queued_relative}.",
            "queued_path": queued_relative,
        }
        if source_page_path:
            response["redirect"] = f"/page/{source_page_path}"
        return JSONResponse(response)

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
    latest_source_page = next(
        (
            page
            for page in pages
            if report.latest_ingest and f"sources/{page.stem}" in report.latest_ingest.touched_pages
        ),
        None,
    )
    prioritized_pages: list[Path] = []
    if latest_source_page is not None:
        prioritized_pages.append(latest_source_page)
    prioritized_pages.extend(page for page in pages if page != latest_source_page)
    recent = prioritized_pages[:8]
    stats = {
        "Sources": len([page for page in (repo_paths.wiki_root / "sources").glob("*.md") if not is_placeholder_artifact(page)]),
        "Entities": len([page for page in (repo_paths.wiki_root / "entities").glob("*.md") if not is_placeholder_artifact(page)]),
        "Concepts": len([page for page in (repo_paths.wiki_root / "concepts").glob("*.md") if not is_placeholder_artifact(page)]),
        "Syntheses": len([page for page in (repo_paths.wiki_root / "syntheses").glob("*.md") if not is_placeholder_artifact(page)]),
    }

    stat_html = "".join(
        f'<div class="stat"><div class="stat-label">{escape(label)}</div><div class="stat-value">{value}</div></div>'
        for label, value in stats.items()
    )
    recent_html = "".join(render_page_list_item(repo_paths, page) for page in recent) or "<li>No pages yet.</li>"
    inbox_count = len([path for path in repo_paths.raw_inbox.glob("*") if path.is_file() and not is_placeholder_artifact(path)])
    latest_ingest_html = render_latest_ingest(repo_paths, report)
    health_html = render_health_surface(repo_paths, report, inbox_count)
    return f"""
    <section class="hero-stage">
      <div class="hero-grid">
        <div class="hero">
          <p class="eyebrow">Local wiki workspace</p>
          <h1>Research against the wiki, not against scattered notes.</h1>
          <p class="lede">Clip sources or paste copied notes into the inbox pipeline, let oamc process them, then ask one bounded research question at a time.</p>
        </div>
        <div class="hero-note">
          <p class="eyebrow">Official workflow</p>
          <p class="meta-line">Everything still flows through <code>raw/inbox/</code>. Clipboard capture writes a real markdown source there before ingest.</p>
          <p class="meta-line">On macOS, the canonical runtime is <code>uv run llm-wiki install-menubar</code>.</p>
        </div>
      </div>
    </section>
    <section class="editorial-band">
      {render_ask_form(compact=True)}
      <div class="stats-panel">
        <p class="eyebrow">Knowledge shape</p>
        <div class="stats">{stat_html}</div>
      </div>
    </section>
    {render_capture_form(compact=True)}
    {latest_ingest_html}
    <section class="split">
      <div class="section-panel">
        <h2>Recent wiki pages</h2>
        <p class="meta-line">This list includes the newest source page first, then the most recently touched wiki pages.</p>
        <ul class="page-list">{recent_html}</ul>
      </div>
      <div>
        {health_html}
      </div>
    </section>
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
      <div class="panel-heading">
        <div>
          <p class="eyebrow">Research mode</p>
          <h2>Ask what the wiki currently knows.</h2>
        </div>
        <p class="template-note">Default outcome: a saved synthesis page, not a disposable answer.</p>
      </div>
      <form action="/ask" method="get" class="ask-form">
        <input type="search" name="q" value="{escape(question)}" placeholder="What does the wiki currently know about..." required>
        <div class="ask-controls">
          <input type="text" name="scope" value="{escape(scope)}" placeholder="Optional scope: gpt-5-4, frontend-design">
          <select name="template">{template_options}</select>
          <button type="submit">Ask</button>
        </div>
      </form>
      <p class="helper">Every serious question writes a synthesis page, updates the index, and is meant to be continued in Obsidian.</p>
    </section>
    """


def render_capture_form(*, compact: bool = False) -> str:
    panel_class = "capture-panel capture-panel-compact" if compact else "capture-panel"
    return f"""
    <section class="{panel_class}">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">Capture mode</p>
          <h2>Paste a copied note into the wiki pipeline.</h2>
        </div>
        <p class="template-note">Best for prompts, copied chat snippets, command recipes, or partial notes that are not worth a full web clip.</p>
      </div>
      <form class="capture-form" data-capture-form>
        <textarea name="text" placeholder="Paste the note you copied here..." required></textarea>
        <div class="capture-controls">
          <input type="text" name="title" placeholder="Optional title">
          <input type="url" name="source_url" placeholder="Optional source URL">
          <button type="submit">Capture And Process</button>
        </div>
      </form>
      <p class="helper">oamc writes a markdown file into <code>raw/inbox/</code>, then processes it with the same ingest pipeline as normal clips.</p>
      <p class="capture-status" data-capture-status aria-live="polite"></p>
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
    <section class="section-panel">
      <p class="eyebrow">Search</p>
      <h1>{escape(query)}</h1>
      <p class="meta-line">Top matches across sources, entities, concepts, and syntheses.</p>
      <ul class="result-list">{''.join(items)}</ul>
    </section>
    """


def render_ask_result(
    repo_paths: RepoPaths,
    question: str,
    scope: str,
    template: str,
    result: QueryResult,
) -> str:
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
      <div class="panel-heading panel-heading-tight">
        <div>
          <p class="eyebrow">Saved synthesis</p>
          <h1>{escape(result.title)}</h1>
        </div>
      </div>
      {saved_html}
      {action_html}
      <div class="answer-copy">{render_markdown(result.answer_preview)}</div>
    </section>
    <section class="split">
      <div class="section-panel">
        <h2>Context pages</h2>
        <ul class="link-list">{context_html}</ul>
      </div>
      <div class="section-panel">
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


def extract_preview_line(body: str) -> str:
    for line in parse_markdown(body).content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if stripped in {"---", "***"}:
            continue
        return str(stripped)
    return ""


def render_markdown(body: str) -> str:
    linked = WIKILINK_RE.sub(_wikilink_replacer, body)
    return str(MD.render(linked))


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
    summary = extract_preview_line(body)
    date_label = datetime.fromtimestamp(page.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    return (
        f'<li><a href="/page/{escape(relative_path)}">{escape(str(metadata.get("title") or page.stem))}</a>'
        f'<div class="result-path">{escape(relative_path)} · {escape(date_label)}</div>'
        f'<p class="result-summary">{escape(summary)}</p></li>'
    )


def render_template_label(template: str) -> str:
    return template.replace("-", " ").title()


def render_health_surface(repo_paths: RepoPaths, report: DoctorReport, inbox_count: int) -> str:
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
      <dl class="status-list">
        <div><dt>Inbox files</dt><dd>{inbox_count}</dd></div>
        <div><dt>Latest source</dt><dd><code>{escape(report.latest_processed_source or 'none yet')}</code></dd></div>
        <div><dt>Latest log</dt><dd>{escape(report.latest_log_heading or 'none yet')}</dd></div>
        <div><dt>Index</dt><dd>{escape(index_check.detail if index_check else 'unknown')}</dd></div>
        <div><dt>Runtime</dt><dd>{escape(runtime_check.detail if runtime_check else 'unknown')}</dd></div>
      </dl>
      {clippings_note}
      <p class="meta-line">Next step: {escape(report.recommended_next_step)}</p>
    </section>
    """


def render_latest_ingest(repo_paths: RepoPaths, report: DoctorReport) -> str:
    entry = report.latest_ingest
    if not entry:
        return ""
    source_page = next((page for page in entry.touched_pages if page.startswith("sources/")), None)
    related_pages = [page for page in entry.touched_pages if page.startswith("entities/") or page.startswith("concepts/")]
    related_html = "".join(
        f'<li><a href="/page/{escape(page)}">{escape(page)}</a></li>' for page in related_pages[:6]
    ) or "<li>No related pages yet.</li>"
    source_action = ""
    if source_page:
        source_action = f"""
        <div class="action-row">
          <a class="action-link" href="/open?kind=wiki&path={escape(source_page)}&target=obsidian">Open source page</a>
          <a class="action-link action-link-muted" href="/open?kind=raw&path={escape(report.latest_processed_source or '')}&target=finder">Reveal raw source</a>
        </div>
        """
    summary_intro, summary_points = split_activity_summary(entry.summary)
    summary_html = ""
    if summary_intro:
        summary_html += f'<p class="meta-line summary-intro">{escape(summary_intro)}</p>'
    if summary_points:
        points_html = "".join(f"<li>{escape(point)}</li>" for point in summary_points)
        summary_html += f'<ul class="summary-list">{points_html}</ul>'
    return f"""
    <section class="split split-tight">
      <div class="answer-panel ingest-panel">
        <p class="eyebrow">Latest ingest</p>
        <h2>{escape(entry.title)}</h2>
        <p class="meta-line">Raw source: <code>{escape(report.latest_processed_source or 'unknown')}</code></p>
        {summary_html}
        {source_action}
      </div>
      <div class="status-card touched-panel">
        <p class="eyebrow">Touched pages</p>
        <ul class="link-list">{related_html}</ul>
      </div>
    </section>
    """


def split_activity_summary(summary: str) -> tuple[str, list[str]]:
    intro_parts: list[str] = []
    bullets: list[str] = []
    for raw_line in summary.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("Raw source:") or stripped.startswith("Source page:"):
            continue
        if stripped.startswith("- "):
            bullets.append(stripped[2:].strip())
        else:
            intro_parts.append(stripped)
    intro = " ".join(intro_parts).strip()
    return intro, bullets


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
      --panel-soft: rgba(255, 251, 245, 0.56);
      --text: #191713;
      --muted: #6f675d;
      --border: rgba(54, 45, 34, 0.10);
      --accent: #295c52;
      --accent-soft: rgba(41, 92, 82, 0.12);
      --warm-soft: rgba(186, 150, 103, 0.09);
      --shadow: 0 16px 40px rgba(31, 25, 20, 0.06);
      --shadow-strong: 0 22px 60px rgba(31, 25, 20, 0.08);
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
    body::after {{
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background: linear-gradient(180deg, rgba(255,255,255,0.16), transparent 28%);
      opacity: 0.9;
    }}
    a {{
      color: inherit;
      text-decoration-color: rgba(41, 92, 82, 0.3);
      text-underline-offset: 0.18em;
      transition: color 180ms ease, text-decoration-color 180ms ease;
    }}
    a:hover {{
      color: var(--accent);
      text-decoration-color: rgba(41, 92, 82, 0.5);
    }}
    code {{ font-family: "SF Mono", "JetBrains Mono", monospace; font-size: 0.92em; }}
    .shell {{
      max-width: 1240px;
      margin: 0 auto;
      padding: 30px 28px 64px;
      position: relative;
      z-index: 1;
    }}
    header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 24px;
      padding: 0 0 28px;
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
    header form {{
      display: flex;
      gap: 10px;
      min-width: min(460px, 100%);
    }}
    header input[type="search"] {{
      flex: 1;
      border: 1px solid var(--border);
      background: var(--panel-strong);
      color: var(--text);
      border-radius: 999px;
      padding: 13px 18px;
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
      padding-top: 34px;
      display: grid;
      gap: 34px;
    }}
    .hero-stage {{
      position: relative;
      padding: 8px 0 6px;
      animation: rise-in 420ms ease both;
    }}
    .hero-stage::before {{
      content: "";
      position: absolute;
      inset: 0;
      border-top: 1px solid rgba(41, 92, 82, 0.14);
      background:
        linear-gradient(180deg, rgba(255,255,255,0.22), rgba(255,255,255,0)),
        radial-gradient(circle at 12% 18%, rgba(41, 92, 82, 0.10), transparent 26%);
      border-radius: 32px;
      z-index: -1;
    }}
    .hero-grid {{
      display: grid;
      grid-template-columns: minmax(0, 1.65fr) minmax(280px, 0.85fr);
      gap: 30px;
      align-items: stretch;
    }}
    .hero {{
      max-width: 760px;
      padding: 18px 0 8px;
    }}
    .hero h1, article h1, section h1 {{
      margin: 0 0 12px;
      font-family: {DISPLAY_FONT};
      font-size: clamp(2.2rem, 4vw, 4.2rem);
      line-height: 0.95;
      letter-spacing: -0.04em;
      max-width: 11ch;
    }}
    .hero-note {{
      display: grid;
      align-content: end;
      gap: 10px;
      padding: 22px 24px;
      border-radius: 28px;
      border: 1px solid var(--border);
      background: linear-gradient(180deg, rgba(255,251,245,0.88), rgba(255,251,245,0.62));
      box-shadow: var(--shadow);
      backdrop-filter: blur(14px);
    }}
    h2 {{
      font-family: {DISPLAY_FONT};
      font-size: 1.5rem;
      margin: 0 0 10px;
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
    .lede {{
      font-size: 1.06rem;
      max-width: 44ch;
      margin: 0;
    }}
    .editorial-band {{
      display: grid;
      grid-template-columns: minmax(0, 1.3fr) minmax(280px, 0.9fr);
      gap: 20px;
      align-items: start;
      animation: rise-in 520ms ease both;
    }}
    .stats-panel {{
      padding-top: 8px;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }}
    .ask-panel,
    .answer-panel,
    .capture-panel,
    .stat {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 26px;
      padding: 22px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(14px);
    }}
    .ask-panel-compact {{
      padding: 24px 24px 22px;
    }}
    .capture-panel-compact {{
      padding: 24px;
      animation: rise-in 580ms ease both;
    }}
    .panel-heading {{
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 16px;
    }}
    .panel-heading-tight {{
      margin-bottom: 4px;
    }}
    .template-note {{
      max-width: 24ch;
      margin: 0;
      color: var(--muted);
      font-size: 0.92rem;
      line-height: 1.45;
    }}
    .ask-form {{
      display: grid;
      gap: 14px;
      min-width: 0;
      margin-top: 18px;
    }}
    .capture-form {{
      display: grid;
      gap: 14px;
      min-width: 0;
      margin-top: 18px;
    }}
    .ask-form input[type="search"],
    .ask-form input[type="text"],
    .ask-form select,
    .capture-form input[type="text"],
    .capture-form input[type="url"],
    .capture-form textarea {{
      width: 100%;
      border: 1px solid var(--border);
      background: rgba(255,255,255,0.74);
      color: var(--text);
      border-radius: 16px;
      padding: 13px 16px;
      font: inherit;
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.4);
    }}
    .capture-form textarea {{
      min-height: 180px;
      resize: vertical;
      line-height: 1.55;
    }}
    .ask-controls {{
      display: grid;
      grid-template-columns: minmax(0, 1.2fr) 180px auto;
      gap: 12px;
      align-items: center;
    }}
    .capture-controls {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
      gap: 12px;
      align-items: center;
    }}
    .helper {{
      color: var(--muted);
      margin: 10px 0 0;
      max-width: 56ch;
    }}
    .capture-status {{
      min-height: 1.5em;
      margin: 8px 0 0;
      color: var(--muted);
    }}
    .capture-status[data-state="error"] {{
      color: #8a3e2d;
    }}
    .capture-status[data-state="working"] {{
      color: var(--accent);
    }}
    .stat-label {{
      color: var(--muted);
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.1em;
    }}
    .stat-value {{
      margin-top: 10px;
      font-family: {DISPLAY_FONT};
      font-size: clamp(2rem, 3vw, 2.8rem);
      letter-spacing: -0.04em;
    }}
    .split {{
      display: grid;
      grid-template-columns: minmax(0, 1.65fr) minmax(280px, 0.95fr);
      gap: 24px;
    }}
    .split-tight {{
      gap: 20px;
    }}
    .section-panel {{
      padding: 4px 0;
    }}
    .page-list, .result-list, .link-list {{
      list-style: none;
      padding: 0;
      margin: 0;
    }}
    .page-list li, .result {{
      padding: 18px 0 20px;
      border-top: 1px solid var(--border);
      transition: transform 180ms ease, border-color 180ms ease;
    }}
    .page-list li:hover, .result:hover {{
      transform: translateX(4px);
      border-color: rgba(41, 92, 82, 0.2);
    }}
    .page {{
      max-width: 760px;
      background: var(--panel-strong);
      border: 1px solid var(--border);
      border-radius: 30px;
      padding: 30px 32px 34px;
      box-shadow: var(--shadow-strong);
    }}
    .result-title, .page-list a {{
      font-size: 1.08rem;
      font-weight: 600;
      text-decoration: none;
    }}
    .result-path {{
      color: var(--muted);
      font-size: 0.9rem;
      margin-top: 4px;
    }}
    .result-summary {{
      margin: 7px 0 0;
      color: var(--muted);
      max-width: 52ch;
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
      padding: 22px;
      border: 1px solid var(--border);
      border-radius: 26px;
      background: var(--panel);
      box-shadow: var(--shadow);
      align-self: start;
      position: sticky;
      top: 24px;
    }}
    .markdown-body {{
      font-size: 1.06rem;
      line-height: 1.7;
    }}
    .answer-copy {{
      font-size: 1.06rem;
      line-height: 1.72;
    }}
    .status-card {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 26px;
      padding: 22px;
      box-shadow: var(--shadow);
    }}
    .status-list {{
      display: grid;
      gap: 14px;
      margin: 0 0 14px;
    }}
    .status-list div {{
      display: grid;
      gap: 4px;
      padding-top: 14px;
      border-top: 1px solid var(--border);
    }}
    .status-list div:first-child {{
      border-top: 0;
      padding-top: 0;
    }}
    .status-list dt {{
      color: var(--muted);
      font-size: 0.78rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .status-list dd {{
      margin: 0;
      color: var(--text);
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
      margin: 14px 0 16px;
    }}
    .action-link {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border-radius: 999px;
      background: var(--text);
      color: white;
      padding: 10px 15px;
      text-decoration: none;
      font-size: 0.95rem;
      box-shadow: 0 10px 24px rgba(25, 23, 19, 0.12);
    }}
    .action-link:hover {{
      color: white;
      background: var(--accent);
      text-decoration: none;
      transform: translateY(-1px);
    }}
    .action-link-muted {{
      background: rgba(25, 23, 19, 0.07);
      color: var(--text);
      box-shadow: none;
    }}
    .action-link-muted:hover {{
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
    .summary-intro {{
      margin-bottom: 10px;
    }}
    .summary-list {{
      margin: 0;
      padding-left: 1.1em;
      color: var(--muted);
      display: grid;
      gap: 8px;
    }}
    .touched-panel .link-list li,
    .section-panel .link-list li {{
      padding: 11px 0;
      border-top: 1px solid var(--border);
    }}
    .touched-panel .link-list li:first-child,
    .section-panel .link-list li:first-child {{
      border-top: 0;
      padding-top: 0;
    }}
    .ingest-panel {{
      position: relative;
      overflow: hidden;
    }}
    .ingest-panel::after {{
      content: "";
      position: absolute;
      inset: auto -14% -26% auto;
      width: 220px;
      height: 220px;
      border-radius: 999px;
      background: radial-gradient(circle, rgba(41, 92, 82, 0.14), transparent 70%);
      pointer-events: none;
    }}
    .ask-panel,
    .capture-panel,
    .stats-panel,
    .answer-panel,
    .status-card,
    .page,
    .sidebar,
    .section-panel {{
      animation: rise-in 520ms ease both;
    }}
    .stats-panel {{
      animation-delay: 60ms;
    }}
    .answer-panel {{
      animation-delay: 80ms;
    }}
    .status-card {{
      animation-delay: 100ms;
    }}
    @keyframes rise-in {{
      from {{
        opacity: 0;
        transform: translateY(10px);
      }}
      to {{
        opacity: 1;
        transform: translateY(0);
      }}
    }}
    @media (max-width: 860px) {{
      header, .split, .hero-grid, .editorial-band {{ grid-template-columns: 1fr; display: grid; }}
      header form {{ min-width: 0; }}
      .ask-controls {{ grid-template-columns: 1fr; }}
      .capture-controls {{ grid-template-columns: 1fr; }}
      .panel-heading {{ grid-template-columns: 1fr; display: grid; align-items: start; }}
      .sidebar {{ position: static; }}
      .stats {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .hero-stage::before {{ border-radius: 24px; }}
      .shell {{ padding: 24px 18px 52px; }}
    }}
    @media (max-width: 560px) {{
      .hero h1, article h1, section h1 {{
        max-width: 100%;
        font-size: clamp(2rem, 11vw, 3.1rem);
      }}
      .stats {{
        grid-template-columns: 1fr 1fr;
      }}
      .page {{
        padding: 24px 22px 28px;
      }}
      .ask-panel,
      .capture-panel,
      .answer-panel,
      .stat,
      .status-card,
      .hero-note {{
        border-radius: 22px;
      }}
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
  <script>
    document.addEventListener("submit", async (event) => {{
      const form = event.target;
      if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-capture-form")) {{
        return;
      }}
      event.preventDefault();
      const status = form.parentElement?.querySelector("[data-capture-status]");
      const button = form.querySelector('button[type="submit"]');
      const textField = form.querySelector('textarea[name="text"]');
      const titleField = form.querySelector('input[name="title"]');
      const sourceUrlField = form.querySelector('input[name="source_url"]');
      if (!(textField instanceof HTMLTextAreaElement)) {{
        return;
      }}
      if (status instanceof HTMLElement) {{
        status.dataset.state = "working";
        status.textContent = "Capturing note and processing the inbox...";
      }}
      if (button instanceof HTMLButtonElement) {{
        button.disabled = true;
      }}
      try {{
        const response = await fetch("/capture", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{
            text: textField.value,
            title: titleField instanceof HTMLInputElement ? titleField.value : "",
            source_url: sourceUrlField instanceof HTMLInputElement ? sourceUrlField.value : "",
          }}),
        }});
        const payload = await response.json();
        if (!response.ok || !payload.ok) {{
          throw new Error(payload.message || "Could not capture the note.");
        }}
        if (payload.redirect) {{
          window.location.assign(payload.redirect);
          return;
        }}
        form.reset();
        if (status instanceof HTMLElement) {{
          status.dataset.state = "";
          status.textContent = payload.message || "Captured note.";
        }}
      }} catch (error) {{
        if (status instanceof HTMLElement) {{
          status.dataset.state = "error";
          status.textContent = error instanceof Error ? error.message : "Could not capture the note.";
        }}
      }} finally {{
        if (button instanceof HTMLButtonElement) {{
          button.disabled = false;
        }}
      }}
    }});
  </script>
</body>
</html>"""
