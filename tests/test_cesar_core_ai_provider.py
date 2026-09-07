import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from app.ai_provider.cesar_core import (
    CesarCoreAIProvider,
    CesarCoreConnectionUnavailable,
)
from app.ai_provider.contracts import (
    AIMessage,
    AIMessageRole,
    AIProviderError,
    AIRequest,
    AIRequestError,
)
from app.ai_provider.manager import (
    CesarCoreAIProviderManager,
    _build_cesar_core_manager,
    build_admin_dev_ai_provider_manager,
    build_user_ai_provider_manager,
)
from app.core.config import Settings
from app.users.models import UserRole


def request(profile: UserRole = UserRole.USER):
    return AIRequest(
        uuid4(),
        profile,
        "typed_roles_validation",
        (
            AIMessage(AIMessageRole.SYSTEM, "internal-instruction"),
            AIMessage(AIMessageRole.USER, "first-question"),
            AIMessage(AIMessageRole.ASSISTANT, "previous-answer"),
            AIMessage(AIMessageRole.USER, "next-question"),
        ),
        datetime.now(UTC),
    )


def provider(tmp_path, handler, *, ai_profile="admin_dev"):
    key = tmp_path / "key"
    key.write_text("synthetic-fixture-only", encoding="utf-8")
    return CesarCoreAIProvider(
        api_key_file=key,
        base_url="http://127.0.0.1:8100",
        service="backend",
        ai_profile=ai_profile,
        max_tokens=512,
        client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )


def test_messages_preserved_and_response_normalized(tmp_path, caplog):
    original = request()
    wire = []

    def handler(req):
        wire.append(json.loads(req.content))
        assert req.headers["Authorization"] == "Bearer synthetic-fixture-only"
        assert req.headers["X-Purpose"] == original.purpose
        return httpx.Response(
            200,
            json={
                "request_id": "core-id",
                "correlation_id": str(original.request_id),
                "provider_gateway": "omniroute",
                "model": "resolved",
                "content": "answer",
                "usage": {"completion_tokens": 12},
            },
        )

    result = asyncio.run(provider(tmp_path, handler).generate(original))
    assert wire == [
        {
            "ai_profile": "admin_dev",
            "messages": [
                {"role": m.role.value, "content": m.content} for m in original.messages
            ],
            "requirements": {"service_class": "economy", "cost_policy": "free_only"},
            "max_tokens": 512,
        }
    ]
    assert result.request_id == original.request_id
    assert result.provider == "cesar_core" and result.model == "resolved"
    assert "internal-instruction" not in caplog.text
    assert "synthetic-fixture-only" not in caplog.text


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 502, 503, 307])
def test_gateway_response_never_allows_local_disaster_fallback(tmp_path, status):
    with pytest.raises(AIProviderError) as error:
        asyncio.run(
            provider(tmp_path, lambda req: httpx.Response(status)).generate(request())
        )
    assert not isinstance(error.value, CesarCoreConnectionUnavailable)
    assert not error.value.retryable


@pytest.mark.parametrize("completion", [None, 513, True, "12"])
def test_invalid_usage_is_fail_closed(tmp_path, completion):
    original = request()

    def handler(req):
        return httpx.Response(
            200,
            json={
                "request_id": "core",
                "correlation_id": str(original.request_id),
                "provider_gateway": "omniroute",
                "model": "resolved",
                "content": "answer",
                "usage": {"completion_tokens": completion},
            },
        )

    with pytest.raises(AIProviderError, match="cesar_core_invalid_response"):
        asyncio.run(provider(tmp_path, handler).generate(original))


def test_timeout_is_not_disaster_connection_failure(tmp_path):
    def handler(req):
        raise httpx.ReadTimeout("uncertain after consumption")

    with pytest.raises(AIProviderError, match="cesar_core_transport_uncertain"):
        asyncio.run(provider(tmp_path, handler).generate(request()))


def test_core_connection_failure_never_calls_legacy(tmp_path):
    def handler(req):
        raise httpx.ConnectError("refused")

    manager = CesarCoreAIProviderManager(
        provider(tmp_path, handler),
        profile=UserRole.USER,
        circuit_failure_threshold=100,
        circuit_open_seconds=1,
    )
    with pytest.raises(CesarCoreConnectionUnavailable):
        asyncio.run(manager.generate(request()))


