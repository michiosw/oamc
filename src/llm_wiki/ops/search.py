from __future__ import annotations

from collections import Counter
from pathlib import Path

from llm_wiki.markdown import link_target_for_path, load_markdown, slugify, summary_from_content
from llm_wiki.models import RepoPaths, SearchCandidate


WIKI_DIRS = ("sources", "entities", "concepts", "syntheses")


def iter_wiki_pages(repo_paths: RepoPaths) -> list[Path]:
    pages: list[Path] = []
    for directory in WIKI_DIRS:
        pages.extend(sorted((repo_paths.wiki_root / directory).glob("*.md")))
    return pages


def list_candidates(repo_paths: RepoPaths) -> list[SearchCandidate]:
    candidates: list[SearchCandidate] = []
    for page in iter_wiki_pages(repo_paths):
        metadata, body = load_markdown(page)
        relative_path = page.relative_to(repo_paths.wiki_root).as_posix()
        candidates.append(
            SearchCandidate(
                relative_path=relative_path,
                title=str(metadata.get("title") or page.stem),
                summary=summary_from_content(body, fallback=""),
            )
        )
    return candidates


def _score_text(query_terms: list[str], text: str, weight: float) -> float:
    lowered = text.lower()
    return sum(lowered.count(term) for term in query_terms) * weight


def search_pages(repo_paths: RepoPaths, query: str, top_k: int = 6) -> list[SearchCandidate]:
    query_terms = [term for term in slugify(query).split("-") if term]
    ranked: list[SearchCandidate] = []
    for candidate in list_candidates(repo_paths):
        page = repo_paths.wiki_root / candidate.relative_path
        content = page.read_text(encoding="utf-8")
        score = 0.0
        score += _score_text(query_terms, candidate.title, 5.0)
        score += _score_text(query_terms, candidate.relative_path, 3.0)
        score += _score_text(query_terms, candidate.summary, 2.0)
        score += _score_text(query_terms, content, 0.5)
        ranked.append(candidate.model_copy(update={"score": score}))

    ranked.sort(key=lambda item: (-item.score, item.relative_path))
    return ranked[:top_k]


def load_page_contexts(
    repo_paths: RepoPaths,
    candidates: list[SearchCandidate],
    *,
    max_chars: int,
) -> dict[str, str]:
    contexts: dict[str, str] = {}
    remaining = max_chars
    for candidate in candidates:
        content = (repo_paths.wiki_root / candidate.relative_path).read_text(encoding="utf-8")
        if remaining <= 0:
            break
        if len(content) > remaining:
            content = content[:remaining]
        contexts[candidate.relative_path] = content
        remaining -= len(content)
    return contexts


def inbound_link_counts(repo_paths: RepoPaths) -> Counter[str]:
    from llm_wiki.markdown import extract_wikilinks

    counts: Counter[str] = Counter()
    for page in iter_wiki_pages(repo_paths):
        content = page.read_text(encoding="utf-8")
        for target in extract_wikilinks(content):
            counts[target] += 1
    return counts


def relative_link_targets(repo_paths: RepoPaths) -> set[str]:
    return {
        link_target_for_path(page.relative_to(repo_paths.wiki_root).as_posix())
        for page in iter_wiki_pages(repo_paths)
    }
