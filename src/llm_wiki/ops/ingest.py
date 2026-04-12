from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from llm_wiki.llm.base import LLMClient
from llm_wiki.markdown import read_text, slugify
from llm_wiki.models import AppConfig, IngestRequest, IngestResult, RepoPaths
from llm_wiki.ops.common import append_log_entry, write_wiki_draft
from llm_wiki.ops.rebuild_index import rebuild_index
from llm_wiki.ops.search import list_candidates
from llm_wiki.paths import repo_relative


def _resolve_source_path(repo_paths: RepoPaths, value: Path) -> Path:
    candidate = value if value.is_absolute() else (repo_paths.base_dir / value)
    if candidate.exists():
        return candidate.resolve()
    inbox_candidate = repo_paths.raw_inbox / value
    if inbox_candidate.exists():
        return inbox_candidate.resolve()
    raise FileNotFoundError(f"Source path not found: {value}")


def _planned_storage_destination(repo_paths: RepoPaths, source_path: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    destination_name = f"{stamp}-{slugify(source_path.stem)}{source_path.suffix.lower()}"
    destination = repo_paths.raw_sources / destination_name
    counter = 2
    while destination.exists():
        destination = repo_paths.raw_sources / f"{stamp}-{slugify(source_path.stem)}-{counter}{source_path.suffix.lower()}"
        counter += 1
    return destination


def _store_source(repo_paths: RepoPaths, source_path: Path, destination: Path) -> tuple[Path, str]:
    try:
        source_path.resolve().relative_to(destination.parents[1] / "inbox")
        is_in_inbox = True
    except ValueError:
        is_in_inbox = False

    if source_path.resolve() == destination.resolve():
        stored = destination
    elif is_in_inbox:
        source_path.rename(destination)
        stored = destination
    else:
        shutil.copy2(source_path, destination)
        stored = destination

    return stored, repo_relative(stored, repo_paths.base_dir)


def ingest_sources(
    config: AppConfig,
    repo_paths: RepoPaths,
    client: LLMClient,
    source_paths: list[Path],
) -> IngestResult:
    all_touched: list[str] = []
    processed_sources: list[str] = []
    source_pages: list[str] = []
    entity_pages: list[str] = []
    concept_pages: list[str] = []
    schema_text = (repo_paths.config_dir / "schema.md").read_text(encoding="utf-8")
    index_text = repo_paths.index.read_text(encoding="utf-8")

    for original_path in source_paths:
        touched: list[str] = []
        source_path = _resolve_source_path(repo_paths, original_path)
        source_text = read_text(source_path)
        storage_destination = _planned_storage_destination(repo_paths, source_path)
        stored_relative = repo_relative(storage_destination, repo_paths.base_dir)
        request = IngestRequest(
            source_name=storage_destination.name,
            source_text=source_text,
            source_path=stored_relative,
            schema_text=schema_text,
            index_text=index_text,
            existing_pages=list_candidates(repo_paths),
        )
        response = client.ingest(request)

        source_page_default = f"sources/{storage_destination.stem}.md"
        source_page_path = write_wiki_draft(
            response.source_page,
            repo_paths=repo_paths,
            default_relative_path=source_page_default,
            source_refs=[stored_relative],
        )
        touched.append(source_page_path)
        source_pages.append(source_page_path)
        for draft in response.entity_pages:
            page_path = write_wiki_draft(
                draft,
                repo_paths=repo_paths,
                source_refs=[stored_relative],
            )
            touched.append(page_path)
            entity_pages.append(page_path)
        for draft in response.concept_pages:
            page_path = write_wiki_draft(
                draft,
                repo_paths=repo_paths,
                source_refs=[stored_relative],
            )
            touched.append(page_path)
            concept_pages.append(page_path)
        index_text = rebuild_index(repo_paths)
        touched.append(repo_relative(repo_paths.index, repo_paths.base_dir))
        append_log_entry(
            repo_paths,
            operation="ingest",
            title=storage_destination.stem,
            summary=response.notes or f"Ingested {stored_relative} into the wiki.",
            touched_pages=sorted(set(touched)),
        )
        _store_source(repo_paths, source_path, storage_destination)
        touched.append(repo_relative(repo_paths.log, repo_paths.base_dir))
        all_touched.extend(touched)
        processed_sources.append(stored_relative)

    return IngestResult(
        touched=sorted(set(all_touched)),
        processed_sources=processed_sources,
        source_pages=source_pages,
        entity_pages=entity_pages,
        concept_pages=concept_pages,
    )
