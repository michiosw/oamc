from __future__ import annotations

from abc import ABC, abstractmethod

from llm_wiki.core.models import (
    IngestRequest,
    IngestResponse,
    LintRequest,
    LintResponse,
    QueryRequest,
    QueryResponse,
)


class LLMClient(ABC):
    @abstractmethod
    def ingest(self, request: IngestRequest) -> IngestResponse:
        raise NotImplementedError

    @abstractmethod
    def query(self, request: QueryRequest) -> QueryResponse:
        raise NotImplementedError

    @abstractmethod
    def lint(self, request: LintRequest) -> LintResponse:
        raise NotImplementedError
