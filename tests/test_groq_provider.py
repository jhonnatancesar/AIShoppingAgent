from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from app.ai_provider import (
    AIMessage,
    AIMessageRole,
    AIProviderError,
    AIProviderQuotaExceeded,
    AIProviderUnavailable,
    AIRequest,
    AIRequestError,
    GroqProvider,
)
from app.users.models import UserRole
from pydantic import SecretStr


def _request(messages: tuple[AIMessage, ...] | None = None) -> AIRequest:
    return AIRequest(
        request_id=uuid4(),
        profile=UserRole.ADMIN,
        purpose="user_assistance",
        messages=messages or (AIMessage(AIMessageRole.USER, "Olá"),),
        requested_at=datetime.now(UTC),
    )


class _FakeResponse:
    def __init__(self, status_code: int, body: object = None, headers=None) -> None:
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def json(self) -> object:
        if self._body is None:
            raise ValueError("no body")
        return self._body


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse | None = None, error=None) -> None:
        self._response = response
        self._error = error
        self.captured_call: dict | None = None

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *_args) -> bool:
        return False

    async def post(self, url, *, headers, json):
        self.captured_call = {"url": url, "headers": headers, "json": json}
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


def _provider(
    response: _FakeResponse | None = None, error=None
) -> tuple[GroqProvider, _FakeAsyncClient]:
    client = _FakeAsyncClient(response, error)
    provider = GroqProvider(
        SecretStr("test-groq-key"), client_factory=lambda **_kwargs: client
    )
    return provider, client


def _body(content: str = "Resposta Groq") -> dict:
    return {"choices": [{"message": {"content": content}}]}


@pytest.mark.anyio
async def test_groq_generates_response_and_sends_authenticated_messages() -> None:
    provider, client = _provider(_FakeResponse(200, _body()))
    request = _request(
        messages=(
            AIMessage(AIMessageRole.SYSTEM, "Seja direto"),
            AIMessage(AIMessageRole.USER, "Quero um notebook"),
        )
    )

    response = await provider.generate(request)

    assert response.provider == "groq"
    assert response.model == "openai/gpt-oss-120b"
    assert response.content == "Resposta Groq"
    assert client.captured_call is not None
    assert client.captured_call["headers"]["Authorization"] == "Bearer test-groq-key"
    assert client.captured_call["json"]["messages"] == [
        {"role": "system", "content": "Seja direto"},
        {"role": "user", "content": "Quero um notebook"},
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "exception_type", "code"),
    [
        (429, AIProviderQuotaExceeded, "provider_quota_exceeded"),
        (503, AIProviderUnavailable, "provider_unavailable"),
        (401, AIProviderError, "provider_authentication_failed"),
        (400, AIProviderError, "provider_request_rejected"),
    ],
)
async def test_groq_translates_api_errors_without_leaking_details(
    status: int, exception_type: type[AIProviderError], code: str
) -> None:
    provider, _ = _provider(
        _FakeResponse(status, {"error": {"message": "secret upstream detail"}})
    )

    with pytest.raises(exception_type) as captured:
        await provider.generate(_request())

    assert captured.value.code == code
    assert "secret" not in str(captured.value)


@pytest.mark.anyio
async def test_groq_extracts_quota_reset_from_retry_after_header() -> None:
    provider, _ = _provider(_FakeResponse(429, headers={"retry-after": "12"}))

    with pytest.raises(AIProviderQuotaExceeded) as captured:
        await provider.generate(_request())

    assert captured.value.quota_reset_at is not None
    remaining = (captured.value.quota_reset_at - datetime.now(UTC)).total_seconds()
    assert 0 < remaining <= 12


@pytest.mark.anyio
async def test_groq_quota_without_retry_after_header_has_unknown_reset() -> None:
    provider, _ = _provider(_FakeResponse(429))

    with pytest.raises(AIProviderQuotaExceeded) as captured:
        await provider.generate(_request())

    assert captured.value.quota_reset_at is None


@pytest.mark.anyio
async def test_groq_rejects_empty_response() -> None:
    provider, _ = _provider(_FakeResponse(200, _body(content="")))

    with pytest.raises(AIProviderError, match="provider_empty_response"):
        await provider.generate(_request())


@pytest.mark.anyio
async def test_groq_translates_network_timeout_and_transport_errors() -> None:
    timeout_provider, _ = _provider(error=httpx.TimeoutException("timed out"))
    with pytest.raises(AIProviderUnavailable, match="provider_timeout"):
        await timeout_provider.generate(_request())

    transport_provider, _ = _provider(error=httpx.ConnectError("refused"))
    with pytest.raises(AIProviderUnavailable, match="provider_unavailable"):
        await transport_provider.generate(_request())


def test_groq_requires_non_blank_secret_and_model() -> None:
    with pytest.raises(AIRequestError, match="API key"):
        GroqProvider(SecretStr(""))
    with pytest.raises(AIRequestError, match="model"):
        GroqProvider(SecretStr("key"), "")
