"""Paradas reais somente nos containers descartáveis 118H; nunca no stack original."""

import asyncio
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from app.ai_provider.contracts import (
    AIMessage,
    AIMessageRole,
    AIProviderError,
    AIRequest,
)
from app.ai_provider.manager import build_admin_dev_ai_provider_manager
from app.core.config import Settings
from app.search.contracts import WebSearchError
from app.search.manager import build_web_search_manager
from app.users.models import UserRole

pytestmark = pytest.mark.skipif(
    os.environ.get("AISHOPPING_RUN_118H_RESILIENCE") != "1",
    reason="Exige harness e containers DEV isolados da 118H",
)
DOCKER = (
    Path(os.environ["LOCALAPPDATA"]) / "Programs/DockerDesktop/resources/bin/docker.exe"
)


def docker(action, container):
    assert action in {"stop", "start"}
    assert container in {"omniroute-118h-validation", "searxng-118h-validation"}
    result = subprocess.run(
        [str(DOCKER), action, container], capture_output=True, timeout=45
    )
    assert result.returncode == 0, "Operação DEV falhou; saída omitida"


def wait_ready(client):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            if client.get("/ready").json().get("status") == "ok":
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    pytest.fail("Core não recuperou readiness após reiniciar dependência")


@pytest.mark.parametrize(
    "container", ["omniroute-118h-validation", "searxng-118h-validation"]
)
def test_dependency_outage_and_recovery(container, caplog, monkeypatch):
    settings = Settings(_env_file=None)
    assert settings.environment == "development"
    secret = settings.cesar_core_api_key_file.read_text().strip()
    manager = build_web_search_manager(settings)
    wire = []
    original = httpx.AsyncHTTPTransport.handle_async_request

    async def observe(transport, request):
        if request.url.host == "api.firecrawl.dev":
            wire.append(request.url.path)
        return await original(transport, request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", observe)
    with httpx.Client(
        base_url=settings.cesar_core_base_url, trust_env=False, timeout=90
    ) as client:
        wait_ready(client)
        try:
            docker("stop", container)
            assert client.get("/health").status_code == 200
            assert client.get("/ready").json()["status"] == "degraded"
            # Query própria evita que um cache quente esconda a indisponibilidade.
            query = f"Python programming language documentation {uuid4().hex[:8]}"
            with pytest.raises(WebSearchError) as search_error:
                asyncio.run(manager.search(query, limit=3))
            assert search_error.value.code == "core_search_unavailable"
            assert wire == []
            if container == "omniroute-118h-validation":
                response = client.post(
                    "/v1/ai/generate",
                    headers={
                        "Authorization": "Bearer " + secret,
                        "X-Service": "contract_test",
                        "X-Purpose": "resilience_validation",
                        "X-Correlation-Id": "118h-omniroute-down-ai",
                    },
                    json={
                        "prompt": "Reply OK",
                        "max_tokens": 8,
                        "requirements": {
                            "service_class": "economy",
                            "cost_policy": "free_only",
                        },
                    },
                )
                assert response.status_code == 503
                assert response.json()["error"]["code"] == "ai_upstream_unavailable"
                assert (
                    response.json()["error"]["correlation_id"]
                    == "118h-omniroute-down-ai"
                )
                ai_request = AIRequest(
                    uuid4(),
                    UserRole.DEV,
                    "resilience_validation",
                    (AIMessage(AIMessageRole.USER, "Reply OK"),),
                    datetime.now(UTC),
                )
                with pytest.raises(AIProviderError) as gg_error:
                    asyncio.run(
                        build_admin_dev_ai_provider_manager(settings).generate(
                            ai_request
                        )
                    )
                assert gg_error.value.code == "cesar_core_request_failed"
                assert not gg_error.value.retryable
                assert wire == []
        finally:
            docker("start", container)
            wait_ready(client)
        recovered = asyncio.run(
            manager.search(
                "Python programming language official documentation", limit=3
            )
        )
        assert recovered.provider == "cesar_core"
        assert recovered.source == "searxng-search"
        assert 1 <= len(recovered.results) <= 3
        assert wire == []
    assert secret not in caplog.text
    assert settings.firecrawl_api_key.get_secret_value() not in caplog.text
