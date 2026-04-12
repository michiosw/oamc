from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

CURRENT_SCHEMA_VERSION = 1

ResearchTemplate = Literal[
    "synthesis",
    "compare",
    "timeline",
    "open-questions",
    "decision-brief",
]

RESEARCH_TEMPLATES = (
    "synthesis",
    "compare",
    "timeline",
    "open-questions",
    "decision-brief",
)


class PathsConfig(BaseModel):
    raw_inbox: str = "raw/inbox"
    raw_sources: str = "raw/sources"
    assets: str = "raw/assets"
    wiki_root: str = "wiki"
    index: str = "wiki/index.md"
    log: str = "wiki/log.md"


class StyleConfig(BaseModel):
    summary_target_words: int = 250
    enable_citations: bool = True
    use_yaml_frontmatter: bool = True


class SearchConfig(BaseModel):
    default_top_k: int = 6
    max_context_chars: int = 16000


class ObsidianConfig(BaseModel):
    attachment_dir: str = "raw/assets"
    use_wikilinks: bool = True


class AppConfig(BaseModel):
    schema_version: int = CURRENT_SCHEMA_VERSION
    model_provider: Literal["openai"] = "openai"
    model_name: str = "gpt-4.1"
    openai_api_key_env: str = "OPENAI_API_KEY"
    base_dir: str = "."
    paths: PathsConfig = Field(default_factory=PathsConfig)
    style: StyleConfig = Field(default_factory=StyleConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    obsidian: ObsidianConfig = Field(default_factory=ObsidianConfig)


class RepoPaths(BaseModel):
    base_dir: Path
    config_dir: Path
    raw_inbox: Path
    raw_sources: Path
    assets: Path
    wiki_root: Path
    index: Path
    log: Path


class PageDraft(BaseModel):
    relative_path: str
    content: str


class SearchCandidate(BaseModel):
    relative_path: str
    title: str
    summary: str
    score: float = 0.0


class IngestRequest(BaseModel):
    source_name: str
    source_text: str
    source_path: str
    schema_text: str
    index_text: str
    existing_pages: list[SearchCandidate] = Field(default_factory=list)


class IngestResponse(BaseModel):
    source_page: PageDraft
    entity_pages: list[PageDraft] = Field(default_factory=list)
    concept_pages: list[PageDraft] = Field(default_factory=list)
    notes: str = ""


class IngestResult(BaseModel):
    touched: list[str] = Field(default_factory=list)
    processed_sources: list[str] = Field(default_factory=list)
    source_pages: list[str] = Field(default_factory=list)
    entity_pages: list[str] = Field(default_factory=list)
    concept_pages: list[str] = Field(default_factory=list)
    operation_id: str = ""


class QueryRequest(BaseModel):
    question: str
    template: ResearchTemplate = "synthesis"
    schema_text: str
    index_text: str
    candidates: list[SearchCandidate] = Field(default_factory=list)
    page_contexts: dict[str, str] = Field(default_factory=dict)


class QueryResponse(BaseModel):
    page: PageDraft
    notes: str = ""


class QueryResult(BaseModel):
    touched: list[str] = Field(default_factory=list)
    page_path: str | None = None
    title: str = ""
    answer_preview: str = ""
    content: str = ""
    template: str = "synthesis"
    selected_candidates: list[str] = Field(default_factory=list)
    operation_id: str = ""


class LintIssue(BaseModel):
    code: Literal["orphan_page", "missing_concept_page"]
    relative_path: str | None = None
    detail: str


class LintRequest(BaseModel):
    schema_text: str
    index_text: str
    issues: list[LintIssue] = Field(default_factory=list)
    page_contexts: dict[str, str] = Field(default_factory=dict)


class LintResponse(BaseModel):
    created_pages: list[PageDraft] = Field(default_factory=list)
    updated_pages: list[PageDraft] = Field(default_factory=list)
    notes: str = ""


class LintResult(BaseModel):
    issues: list[LintIssue] = Field(default_factory=list)
    touched: list[str] = Field(default_factory=list)
    normalized_pages: list[str] = Field(default_factory=list)
    operation_id: str = ""


class HealthCheck(BaseModel):
    key: str
    label: str
    status: Literal["ok", "warn", "error"]
    detail: str
    recommendation: str = ""


class ActivityEntry(BaseModel):
    heading: str
    operation: str
    title: str
    summary: str
    touched_pages: list[str] = Field(default_factory=list)


class DoctorReport(BaseModel):
    checks: list[HealthCheck] = Field(default_factory=list)
    latest_log_heading: str | None = None
    latest_processed_source: str | None = None
    latest_ingest: ActivityEntry | None = None
    clippings_files: list[str] = Field(default_factory=list)
    overall_status: Literal["ok", "warn", "error"] = "ok"
    recommended_next_step: str = "System healthy. Clip into raw/inbox/ and ask the wiki."
