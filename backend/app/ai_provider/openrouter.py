"""Adaptador OpenRouter usado pelas rotas gratuita USER e paga DEV."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import SecretStr

from app.ai_provider.contracts import (
    AIMessageRole,
    AIProviderError,
    AIProviderQuotaExceeded,
    AIProviderUnavailable,
    AIRequest,
    AIRequestError,
    AIResponse,
)

_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
_ROLE_MAP = {
    AIMessageRole.SYSTEM: "system",
    AIMessageRole.USER: "user",
    AIMessageRole.ASSISTANT: "assistant",
}


class OpenRouterProvider:
    provider_id = "openrouter"

    def __init__(
        self,
        api_key: SecretStr,
        model: str,
        *,
        web_search_enabled: bool = False,
        web_search_engine: str = "firecrawl",
        client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not api_key.get_secret_value().strip():
            raise AIRequestError("OpenRouter API key is required")
        if not model.strip():
            raise AIRequestError("OpenRouter model is required")
        if timeout_seconds <= 0:
            raise AIRequestError("OpenRouter timeout must be positive")
        self._api_key = api_key
        self.model = model
        self._web_search_enabled = web_search_enabled
        self._web_search_engine = web_search_engine
        self._client_factory = client_factory
        self._timeout_seconds = timeout_seconds

    async def generate(self, request: AIRequest) -> AIResponse:
        if request.require_search_grounding and not self._web_search_enabled:
            from app.ai_provider.contracts import AIProviderCapabilityUnsupported

            raise AIProviderCapabilityUnsupported("search_grounding")

        payload: dict[str, object] = {
            "model": self.model,
            "messages": [
                {"role": _ROLE_MAP[message.role], "content": message.content}
                for message in request.messages
            ],
        }
        if request.require_search_grounding:
            payload["tools"] = [
                {
                    "type": "openrouter:web_search",
                    "parameters": {"engine": self._web_search_engine},
                }
            ]

        try:
            async with self._client_factory(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    _ENDPOINT,
                    headers={
                        "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                        "content-type": "application/json",
                    },
                    json=payload,
                )
        except httpx.TimeoutException:
            raise AIProviderUnavailable("provider_timeout") from None
        except httpx.HTTPError:
            raise AIProviderUnavailable("provider_unavailable") from None

        if response.status_code != 200:
            raise _translate_api_error(response)
        body = _response_body(response)
        content = _extract_content(body)
        if content is None:
            raise AIProviderError("provider_empty_response", retryable=False)
        performed, sources = _extract_web_search_evidence(body)
        return AIResponse(
            request_id=request.request_id,
            provider=self.provider_id,
            model=self.model,
            content=content,
            finished_at=datetime.now(UTC),
            grounding_requested=request.require_search_grounding,
            grounding_performed=performed,
            grounding_sources=sources,
        )


def _response_body(response: httpx.Response) -> dict[str, object]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _extract_content(body: dict[str, object]) -> str | None:
    try:
        choices = body["choices"]
        message = choices[0]["message"]  # type: ignore[index]
        content = message["content"]
    except KeyError, IndexError, TypeError:
        return None
    return content if isinstance(content, str) and content.strip() else None


def _extract_web_search_evidence(
    body: dict[str, object],
) -> tuple[bool, tuple[str, ...]]:
    annotations: object = None
    try:
        annotations = body["choices"][0]["message"].get("annotations")  # type: ignore[index,union-attr]
    except KeyError, IndexError, TypeError, AttributeError:
        pass
    sources: list[str] = []
    if isinstance(annotations, list):
        for annotation in annotations:
            if not isinstance(annotation, dict):
                continue
            citation = annotation.get("url_citation")
            if isinstance(citation, dict):
                url = citation.get("url")
                if isinstance(url, str) and url.strip() and url not in sources:
                    sources.append(url)
    usage = body.get("usage")
    server_tools = usage.get("server_tool_use") if isinstance(usage, dict) else None
    searches = (
        server_tools.get("web_search_requests")
        if isinstance(server_tools, dict)
        else None
    )
    performed = bool(sources) or isinstance(searches, int) and searches > 0
    return performed, tuple(sources)


def _translate_api_error(response: httpx.Response) -> AIProviderError:
    if response.status_code == 429:
        return AIProviderQuotaExceeded(quota_reset_at=_extract_reset_at(response))
    if response.status_code in {408, 500, 502, 503, 504}:
        return AIProviderUnavailable()
    if response.status_code in {401, 403}:
        return AIProviderError("provider_authentication_failed", retryable=False)
    if response.status_code == 400:
        return AIProviderError("provider_request_rejected", retryable=False)
    return AIProviderError("provider_error", retryable=False)


def _extract_reset_at(response: httpx.Response) -> datetime | None:
    retry_after = response.headers.get("retry-after")
    if retry_after is None:
        return None
    try:
        seconds = float(retry_after)
    except ValueError:
        return None
    if seconds < 0:
        return None
    return datetime.now(UTC) + timedelta(seconds=seconds)
