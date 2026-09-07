"""Cliente Search do Core, sem conhecimento de OmniRoute ou credenciais upstream."""

from pathlib import Path

import httpx

from app.core.urls import normalize_cesar_core_http_endpoint
from app.search.contracts import WebSearchError, WebSearchResponse, WebSearchResult


class CesarCoreSearchProvider:
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
        endpoint = normalize_cesar_core_http_endpoint(base_url)
        if endpoint is None:
            raise ValueError(
                "Cesar Core endpoint must be HTTP loopback or host.docker.internal"
            )
        self._endpoint = endpoint + "/v1/search"
        self._key_file = api_key_file
        self._service = service
        self._class = service_class
        self._timeout = timeout_seconds
        self._client_factory = client_factory

    async def search(
        self, query: str, *, limit: int, correlation_id: str
    ) -> WebSearchResponse:
        try:
            key = self._key_file.read_text(encoding="utf-8").strip()
        except OSError, UnicodeError:
            raise WebSearchError("core_search_credential_unavailable") from None
        if not key:
            raise WebSearchError("core_search_credential_unavailable")
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
                        "query": query,
                        "max_results": limit,
                        "requirements": {
                            "service_class": self._class,
                            "cost_policy": "free_only",
                        },
                    },
                )
        except httpx.RequestError:
            raise WebSearchError(
                "core_search_network_unavailable", retryable=True
            ) from None
        if response.status_code != 200:
            # 502 inclui auth/input/contrato upstream: não é retriável por padrão.
            retryable = response.status_code in {500, 503, 504}
            try:
                code = response.json()["error"]["code"]
            except ValueError, KeyError, TypeError:
                code = None
            if code in {
                "search_policy_denied",
                "quota_exceeded",
                "capability_denied",
                "authentication_required",
                "invalid_credentials",
                "search_upstream_error",
                # ADR 0018 (César Core): falha da própria proteção de quota
                # (Redis indisponível/config incompatível) nunca pode ser
                # contornada silenciosamente pelo fallback Firecrawl -- isso
                # mascararia um problema operacional real do gate de quota.
                "quota_store_unavailable",
                "quota_store_misconfigured",
            }:
                retryable = False
            raise WebSearchError(
                "core_search_unavailable" if retryable else "core_search_rejected",
                retryable=retryable,
            )
        try:
            body = response.json()
            if (
                body["correlation_id"] != correlation_id
                or body["provider_gateway"] != "omniroute"
                or not isinstance(body["provider"], str)
                or not body["provider"]
                or body["provider"] == "context7"
                or not isinstance(body["request_id"], str)
                or not body["request_id"]
                or not isinstance(body["cached"], bool)
                or not isinstance(body["results"], list)
            ):
                raise ValueError("Invalid envelope")
            results = []
            for item in body["results"]:
                if (
                    not all(
                        isinstance(item[k], str) for k in ("title", "url", "snippet")
                    )
                    or not item["title"].strip()
                    or not item["url"].strip()
                    or (
                        item.get("position") is not None
                        and (type(item["position"]) is not int or item["position"] < 1)
                    )
                ):
                    raise ValueError("Invalid result")
                results.append(
                    WebSearchResult(
                        item["title"],
                        item["url"],
                        item["snippet"],
                        item.get("position"),
                    )
                )
            queries = body["usage"]["queries_used"]
            if type(queries) is not int or queries < 0:
                raise ValueError("Invalid usage")
            return WebSearchResponse(
                tuple(results[:limit]),
                "cesar_core",
                body["provider"],
                correlation_id,
                body["request_id"],
                body.get("upstream_request_id"),
                body["cached"],
                queries,
            )
        except ValueError, KeyError, TypeError:
            raise WebSearchError("core_search_invalid_response") from None
