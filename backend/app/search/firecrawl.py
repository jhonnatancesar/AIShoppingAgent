"""Cliente direto e contrato mínimo da Firecrawl Search API v2."""

from collections.abc import Callable
from dataclasses import dataclass

import httpx
from pydantic import SecretStr

from app.core.resilience import (
    CIRCUITS,
    CircuitOpenError,
    OperationSafety,
    RetryPolicy,
    retry_operation,
)

_SEARCH_ENDPOINT = "https://api.firecrawl.dev/v2/search"
_SCRAPE_ENDPOINT = "https://api.firecrawl.dev/v2/scrape"
_SEARCH_CIRCUIT_KEY = "firecrawl_search"
_SCRAPE_CIRCUIT_KEY = "firecrawl_scrape"
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


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


@dataclass(frozen=True, slots=True)
class FirecrawlScrapeResult:
    url: str
    title: str | None
    markdown: str | None
    credits_used: int | float | None = None


def _is_transient_error(error: Exception) -> bool:
    """429/5xx/timeout/indisponibilidade são do SERVIÇO Firecrawl, sempre
    retryable com backoff; 400/401/403/payload inválido são
    deterministicos, nunca retentados (TASK-113, correção pós-plano
    ponto 8 -- nunca confundir rate limit do serviço Firecrawl com uma
    origem específica recusando scraping, tratada à parte em
    `scrape_basic`/`parse_firecrawl_scrape_response`, que nunca levanta
    exceção para esse caso)."""
    if not isinstance(error, FirecrawlSearchError):
        return False
    if error.code in {"firecrawl_timeout", "firecrawl_unavailable"}:
        return True
    return error.status_code in _RETRYABLE_STATUS_CODES


class FirecrawlSearchProvider:
    def __init__(
        self,
        api_key: SecretStr,
        *,
        client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
        timeout_seconds: float = 30.0,
        retry_policy: RetryPolicy | None = None,
        circuit_failure_threshold: int = 5,
        circuit_open_seconds: float = 30.0,
    ) -> None:
        if not api_key.get_secret_value().strip():
            raise FirecrawlSearchError("firecrawl_api_key_required")
        if timeout_seconds <= 0:
            raise FirecrawlSearchError("firecrawl_timeout_invalid")
        self._api_key = api_key
        self._client_factory = client_factory
        self._timeout_seconds = timeout_seconds
        self._retry_policy = retry_policy or RetryPolicy()
        self._circuit_failure_threshold = circuit_failure_threshold
        self._circuit_open_seconds = circuit_open_seconds

    async def _request(self, endpoint: str, payload: dict) -> httpx.Response:
        try:
            async with self._client_factory(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    endpoint,
                    headers={
                        "Authorization": (f"Bearer {self._api_key.get_secret_value()}"),
                        "Content-Type": "application/json",
                    },
                    json=payload,
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
        return response

    async def _call_with_resilience(
        self, *, circuit_key: str, endpoint: str, payload: dict
    ) -> httpx.Response:
        circuit = CIRCUITS.get(
            circuit_key,
            failure_threshold=self._circuit_failure_threshold,
            open_seconds=self._circuit_open_seconds,
        )
        try:
            circuit.before_call()
        except CircuitOpenError:
            raise FirecrawlSearchError("firecrawl_circuit_open") from None
        try:
            response = await retry_operation(
                lambda: self._request(endpoint, payload),
                safety=OperationSafety.SAFE,
                policy=self._retry_policy,
                is_transient=_is_transient_error,
            )
        except Exception as error:
            circuit.record_failure(
                transient=_is_transient_error(error)
                if isinstance(error, FirecrawlSearchError)
                else True
            )
            raise
        circuit.record_success()
        return response

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
        response = await self._call_with_resilience(
            circuit_key=_SEARCH_CIRCUIT_KEY,
            endpoint=_SEARCH_ENDPOINT,
            payload={"query": query, "sources": list(sources), "limit": limit},
        )
        try:
            body = response.json()
        except ValueError:
            raise FirecrawlSearchError(
                "firecrawl_response_invalid", status_code=response.status_code
            ) from None
        return parse_firecrawl_search_response(body, status_code=response.status_code)

    async def scrape_basic(self, url: str) -> FirecrawlScrapeResult | None:
        """`/v2/scrape` básico -- sem stealth/proxy/parâmetro de evasão
        (TASK-113 §33.18 item 6, fallback só quando a busca sozinha não
        atinge o mínimo de evidência, no máximo 3 URLs por chamador).

        `None` quando a própria Firecrawl reporta que não conseguiu ler
        a página (`success: false` -- bloqueio/paywall/404 da ORIGEM
        específica) -- nunca uma exceção nesse caso: é resultado honesto
        de "esta fonte não deu evidência", nunca falha do serviço
        Firecrawl (que continua contando para o circuit breaker só
        quando é a própria API da Firecrawl que falha -- ver
        `_call_with_resilience`)."""
        if not url.strip():
            raise FirecrawlSearchError("firecrawl_query_required")
        response = await self._call_with_resilience(
            circuit_key=_SCRAPE_CIRCUIT_KEY,
            endpoint=_SCRAPE_ENDPOINT,
            payload={"url": url, "formats": ["markdown"]},
        )
        try:
            body = response.json()
        except ValueError:
            raise FirecrawlSearchError(
                "firecrawl_response_invalid", status_code=response.status_code
            ) from None
        return parse_firecrawl_scrape_response(body)


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


def parse_firecrawl_scrape_response(body: object) -> FirecrawlScrapeResult | None:
    """`None` (nunca exceção) quando a própria Firecrawl reporta
    `success: false`, OU quando reporta sucesso na chamada mas a ORIGEM
    específica recusou/falhou (`data.metadata.statusCode` fora de
    2xx/3xx, `data.metadata.error` presente) -- confirmado contra a
    documentação oficial (`docs.firecrawl.dev`, auditoria 2026-08-27):
    a API pode devolver `success: true` no envelope mesmo quando a
    página de origem específica respondeu com erro; só `metadata`
    revela isso. Nunca conta para o circuit breaker do SERVIÇO
    (`scrape_basic`/`_call_with_resilience` só vê a chamada HTTP em si,
    que teve sucesso) -- ver docstring de `scrape_basic`."""
    if not isinstance(body, dict) or body.get("success") is not True:
        return None
    data = body.get("data")
    if not isinstance(data, dict):
        return None
    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        status_code = metadata.get("statusCode")
        if isinstance(status_code, int) and not (200 <= status_code < 400):
            return None
        if metadata.get("error"):
            return None
    markdown = data.get("markdown")
    title = metadata.get("title") if isinstance(metadata, dict) else None
    # `data.metadata.url` (URL final, pós-redirecionamento) é a fonte
    # correta -- confirmado contra a documentação oficial: a resposta
    # NUNCA tem um `data.url` de nível superior (achado da auditoria,
    # a versão anterior tentava lê-lo e sempre caía no fallback).
    url = (
        metadata.get("url") or metadata.get("sourceURL")
        if isinstance(metadata, dict)
        else None
    )
    return FirecrawlScrapeResult(
        url=str(url) if isinstance(url, str) and url.strip() else "",
        title=title.strip() if isinstance(title, str) and title.strip() else None,
        markdown=markdown.strip() if isinstance(markdown, str) and markdown.strip() else None,
        credits_used=(metadata.get("creditsUsed") if isinstance(metadata, dict)
                      and type(metadata.get("creditsUsed")) in (int, float)
                      and metadata["creditsUsed"] >= 0 else None),
    )
