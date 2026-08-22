"""Adaptador Gemini usado exclusivamente pelo perfil USER."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
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
        timeout_seconds: float = 10.0,
    ) -> None:
        if not api_key.get_secret_value().strip():
            raise AIRequestError("Gemini API key is required")
        if not model.strip():
            raise AIRequestError("Gemini model is required")
        self._api_key = api_key
        self.model = model
        self._client_factory = client_factory
        if timeout_seconds <= 0:
            raise AIRequestError("Gemini timeout must be positive")
        self._timeout_milliseconds = int(timeout_seconds * 1000)

    async def generate(self, request: AIRequest) -> AIResponse:
        contents, system_instruction = _translate_messages(request)
        config = _build_config(
            system_instruction=system_instruction,
            require_search_grounding=request.require_search_grounding,
        )
        client = None
        try:
            client = self._client_factory(
                api_key=self._api_key.get_secret_value(),
                http_options=types.HttpOptions(
                    timeout=self._timeout_milliseconds,
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
            async with client.aio as async_client:
                result = await async_client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=config,
                )
            content = result.text
        except errors.APIError as error:
            raise _translate_api_error(error) from None
        except httpx.TimeoutException:
            raise AIProviderUnavailable("provider_timeout") from None
        finally:
            if client is not None:
                client.close()

        if not isinstance(content, str) or not content.strip():
            raise AIProviderError("provider_empty_response", retryable=False)
        grounding_performed, grounding_sources = (
            _extract_grounding_evidence(result)
            if request.require_search_grounding
            else (False, ())
        )
        return AIResponse(
            request_id=request.request_id,
            provider=self.provider_id,
            model=self.model,
            content=content,
            finished_at=datetime.now(UTC),
            grounding_requested=request.require_search_grounding,
            grounding_performed=grounding_performed,
            grounding_sources=grounding_sources,
        )


def _build_config(
    *, system_instruction: str | None, require_search_grounding: bool
) -> types.GenerateContentConfig | None:
    """TASK-083: `tools=[Tool(google_search=...)]` só disponibiliza a busca
    ao modelo -- não força nem garante que ele pesquise (ver
    `_extract_grounding_evidence`, a única fonte de verdade sobre se a
    busca de fato aconteceu)."""
    if not system_instruction and not require_search_grounding:
        return None
    tools = (
        [types.Tool(google_search=types.GoogleSearch())]
        if require_search_grounding
        else None
    )
    return types.GenerateContentConfig(
        system_instruction=system_instruction, tools=tools
    )


def _extract_grounding_evidence(result: object) -> tuple[bool, tuple[str, ...]]:
    """Lê só o metadado estruturado devolvido pela API (`grounding_metadata`
    do primeiro candidato) -- nunca infere grounding a partir do texto da
    resposta. `performed=True` exige evidência real (consultas de busca
    e/ou trechos de fonte não vazios); tool disponibilizada sem nenhum dos
    dois presentes conta como não realizada."""
    candidates = getattr(result, "candidates", None) or []
    if not candidates:
        return False, ()
    metadata = getattr(candidates[0], "grounding_metadata", None)
    if metadata is None:
        return False, ()
    queries = getattr(metadata, "web_search_queries", None) or []
    chunks = getattr(metadata, "grounding_chunks", None) or []
    sources = tuple(
        uri
        for chunk in chunks
        if (web := getattr(chunk, "web", None)) is not None
        and isinstance(uri := getattr(web, "uri", None), str)
        and uri.strip()
    )
    performed = bool(queries) or bool(chunks)
    return performed, sources


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
