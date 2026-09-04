"""Busca de URLs; extração de páginas é uma capacidade separada."""

from dataclasses import dataclass
from typing import Protocol


class WebSearchError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class WebSearchResult:
    title: str
    url: str
    snippet: str
    position: int | None = None

    @property
    def description(self) -> str:
        return self.snippet


@dataclass(frozen=True, slots=True)
class WebSearchResponse:
    results: tuple[WebSearchResult, ...]
    provider: str
    source: str
    correlation_id: str
    request_id: str | None = None
    upstream_request_id: str | None = None
    cached: bool = False
    queries_used: int = 1
    credits_used: float | None = None
    fallback_reason: str | None = None


class WebSearchProvider(Protocol):
    async def search(
        self, query: str, *, limit: int, correlation_id: str
    ) -> WebSearchResponse: ...
