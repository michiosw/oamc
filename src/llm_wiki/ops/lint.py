from __future__ import annotations

from collections import defaultdict

from llm_wiki.health import index_drift, log_heading_issues, page_metadata_issues
from llm_wiki.llm.base import LLMClient
from llm_wiki.markdown import extract_wikilinks, link_target_for_path
from llm_wiki.models import AppConfig, LintIssue, LintRequest, LintResult, RepoPaths
from llm_wiki.ops.common import append_log_entry, normalize_existing_wiki_page, write_wiki_draft
from llm_wiki.ops.rebuild_index import rebuild_index
from llm_wiki.ops.search import inbound_link_counts, iter_wiki_pages, relative_link_targets
from llm_wiki.paths import repo_relative


def detect_issues(repo_paths: RepoPaths) -> list[LintIssue]:
    issues: list[LintIssue] = []
    inbound_counts = inbound_link_counts(repo_paths)
    existing_links = relative_link_targets(repo_paths)

    for page in iter_wiki_pages(repo_paths):
        relative_path = page.relative_to(repo_paths.wiki_root).as_posix()
        link_target = link_target_for_path(relative_path)
        if inbound_counts[link_target] == 0:
            issues.append(
                LintIssue(
                    code="orphan_page",
                    relative_path=relative_path,
                    detail=f"{relative_path} has no inbound wikilinks from other wiki pages.",
                )
            )
        for target in extract_wikilinks(page.read_text(encoding="utf-8")):
            if target.startswith("concepts/") and target not in existing_links:
                issues.append(
                    LintIssue(
                        code="missing_concept_page",
                        relative_path=f"{target}.md",
                        detail=f"{relative_path} references missing concept page [[{target}]].",
                    )
                )
    deduped = {}
    for issue in issues:
        deduped[(issue.code, issue.relative_path, issue.detail)] = issue
    return list(deduped.values())


def run_lint(
    config: AppConfig,
    repo_paths: RepoPaths,
    client: LLMClient,
) -> LintResult:
    touched: list[str] = []
    normalized_pages: list[str] = []
    for page in iter_wiki_pages(repo_paths):
        relative_path = page.relative_to(repo_paths.wiki_root).as_posix()
        original = page.read_text(encoding="utf-8")
        normalized = normalize_existing_wiki_page(relative_path, original)
        if normalized != original:
            page.write_text(normalized, encoding="utf-8")
            touched.append(relative_path)
            normalized_pages.append(relative_path)

    index_missing_before, index_extra_before = index_drift(repo_paths)
    metadata_issues = page_metadata_issues(repo_paths)
    log_issues = log_heading_issues(repo_paths)

    issues = detect_issues(repo_paths)
    if not issues:
        rebuild_index(repo_paths)
        touched.append(repo_relative(repo_paths.index, repo_paths.base_dir))
        detail_parts = ["No structural wiki issues were detected."]
        if normalized_pages:
            detail_parts.append(f"Normalized {len(normalized_pages)} page(s).")
        if index_missing_before or index_extra_before:
            detail_parts.append("Rebuilt index to remove drift.")
        if metadata_issues:
            detail_parts.append("Some wiki pages still have metadata issues.")
        if log_issues:
            detail_parts.append("wiki/log.md still has malformed headings.")
        append_log_entry(
            repo_paths,
            operation="lint",
            title="no-op",
            summary=" ".join(detail_parts),
            touched_pages=sorted(set(touched or ["wiki/index.md"])),
        )
        touched.append(repo_relative(repo_paths.log, repo_paths.base_dir))
        return LintResult(
            issues=[],
            touched=sorted(set(touched)),
            normalized_pages=normalized_pages,
        )

    page_contexts: dict[str, str] = {}
    for issue in issues:
        if not issue.relative_path:
            continue
        candidate = repo_paths.wiki_root / issue.relative_path
        if candidate.exists():
            page_contexts[issue.relative_path] = candidate.read_text(encoding="utf-8")

    request = LintRequest(
        schema_text=(repo_paths.config_dir / "schema.md").read_text(encoding="utf-8"),
        index_text=repo_paths.index.read_text(encoding="utf-8"),
        issues=issues,
        page_contexts=page_contexts,
    )
    response = client.lint(request)
    for draft in response.created_pages:
        touched.append(write_wiki_draft(draft, repo_paths=repo_paths))
    for draft in response.updated_pages:
        touched.append(write_wiki_draft(draft, repo_paths=repo_paths))
    rebuild_index(repo_paths)
    touched.append(repo_relative(repo_paths.index, repo_paths.base_dir))
    append_log_entry(
        repo_paths,
        operation="lint",
        title="wiki-health",
        summary=response.notes or f"Addressed {len(issues)} structural wiki issues.",
        touched_pages=sorted(set(touched)),
    )
    touched.append(repo_relative(repo_paths.log, repo_paths.base_dir))
    return LintResult(
        issues=issues,
        touched=sorted(set(touched)),
        normalized_pages=normalized_pages,
    )
