from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from html import escape
from pathlib import Path

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
    normalize_research_template,
)
from llm_wiki.core.paths import is_placeholder_artifact, repo_relative
from llm_wiki.integrations.obsidian import open_in_obsidian, reveal_in_finder
from llm_wiki.llm.openai_client import OpenAIWikiClient
from llm_wiki.ops.capture import capture_text_to_inbox
from llm_wiki.ops.query import run_query
from llm_wiki.ops.search import iter_wiki_pages, search_pages

WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
MD = MarkdownIt("commonmark", {"html": False, "linkify": True}).enable("table")
MetadataValue = str | int | float | bool | None | list[object]

EXAMPLE_PROMPTS: tuple[str, ...] = (
    "agent skill packages",
    "frontend design",
    "ai productivity",
    "browser emulation",
    "engineering decision-making",
    "altitude in prompting",
    "design systems",
    "rate limiting",
    "anti-bot solutions",
    "the assembly line workflow",
)

TEMPLATE_GLYPHS: dict[str, str] = {
    "synthesis": "✦",
    "compare": "⇋",
    "timeline": "↦",
    "open-questions": "?",
    "decision-brief": "▲",
}

TEMPLATE_BLURBS: dict[str, str] = {
    "synthesis": "Pull threads together into a saved page.",
    "compare": "Lay options side by side.",
    "timeline": "Order the story chronologically.",
    "open-questions": "Surface what's still unknown.",
    "decision-brief": "Argue toward a single recommendation.",
}


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
        template_name = normalize_research_template(template)
        if not question:
            return render_layout("Ask", render_ask_stage(template=template_name))

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

        capture_fields = parse_capture_payload(payload)
        if capture_fields is None:
            raise HTTPException(
                status_code=400,
                detail="Capture request must include string text, title, and source_url fields.",
            )

        text, title, source_url = capture_fields
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


def parse_capture_payload(payload: object) -> tuple[str, str, str] | None:
    if not isinstance(payload, dict):
        return None

    text = payload.get("text")
    if not isinstance(text, str):
        return None

    title = payload.get("title")
    if title is None:
        title = ""
    elif not isinstance(title, str):
        return None

    source_url = payload.get("source_url")
    if source_url is None:
        source_url = ""
    elif not isinstance(source_url, str):
        return None

    return text, title, source_url


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


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
        "sources": len([page for page in (repo_paths.wiki_root / "sources").glob("*.md") if not is_placeholder_artifact(page)]),
        "entities": len([page for page in (repo_paths.wiki_root / "entities").glob("*.md") if not is_placeholder_artifact(page)]),
        "concepts": len([page for page in (repo_paths.wiki_root / "concepts").glob("*.md") if not is_placeholder_artifact(page)]),
        "syntheses": len([page for page in (repo_paths.wiki_root / "syntheses").glob("*.md") if not is_placeholder_artifact(page)]),
    }
    inbox_count = len([path for path in repo_paths.raw_inbox.glob("*") if path.is_file() and not is_placeholder_artifact(path)])

    ticker_html = render_ticker(stats, inbox_count, report)
    rail_html = render_recent_rail(repo_paths, recent)
    ingest_html = render_latest_ingest(repo_paths, report)
    health_html = render_health_surface(repo_paths, report, inbox_count)

    return f"""
    <section class="stage" data-stage="home">
      {render_aurora()}
      <div class="hero">
        <p class="kicker">your private research engine · oamc</p>
        <h1 class="display">
          <span class="display-line">Ask the wiki<span class="display-accent">.</span></span>
        </h1>
        <p class="subhead">
          What does it know about
          <span class="rotor" data-rotor>
            <span class="rotor-word" data-rotor-word>{escape(EXAMPLE_PROMPTS[0])}</span>
            <span class="rotor-caret" aria-hidden="true">▍</span>
          </span>?
        </p>
        {render_ask_form()}
      </div>
      {ticker_html}
    </section>
    {ingest_html}
    {rail_html}
    {health_html}
    """


def render_aurora() -> str:
    return """
    <div class="aurora" aria-hidden="true">
      <span class="aurora-blob aurora-blob-1"></span>
      <span class="aurora-blob aurora-blob-2"></span>
      <span class="aurora-blob aurora-blob-3"></span>
    </div>
    """


def render_ticker(stats: dict[str, int], inbox_count: int, report: DoctorReport) -> str:
    last_log = report.latest_log_heading or "no activity yet"
    items = [
        ("sources", stats["sources"]),
        ("entities", stats["entities"]),
        ("concepts", stats["concepts"]),
        ("syntheses", stats["syntheses"]),
    ]
    stat_chips = "".join(
        f'<span class="ticker-chip"><strong>{value}</strong>&nbsp;<span>{escape(label)}</span></span>'
        for label, value in items
    )
    inbox_chip = (
        f'<span class="ticker-chip ticker-chip-active"><strong>{inbox_count}</strong>&nbsp;<span>in inbox</span></span>'
        if inbox_count
        else '<span class="ticker-chip ticker-chip-quiet"><span>inbox quiet</span></span>'
    )
    return f"""
    <div class="ticker">
      {stat_chips}
      {inbox_chip}
      <span class="ticker-divider" aria-hidden="true"></span>
      <span class="ticker-meta">last activity · {escape(last_log)}</span>
    </div>
    """


def render_ask_stage(*, question: str = "", scope: str = "", template: str = "synthesis") -> str:
    return f"""
    <section class="stage" data-stage="ask">
      {render_aurora()}
      <div class="hero">
        <p class="kicker">research mode</p>
        <h1 class="display"><span class="display-line">A question, well posed.</span></h1>
        <p class="subhead">Each ask becomes a saved synthesis page — not a disposable answer.</p>
        {render_ask_form(question=question, scope=scope, template=template)}
      </div>
    </section>
    """


def render_ask_form(question: str = "", scope: str = "", template: str = "synthesis") -> str:
    template_buttons = "".join(
        f"""
        <label class="seg-option" data-tooltip="{escape(TEMPLATE_BLURBS.get(option, ''))}">
          <input type="radio" name="template" value="{escape(option)}"{' checked' if option == template else ''}>
          <span class="seg-glyph" aria-hidden="true">{TEMPLATE_GLYPHS.get(option, '·')}</span>
          <span class="seg-label">{escape(render_template_label(option))}</span>
        </label>
        """
        for option in RESEARCH_TEMPLATES
    )
    initial_scopes = [item.strip() for item in scope.split(",") if item.strip()]
    initial_scope_pills = "".join(
        f'<span class="tag-pill"><span>{escape(item)}</span><button type="button" data-tag-remove aria-label="Remove {escape(item)}">×</button></span>'
        for item in initial_scopes
    )

    return f"""
    <form action="/ask" method="get" class="ask" data-ask-form>
      <div class="ask-shell">
        <span class="ask-icon" aria-hidden="true">✦</span>
        <input
          type="search"
          name="q"
          id="ask-input"
          class="ask-input"
          value="{escape(question)}"
          placeholder="Ask anything the wiki should know."
          autocomplete="off"
          spellcheck="false"
          required>
        <button type="submit" class="ask-submit">
          <span class="ask-submit-label">Ask</span>
          <span class="ask-submit-kbd" aria-hidden="true">↵</span>
        </button>
      </div>
      <div class="ask-meta">
        <div class="tag-input" data-tag-input>
          <span class="tag-input-prefix">scope</span>
          <div class="tag-pills" data-tag-pills>{initial_scope_pills}</div>
          <input
            type="text"
            class="tag-input-field"
            data-tag-field
            placeholder="Press enter to add a topic…"
            autocomplete="off">
          <input type="hidden" name="scope" value="{escape(scope)}" data-tag-hidden>
        </div>
        <div class="seg" role="radiogroup" aria-label="Research template">
          {template_buttons}
        </div>
      </div>
    </form>
    """


