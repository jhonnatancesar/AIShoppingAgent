"""Adapter de IA do César Core; mensagens tipadas, sem domínio nem cascata."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.ai_provider.contracts import (
    AIProviderCapabilityUnsupported,
    AIProviderError,
    AIProviderUnavailable,
    AIRequest,
    AIResponse,
)
from app.core.urls import normalize_loopback_http_endpoint


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
        service_class: str = "economy",
        max_tokens: int = 1024,
        timeout_seconds: float = 90,
        client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
    ) -> None:
        endpoint = normalize_loopback_http_endpoint(base_url)
        if endpoint is None:
            raise ValueError("Cesar Core DEV endpoint must be HTTP loopback")
        if not service.strip() or service_class not in {
            "economy",
            "standard",
            "quality",
        }:
            raise ValueError("Invalid Cesar Core service configuration")
        if max_tokens < 1 or timeout_seconds <= 0:
            raise ValueError("Invalid Cesar Core limits")
        self._key_file = api_key_file
        self._endpoint = endpoint + "/v1/ai/generate"
        self._service = service
        self._service_class = service_class
        self._max_tokens = max_tokens
        self._timeout = timeout_seconds
        self._client_factory = client_factory

    async def generate(self, request: AIRequest) -> AIResponse:
        if request.require_search_grounding:
            raise AIProviderCapabilityUnsupported("search_grounding")
        try:
            token = self._key_file.read_text(encoding="utf-8").strip()
        except OSError, UnicodeError:
            raise AIProviderError(
                "cesar_core_credential_unavailable", retryable=False
            ) from None
        if not token:
            raise AIProviderError("cesar_core_credential_unavailable", retryable=False)
        payload = {
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
            ):
                raise ValueError("Invalid normalized response")
            return AIResponse(
                request_id=request.request_id,
                provider=self.provider_id,
                model=body["model"],
                content=body["content"],
                finished_at=datetime.now(UTC),
            )
        except KeyError, TypeError, ValueError:
            raise AIProviderError(
                "cesar_core_invalid_response", retryable=False
            ) from None
