"""Adapter de IA do César Core; mensagens tipadas, sem domínio nem cascata."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.ai_provider.contracts import (
    AIProviderError,
    AIProviderUnavailable,
    AIRequest,
    AIResponse,
)
from app.core.urls import normalize_cesar_core_http_endpoint


class CesarCoreConnectionUnavailable(AIProviderUnavailable):
    """Falha de conexão antes de obter uma resposta; elegível ao disaster opt-in."""


class CesarCoreAIProvider:
    provider_id = "cesar_core"
    model = "policy_selected"

    def __init__(
        self,
        *,
        api_key_file: Path,
        base_url: str,
        service: str,
        ai_profile: str,
        service_class: str = "economy",
        max_tokens: int = 1024,
        timeout_seconds: float = 90,
        client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
    ) -> None:
        endpoint = normalize_cesar_core_http_endpoint(base_url)
        if endpoint is None:
            raise ValueError(
                "Cesar Core endpoint must be HTTP loopback or host.docker.internal"
            )
        if not service.strip() or service_class not in {
            "economy",
            "standard",
            "quality",
        }:
            raise ValueError("Invalid Cesar Core service configuration")
        if ai_profile not in {"user", "admin_dev"}:
            raise ValueError("Invalid Cesar Core ai_profile")
        if max_tokens < 1 or timeout_seconds <= 0:
            raise ValueError("Invalid Cesar Core limits")
        self._key_file = api_key_file
        self._endpoint = endpoint + "/v1/ai/generate"
        self._service = service
        self._ai_profile = ai_profile
        self._service_class = service_class
        self._max_tokens = max_tokens
        self._timeout = timeout_seconds
        self._client_factory = client_factory

    async def generate(self, request: AIRequest) -> AIResponse:
        try:
            token = self._key_file.read_text(encoding="utf-8").strip()
        except OSError, UnicodeError:
            raise AIProviderError(
                "cesar_core_credential_unavailable", retryable=False
            ) from None
        if not token:
            raise AIProviderError("cesar_core_credential_unavailable", retryable=False)
        payload = {
            "ai_profile": self._ai_profile,
            "messages": [
                {"role": item.role.value, "content": item.content}
                for item in request.messages
            ],
            "requirements": {
                "service_class": self._service_class,
                "cost_policy": "free_only",
            },
            "max_tokens": self._max_tokens,
        }
        if request.require_search_grounding:
            payload["require_search_grounding"] = True
        try:
            async with self._client_factory(
                timeout=self._timeout, follow_redirects=False, trust_env=False
            ) as client:
                response = await client.post(
                    self._endpoint,
                    headers={
                        "Authorization": "Bearer " + token,
                        "X-Service": self._service,
                        "X-Purpose": request.purpose,
                        "X-Correlation-Id": str(request.request_id),
                    },
                    json=payload,
                )
        except httpx.ConnectError:
            raise CesarCoreConnectionUnavailable(
                "cesar_core_connection_unavailable"
            ) from None
        except httpx.RequestError:
            # Timeout/leitura podem ocorrer após consumo: não duplicar inferência.
            raise AIProviderError(
                "cesar_core_transport_uncertain", retryable=False
            ) from None
        if response.status_code != 200:
            # Inclui 429/502/503: uma resposta do gateway nunca aciona cascata local.
            raise AIProviderError(
                "cesar_core_request_failed", retryable=False
            ) from None
        try:
            body = response.json()
            completion = body["usage"]["completion_tokens"]
            grounding_requested = body.get("grounding_requested", False)
            grounding_performed = body.get("grounding_performed", False)
            grounding_sources = body.get("grounding_sources", [])
            if (
                body["correlation_id"] != str(request.request_id)
                or body["provider_gateway"] != "omniroute"
                or not isinstance(body["request_id"], str)
                or not body["request_id"]
                or not isinstance(body["model"], str)
                or not body["model"].strip()
                or not isinstance(body["content"], str)
                or not body["content"].strip()
                or type(completion) is not int
                or not 0 <= completion <= self._max_tokens
                or not isinstance(grounding_requested, bool)
                or not isinstance(grounding_performed, bool)
                or not isinstance(grounding_sources, list)
                or any(
                    not isinstance(source, str) or not source.strip()
                    for source in grounding_sources
                )
                or grounding_requested != request.require_search_grounding
                or (request.require_search_grounding and not grounding_performed)
                or (grounding_sources and not grounding_performed)
            ):
                raise ValueError("Invalid normalized response")
            return AIResponse(
                request_id=request.request_id,
                provider=self.provider_id,
                model=body["model"],
                content=body["content"],
                finished_at=datetime.now(UTC),
                grounding_requested=grounding_requested,
                grounding_performed=grounding_performed,
                grounding_sources=tuple(grounding_sources),
            )
        except KeyError, TypeError, ValueError:
            raise AIProviderError(
                "cesar_core_invalid_response", retryable=False
            ) from None
