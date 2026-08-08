"""Adaptador Groq usado como fallback opcional do perfil ADMIN/DEV."""

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

_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
_ROLE_MAP = {
    AIMessageRole.SYSTEM: "system",
    AIMessageRole.USER: "user",
    AIMessageRole.ASSISTANT: "assistant",
}


class GroqProvider:
    provider_id = "groq"

    def __init__(
        self,
        api_key: SecretStr,
        model: str = "llama-3.3-70b-versatile",
        *,
        client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
    ) -> None:
        if not api_key.get_secret_value().strip():
            raise AIRequestError("Groq API key is required")
        if not model.strip():
            raise AIRequestError("Groq model is required")
        self._api_key = api_key
        self.model = model
        self._client_factory = client_factory

    async def generate(self, request: AIRequest) -> AIResponse:
        payload = {
            "model": self.model,
            "messages": [
                {"role": _ROLE_MAP[message.role], "content": message.content}
                for message in request.messages
            ],
        }
        try:
            async with self._client_factory(timeout=30.0) as client:
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

        content = _extract_content(response)
        if not content:
            raise AIProviderError("provider_empty_response", retryable=False)
        return AIResponse(
            request_id=request.request_id,
            provider=self.provider_id,
            model=self.model,
            content=content,
            finished_at=datetime.now(UTC),
        )


def _extract_content(response: httpx.Response) -> str | None:
    try:
        body = response.json()
        content = body["choices"][0]["message"]["content"]
    except ValueError, KeyError, IndexError, TypeError:
        return None
    return content if isinstance(content, str) and content.strip() else None


def _translate_api_error(response: httpx.Response) -> AIProviderError:
    status = response.status_code
    if status == 429:
        return AIProviderQuotaExceeded(quota_reset_at=_extract_reset_at(response))
    if status in {408, 500, 502, 503, 504}:
        return AIProviderUnavailable()
    if status in {401, 403}:
        return AIProviderError("provider_authentication_failed", retryable=False)
    if status == 400:
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