def render_capture_dialog() -> str:
    return """
    <dialog class="capture-dialog" data-capture-dialog>
      <form class="capture" data-capture-form method="dialog">
        <header class="capture-head">
          <div>
            <p class="kicker">capture · ⌘K</p>
            <h2>Drop a note into the pipeline.</h2>
          </div>
          <button type="button" class="ghost-btn" data-capture-close aria-label="Close">esc</button>
        </header>
        <textarea name="text" placeholder="Paste a chat snippet, prompt, recipe, or copied note…" required></textarea>
        <div class="capture-fields">
          <input type="text" name="title" placeholder="Optional title">
          <input type="url" name="source_url" placeholder="Optional source URL">
        </div>
        <div class="capture-actions">
          <p class="capture-hint">writes to <code>raw/inbox/</code> and runs ingest</p>
          <button type="submit" class="primary-btn">
            <span data-capture-label>Capture &amp; process</span>
          </button>
        </div>
        <p class="capture-status" data-capture-status aria-live="polite"></p>
      </form>
    </dialog>
    """


def render_recent_rail(repo_paths: RepoPaths, recent: list[Path]) -> str:
    if not recent:
        return ""
    cards = "".join(render_recent_card(repo_paths, page, idx) for idx, page in enumerate(recent))
    return f"""
    <section class="rail-section">
      <header class="section-head">
        <p class="kicker">recently touched</p>
        <h2>What the wiki has been chewing on.</h2>
      </header>
      <div class="rail" role="list">
        {cards}
      </div>
    </section>
    """


def render_recent_card(repo_paths: RepoPaths, page: Path, idx: int) -> str:
    metadata, body = load_markdown(page)
    relative_path = page.relative_to(repo_paths.wiki_root).as_posix()
    title = str(metadata.get("title") or page.stem)
    summary = extract_preview_line(body)
    section = relative_path.split("/", 1)[0] if "/" in relative_path else "wiki"
    age_label = _age_label(page.stat().st_mtime)
    delay_ms = min(idx * 50, 350)
    return f"""
    <a class="rail-card" role="listitem" href="/page/{escape(relative_path)}" style="animation-delay: {delay_ms}ms">
      <div class="rail-card-meta">
        <span class="rail-card-section">{escape(section)}</span>
        <span class="rail-age">{escape(age_label)}</span>
      </div>
      <h3 class="rail-title">{escape(title)}</h3>
      <p class="rail-summary">{escape(summary)}</p>
      <div class="rail-card-arrow" aria-hidden="true">→</div>
    </a>
    """


def _age_label(mtime: float) -> str:
    delta = datetime.now() - datetime.fromtimestamp(mtime)
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    if seconds < 86400 * 14:
        return f"{seconds // 86400}d"
    return datetime.fromtimestamp(mtime).strftime("%b %d")


def render_search(repo_paths: RepoPaths, query: str) -> str:
    candidates = search_pages(repo_paths, query, top_k=20)
    if not candidates:
        return f"""
        <section class="stage stage-narrow">
          {render_aurora()}
          <div class="hero">
            <p class="kicker">search</p>
            <h1 class="display"><span class="display-line">No matches.</span></h1>
            <p class="subhead">Nothing in the wiki for <code>{escape(query)}</code>. Try a broader phrase or capture more sources.</p>
          </div>
        </section>
        """

    items = []
    for idx, candidate in enumerate(candidates):
        section = candidate.relative_path.split("/", 1)[0] if "/" in candidate.relative_path else "wiki"
        items.append(
            f"""
            <li class="result" style="animation-delay: {min(idx * 30, 300)}ms">
              <div class="result-meta">
                <span class="result-section">{escape(section)}</span>
                <span class="result-path">{escape(candidate.relative_path)}</span>
              </div>
              <a href="/page/{escape(candidate.relative_path)}" class="result-title">{escape(candidate.title)}</a>
              <p class="result-summary">{escape(candidate.summary)}</p>
            </li>
            """
        )
    return f"""
    <section class="stage stage-narrow">
      {render_aurora()}
      <div class="hero">
        <p class="kicker">search · {len(candidates)} matches</p>
        <h1 class="display"><span class="display-line">{escape(query)}</span></h1>
      </div>
    </section>
    <section class="results-section">
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
    context_chips = "".join(
        f'<a class="chip" href="/page/{escape(candidate)}">{escape(candidate)}</a>'
        for candidate in result.selected_candidates
    ) or '<span class="chip chip-empty">no context pages selected</span>'
    saved_html = (
        f'<a class="saved-link" href="/page/{escape(result.page_path)}">saved → {escape(result.page_path)}</a>'
        if result.page_path
        else ""
    )
    action_html = ""
    if result.page_path:
        action_html = f"""
        <div class="answer-actions">
          <a class="primary-btn" href="/open?kind=wiki&path={escape(result.page_path)}&target=obsidian">
            <span>Continue in Obsidian</span>
            <span aria-hidden="true">↗</span>
          </a>
          <a class="ghost-btn" href="/open?kind=wiki&path={escape(result.page_path)}&target=finder">Reveal in Finder</a>
        </div>
        """
    return f"""
    <section class="stage stage-narrow">
      {render_aurora()}
      <div class="hero">
        <p class="kicker">saved {escape(render_template_label(template).lower())} · {datetime.now().strftime("%Y-%m-%d %H:%M")}</p>
        <h1 class="display"><span class="display-line">{escape(result.title)}</span></h1>
        {saved_html}
        {action_html}
      </div>
    </section>
    <section class="answer-section">
      <article class="answer-body">{render_markdown(result.answer_preview)}</article>
      <aside class="answer-side">
        <p class="kicker">context pages</p>
        <div class="chips">{context_chips}</div>
        <p class="kicker kicker-spaced">your question</p>
        <p class="answer-question">{escape(question)}</p>
      </aside>
    </section>
    <section class="ask-followup">
      <header class="section-head">
        <p class="kicker">follow up</p>
        <h2>Push the synthesis further.</h2>
        <p class="section-meta">A new question keeps the same scope &amp; template by default.</p>
      </header>
      {render_ask_form(question="", scope=scope, template=template)}
    </section>
    """


def render_ask_error(question: str, scope: str, template: str, message: str) -> str:
    return f"""
    <section class="stage stage-narrow">
      {render_aurora()}
      <div class="hero">
        <p class="kicker">query failed</p>
        <h1 class="display"><span class="display-line">Could not ask the wiki.</span></h1>
        <p class="subhead">{escape(message)}</p>
        <p class="subhead-muted">Check your <code>.env</code> file and confirm <code>OPENAI_API_KEY</code> is set.</p>
      </div>
    </section>
    <section class="ask-followup">
      {render_ask_form(question=question, scope=scope, template=template)}
    </section>
    """


def render_page(repo_paths: RepoPaths, target: Path) -> str:
    metadata, body = load_markdown(target)
    relative_path = target.relative_to(repo_paths.wiki_root).as_posix()
    section = relative_path.split("/", 1)[0] if "/" in relative_path else "wiki"
    title = str(metadata.get("title") or target.stem)
    rendered = render_markdown(body)
    backlinks = find_backlinks(repo_paths, relative_path)
    backlinks_html = "".join(
        f'<li><a href="/page/{escape(path)}">{escape(label)}</a></li>'
        for path, label in backlinks
    ) or "<li class=\"empty\">No backlinks yet.</li>"
    metadata_pills = "".join(
        f'<span class="meta-pill"><span class="meta-pill-key">{escape(key)}</span><span class="meta-pill-val">{escape(render_metadata_value(value))}</span></span>'
        for key, value in metadata.items()
        if key in {"type", "created", "updated", "status"}
    )
    actions = f"""
    <div class="answer-actions">
      <a class="primary-btn" href="/open?kind=wiki&path={escape(relative_path)}&target=obsidian">
        <span>Open in Obsidian</span>
        <span aria-hidden="true">↗</span>
      </a>
      <a class="ghost-btn" href="/open?kind=wiki&path={escape(relative_path)}&target=finder">Reveal in Finder</a>
    </div>
    """
    breadcrumb = relative_path.replace("/", " › ").removesuffix(".md")
    return f"""
    <section class="stage stage-narrow">
      {render_aurora()}
      <div class="hero">
        <p class="kicker">{escape(breadcrumb)}</p>
        <h1 class="display"><span class="display-line">{escape(title)}</span></h1>
        <div class="meta-pills">{metadata_pills}</div>
        {actions}
      </div>
    </section>
    <section class="page-section">
      <article class="markdown-body">{rendered}</article>
      <aside class="page-side">
        <p class="kicker">backlinks</p>
        <ul class="link-list">{backlinks_html}</ul>
      </aside>
    </section>
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


