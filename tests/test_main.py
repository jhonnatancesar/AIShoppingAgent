"""Testes do ponto de entrada FastAPI."""

from app.main import app


def test_openapi_exposes_application_metadata() -> None:
    """O schema OpenAPI deve refletir os metadados configurados da aplicação."""
    schema = app.openapi()

    assert schema["info"] == {
        "title": "AIShoppingAgent",
        "version": "0.1.0",
        "description": "Agente inteligente de compras.",
    }
    assert "/metrics" not in schema["paths"]
    assert "/ready" in schema["paths"]
    assert "/auth" not in schema["paths"]
    assert "/auth/actions" in schema["paths"]
