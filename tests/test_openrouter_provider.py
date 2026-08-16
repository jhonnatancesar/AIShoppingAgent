"""Roteamento OpenRouter sem tráfego externo (DEC-061)."""

import logging
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from app.ai_provider import (
    AdminDevAIProviderManager,
    AIMessage,
    AIMessageRole,
    AIProviderError,
    AIProviderQuotaExceeded,
    AIProviderUnavailable,
    AIRequest,
    AIResponse,
    OpenRouterProvider,
    UserAIProviderManager,
    build_admin_dev_ai_provider_manager,
    build_user_ai_provider_manager,
)
from app.core.config import Settings
from app.core.resilience import CircuitRegistry
from app.users.models import UserRole
from pydantic import SecretStr


@pytest.fixture(autouse=True)
def _isolated_ai_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.ai_provider.manager.CIRCUITS", CircuitRegistry())


def _request(
    profile: UserRole, *, grounding: bool = False, content: str = "Olá"
) -> AIRequest:
    return AIRequest(
        request_id=uuid4(),
        profile=profile,
        purpose="user_assistance",
        messages=(AIMessage(AIMessageRole.USER, content),),
        requested_at=datetime.now(UTC),
        require_search_grounding=grounding,
    )


class _Provider:
    def __init__(self, provider_id: str, model: str, result: object) -> None:
        self.provider_id = provider_id
        self.model = model
        self.result = result
        self.calls = 0

    async def generate(self, request: AIRequest) -> AIResponse:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return AIResponse(
            request_id=request.request_id,
            provider=self.provider_id,
            model=self.model,
            content=str(self.result),
            finished_at=datetime.now(UTC),
        )


class _Response:
    def __init__(
        self,
        status_code: int = 200,
        body: object | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.headers = headers or {}

    def json(self) -> object:
        return self._body


class _Client:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, _url: str, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _openrouter(
    response: object, *, model: str = "openrouter/free"
) -> tuple[OpenRouterProvider, _Client]:
    client = _Client(response)
    return (
        OpenRouterProvider(
            SecretStr("secret-canary"),
            model,
            client_factory=lambda **_kwargs: client,
        ),
        client,
    )


@pytest.mark.anyio
async def test_user_uses_gemini_without_calling_fallbacks() -> None:
    gemini = _Provider("gemini", "gemini-3.6-flash", "ok")
    groq = _Provider("groq", "openai/gpt-oss-120b", AssertionError())
    free = _Provider("openrouter", "openrouter/free", AssertionError())
    response = await UserAIProviderManager(gemini, groq=groq, openrouter=free).generate(
        _request(UserRole.USER)
    )
    assert response.provider == "gemini"
    assert (gemini.calls, groq.calls, free.calls) == (1, 0, 0)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "gemini_error",
    [AIProviderUnavailable("provider_timeout"), AIProviderUnavailable()],
)
async def test_user_falls_back_from_gemini_timeout_or_504_to_groq(
    gemini_error: AIProviderUnavailable,
) -> None:
    gemini = _Provider("gemini", "gemini-3.6-flash", gemini_error)
    groq = _Provider("groq", "openai/gpt-oss-120b", "ok")
    response = await UserAIProviderManager(gemini, groq=groq).generate(
        _request(UserRole.USER)
    )
    assert (response.provider, response.model) == ("groq", "openai/gpt-oss-120b")


@pytest.mark.anyio
async def test_user_uses_openrouter_free_after_two_eligible_failures() -> None:
    gemini = _Provider("gemini", "gemini-3.6-flash", AIProviderUnavailable())
    groq = _Provider("groq", "openai/gpt-oss-120b", AIProviderQuotaExceeded())
    free = _Provider("openrouter", "openrouter/free", "ok")
    response = await UserAIProviderManager(gemini, groq=groq, openrouter=free).generate(
        _request(UserRole.USER)
    )
    assert (response.provider, response.model) == ("openrouter", "openrouter/free")
    assert (gemini.calls, groq.calls, free.calls) == (1, 1, 1)


@pytest.mark.anyio
async def test_user_fallback_sequence_is_observable_without_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gemini = _Provider("gemini", "gemini-3.6-flash", AIProviderUnavailable())
    groq = _Provider("groq", "openai/gpt-oss-120b", AIProviderUnavailable())
    free = _Provider("openrouter", "openrouter/free", "ok")
    request = _request(UserRole.USER, content="conteudo-ultrassecreto")
    with caplog.at_level(logging.INFO, logger="app.ai_provider"):
        await UserAIProviderManager(gemini, groq=groq, openrouter=free).generate(
            request
        )
    records = [record for record in caplog.records if record.name == "app.ai_provider"]
    assert [record.ai_provider for record in records] == [  # type: ignore[attr-defined]
        "gemini",
        "groq",
        "openrouter",
    ]
    assert [record.ai_outcome for record in records] == [  # type: ignore[attr-defined]
        "unavailable",
        "unavailable",
        "succeeded",
    ]
    assert [record.ai_fallback for record in records] == [  # type: ignore[attr-defined]
        False,
        True,
        True,
    ]
    assert "conteudo-ultrassecreto" not in " ".join(
        str(record.__dict__) for record in records
    )


