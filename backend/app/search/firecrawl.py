"""Cliente direto e contrato mínimo da Firecrawl Search API v2."""

from collections.abc import Callable
from dataclasses import dataclass

import httpx
from pydantic import SecretStr

_SEARCH_ENDPOINT = "https://api.firecrawl.dev/v2/search"


class FirecrawlSearchError(RuntimeError):
    """Erro sanitizado da pesquisa, sem resposta bruta ou credencial."""

    def __init__(self, code: str, *, status_code: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class FirecrawlSearchResult:
    title: str
    description: str | None
    url: str
    markdown: str | None = None
    html: str | None = None


@dataclass(frozen=True, slots=True)
class FirecrawlSearchResponse:
    success: bool
    results: tuple[FirecrawlSearchResult, ...]
    status_code: int
    warning: str | None = None
    request_id: str | None = None
    credits_used: int | float | None = None

    @property
    def search_performed(self) -> bool:
        """Só há grounding quando a API confirma sucesso e entrega fonte web."""
        return self.success and bool(self.results)


class FirecrawlSearchProvider:
    def __init__(
        self,
        api_key: SecretStr,
        *,
        client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not api_key.get_secret_value().strip():
            raise FirecrawlSearchError("firecrawl_api_key_required")
        if timeout_seconds <= 0:
            raise FirecrawlSearchError("firecrawl_timeout_invalid")
        self._api_key = api_key
        self._client_factory = client_factory
        self._timeout_seconds = timeout_seconds

    async def search(
        self,
        query: str,
        *,
        sources: tuple[str, ...] = ("web",),
        limit: int = 2,
    ) -> FirecrawlSearchResponse:
        if not query.strip():
            raise FirecrawlSearchError("firecrawl_query_required")
        if sources != ("web",):
            raise FirecrawlSearchError("firecrawl_sources_invalid")
        if not 1 <= limit <= 100:
            raise FirecrawlSearchError("firecrawl_limit_invalid")
        try:
            async with self._client_factory(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    _SEARCH_ENDPOINT,
                    headers={
                        "Authorization": (f"Bearer {self._api_key.get_secret_value()}"),
                        "Content-Type": "application/json",
                    },
                    json={"query": query, "sources": list(sources), "limit": limit},
                )
        except httpx.TimeoutException:
            raise FirecrawlSearchError("firecrawl_timeout") from None
        except httpx.HTTPError:
            raise FirecrawlSearchError("firecrawl_unavailable") from None

        if response.status_code != 200:
            code = (
                "firecrawl_authentication_failed"
                if response.status_code in {401, 403}
                else "firecrawl_rate_limited"
                if response.status_code == 429
                else "firecrawl_request_failed"
            )
            raise FirecrawlSearchError(code, status_code=response.status_code)
        try:
            body = response.json()
        except ValueError:
            raise FirecrawlSearchError(
                "firecrawl_response_invalid", status_code=response.status_code
            ) from None
        return parse_firecrawl_search_response(body, status_code=response.status_code)


def parse_firecrawl_search_response(
    body: object, *, status_code: int = 200
) -> FirecrawlSearchResponse:
    """Interpreta v2: resultados web ficam em `data.web`, nunca em `data`."""
    if not isinstance(body, dict) or body.get("success") is not True:
        raise FirecrawlSearchError(
            "firecrawl_response_invalid", status_code=status_code
        )
    data = body.get("data")
    web = data.get("web") if isinstance(data, dict) else None
    if not isinstance(web, list):
        raise FirecrawlSearchError(
            "firecrawl_response_invalid", status_code=status_code
        )
    results: list[FirecrawlSearchResult] = []
    for item in web:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        url = item.get("url")
        if not isinstance(title, str) or not title.strip():
            continue
        if not isinstance(url, str) or not url.strip():
            continue
        description = item.get("description")
        markdown = item.get("markdown")
        html = item.get("html")
        results.append(
            FirecrawlSearchResult(
                title=title.strip(),
                description=(
                    description.strip() if isinstance(description, str) else None
                ),
                url=url.strip(),
                markdown=markdown if isinstance(markdown, str) else None,
                html=html if isinstance(html, str) else None,
            )
        )
    warning = body.get("warning")
    request_id = body.get("id")
    credits_used = body.get("creditsUsed")
    return FirecrawlSearchResponse(
        success=True,
        results=tuple(results),
        status_code=status_code,
        warning=warning if isinstance(warning, str) else None,
        request_id=request_id if isinstance(request_id, str) else None,
        credits_used=(
            credits_used
            if isinstance(credits_used, int | float)
            and not isinstance(credits_used, bool)
            else None
        ),
    )