def render_metadata_value(value: MetadataValue) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    text = str(value)
    if "T" in text and len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return text


def render_template_label(template: str) -> str:
    return template.replace("-", " ").title()


def render_health_surface(repo_paths: RepoPaths, report: DoctorReport, inbox_count: int) -> str:
    clippings_note = ""
    if report.clippings_files:
        clippings_note = (
            f'<p class="status-warn">{len(report.clippings_files)} markdown file(s) sit in <code>Clippings/</code>. '
            "Retarget Web Clipper to <code>raw/inbox/</code>.</p>"
        )
    index_check = next((check for check in report.checks if check.key == "index-drift"), None)
    runtime_check = next((check for check in report.checks if check.key == "dashboard"), None)
    return f"""
    <section class="status-section">
      <header class="section-head">
        <p class="kicker">workspace status</p>
        <h2>Health of the pipeline.</h2>
      </header>
      <div class="status-grid">
        <div class="status-cell">
          <span class="status-label">inbox</span>
          <span class="status-value">{inbox_count}</span>
        </div>
        <div class="status-cell">
          <span class="status-label">latest source</span>
          <span class="status-value status-value-mono">{escape(report.latest_processed_source or 'none yet')}</span>
        </div>
        <div class="status-cell">
          <span class="status-label">latest log</span>
          <span class="status-value">{escape(report.latest_log_heading or 'none yet')}</span>
        </div>
        <div class="status-cell">
          <span class="status-label">index</span>
          <span class="status-value">{escape(index_check.detail if index_check else 'unknown')}</span>
        </div>
        <div class="status-cell">
          <span class="status-label">runtime</span>
          <span class="status-value">{escape(runtime_check.detail if runtime_check else 'unknown')}</span>
        </div>
      </div>
      {clippings_note}
      <p class="status-next">next · {escape(report.recommended_next_step)}</p>
    </section>
    """


def render_latest_ingest(repo_paths: RepoPaths, report: DoctorReport) -> str:
    entry = report.latest_ingest
    if not entry:
        return ""
    source_page = next((page for page in entry.touched_pages if page.startswith("sources/")), None)
    related_pages = [page for page in entry.touched_pages if page.startswith("entities/") or page.startswith("concepts/")]
    related_html = "".join(
        f'<a class="chip" href="/page/{escape(page)}">{escape(page)}</a>' for page in related_pages[:8]
    ) or '<span class="chip chip-empty">no related pages yet</span>'
    source_action = ""
    if source_page:
        source_action = f"""
        <div class="answer-actions">
          <a class="primary-btn" href="/open?kind=wiki&path={escape(source_page)}&target=obsidian">
            <span>Open source</span>
            <span aria-hidden="true">↗</span>
          </a>
          <a class="ghost-btn" href="/open?kind=raw&path={escape(report.latest_processed_source or '')}&target=finder">Reveal raw</a>
        </div>
        """
    summary_intro, summary_points = split_activity_summary(entry.summary)
    summary_html = ""
    if summary_intro:
        summary_html += f'<p class="ingest-intro">{escape(summary_intro)}</p>'
    if summary_points:
        points_html = "".join(f"<li>{escape(point)}</li>" for point in summary_points)
        summary_html += f'<ul class="ingest-points">{points_html}</ul>'
    return f"""
    <section class="ingest-section">
      <header class="section-head">
        <p class="kicker">latest ingest</p>
        <h2>{escape(entry.title)}</h2>
        <p class="section-meta">raw · <code>{escape(report.latest_processed_source or 'unknown')}</code></p>
      </header>
      <div class="ingest-grid">
        <div class="ingest-body">
          {summary_html}
          {source_action}
        </div>
        <div class="ingest-side">
          <p class="kicker">touched</p>
          <div class="chips">{related_html}</div>
        </div>
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


# ---------------------------------------------------------------------------
# Layout (CSS + shell + JS)
# ---------------------------------------------------------------------------

_LAYOUT_CSS = r"""
:root {
  --ink: #14110d;
  --ink-soft: #2a2520;
  --paper: #f6f1e7;
  --paper-3: rgba(255, 252, 245, 0.62);
  --muted: #6f675a;
  --muted-2: #8a8175;
  --line: rgba(20, 17, 13, 0.10);
  --line-strong: rgba(20, 17, 13, 0.18);
  --accent: #ff5c2b;
  --accent-press: #e84512;
  --accent-soft: rgba(255, 92, 43, 0.14);
  --forest: #295c52;
  --forest-soft: rgba(41, 92, 82, 0.12);
  --shadow-card:
    0 1px 1px rgba(80, 50, 20, 0.04),
    0 8px 18px -8px rgba(80, 50, 20, 0.12),
    0 22px 44px -22px rgba(80, 50, 20, 0.14);
  --shadow-strong:
    0 1px 2px rgba(80, 50, 20, 0.06),
    0 14px 28px -10px rgba(80, 50, 20, 0.16),
    0 36px 60px -28px rgba(80, 50, 20, 0.22);
  --radius-md: 18px;
  --radius-lg: 28px;
  --radius-xl: 40px;
  --display-font: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
  --body-font: "Inter", "Avenir Next", "Helvetica Neue", system-ui, sans-serif;
  --mono-font: "JetBrains Mono", "SF Mono", "Menlo", monospace;
}

* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font-family: var(--body-font);
  font-size: 16px;
  line-height: 1.55;
  min-height: 100vh;
  position: relative;
  overflow-x: hidden;
}

::selection { background: rgba(255, 92, 43, 0.32); color: var(--ink); }

:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
  border-radius: 6px;
}
.ask-shell:focus-within :focus-visible { outline: 0; }
.ask-input:focus-visible,
.tag-input-field:focus-visible,
.search-mini input:focus-visible { outline: 0; }

body::before {
  content: "";
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='200' height='200'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 0.08 0 0 0 0 0.07 0 0 0 0 0.05 0 0 0 0.42 0'/></filter><rect width='200' height='200' filter='url(%23n)'/></svg>");
  opacity: 0.32;
  mix-blend-mode: multiply;
}

a {
  color: inherit;
  text-decoration-color: rgba(20, 17, 13, 0.22);
  text-underline-offset: 0.18em;
  transition: color 160ms ease, text-decoration-color 160ms ease;
}
a:hover { color: var(--accent); text-decoration-color: var(--accent); }

code { font-family: var(--mono-font); font-size: 0.9em; }

.shell {
  position: relative;
  z-index: 1;
  max-width: 1280px;
  margin: 0 auto;
  padding: 28px 32px 96px;
}

