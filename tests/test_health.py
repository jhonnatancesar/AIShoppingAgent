"""Testes do módulo de saúde."""

import asyncio

import pytest
from app.health import router
from app.health.router import HealthResponse, get_health
from app.main import app
from fastapi import Response


def test_health_returns_ok() -> None:
    """A verificação de vivacidade deve responder com estado estável."""
    assert get_health() == HealthResponse(status="ok")


def test_health_route_is_exposed_in_openapi() -> None:
    """A aplicação deve publicar a rota de saúde no schema OpenAPI."""
    operation = app.openapi()["paths"]["/health"]["get"]

    assert operation["tags"] == ["health"]
    assert operation["operationId"] == "get_health"
    assert operation["summary"] == "Verificar saúde"
    assert operation["responses"]["200"]["description"] == "Aplicação disponível."


def test_readiness_returns_ready_after_real_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked = False

    def check() -> None:
        nonlocal checked
        checked = True

    monkeypatch.setattr(router, "_postgres_is_ready", check)
    response = Response()

    result = asyncio.run(router.get_readiness(response))

    assert checked is True
    assert result.status == "ready"
    assert response.status_code == 200


def test_readiness_returns_503_when_postgresql_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def check() -> None:
        raise router.SQLAlchemyError("database-canary-secret")

    monkeypatch.setattr(router, "_postgres_is_ready", check)
    response = Response()

    result = asyncio.run(router.get_readiness(response))

    assert result.status == "not_ready"
    assert response.status_code == 503
