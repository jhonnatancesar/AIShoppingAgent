import asyncio
import json
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
    AIResponse,
)
from app.ai_provider.manager import (
    CesarCoreAIProviderManager,
    build_user_ai_provider_manager,
)
from app.core.config import Settings
from app.users.models import UserRole


def request():
    return AIRequest(
        uuid4(),
        UserRole.USER,
        "typed_roles_validation",
        (
            AIMessage(AIMessageRole.SYSTEM, "internal-instruction"),
            AIMessage(AIMessageRole.USER, "first-question"),
            AIMessage(AIMessageRole.ASSISTANT, "previous-answer"),
            AIMessage(AIMessageRole.USER, "next-question"),
        ),
        datetime.now(UTC),
    )


def provider(tmp_path, handler):
    key = tmp_path / "key"
    key.write_text("synthetic-fixture-only", encoding="utf-8")
    return CesarCoreAIProvider(
        api_key_file=key,
        base_url="http://127.0.0.1:8100",
        service="backend",
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


@pytest.mark.parametrize("enabled", [False, True])
def test_disaster_is_opt_in_and_only_for_connection_failure(tmp_path, enabled):
    def handler(req):
        raise httpx.ConnectError("refused")

    calls = []

    class Legacy:
        async def generate(self, req):
            calls.append(req)
            return AIResponse(req.request_id, "legacy", "free", "ok", datetime.now(UTC))

    manager = CesarCoreAIProviderManager(
        provider(tmp_path, handler),
        lambda: Legacy(),
        profile=UserRole.USER,
        disaster_fallback=enabled,
        circuit_failure_threshold=100,
        circuit_open_seconds=1,
    )
    if enabled:
        assert asyncio.run(manager.generate(request())).provider == "legacy"
    else:
        with pytest.raises(CesarCoreConnectionUnavailable):
            asyncio.run(manager.generate(request()))
    assert len(calls) == int(enabled)


def test_flag_defaults_off_and_enabled_does_not_require_provider_keys(tmp_path):
    config = Settings(_env_file=None)
    assert config.cesar_core_ai_enabled is False
    assert config.cesar_core_disaster_fallback_enabled is False
    manager = build_user_ai_provider_manager(
        Settings(
            _env_file=None,
            cesar_core_ai_enabled=True,
            cesar_core_api_key_file=tmp_path / "key",
        )
    )
    assert isinstance(manager, CesarCoreAIProviderManager)