/* ---- header ---- */
.topbar {
  position: sticky;
  top: 0;
  z-index: 50;
  background: rgba(246, 241, 231, 0.78);
  backdrop-filter: blur(20px) saturate(1.15);
  -webkit-backdrop-filter: blur(20px) saturate(1.15);
}
.topbar::after {
  content: "";
  position: absolute;
  left: 0; right: 0; bottom: 0;
  height: 1px;
  background: var(--line);
  pointer-events: none;
}
.topbar-inner {
  max-width: 1280px;
  margin: 0 auto;
  padding: 14px 32px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
}
.brand {
  display: flex; align-items: center; gap: 12px;
  text-decoration: none; color: var(--ink);
}
.brand-mark {
  font-family: var(--display-font);
  font-size: 1.6rem;
  letter-spacing: -0.03em;
  line-height: 1;
}
.brand-dot {
  width: 8px; height: 8px; border-radius: 999px;
  background: var(--accent);
  box-shadow: 0 0 0 3px rgba(255, 92, 43, 0.14);
  animation: pulse 4200ms ease-in-out infinite;
}
.brand-tag {
  font-size: 0.78rem;
  color: var(--muted);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.topbar-tools { display: flex; align-items: center; gap: 12px; }
.search-mini {
  display: flex; align-items: center; gap: 8px;
  background: var(--paper-3);
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 8px 14px;
  min-width: 280px;
  transition: border-color 160ms ease, box-shadow 160ms ease;
}
.search-mini:focus-within {
  border-color: var(--ink);
  box-shadow: 0 0 0 3px var(--accent-soft);
}
.search-mini input {
  flex: 1; min-width: 0;
  border: 0; background: transparent; outline: none;
  font: inherit; color: var(--ink);
}
.search-mini button {
  border: 0; background: transparent;
  font-size: 0.78rem; letter-spacing: 0.1em; text-transform: uppercase;
  color: var(--muted); cursor: pointer;
  font-family: var(--mono-font);
}
.search-mini button:hover { color: var(--ink); }
.kbd {
  font-family: var(--mono-font);
  font-size: 0.72rem;
  padding: 2px 6px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  color: var(--muted);
  background: var(--paper);
}

main {
  padding-top: 36px;
  display: flex;
  flex-direction: column;
  gap: 56px;
  min-width: 0;
}
main > * { min-width: 0; }

/* ---- aurora background per stage ---- */
.stage { position: relative; padding: 12px 0 0; }
.stage-narrow .hero { max-width: 880px; }
.aurora {
  position: absolute; inset: -10% -8% auto -8%;
  height: 540px; pointer-events: none; z-index: 0;
  filter: blur(60px) saturate(1.15);
  opacity: 0.85;
}
.aurora-blob {
  position: absolute; border-radius: 50%; opacity: 0.55;
  will-change: transform;
}
.aurora-blob-1 {
  width: 520px; height: 520px;
  background: radial-gradient(circle, rgba(255, 92, 43, 0.55), transparent 60%);
  top: -120px; left: -40px;
  animation: drift1 22s ease-in-out infinite alternate;
}
.aurora-blob-2 {
  width: 460px; height: 460px;
  background: radial-gradient(circle, rgba(41, 92, 82, 0.4), transparent 60%);
  top: -60px; right: -60px;
  animation: drift2 26s ease-in-out infinite alternate;
}
.aurora-blob-3 {
  width: 380px; height: 380px;
  background: radial-gradient(circle, rgba(107, 78, 232, 0.3), transparent 60%);
  top: 80px; left: 38%;
  animation: drift3 30s ease-in-out infinite alternate;
}

@keyframes drift1 {
  0% { transform: translate(0, 0) scale(1); }
  100% { transform: translate(60px, 40px) scale(1.08); }
}
@keyframes drift2 {
  0% { transform: translate(0, 0) scale(1); }
  100% { transform: translate(-50px, 60px) scale(1.05); }
}
@keyframes drift3 {
  0% { transform: translate(-20px, 0) scale(1); }
  100% { transform: translate(40px, -30px) scale(1.1); }
}
@keyframes pulse {
  0%, 100% { box-shadow: 0 0 0 4px rgba(255, 92, 43, 0.18); }
  50% { box-shadow: 0 0 0 8px rgba(255, 92, 43, 0.04); }
}

/* ---- hero ---- */
.hero {
  position: relative;
  z-index: 1;
  max-width: 1100px;
  padding: 32px 0 28px;
  animation: rise 540ms cubic-bezier(0.2, 0.7, 0.2, 1) both;
}
[data-stage="home"] .hero { padding-bottom: 36px; }
.kicker {
  margin: 0 0 18px;
  font-family: var(--mono-font);
  font-size: 0.74rem;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--muted);
}
.kicker-spaced { margin-top: 22px; }
.display {
  margin: 0 0 16px;
  font-family: var(--display-font);
  font-weight: 400;
  letter-spacing: -0.045em;
  line-height: 0.92;
  font-size: clamp(2.6rem, 8.4vw, 7rem);
  color: var(--ink);
}
.display-line { display: inline; }
.display-accent { color: var(--accent); }
.subhead {
  font-family: var(--display-font);
  font-size: clamp(1.2rem, 2.2vw, 1.7rem);
  line-height: 1.3;
  color: var(--ink-soft);
  max-width: 52ch;
  margin: 0 0 22px;
  font-style: italic;
}
.subhead-muted {
  color: var(--muted);
  margin: -16px 0 0;
  font-size: 1rem;
  font-style: normal;
}

/* ---- rotor word ---- */
.rotor {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  position: relative;
  padding: 0 4px;
  border-radius: 6px;
  background: var(--accent-soft);
  font-style: normal;
  color: var(--accent);
  transition: background 320ms ease;
}
.rotor-word {
  display: inline-block;
  transition: opacity 280ms ease, transform 280ms ease;
}
.rotor-word.rotor-out {
  opacity: 0; transform: translateY(-4px);
}
.rotor-caret {
  display: inline-block;
  font-family: var(--mono-font);
  color: var(--accent);
  animation: blink 1100ms steps(2) infinite;
  font-size: 0.78em;
}
@keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0.1; } }

/* ---- ask form ---- */
.ask {
  display: grid; gap: 18px;
  margin-top: 14px;
  max-width: 880px;
}
.ask-shell {
  position: relative;
  display: flex; align-items: center; gap: 14px;
  background: rgba(255, 252, 245, 0.92);
  border: 1px solid var(--line);
  border-radius: var(--radius-xl);
  padding: 12px 12px 12px 24px;
  box-shadow: var(--shadow-card);
  transition: border-color 200ms ease, box-shadow 200ms ease, transform 200ms ease;
}
.ask-shell:focus-within {
  border-color: var(--ink);
  box-shadow: 0 0 0 3px var(--accent-soft), var(--shadow-card);
  transform: translateY(-1px);
}
.ask-icon {
  font-family: var(--display-font);
  font-size: 1.4rem;
  color: var(--accent);
  line-height: 1;
}
.ask-input {
  flex: 1; min-width: 0;
  border: 0; outline: none; background: transparent;
  font: inherit;
  font-size: 1.18rem;
  padding: 14px 8px;
  caret-color: var(--accent);
  color: var(--ink);
}
.ask-input::placeholder { color: var(--muted-2); }
.ask-submit {
  display: inline-flex; align-items: center; gap: 10px;
  border: 0;
  background: var(--ink);
  color: var(--paper);
  border-radius: 999px;
  padding: 14px 22px;
  font: inherit;
  font-weight: 500;
  cursor: pointer;
  transition: transform 160ms ease, background 160ms ease, box-shadow 160ms ease;
  box-shadow: 0 14px 30px -16px rgba(20, 17, 13, 0.4);
}
.ask-submit:hover { background: var(--accent); transform: translateY(-1px); }
.ask-submit:active { transform: translateY(0) scale(0.98); }
.ask-submit-kbd {
  font-family: var(--mono-font);
  font-size: 0.78rem;
  padding: 1px 6px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.14);
}
form[data-ask-form][data-loading] .ask-submit {
  background: var(--accent);
  pointer-events: none;
}
form[data-ask-form][data-loading] .ask-submit-label {
  position: relative; padding-right: 18px;
}
form[data-ask-form][data-loading] .ask-submit-label::after {
  content: "";
  position: absolute; right: 0; top: 50%;
  width: 12px; height: 12px;
  margin-top: -6px;
  border-radius: 50%;
  border: 2px solid rgba(255, 255, 255, 0.4);
  border-top-color: white;
  animation: spin 700ms linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* ---- ask meta (scope + segmented templates) ---- */
.ask-meta {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr);
  gap: 14px;
  align-items: stretch;
}

