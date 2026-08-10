from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.ai_provider import (
    AdminDevAIProviderManager,
    AIMessage,
    AIMessageRole,
    AIProviderError,
    AIProviderQuotaExceeded,
    AIProviderUnavailable,
    AIRequest,
    AIRequestError,
    AIResponse,
    GeminiProvider,
    GroqProvider,
    UserAIProviderManager,
    build_admin_dev_ai_provider_manager,
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


class _StaticProvider:
    """Fake AIProvider mínimo, sem passar pelo protocolo HTTP do Gemini/Groq."""

    def __init__(
        self, provider_id: str, model: str, *, response_text: str = "", error=None
    ) -> None:
        self.provider_id = provider_id
        self.model = model
        self._response_text = response_text
        self._error = error

    async def generate(self, request: AIRequest) -> AIResponse:
        if self._error is not None:
            raise self._error
        return AIResponse(
            request_id=request.request_id,
            provider=self.provider_id,
            model=self.model,
            content=self._response_text,
            finished_at=datetime.now(UTC),
        )


class _PoisonProvider:
    """Fake AIProvider que falha o teste se for chamado (tier que deve ser pulado)."""

    provider_id = "poison"
    model = "poison"

    async def generate(self, request: AIRequest) -> AIResponse:
        raise AssertionError("this provider tier should not have been called")


@pytest.mark.anyio
@pytest.mark.parametrize("profile", [UserRole.ADMIN, UserRole.DEV])
async def test_admin_dev_uses_gemini_flash_first(profile: UserRole) -> None:
    gemini, _ = _provider(_FakeModels(response_text="Resposta Flash"))
    manager = AdminDevAIProviderManager(gemini)

    response = await manager.generate(_request(profile))

    assert response.model == "gemini-3.6-flash"
    assert response.content == "Resposta Flash"


@pytest.mark.anyio
async def test_admin_dev_manager_rejects_user() -> None:
    gemini, _ = _provider(_FakeModels())
    manager = AdminDevAIProviderManager(gemini)

    with pytest.raises(AIRequestError, match="ADMIN/DEV"):
        await manager.generate(_request(UserRole.USER))


@pytest.mark.anyio
@pytest.mark.parametrize("error", [AIProviderQuotaExceeded(), AIProviderUnavailable()])
async def test_admin_dev_falls_back_to_groq_when_gemini_fails(
    error: AIProviderError,
) -> None:
    gemini, _ = _provider(_FakeModels(error=error))
    groq = _StaticProvider("groq", "llama-3.3-70b-versatile", response_text="Groq")
    manager = AdminDevAIProviderManager(gemini, groq=groq)

    response = await manager.generate(_request(UserRole.ADMIN))

    assert response.provider == "groq"
    assert response.content == "Groq"


@pytest.mark.anyio
async def test_admin_dev_raises_last_error_when_every_tier_fails() -> None:
    gemini, _ = _provider(_FakeModels(error=AIProviderQuotaExceeded()))
    groq = _StaticProvider("groq", "m", error=AIProviderUnavailable())
    manager = AdminDevAIProviderManager(gemini, groq=groq)

    with pytest.raises(AIProviderUnavailable):
        await manager.generate(_request(UserRole.ADMIN))


@pytest.mark.anyio
async def test_admin_dev_non_retryable_gemini_error_skips_remaining_tiers() -> None:
    gemini, _ = _provider(
        _FakeModels(error=AIProviderError("provider_request_rejected", retryable=False))
    )
    manager = AdminDevAIProviderManager(gemini, groq=_PoisonProvider())

    with pytest.raises(AIProviderError, match="provider_request_rejected"):
        await manager.generate(_request(UserRole.ADMIN))


@pytest.mark.anyio
async def test_admin_dev_no_groq_configured_raises_gemini_error() -> None:
    gemini, _ = _provider(_FakeModels(error=AIProviderUnavailable()))
    manager = AdminDevAIProviderManager(gemini)

    with pytest.raises(AIProviderUnavailable):
        await manager.generate(_request(UserRole.ADMIN))


def test_admin_dev_factory_wires_groq_only_when_key_configured() -> None:
    without_groq = build_admin_dev_ai_provider_manager(
        Settings(
            _env_file=None,
            gemini_api_key_admin_dev="configured-key",
            gemini_model="gemini-3.6-flash",
        )
    )
    assert without_groq._groq is None  # noqa: SLF001

    with_groq = build_admin_dev_ai_provider_manager(
        Settings(
            _env_file=None,
            gemini_api_key_admin_dev="configured-key",
            groq_api_key="configured-groq-key",
            groq_model="llama-3.3-70b-versatile",
        )
    )
    assert isinstance(with_groq._groq, GroqProvider)  # noqa: SLF001
    assert with_groq._groq.model == "llama-3.3-70b-versatile"  # noqa: SLF001


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
    with pytest.raises(AIRequestError, match="AISHOPPING_GEMINI_API_KEY_USER"):
        build_user_ai_provider_manager(Settings(_env_file=None))

    manager = build_user_ai_provider_manager(
        Settings(
            _env_file=None,
            gemini_api_key_user="configured-key",
            gemini_model="gemini-3.6-flash",
        )
    )
    assert isinstance(manager, UserAIProviderManager)


def test_admin_dev_factory_requires_key_and_configures_flash_model() -> None:
    with pytest.raises(AIRequestError, match="AISHOPPING_GEMINI_API_KEY_ADMIN_DEV"):
        build_admin_dev_ai_provider_manager(Settings(_env_file=None))

    manager = build_admin_dev_ai_provider_manager(
        Settings(
            _env_file=None,
            gemini_api_key_admin_dev="configured-key",
            gemini_model="gemini-3.6-flash",
        )
    )
    assert isinstance(manager, AdminDevAIProviderManager)
    assert manager._gemini.model == "gemini-3.6-flash"  # noqa: SLF001


@pytest.mark.anyio
async def test_user_manager_records_telemetry_and_raises_on_time_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regressão: `validate_provider_response` precisa ficar dentro do
    try/except, senão uma resposta com `finished_at` anterior ao
    `requested_at` (relógio local sem sincronia NTP, por exemplo) escapa
    sem telemetria nem log — só sobe crua para quem chamou.
    """
    recorded: list[str] = []
    monkeypatch.setattr(
        "app.ai_provider.manager.record_ai_attempt",
        lambda *args, **kwargs: recorded.append(kwargs.get("outcome")),
    )

    provider, _ = _provider(_FakeModels())
    manager = UserAIProviderManager(provider)
    request = AIRequest(
        uuid4(),
        UserRole.USER,
        "user_assistance",
        (AIMessage(AIMessageRole.USER, "Olá"),),
        datetime.now(UTC) + timedelta(seconds=5),
    )

    with pytest.raises(AIProviderError, match="provider_time_mismatch"):
        await manager.generate(request)

    assert recorded == ["failed"]


@pytest.mark.anyio
async def test_admin_dev_records_telemetry_and_raises_on_time_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[str] = []
    monkeypatch.setattr(
        "app.ai_provider.manager.record_ai_attempt",
        lambda *args, **kwargs: recorded.append(kwargs.get("outcome")),
    )

    gemini, _ = _provider(_FakeModels(response_text="Resposta Flash"))
    manager = AdminDevAIProviderManager(gemini, groq=_PoisonProvider())
    request = AIRequest(
        uuid4(),
        UserRole.ADMIN,
        "user_assistance",
        (AIMessage(AIMessageRole.USER, "Olá"),),
        datetime.now(UTC) + timedelta(seconds=5),
    )

    with pytest.raises(AIProviderError, match="provider_time_mismatch"):
        await manager.generate(request)

    assert recorded == ["failed"]