@pytest.mark.parametrize(
    ("role", "manager_profile"),
    [
        (UserRole.USER, UserRole.USER),
        (UserRole.ADMIN, None),
        (UserRole.DEV, None),
    ],
)
def test_all_normal_profiles_use_core_only(tmp_path, role, manager_profile):
    original = request(role)

    def handler(req):
        return httpx.Response(
            200,
            json={
                "request_id": "core-id",
                "correlation_id": str(original.request_id),
                "provider_gateway": "omniroute",
                "model": "resolved",
                "content": "answer",
                "usage": {"completion_tokens": 1},
            },
        )

    manager = CesarCoreAIProviderManager(
        provider(tmp_path, handler),
        profile=manager_profile,
        circuit_failure_threshold=100,
        circuit_open_seconds=1,
    )
    assert asyncio.run(manager.generate(original)).provider == "cesar_core"


def test_core_is_mandatory_for_user_and_admin_dev(tmp_path):
    config = Settings(_env_file=None)
    with pytest.raises(AIRequestError, match="CESAR_CORE_API_KEY_FILE"):
        build_user_ai_provider_manager(config)
    with pytest.raises(AIRequestError, match="CESAR_CORE_API_KEY_FILE"):
        build_admin_dev_ai_provider_manager(config)

    core_config = Settings(
        _env_file=None,
        cesar_core_api_key_file=tmp_path / "key",
    )
    assert isinstance(
        build_user_ai_provider_manager(core_config), CesarCoreAIProviderManager
    )
    assert isinstance(
        build_admin_dev_ai_provider_manager(core_config), CesarCoreAIProviderManager
    )


@pytest.mark.parametrize(
    ("profile", "expected_ai_profile"),
    [
        (UserRole.USER, "user"),
        (UserRole.ADMIN, "admin_dev"),
        (UserRole.DEV, "admin_dev"),
        (None, "admin_dev"),
    ],
)
def test_ai_profile_is_derived_from_the_manager_profile_never_from_service_class(
    tmp_path, profile, expected_ai_profile
) -> None:
    """GG só informa quem está chamando -- nunca escolhe provider/rota."""
    config = Settings(_env_file=None, cesar_core_api_key_file=tmp_path / "key")
    manager = _build_cesar_core_manager(config, profile)
    assert manager._provider._ai_profile == expected_ai_profile


def test_public_ai_package_exposes_no_direct_provider_factory() -> None:
    import app.ai_provider as package

    for name in (
        "GeminiProvider",
        "GroqProvider",
        "OpenRouterProvider",
        "UserAIProviderManager",
        "AdminDevAIProviderManager",
    ):
        assert not hasattr(package, name)


@pytest.mark.parametrize("status", [401, 403, 429, 503])
def test_grounding_core_rejection_never_calls_legacy(tmp_path, status):
    manager = CesarCoreAIProviderManager(
        provider(tmp_path, lambda req: httpx.Response(status)),
        profile=UserRole.USER,
        circuit_failure_threshold=100,
        circuit_open_seconds=1,
    )
    with pytest.raises(AIProviderError):
        asyncio.run(manager.generate(replace(request(), require_search_grounding=True)))


def test_grounding_uses_core_and_preserves_typed_messages(tmp_path):
    original = replace(request(), require_search_grounding=True)
    wire = []

    def handler(req):
        wire.append(json.loads(req.content))
        return httpx.Response(
            200,
            json={
                "request_id": "core-id",
                "correlation_id": str(original.request_id),
                "provider_gateway": "omniroute",
                "model": "resolved",
                "content": "grounded answer",
                "usage": {"completion_tokens": 12},
                "grounding_requested": True,
                "grounding_performed": True,
                "grounding_sources": ["https://example.test/source"],
            },
        )

    manager = CesarCoreAIProviderManager(
        provider(tmp_path, handler),
        profile=UserRole.USER,
        circuit_failure_threshold=100,
        circuit_open_seconds=1,
    )
    response = asyncio.run(manager.generate(original))
    assert response.provider == "cesar_core"
    assert response.grounding_performed
    assert response.grounding_sources == ("https://example.test/source",)
    assert wire[0]["require_search_grounding"] is True
    assert wire[0]["messages"] == [
        {"role": message.role.value, "content": message.content}
        for message in original.messages
    ]


def test_grounding_core_failure_is_fail_closed(tmp_path):
    grounded = replace(request(), require_search_grounding=True)

    def unavailable(_request):
        raise httpx.ConnectError("down")

    manager = CesarCoreAIProviderManager(
        provider(tmp_path, unavailable),
        profile=UserRole.USER,
        circuit_failure_threshold=100,
        circuit_open_seconds=1,
    )
    with pytest.raises(CesarCoreConnectionUnavailable):
        asyncio.run(manager.generate(grounded))
