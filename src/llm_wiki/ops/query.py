from __future__ import annotations

from uuid import uuid4

from llm_wiki.llm.base import LLMClient
from llm_wiki.core.markdown import extract_section, slugify, summary_from_content, title_from_content
from llm_wiki.core.models import AppConfig, QueryRequest, QueryResult, RepoPaths, ResearchTemplate
from llm_wiki.ops.common import append_log_entry, write_wiki_draft
from llm_wiki.ops.rebuild_index import rebuild_index
from llm_wiki.ops.search import load_page_contexts, search_pages
from llm_wiki.core.paths import repo_relative
from llm_wiki.core.telemetry import get_logger, log_event


LOGGER = get_logger(__name__)


def run_query(
    config: AppConfig,
    repo_paths: RepoPaths,
    client: LLMClient,
    question: str,
    *,
    write_page: bool,
    template: ResearchTemplate = "synthesis",
    top_k: int | None = None,
    scopes: list[str] | None = None,
) -> QueryResult:
    operation_id = uuid4().hex[:8]
    candidates = search_pages(
        repo_paths,
        question,
        top_k=top_k or config.search.default_top_k,
        scopes=scopes or [],
    )
    contexts = load_page_contexts(
        repo_paths,
        candidates,
        max_chars=config.search.max_context_chars,
    )
    request = QueryRequest(
        question=question,
        template=template,
        schema_text=(repo_paths.config_dir / "schema.md").read_text(encoding="utf-8"),
        index_text=repo_paths.index.read_text(encoding="utf-8"),
        candidates=candidates,
        page_contexts=contexts,
    )
    response = client.query(request)
    log_event(
        LOGGER,
        "query_completed",
        operation_id=operation_id,
        question=question,
        template=template,
        candidate_count=len(candidates),
    )
    touched: list[str] = []
    page_path: str | None = None
    if write_page:
        default_relative_path = f"syntheses/{slugify(question)[:80]}.md"
        page_path = write_wiki_draft(
            response.page,
            repo_paths=repo_paths,
            default_relative_path=default_relative_path,
            source_refs=[candidate.relative_path for candidate in candidates],
        )
        touched.append(page_path)
        rebuild_index(repo_paths)
        touched.append(repo_relative(repo_paths.index, repo_paths.base_dir))
        append_log_entry(
            repo_paths,
            operation="query",
            title=question[:80],
            summary=response.notes or f"Answered question and filed synthesis for: {question}",
            touched_pages=sorted(set(touched)),
        )
        touched.append(repo_relative(repo_paths.log, repo_paths.base_dir))
    title = title_from_content(response.page.content, fallback=slugify(question).replace("-", " ").title())
    answer_preview = extract_section(response.page.content, "Summary Answer") or summary_from_content(response.page.content, fallback=title)
    return QueryResult(
        touched=sorted(set(touched)),
        page_path=page_path,
        title=title,
        answer_preview=answer_preview,
        content=response.page.content,
        template=template,
        selected_candidates=[candidate.relative_path for candidate in candidates],
        operation_id=operation_id,
    )