.tag-input {
  display: flex; align-items: center; flex-wrap: wrap; gap: 8px;
  background: var(--paper-3);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  padding: 8px 12px;
  min-height: 50px;
  transition: border-color 160ms ease, background 160ms ease;
}
.tag-input:focus-within { border-color: var(--ink); background: rgba(255, 252, 245, 0.95); }
.tag-input-prefix {
  font-family: var(--mono-font);
  font-size: 0.74rem;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--muted);
  padding-right: 4px;
}
.tag-pills { display: contents; }
.tag-pill {
  display: inline-flex; align-items: center; gap: 4px;
  background: var(--ink);
  color: var(--paper);
  border-radius: 999px;
  padding: 4px 4px 4px 12px;
  font-size: 0.84rem;
  animation: pop 280ms cubic-bezier(0.2, 0.9, 0.3, 1.4) both;
}
.tag-pill button {
  width: 22px; height: 22px;
  border: 0; border-radius: 999px;
  background: rgba(255, 255, 255, 0.14);
  color: var(--paper);
  font-size: 0.92rem; line-height: 1;
  cursor: pointer;
  transition: background 160ms ease, color 160ms ease;
}
.tag-pill button:hover { background: var(--accent); }
.tag-input-field {
  flex: 1; min-width: 100px;
  border: 0; outline: none; background: transparent;
  font: inherit; font-size: 0.92rem;
  color: var(--ink);
  padding: 4px 0;
}
.tag-input-field::placeholder { color: var(--muted-2); }
@keyframes pop {
  0% { transform: scale(0.8); opacity: 0; }
  60% { transform: scale(1.05); opacity: 1; }
  100% { transform: scale(1); opacity: 1; }
}

.seg {
  display: flex; flex-wrap: wrap; gap: 6px;
  background: var(--paper-3);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  padding: 6px;
}
.seg-option {
  position: relative;
  display: inline-flex; align-items: center; gap: 6px;
  padding: 8px 12px;
  border-radius: 12px;
  cursor: pointer;
  font-size: 0.88rem;
  color: var(--muted);
  transition: color 160ms ease, background 160ms ease, transform 120ms ease;
}
.seg-option input { position: absolute; opacity: 0; pointer-events: none; }
.seg-option:hover { color: var(--ink); background: rgba(20, 17, 13, 0.04); }
.seg-option:active { transform: scale(0.97); }
.seg-option:has(input:checked) {
  background: var(--ink);
  color: var(--paper);
  transform: translateY(-1px);
  box-shadow: 0 8px 18px -10px rgba(20, 17, 13, 0.4);
}
.seg-option:has(input:checked):hover { background: var(--ink); }
.seg-option:has(input:checked) .seg-glyph { color: var(--accent); }
.seg-glyph {
  font-family: var(--display-font);
  font-size: 1rem;
  color: var(--accent);
}

/* ---- ticker ---- */
.ticker {
  position: relative; z-index: 1;
  display: flex; flex-wrap: wrap; align-items: center; gap: 10px 12px;
  margin-top: 4px;
  padding: 14px 22px;
  border-radius: 22px;
  background: rgba(255, 252, 245, 0.72);
  border: 1px solid var(--line);
  font-size: 0.86rem;
  color: var(--muted);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  animation: rise 600ms cubic-bezier(0.2, 0.7, 0.2, 1) 120ms both;
}
.ticker-chip {
  display: inline-flex; align-items: baseline; gap: 4px;
  padding: 4px 12px;
  border-radius: 999px;
  background: rgba(20, 17, 13, 0.04);
}
.ticker-chip strong {
  font-family: var(--display-font);
  font-size: 1.1rem;
  color: var(--ink);
  letter-spacing: -0.02em;
}
.ticker-chip-active { background: var(--accent-soft); color: var(--ink); }
.ticker-chip-active strong { color: var(--accent); }
.ticker-chip-quiet { background: rgba(20, 17, 13, 0.04); }
.ticker-divider {
  width: 1px; height: 18px;
  background: var(--line-strong);
  margin: 0 6px;
}
.ticker-meta { font-family: var(--mono-font); font-size: 0.78rem; }

/* ---- section heads ---- */
.section-head {
  margin: 0 0 22px;
  display: grid; gap: 4px;
}
.section-head .kicker { margin: 0; }
.section-head h2 {
  margin: 0;
  font-family: var(--display-font);
  font-size: clamp(1.6rem, 3vw, 2.2rem);
  letter-spacing: -0.025em;
  font-weight: 400;
}
.section-meta {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 0.92rem;
}

/* ---- recent rail ---- */
.rail-section { padding-top: 8px; min-width: 0; }
.rail {
  display: flex;
  flex-wrap: nowrap;
  gap: 16px;
  overflow-x: auto;
  overflow-y: hidden;
  padding: 8px 4px 22px;
  margin: 0 -4px;
  scroll-snap-type: x mandatory;
  scrollbar-width: thin;
  scrollbar-color: var(--line-strong) transparent;
  -webkit-overflow-scrolling: touch;
  overscroll-behavior-x: contain;
}
.rail::-webkit-scrollbar { height: 8px; }
.rail::-webkit-scrollbar-thumb { background: var(--line-strong); border-radius: 999px; }
.rail::-webkit-scrollbar-track { background: transparent; }
.rail-card {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 14px;
  flex: 0 0 320px;
  width: 320px;
  text-decoration: none;
  color: var(--ink);
  padding: 24px 24px 22px;
  background: rgba(255, 252, 245, 0.82);
  border: 1px solid var(--line);
  border-radius: 32px;
  scroll-snap-align: start;
  min-height: 220px;
  box-shadow: 0 1px 2px rgba(80, 50, 20, 0.04);
  transition: transform 280ms cubic-bezier(0.2, 0.8, 0.3, 1), box-shadow 280ms ease, border-color 240ms ease, background 240ms ease;
  animation: rise 480ms cubic-bezier(0.2, 0.7, 0.2, 1) both;
  overflow: hidden;
}
.rail-card .rail-card-arrow { margin-top: auto; }
.rail-card::after {
  content: "";
  position: absolute; left: 24px; right: 24px; bottom: 0;
  height: 2px;
  background: linear-gradient(90deg, var(--accent), transparent 90%);
  transform: scaleX(0);
  transform-origin: left center;
  transition: transform 320ms cubic-bezier(0.2, 0.7, 0.2, 1);
  border-radius: 999px;
}
.rail-card:hover {
  transform: translateY(-3px);
  border-color: var(--line);
  background: rgba(255, 252, 245, 0.96);
  box-shadow:
    0 2px 4px rgba(80, 50, 20, 0.04),
    0 12px 24px -10px rgba(80, 50, 20, 0.14),
    0 30px 50px -28px rgba(80, 50, 20, 0.22);
  color: var(--ink);
}
.rail-card:hover::after { transform: scaleX(1); }
.rail-card-meta {
  display: flex; justify-content: space-between; align-items: center;
  font-family: var(--mono-font);
  font-size: 0.74rem;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--muted);
}
.rail-card-section { color: var(--accent); }
.rail-title {
  margin: 0;
  font-family: var(--display-font);
  font-size: 1.45rem;
  font-weight: 400;
  letter-spacing: -0.025em;
  line-height: 1.1;
}
.rail-summary {
  margin: 0;
  color: var(--muted);
  font-size: 0.94rem;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.rail-card-arrow {
  font-family: var(--mono-font);
  align-self: end;
  color: var(--accent);
  font-size: 1.1rem;
  transform: translateX(-4px);
  opacity: 0.5;
  transition: transform 240ms ease, opacity 240ms ease;
}
.rail-card:hover .rail-card-arrow { transform: translateX(0); opacity: 1; }

/* ---- search results ---- */
.results-section { padding: 0 0 8px; }
.result-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 4px; }
.result {
  padding: 18px 20px;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  transition: background 200ms ease, border-color 200ms ease;
  animation: rise 420ms cubic-bezier(0.2, 0.7, 0.2, 1) both;
}
.result:hover {
  background: rgba(255, 252, 245, 0.92);
  border-color: var(--line);
}
.result:hover .result-title { color: var(--accent); }
.result-meta {
  display: flex; gap: 12px; align-items: center;
  font-family: var(--mono-font);
  font-size: 0.74rem;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--muted);
  margin-bottom: 6px;
}
.result-section { color: var(--accent); }
.result-title {
  font-family: var(--display-font);
  font-size: 1.45rem;
  letter-spacing: -0.02em;
  text-decoration: none;
  display: block;
  margin-bottom: 4px;
}
.result-summary {
  margin: 0; color: var(--muted); max-width: 70ch;
}

