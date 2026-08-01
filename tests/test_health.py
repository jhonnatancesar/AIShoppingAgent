"""Testes do módulo de saúde."""

from app.health.router import HealthResponse, get_health
from app.main import app


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
