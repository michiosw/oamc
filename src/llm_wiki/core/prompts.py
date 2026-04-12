from __future__ import annotations

from llm_wiki.core.models import IngestRequest, LintRequest, QueryRequest, SearchCandidate

QUERY_TEMPLATE_INSTRUCTIONS = {
    "synthesis": "Write a synthesis page that answers the question directly, then expands with analysis and implications.",
    "compare": "Write a comparison-focused synthesis page. Highlight the main dimensions, tradeoffs, and strongest contrasts before implications.",
    "timeline": "Write a chronology-focused synthesis page. Emphasize what changed over time, sequence, and transitions.",
    "open-questions": "Write a synthesis page that separates what is known from what remains uncertain. End with the highest-value next questions.",
    "decision-brief": "Write a decision-oriented synthesis page. Lead with a recommendation, rationale, risks, and next steps.",
}


def _format_candidates(candidates: list[SearchCandidate]) -> str:
    if not candidates:
        return "No existing wiki pages yet."
    lines = []
    for candidate in candidates:
        lines.append(
            f"- {candidate.relative_path}: {candidate.title} | {candidate.summary}"
        )
    return "\n".join(lines)


def build_ingest_prompts(request: IngestRequest) -> tuple[str, str]:
    system_prompt = (
        "You maintain a markdown wiki. Return a structured ingest response with full "
        "markdown content for every page to create or update. Keep edits incremental, "
        "use wikilinks, and include a Sources section in every page body."
    )
    user_prompt = f"""Schema:
{request.schema_text}

Current index:
{request.index_text}

Existing pages:
{_format_candidates(request.existing_pages)}

Source path:
{request.source_path}

Source name:
{request.source_name}

Source text:
{request.source_text}
"""
    return system_prompt, user_prompt


def build_query_prompts(request: QueryRequest) -> tuple[str, str]:
    system_prompt = (
        "You answer questions against a maintained markdown wiki. Return a single "
        "synthesis page as structured markdown with YAML frontmatter and a Sources section."
    )
    template_instruction = QUERY_TEMPLATE_INSTRUCTIONS.get(request.template, QUERY_TEMPLATE_INSTRUCTIONS["synthesis"])
    context_blocks = "\n\n".join(
        f"### {path}\n{content}" for path, content in request.page_contexts.items()
    )
    user_prompt = f"""Schema:
{request.schema_text}

Current index:
{request.index_text}

Question:
{request.question}

Template:
{request.template}

Template instruction:
{template_instruction}

Candidate pages:
{_format_candidates(request.candidates)}

Selected page contents:
{context_blocks}
"""
    return system_prompt, user_prompt


def build_lint_prompts(request: LintRequest) -> tuple[str, str]:
    system_prompt = (
        "You repair structural issues in a markdown wiki. Return concrete page creations "
        "and updates only for the issues provided. Keep the changes narrow and use wikilinks."
    )
    issue_lines = "\n".join(
        f"- {issue.code}: {issue.relative_path or 'n/a'} | {issue.detail}"
        for issue in request.issues
    )
    context_blocks = "\n\n".join(
        f"### {path}\n{content}" for path, content in request.page_contexts.items()
    )
    user_prompt = f"""Schema:
{request.schema_text}

Current index:
{request.index_text}

Issues:
{issue_lines}

Relevant page contents:
{context_blocks}
"""
    return system_prompt, user_prompt
