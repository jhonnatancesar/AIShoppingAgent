from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.ai_provider import (
    AIMessage,
    AIMessageRole,
    AIProviderError,
    AIProviderQuotaExceeded,
    AIProviderUnavailable,
    AIRequest,
    AIRequestError,
    GeminiProvider,
    UserAIProviderManager,
    build_user_ai_provider_manager,
)
from app.core.config import Settings
from app.users.models import UserRole
from google.genai import errors
from pydantic import SecretStr


def _request(
    profile: UserRole = UserRole.USER,
    messages: tuple[AIMessage, ...] | None = None,
) -> AIRequest:
    return AIRequest(
        uuid4(),
        profile,
        "user_assistance",
        messages or (AIMessage(AIMessageRole.USER, "Olá"),),
        datetime.now(UTC),
    )


class _FakeModels:
    def __init__(self, *, response_text: str = "Resposta Gemini", error=None) -> None:
        self.response_text = response_text
        self.error = error
        self.call = None

    async def generate_content(self, **kwargs):
        self.call = kwargs
        if self.error is not None:
            raise self.error
        return SimpleNamespace(text=self.response_text)


class _FakeAsyncClient:
    def __init__(self, models: _FakeModels) -> None:
        self.models = models
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.closed = True


class _FakeClient:
    def __init__(self, models: _FakeModels) -> None:
        self.aio = _FakeAsyncClient(models)
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _provider(models: _FakeModels) -> tuple[GeminiProvider, _FakeClient]:
    client = _FakeClient(models)
    provider = GeminiProvider(
        SecretStr("test-api-key"), client_factory=lambda **_kwargs: client
    )
    return provider, client


@pytest.mark.anyio
async def test_user_manager_calls_gemini_and_closes_async_client() -> None:
    models = _FakeModels()
    provider, client = _provider(models)
    manager = UserAIProviderManager(provider)
    request = _request(
        messages=(
            AIMessage(AIMessageRole.SYSTEM, "Seja direto"),
            AIMessage(AIMessageRole.USER, "Quero um notebook"),
        )
    )

    response = await manager.generate(request)

    assert response.provider == "gemini"
    assert response.model == "gemini-3.6-flash"
    assert response.content == "Resposta Gemini"
    assert client.aio.closed is True
    assert client.closed is True
    assert models.call["model"] == "gemini-3.6-flash"
    assert models.call["contents"][0].role == "user"
    assert models.call["config"].system_instruction == "Seja direto"


@pytest.mark.anyio
async def test_user_manager_rejects_admin_and_dev_profiles() -> None:
    provider, _ = _provider(_FakeModels())
    manager = UserAIProviderManager(provider)

    for profile in (UserRole.ADMIN, UserRole.DEV):
        with pytest.raises(AIRequestError, match="only USER"):
            await manager.generate(_request(profile))


@pytest.mark.anyio
async def test_gemini_rejects_system_only_or_assistant_final_turn() -> None:
    provider, _ = _provider(_FakeModels())
    with pytest.raises(AIRequestError, match="non-system"):
        await provider.generate(
            _request(messages=(AIMessage(AIMessageRole.SYSTEM, "system"),))
        )
    with pytest.raises(AIRequestError, match="end with a user"):
        await provider.generate(
            _request(messages=(AIMessage(AIMessageRole.ASSISTANT, "answer"),))
        )


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
async def test_gemini_translates_api_errors_without_leaking_details(
    status: int, exception_type: type[AIProviderError], code: str
) -> None:
    api_error = errors.APIError(status, {"message": "secret upstream detail"})
    provider, _ = _provider(_FakeModels(error=api_error))

    with pytest.raises(exception_type) as captured:
        await provider.generate(_request())

    assert captured.value.code == code
    assert "secret" not in str(captured.value)


@pytest.mark.anyio
async def test_gemini_rejects_empty_response() -> None:
    provider, _ = _provider(_FakeModels(response_text=""))

    with pytest.raises(AIProviderError, match="provider_empty_response"):
        await provider.generate(_request())


def test_gemini_requires_non_blank_secret_and_model() -> None:
    with pytest.raises(AIRequestError, match="API key"):
        GeminiProvider(SecretStr(""))
    with pytest.raises(AIRequestError, match="model"):
        GeminiProvider(SecretStr("key"), "")


def test_user_manager_factory_requires_key_and_uses_configured_model() -> None:
    with pytest.raises(AIRequestError, match="AISHOPPING_GEMINI_API_KEY"):
        build_user_ai_provider_manager(Settings())

    manager = build_user_ai_provider_manager(
        Settings(gemini_api_key="configured-key", gemini_model="gemini-3.6-flash")
    )
    assert isinstance(manager, UserAIProviderManager)