/* ---- ask result page ---- */
.saved-link {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 18px;
  padding: 6px 14px;
  border-radius: 999px;
  background: rgba(20, 17, 13, 0.05);
  color: var(--muted);
  font-family: var(--mono-font);
  font-size: 0.8rem;
  text-decoration: none;
  border: 1px solid transparent;
  transition: color 160ms ease, border-color 160ms ease, background 160ms ease;
}
.saved-link:hover { color: var(--ink); border-color: var(--line-strong); background: rgba(255,252,245,0.9); }
.answer-actions { display: flex; flex-wrap: wrap; gap: 10px; margin: 6px 0 0; }
.primary-btn {
  display: inline-flex; align-items: center; gap: 8px;
  background: var(--ink);
  color: var(--paper);
  text-decoration: none;
  border: 0;
  border-radius: 999px;
  padding: 12px 20px;
  font: inherit;
  font-weight: 500;
  cursor: pointer;
  transition: transform 180ms cubic-bezier(0.2, 0.8, 0.3, 1), background 180ms ease, box-shadow 220ms ease;
  box-shadow: 0 12px 24px -14px rgba(20, 17, 13, 0.45);
}
.primary-btn:hover {
  background: var(--accent);
  transform: translateY(-1px);
  color: var(--paper);
  box-shadow: 0 18px 32px -14px rgba(255, 92, 43, 0.45);
}
.primary-btn:active { transform: translateY(0); box-shadow: 0 8px 18px -12px rgba(20, 17, 13, 0.4); }
.ghost-btn {
  display: inline-flex; align-items: center; gap: 8px;
  background: transparent;
  color: var(--ink);
  text-decoration: none;
  border: 1px solid var(--line-strong);
  border-radius: 999px;
  padding: 11px 18px;
  font: inherit;
  cursor: pointer;
  transition: background 180ms ease, color 180ms ease, border-color 180ms ease, transform 180ms ease;
}
.ghost-btn:hover { background: var(--ink); color: var(--paper); border-color: var(--ink); }
.ghost-btn:active { transform: translateY(1px); }

.answer-section {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(280px, 1fr);
  gap: 36px;
  align-items: start;
}
.answer-body {
  font-family: var(--display-font);
  font-size: 1.18rem;
  line-height: 1.65;
  color: var(--ink);
  padding: 0 8px;
}
.answer-side {
  position: sticky; top: 100px;
  padding: 22px;
  border-radius: var(--radius-lg);
  background: rgba(255, 252, 245, 0.78);
  border: 1px solid var(--line);
}
.answer-question {
  font-family: var(--display-font);
  font-style: italic;
  color: var(--ink-soft);
  margin: 6px 0 0;
}

.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 12px;
  border-radius: 999px;
  background: rgba(20, 17, 13, 0.06);
  color: var(--ink-soft);
  text-decoration: none;
  font-size: 0.86rem;
  font-family: var(--mono-font);
  transition: background 160ms ease, color 160ms ease, transform 160ms ease;
}
.chip:hover { background: var(--ink); color: var(--paper); transform: translateY(-1px); }
.chip-empty { color: var(--muted); background: transparent; border: 1px dashed var(--line-strong); }

.ask-followup {
  padding: 28px 0 0;
  border-top: 1px solid var(--line);
}

/* ---- markdown body ---- */
.markdown-body {
  font-family: var(--display-font);
  font-size: 1.14rem;
  line-height: 1.7;
  padding: 0 4px;
}
.markdown-body p { margin: 0 0 1em; }
.markdown-body ul, .markdown-body ol { padding-left: 1.4em; margin: 0 0 1.2em; }
.markdown-body h1, .markdown-body h2, .markdown-body h3 {
  font-family: var(--display-font);
  letter-spacing: -0.02em;
  margin: 1.6em 0 0.5em;
  font-weight: 400;
}
.markdown-body h1 { font-size: 1.8rem; }
.markdown-body h2 { font-size: 1.45rem; }
.markdown-body h3 { font-size: 1.2rem; }
.markdown-body blockquote {
  margin: 1.2em 0;
  padding: 6px 0 6px 18px;
  border-left: 2px solid var(--accent);
  color: var(--muted);
  font-style: italic;
}
.markdown-body code {
  background: rgba(20, 17, 13, 0.06);
  padding: 2px 6px;
  border-radius: 6px;
  font-size: 0.88em;
  font-family: var(--mono-font);
}
.markdown-body pre {
  background: var(--ink);
  color: var(--paper);
  padding: 18px 20px;
  border-radius: var(--radius-md);
  overflow-x: auto;
  font-family: var(--mono-font);
  font-size: 0.86rem;
  line-height: 1.5;
}
.markdown-body pre code { background: transparent; padding: 0; color: inherit; }
.markdown-body a {
  color: var(--accent);
  text-decoration: none;
  background-image: linear-gradient(var(--accent), var(--accent));
  background-position: 0 100%;
  background-size: 0% 1px;
  background-repeat: no-repeat;
  transition: background-size 240ms cubic-bezier(0.2, 0.7, 0.2, 1);
  padding-bottom: 1px;
}
.markdown-body a:hover { background-size: 100% 1px; }

/* ---- page view ---- */
.meta-pills { display: flex; flex-wrap: wrap; gap: 8px; margin: 4px 0 22px; }
.meta-pill {
  display: inline-flex; gap: 6px; align-items: baseline;
  padding: 6px 12px;
  border-radius: 999px;
  background: rgba(20, 17, 13, 0.05);
  font-size: 0.84rem;
}
.meta-pill-key {
  font-family: var(--mono-font);
  font-size: 0.72rem;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--muted);
}
.page-section {
  display: grid;
  grid-template-columns: minmax(0, 1.7fr) minmax(260px, 1fr);
  gap: 36px;
  align-items: start;
}
.page-side {
  position: sticky; top: 100px;
  padding: 22px;
  border-radius: var(--radius-lg);
  background: rgba(255, 252, 245, 0.78);
  border: 1px solid var(--line);
}
.link-list {
  list-style: none; margin: 0; padding: 0;
  display: grid; gap: 8px;
  font-family: var(--mono-font);
  font-size: 0.88rem;
}
.link-list li.empty { color: var(--muted); font-style: italic; }
.link-list a { text-decoration: none; }
.link-list a:hover { color: var(--accent); }

/* ---- ingest section ---- */
.ingest-section {
  padding: 32px;
  border-radius: var(--radius-xl);
  background: linear-gradient(140deg, rgba(255, 252, 245, 0.92), rgba(237, 228, 211, 0.6));
  border: 1px solid var(--line);
  position: relative;
  overflow: hidden;
}
.ingest-section::before {
  content: "";
  position: absolute; right: -120px; bottom: -160px;
  width: 320px; height: 320px;
  border-radius: 50%;
  background: radial-gradient(circle, var(--accent-soft), transparent 70%);
  pointer-events: none;
}
.ingest-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr);
  gap: 28px;
  position: relative;
}
.ingest-intro { margin: 0 0 12px; color: var(--ink-soft); font-size: 1.04rem; }
.ingest-points {
  list-style: none; padding: 0; margin: 0 0 18px;
  display: grid; gap: 10px;
  color: var(--ink-soft);
}
.ingest-points li {
  position: relative;
  padding-left: 22px;
}
.ingest-points li::before {
  content: "✦";
  position: absolute; left: 0;
  color: var(--accent);
  font-family: var(--display-font);
}
.ingest-side .kicker { margin: 0 0 10px; }

/* ---- status ---- */
.status-section {
  padding: 32px;
  border-radius: var(--radius-xl);
  background: linear-gradient(160deg, rgba(255, 252, 245, 0.92), rgba(237, 228, 211, 0.55));
  border: 1px solid var(--line);
  position: relative;
  overflow: hidden;
}
.status-section::before {
  content: "";
  position: absolute;
  left: -120px; top: -120px;
  width: 280px; height: 280px;
  border-radius: 50%;
  background: radial-gradient(circle, var(--forest-soft), transparent 70%);
  pointer-events: none;
}
.status-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 14px;
  margin: 0 0 18px;
}
.status-cell {
  padding: 16px 18px;
  border-radius: var(--radius-md);
  background: rgba(20, 17, 13, 0.04);
  display: grid; gap: 4px;
}
.status-label {
  font-family: var(--mono-font);
  font-size: 0.72rem;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--muted);
}
.status-value {
  font-family: var(--display-font);
  font-size: 1.1rem;
  letter-spacing: -0.01em;
  word-break: break-word;
}
.status-value-mono {
  font-family: var(--mono-font);
  font-size: 0.92rem;
}
.status-warn {
  margin: 12px 0 0;
  padding: 12px 16px;
  border-radius: var(--radius-md);
  background: rgba(217, 122, 64, 0.12);
  color: #7d4a29;
}
.status-next {
  margin: 0;
  font-family: var(--mono-font);
  font-size: 0.84rem;
  color: var(--muted);
}

