from __future__ import annotations

from llm_wiki.llm.base import LLMClient
from llm_wiki.markdown import slugify
from llm_wiki.models import AppConfig, QueryRequest, RepoPaths
from llm_wiki.ops.common import append_log_entry, write_wiki_draft
from llm_wiki.ops.rebuild_index import rebuild_index
from llm_wiki.ops.search import load_page_contexts, search_pages
from llm_wiki.paths import repo_relative


def run_query(
    config: AppConfig,
    repo_paths: RepoPaths,
    client: LLMClient,
    question: str,
    *,
    write_page: bool,
) -> list[str]:
    candidates = search_pages(
        repo_paths,
        question,
        top_k=config.search.default_top_k,
    )
    contexts = load_page_contexts(
        repo_paths,
        candidates,
        max_chars=config.search.max_context_chars,
    )
    request = QueryRequest(
        question=question,
        schema_text=(repo_paths.config_dir / "schema.md").read_text(encoding="utf-8"),
        index_text=repo_paths.index.read_text(encoding="utf-8"),
        candidates=candidates,
        page_contexts=contexts,
    )
    response = client.query(request)
    touched: list[str] = []
    if write_page:
        default_relative_path = f"syntheses/{slugify(question)[:80]}.md"
        touched.append(
            write_wiki_draft(
                response.page,
                repo_paths=repo_paths,
                default_relative_path=default_relative_path,
                source_refs=[candidate.relative_path for candidate in candidates],
            )
        )
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
    return sorted(set(touched))
