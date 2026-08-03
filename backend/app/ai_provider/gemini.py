"""Adaptador Gemini usado exclusivamente pelo perfil USER."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from google import genai
from google.genai import errors, types
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


class GeminiProvider:
    provider_id = "gemini"

    def __init__(
        self,
        api_key: SecretStr,
        model: str = "gemini-3.6-flash",
        *,
        client_factory: Callable[..., genai.Client] = genai.Client,
    ) -> None:
        if not api_key.get_secret_value().strip():
            raise AIRequestError("Gemini API key is required")
        if not model.strip():
            raise AIRequestError("Gemini model is required")
        self._api_key = api_key
        self.model = model
        self._client_factory = client_factory

    async def generate(self, request: AIRequest) -> AIResponse:
        contents, system_instruction = _translate_messages(request)
        config = (
            types.GenerateContentConfig(system_instruction=system_instruction)
            if system_instruction
            else None
        )
        client = None
        try:
            client = self._client_factory(api_key=self._api_key.get_secret_value())
            async with client.aio as async_client:
                result = await async_client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=config,
                )
            content = result.text
        except errors.APIError as error:
            raise _translate_api_error(error) from None
        except TimeoutError:
            raise AIProviderUnavailable("provider_timeout") from None
        finally:
            if client is not None:
                client.close()

        if not isinstance(content, str) or not content.strip():
            raise AIProviderError("provider_empty_response", retryable=False)
        return AIResponse(
            request_id=request.request_id,
            provider=self.provider_id,
            model=self.model,
            content=content,
            finished_at=datetime.now(UTC),
        )


def _translate_messages(
    request: AIRequest,
) -> tuple[list[types.Content], str | None]:
    system_parts: list[str] = []
    contents: list[types.Content] = []
    for message in request.messages:
        if message.role is AIMessageRole.SYSTEM:
            system_parts.append(message.content)
            continue
        role = "model" if message.role is AIMessageRole.ASSISTANT else "user"
        contents.append(
            types.Content(
                role=role,
                parts=[types.Part.from_text(text=message.content)],
            )
        )
    if not contents:
        raise AIRequestError("Gemini requires at least one non-system message")
    if contents[-1].role == "model":
        raise AIRequestError("Gemini request must end with a user message")
    return contents, "\n\n".join(system_parts) or None


def _translate_api_error(error: errors.APIError) -> AIProviderError:
    if error.code == 429:
        retry_delay = _extract_retry_delay(error.details)
        quota_reset_at = (
            datetime.now(UTC) + retry_delay if retry_delay is not None else None
        )
        return AIProviderQuotaExceeded(quota_reset_at=quota_reset_at)
    if error.code in {408, 500, 502, 503, 504}:
        return AIProviderUnavailable()
    if error.code in {401, 403}:
        return AIProviderError("provider_authentication_failed", retryable=False)
    if error.code == 400:
        return AIProviderError("provider_request_rejected", retryable=False)
    return AIProviderError("provider_error", retryable=False)


def _extract_retry_delay(value: Any) -> timedelta | None:
    """Extrai apenas google.rpc.RetryInfo, descartando todo o restante do erro."""
    if isinstance(value, dict):
        if value.get("@type") == "type.googleapis.com/google.rpc.RetryInfo":
            return _parse_duration(value.get("retryDelay"))
        for nested in value.values():
            delay = _extract_retry_delay(nested)
            if delay is not None:
                return delay
    elif isinstance(value, list):
        for nested in value:
            delay = _extract_retry_delay(nested)
            if delay is not None:
                return delay
    return None


def _parse_duration(value: Any) -> timedelta | None:
    if not isinstance(value, str) or not value.endswith("s"):
        return None
    try:
        seconds = Decimal(value[:-1])
    except InvalidOperation:
        return None
    if not seconds.is_finite() or seconds < 0:
        return None
    return timedelta(seconds=float(seconds))