/* ---- capture FAB + dialog ---- */
.fab {
  position: fixed; right: 28px; bottom: 28px;
  z-index: 60;
  display: inline-flex; align-items: center; gap: 10px;
  padding: 14px 20px;
  border: 0; border-radius: 999px;
  background: var(--ink); color: var(--paper);
  font: inherit; font-size: 0.94rem; font-weight: 500;
  cursor: pointer;
  box-shadow: 0 24px 50px -16px rgba(20, 17, 13, 0.5);
  transition: transform 200ms cubic-bezier(0.2, 0.8, 0.3, 1.2), background 160ms ease;
}
.fab:hover { transform: translateY(-2px) scale(1.02); background: var(--accent); }
body[data-capture-open] .fab {
  opacity: 0;
  transform: translateY(8px);
  pointer-events: none;
  transition: opacity 200ms ease, transform 200ms ease;
}
.fab-glyph {
  font-family: var(--display-font);
  font-size: 1.1rem;
  color: var(--accent);
}
.fab:hover .fab-glyph { color: var(--paper); }
.fab-kbd {
  font-family: var(--mono-font);
  font-size: 0.72rem;
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(255,255,255,0.14);
  color: rgba(255,255,255,0.85);
}

.capture-dialog {
  width: min(580px, calc(100% - 32px));
  max-height: 85vh;
  margin: auto;
  padding: 0;
  border: 0;
  border-radius: var(--radius-xl);
  background: var(--paper);
  box-shadow: var(--shadow-strong);
  color: var(--ink);
  overflow: hidden;
}
.capture { max-height: 85vh; overflow-y: auto; }
.capture-dialog::backdrop {
  background: rgba(20, 17, 13, 0.4);
  backdrop-filter: blur(6px);
}
.capture-dialog[open] { animation: dialogIn 320ms cubic-bezier(0.2, 0.7, 0.2, 1.1); }
@keyframes dialogIn {
  from { transform: translateY(20px) scale(0.97); opacity: 0; }
  to { transform: translateY(0) scale(1); opacity: 1; }
}

.capture {
  padding: 30px 32px 28px;
  display: grid; gap: 18px;
  background: linear-gradient(160deg, rgba(255, 252, 245, 1), rgba(237, 228, 211, 0.4));
}
.capture-head {
  display: flex; justify-content: space-between; align-items: flex-start;
  gap: 16px;
}
.capture-head h2 {
  font-family: var(--display-font);
  font-size: 1.6rem;
  letter-spacing: -0.02em;
  margin: 0;
  font-weight: 400;
}
.capture textarea {
  width: 100%;
  min-height: 200px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.86);
  border-radius: var(--radius-md);
  padding: 16px;
  font: inherit;
  font-size: 0.96rem;
  line-height: 1.5;
  resize: vertical;
  outline: none;
  transition: border-color 160ms ease, box-shadow 160ms ease;
}
.capture textarea:focus { border-color: var(--ink); box-shadow: 0 0 0 3px var(--accent-soft); }
.capture-fields {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.capture-fields input {
  width: 100%;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.86);
  border-radius: var(--radius-md);
  padding: 12px 14px;
  font: inherit;
  outline: none;
  transition: border-color 160ms ease, box-shadow 160ms ease;
}
.capture-fields input:focus { border-color: var(--ink); box-shadow: 0 0 0 3px var(--accent-soft); }
.capture-actions {
  display: flex; align-items: center; justify-content: space-between;
  gap: 14px; flex-wrap: wrap;
}
.capture-hint { margin: 0; color: var(--muted); font-size: 0.86rem; font-family: var(--mono-font); }
.capture-status {
  margin: 0; min-height: 1.4em;
  font-size: 0.92rem; color: var(--muted);
}
.capture-status[data-state="error"] { color: var(--accent-press); }
.capture-status[data-state="working"] { color: var(--forest); }
.capture-status[data-state="ok"] { color: var(--forest); }
.capture[data-loading] .primary-btn {
  pointer-events: none;
  background: var(--accent);
}

/* ---- thinking overlay ---- */
.thinking {
  position: fixed; inset: 0;
  display: none;
  z-index: 80;
  background: rgba(20, 17, 13, 0.8);
  backdrop-filter: blur(14px);
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 22px;
  color: var(--paper);
  font-family: var(--display-font);
  font-size: clamp(1.6rem, 3vw, 2.4rem);
  letter-spacing: -0.02em;
  animation: fadeIn 240ms ease;
}
.thinking[data-on] { display: flex; }
.thinking-dots {
  display: inline-flex; gap: 8px;
}
.thinking-dots span {
  width: 12px; height: 12px;
  border-radius: 999px;
  background: var(--accent);
  animation: bounce 1100ms cubic-bezier(0.4, 0, 0.6, 1) infinite;
}
.thinking-dots span:nth-child(2) { animation-delay: 140ms; background: var(--paper); }
.thinking-dots span:nth-child(3) { animation-delay: 280ms; background: var(--forest); }
.thinking-text {
  display: inline-flex; align-items: baseline; gap: 14px;
  text-align: center;
  max-width: 30ch;
}
.thinking-progress {
  width: min(360px, 70vw);
  height: 2px;
  background: rgba(255, 255, 255, 0.16);
  border-radius: 999px;
  overflow: hidden;
}
.thinking-progress span {
  display: block; height: 100%;
  width: 22%;
  background: linear-gradient(90deg, transparent, var(--accent), transparent);
  animation: scan 1400ms linear infinite;
}
@keyframes bounce {
  0%, 80%, 100% { transform: translateY(0); opacity: 0.6; }
  40% { transform: translateY(-10px); opacity: 1; }
}
@keyframes scan {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(500%); }
}
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }

/* ---- footer hints ---- */
.foot-hints {
  margin-top: 28px;
  padding: 18px 0 0;
  border-top: 1px solid var(--line);
  display: flex; flex-wrap: wrap; gap: 16px;
  font-family: var(--mono-font);
  font-size: 0.78rem;
  color: var(--muted);
  letter-spacing: 0.04em;
}

/* ---- animations ---- */
@keyframes rise {
  from { opacity: 0; transform: translateY(14px); }
  to   { opacity: 1; transform: translateY(0); }
}

main > .ingest-section,
main > .rail-section,
main > .status-section {
  animation: rise 620ms cubic-bezier(0.2, 0.7, 0.2, 1) both;
}
main > .ingest-section { animation-delay: 80ms; }
main > .rail-section   { animation-delay: 160ms; }
main > .status-section { animation-delay: 240ms; }

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.001ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.001ms !important;
  }
}

/* ---- responsive ---- */
@media (max-width: 960px) {
  .topbar-inner { gap: 14px; flex-wrap: wrap; }
  .search-mini { min-width: 220px; flex: 1; }
  .ask-meta { grid-template-columns: 1fr; }
  .answer-section, .page-section, .ingest-grid {
    grid-template-columns: 1fr;
  }
  .answer-side, .page-side { position: static; }
  .display { font-size: clamp(2.4rem, 11vw, 5rem); }
}
@media (max-width: 600px) {
  .shell { padding: 20px 18px 96px; }
  .topbar-inner { padding: 12px 18px 14px; }
  .ask-shell { padding: 8px 8px 8px 16px; }
  .ask-input { font-size: 1.04rem; }
  .ask-submit { padding: 12px 16px; }
  .fab { right: 14px; bottom: 14px; padding: 12px 16px; }
  .fab-kbd { display: none; }
  .capture-fields { grid-template-columns: 1fr; }
}
"""

_LAYOUT_JS = r"""
const PROMPTS = __PROMPTS__;