@pytest.mark.anyio
async def test_user_all_free_failures_never_reach_paid_model() -> None:
    tiers = [
        _Provider("gemini", "gemini-3.6-flash", AIProviderUnavailable()),
        _Provider("groq", "openai/gpt-oss-120b", AIProviderUnavailable()),
        _Provider("openrouter", "openrouter/free", AIProviderUnavailable()),
    ]
    with pytest.raises(AIProviderUnavailable):
        await UserAIProviderManager(
            tiers[0], groq=tiers[1], openrouter=tiers[2]
        ).generate(_request(UserRole.USER))
    assert [tier.model for tier in tiers] == [
        "gemini-3.6-flash",
        "openai/gpt-oss-120b",
        "openrouter/free",
    ]
    assert [tier.calls for tier in tiers] == [1, 1, 1]


@pytest.mark.anyio
async def test_nonretryable_authentication_error_does_not_loop() -> None:
    gemini = _Provider(
        "gemini",
        f"gemini-auth-{uuid4().hex}",
        AIProviderError("provider_authentication_failed", retryable=False),
    )
    fallback = _Provider("groq", "openai/gpt-oss-120b", AssertionError())
    with pytest.raises(AIProviderError, match="provider_authentication_failed"):
        await UserAIProviderManager(gemini, groq=fallback).generate(
            _request(UserRole.USER)
        )
    assert (gemini.calls, fallback.calls) == (1, 0)


@pytest.mark.anyio
async def test_dev_normal_request_uses_same_free_cascade_as_user() -> None:
    gemini = _Provider("gemini", "gemini-3.6-flash", AIProviderUnavailable())
    groq = _Provider("groq", "openai/gpt-oss-120b", AIProviderUnavailable())
    free = _Provider("openrouter", "openrouter/free", "ok")
    manager = AdminDevAIProviderManager(gemini, groq=groq, openrouter=free)
    response = await manager.generate(_request(UserRole.DEV))
    assert (response.provider, response.model) == ("openrouter", "openrouter/free")
    assert (gemini.calls, groq.calls, free.calls) == (1, 1, 1)
    with pytest.raises(Exception, match="ADMIN/DEV manager"):
        await manager.generate(_request(UserRole.USER))


def test_builders_keep_user_and_dev_on_free_models() -> None:
    settings = Settings(
        _env_file=None,
        gemini_api_key_user="gemini",
        gemini_api_key_admin_dev="gemini-dev",
        groq_api_key="groq",
        openrouter_api_key="openrouter",
        firecrawl_api_key="firecrawl",
    )
    user = build_user_ai_provider_manager(settings)
    dev = build_admin_dev_ai_provider_manager(settings)
    assert user._groq.model == "openai/gpt-oss-120b"  # noqa: SLF001
    assert user._openrouter.model == "openrouter/free"  # noqa: SLF001
    assert dev._groq.model == "openai/gpt-oss-120b"  # noqa: SLF001
    assert dev._openrouter.model == "openrouter/free"  # noqa: SLF001
    assert dev._search_provider is not None  # noqa: SLF001


@pytest.mark.anyio
async def test_openrouter_is_only_an_llm_and_rejects_grounding_requests() -> None:
    provider, client = _openrouter(
        _Response(body={"choices": [{"message": {"content": "normal"}}]})
    )

    with pytest.raises(AIProviderError, match="capability_unsupported"):
        await provider.generate(_request(UserRole.DEV, grounding=True))

    assert client.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("response", "error_type"),
    [
        (_Response(429, headers={"retry-after": "2"}), AIProviderQuotaExceeded),
        (_Response(504), AIProviderUnavailable),
        (_Response(401), AIProviderError),
        (_Response(400), AIProviderError),
        (httpx.ReadTimeout("timeout"), AIProviderUnavailable),
    ],
)
async def test_openrouter_sanitizes_failures(
    response: object, error_type: type
) -> None:
    provider, _ = _openrouter(response)
    with pytest.raises(error_type) as captured:
        await provider.generate(_request(UserRole.USER))
    assert "secret-canary" not in str(captured.value)
