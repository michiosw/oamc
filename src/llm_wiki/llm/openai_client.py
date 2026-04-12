from __future__ import annotations

import os
from typing import TypeVar

from openai import AuthenticationError, OpenAI

from llm_wiki.env import api_key_issue

from llm_wiki.llm.base import LLMClient
from llm_wiki.models import (
    AppConfig,
    IngestRequest,
    IngestResponse,
    LintRequest,
    LintResponse,
    QueryRequest,
    QueryResponse,
)
from llm_wiki.prompts import (
    build_ingest_prompts,
    build_lint_prompts,
    build_query_prompts,
)


ResponseT = TypeVar("ResponseT")


def resolve_api_key(config: AppConfig) -> str:
    issue = api_key_issue(config.openai_api_key_env)
    if issue == "missing":
        raise RuntimeError(
            f"Missing required environment variable: {config.openai_api_key_env}"
        )
    if issue == "placeholder":
        raise RuntimeError(
            f"Invalid {config.openai_api_key_env}: replace the placeholder value in .env with a real OpenAI API key."
        )

    api_key = os.getenv(config.openai_api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(
            f"Missing required environment variable: {config.openai_api_key_env}"
        )
    return api_key


class OpenAIWikiClient(LLMClient):
    def __init__(self, config: AppConfig) -> None:
        api_key = resolve_api_key(config)
        self.config = config
        self.client = OpenAI(api_key=api_key)

    def _parse(self, system_prompt: str, user_prompt: str, response_model: type[ResponseT]) -> ResponseT:
        try:
            completion = self.client.beta.chat.completions.parse(
                model=self.config.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=response_model,
            )
        except AuthenticationError as exc:
            raise RuntimeError(
                f"OpenAI authentication failed. Update {self.config.openai_api_key_env} in .env, then restart oamc."
            ) from exc
        parsed = completion.choices[0].message.parsed
        if parsed is None:
            raise RuntimeError("OpenAI response did not produce structured output.")
        return parsed

    def ingest(self, request: IngestRequest) -> IngestResponse:
        system_prompt, user_prompt = build_ingest_prompts(request)
        return self._parse(system_prompt, user_prompt, IngestResponse)

    def query(self, request: QueryRequest) -> QueryResponse:
        system_prompt, user_prompt = build_query_prompts(request)
        return self._parse(system_prompt, user_prompt, QueryResponse)

    def lint(self, request: LintRequest) -> LintResponse:
        system_prompt, user_prompt = build_lint_prompts(request)
        return self._parse(system_prompt, user_prompt, LintResponse)