function rotateHero() {
  const word = document.querySelector("[data-rotor-word]");
  if (!word) return;
  const input = document.getElementById("ask-input");
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduced) return;
  let idx = 0;
  setInterval(() => {
    idx = (idx + 1) % PROMPTS.length;
    word.classList.add("rotor-out");
    setTimeout(() => {
      word.textContent = PROMPTS[idx];
      word.classList.remove("rotor-out");
      if (input && !input.value && document.activeElement !== input) {
        input.placeholder = `What does the wiki know about ${PROMPTS[idx]}?`;
      }
    }, 280);
  }, 2800);
}

function bindTagInput() {
  document.querySelectorAll("[data-tag-input]").forEach((wrap) => {
    const field = wrap.querySelector("[data-tag-field]");
    const hidden = wrap.querySelector("[data-tag-hidden]");
    const pillsHost = wrap.querySelector("[data-tag-pills]");
    if (!field || !hidden || !pillsHost) return;
    let tags = (hidden.value || "")
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);

    const render = () => {
      pillsHost.innerHTML = "";
      tags.forEach((t, i) => {
        const pill = document.createElement("span");
        pill.className = "tag-pill";
        const label = document.createElement("span");
        label.textContent = t;
        const btn = document.createElement("button");
        btn.type = "button";
        btn.setAttribute("aria-label", `Remove ${t}`);
        btn.textContent = "×";
        btn.addEventListener("click", () => {
          tags.splice(i, 1);
          sync();
        });
        pill.appendChild(label);
        pill.appendChild(btn);
        pillsHost.appendChild(pill);
      });
      hidden.value = tags.join(",");
    };
    const sync = () => render();

    const commit = () => {
      const raw = field.value.trim().replace(/,$/, "").trim();
      if (raw && !tags.includes(raw)) tags.push(raw);
      field.value = "";
      sync();
    };

    field.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === "," || event.key === "Tab") {
        if (field.value.trim()) {
          event.preventDefault();
          commit();
        }
      } else if (event.key === "Backspace" && !field.value && tags.length) {
        tags.pop();
        sync();
      }
    });
    field.addEventListener("blur", () => {
      if (field.value.trim()) commit();
    });

    sync();
  });
}

function bindAskLoading() {
  const overlay = document.querySelector("[data-thinking]");
  const messageEl = overlay && overlay.querySelector("[data-thinking-message]");
  const messages = [
    "Searching the wiki",
    "Pulling relevant pages",
    "Reading carefully",
    "Synthesizing",
    "Drafting your saved page",
  ];

  document.querySelectorAll("[data-ask-form]").forEach((form) => {
    form.addEventListener("submit", () => {
      form.dataset.loading = "true";
      if (!overlay || !messageEl) return;
      overlay.dataset.on = "true";
      let i = 0;
      messageEl.textContent = messages[0];
      const tick = () => {
        i = (i + 1) % messages.length;
        messageEl.style.opacity = "0";
        setTimeout(() => {
          messageEl.textContent = messages[i];
          messageEl.style.opacity = "1";
        }, 220);
      };
      const timer = setInterval(tick, 2200);
      window.addEventListener("pagehide", () => clearInterval(timer), { once: true });
    });
  });
}

function bindCapture() {
  const dialog = document.querySelector("[data-capture-dialog]");
  const trigger = document.querySelector("[data-capture-trigger]");
  const closeBtn = document.querySelector("[data-capture-close]");
  if (!dialog) return;

  const open = () => {
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "true");
    setTimeout(() => {
      const ta = dialog.querySelector("textarea");
      if (ta) ta.focus();
    }, 30);
  };
  const close = () => {
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
  };

  const setBodyState = (isOpen) => {
    document.body.toggleAttribute("data-capture-open", isOpen);
  };
  if (trigger) trigger.addEventListener("click", () => { open(); setBodyState(true); });
  if (closeBtn) closeBtn.addEventListener("click", () => { close(); setBodyState(false); });
  dialog.addEventListener("close", () => setBodyState(false));
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) { close(); setBodyState(false); }
  });

  const form = dialog.querySelector("[data-capture-form]");
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const status = form.querySelector("[data-capture-status]");
    const button = form.querySelector('button[type="submit"]');
    const label = form.querySelector("[data-capture-label]");
    const text = form.querySelector('textarea[name="text"]');
    const title = form.querySelector('input[name="title"]');
    const url = form.querySelector('input[name="source_url"]');
    if (!text) return;
    form.dataset.loading = "true";
    if (status) {
      status.dataset.state = "working";
      status.textContent = "Capturing and processing…";
    }
    if (label) label.textContent = "Working…";
    if (button) button.disabled = true;
    try {
      const response = await fetch("/capture", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: text.value,
          title: title ? title.value : "",
          source_url: url ? url.value : "",
        }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.message || "Could not capture the note.");
      if (payload.redirect) {
        window.location.assign(payload.redirect);
        return;
      }
      form.reset();
      if (status) {
        status.dataset.state = "ok";
        status.textContent = payload.message || "Captured.";
      }
    } catch (error) {
      if (status) {
        status.dataset.state = "error";
        status.textContent = error instanceof Error ? error.message : "Could not capture the note.";
      }
    } finally {
      delete form.dataset.loading;
      if (label) label.textContent = "Capture & process";
      if (button) button.disabled = false;
    }
  });
}

function bindShortcuts() {
  document.addEventListener("keydown", (event) => {
    const target = event.target;
    const isTyping = target instanceof HTMLElement && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);

    if (event.key === "/" && !isTyping) {
      const ask = document.getElementById("ask-input");
      if (ask) {
        event.preventDefault();
        ask.focus();
        ask.select();
      }
    }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      const trigger = document.querySelector("[data-capture-trigger]");
      if (trigger) trigger.click();
    }
    if (event.key === "Escape") {
      const overlay = document.querySelector("[data-thinking]");
      if (overlay && overlay.dataset.on) {
        delete overlay.dataset.on;
      }
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  rotateHero();
  bindTagInput();
  bindAskLoading();
  bindCapture();
  bindShortcuts();
});
"""


def _layout_js() -> str:
    return _LAYOUT_JS.replace("__PROMPTS__", json.dumps(list(EXAMPLE_PROMPTS)))


def render_layout(title: str, body: str, *, q: str = "") -> str:
    safe_title = escape(title)
    search_value = escape(q)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title} · oamc</title>
  <style>{_LAYOUT_CSS}</style>
</head>
<body>
  <header class="topbar">
    <div class="topbar-inner">
      <a class="brand" href="/">
        <span class="brand-dot" aria-hidden="true"></span>
        <div>
          <div class="brand-mark">oamc</div>
          <div class="brand-tag">private wiki · research mode</div>
        </div>
      </a>
      <div class="topbar-tools">
        <form class="search-mini" action="/search" method="get" role="search">
          <span aria-hidden="true">⌕</span>
          <input type="search" name="q" value="{search_value}" placeholder="Search the wiki" aria-label="Search">
          <button type="submit">go</button>
        </form>
      </div>
    </div>
  </header>
  <div class="shell">
    <main>{body}</main>
    <footer class="foot-hints">
      <span><span class="kbd">/</span> focus ask</span>
      <span><span class="kbd">⌘K</span> capture note</span>
      <span><span class="kbd">esc</span> dismiss</span>
    </footer>
  </div>

  <button class="fab" type="button" data-capture-trigger aria-haspopup="dialog">
    <span class="fab-glyph" aria-hidden="true">＋</span>
    <span>Capture note</span>
    <span class="fab-kbd" aria-hidden="true">⌘K</span>
  </button>

  {render_capture_dialog()}

  <div class="thinking" data-thinking aria-hidden="true">
    <div class="thinking-dots" aria-hidden="true"><span></span><span></span><span></span></div>
    <div class="thinking-text">
      <span data-thinking-message style="transition: opacity 220ms ease;">Searching the wiki</span>
    </div>
    <div class="thinking-progress" aria-hidden="true"><span></span></div>
  </div>

  <script>{_layout_js()}</script>
</body>
</html>"""
