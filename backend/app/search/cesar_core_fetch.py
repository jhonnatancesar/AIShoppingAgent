"""Cliente de enriquecimento de URL do César Core, sem conhecimento de
OmniRoute, Firecrawl ou qualquer provider upstream concreto.

Substitui `FirecrawlScrapeProvider` como dependência do Market Research e do
worker nativo: o GG Oferta não guarda mais credencial, endpoint nem provider
de enriquecimento -- reusa a MESMA credencial de aplicação já usada por AI e
Search (`settings.cesar_core_api_key_file`), porque a distinção de capability
é feita pelo Core (X-Service/X-Purpose), nunca por uma credencial separada
por capability no lado do GG.
"""

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import httpx

from app.core.urls import normalize_loopback_http_endpoint


class CesarCoreFetchError(RuntimeError):
    """Erro sanitizado do enriquecimento, sem resposta bruta ou credencial."""

    def __init__(self, code: str, *, status_code: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class CesarCoreFetchResult:
    url: str
    title: str | None
    markdown: str | None


class CesarCoreFetchProvider:
    """Enriquecimento de UMA URL já conhecida, via César Core (`/v1/fetch`).

    Mesma semântica que `FirecrawlScrapeProvider.scrape_basic` já tinha:
    `None` (nunca exceção) quando a origem específica não devolveu conteúdo
    aproveitável -- resultado honesto de "esta fonte não deu evidência".
    """

    def __init__(
        self,
        *,
        api_key_file: Path,
        base_url: str,
        service: str,
        service_class: str = "economy",
        timeout_seconds: float = 30,
        client_factory=httpx.AsyncClient,
    ) -> None:
        endpoint = normalize_loopback_http_endpoint(base_url)
        if endpoint is None:
            raise ValueError("Cesar Core DEV endpoint must be HTTP loopback")
        self._endpoint = endpoint + "/v1/fetch"
        self._key_file = api_key_file
        self._service = service
        self._class = service_class
        self._timeout = timeout_seconds
        self._client_factory = client_factory

    async def scrape_basic(self, url: str) -> CesarCoreFetchResult | None:
        if not url.strip():
            raise CesarCoreFetchError("core_fetch_query_required")
        try:
            key = self._key_file.read_text(encoding="utf-8").strip()
        except OSError, UnicodeError:
            raise CesarCoreFetchError("core_fetch_credential_unavailable") from None
        if not key:
            raise CesarCoreFetchError("core_fetch_credential_unavailable")
        correlation_id = str(uuid4())
        try:
            async with self._client_factory(
                timeout=self._timeout, trust_env=False, follow_redirects=False
            ) as client:
                response = await client.post(
                    self._endpoint,
                    headers={
                        "Authorization": "Bearer " + key,
                        "X-Service": self._service,
                        "X-Purpose": "market_research",
                        "X-Correlation-Id": correlation_id,
                    },
                    json={
                        "url": url,
                        "requirements": {
                            "service_class": self._class,
                            "cost_policy": "free_only",
                        },
                    },
                )
        except httpx.TimeoutException:
            raise CesarCoreFetchError("core_fetch_timeout") from None
        except httpx.RequestError:
            raise CesarCoreFetchError("core_fetch_network_unavailable") from None
        if response.status_code != 200:
            code = (
                "core_fetch_authentication_failed"
                if response.status_code in {401, 403}
                else "core_fetch_rate_limited"
                if response.status_code == 429
                else "core_fetch_request_failed"
            )
            raise CesarCoreFetchError(code, status_code=response.status_code)
        try:
            body = response.json()
            if (
                not isinstance(body, dict)
                or body.get("correlation_id") != correlation_id
                or body.get("provider_gateway") != "omniroute"
                or not isinstance(body.get("provider"), str)
                or not body["provider"]
                or not isinstance(body.get("fetched"), bool)
            ):
                raise ValueError("Invalid envelope")
        except ValueError:
            raise CesarCoreFetchError(
                "core_fetch_invalid_response", status_code=response.status_code
            ) from None
        if not body["fetched"]:
            return None
        content = body.get("content")
        if not isinstance(content, str) or not content.strip():
            return None
        title = body.get("title")
        return CesarCoreFetchResult(
            url=body.get("url") if isinstance(body.get("url"), str) else url,
            title=title if isinstance(title, str) and title.strip() else None,
            markdown=content,
        )
