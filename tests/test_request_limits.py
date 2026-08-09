"""Limite de corpo HTTP antes de parsing e dependências de rota."""

from app.main import app
from fastapi.testclient import TestClient


def test_request_body_above_64_kib_is_rejected() -> None:
    response = TestClient(app).post(
        "/auth/actions",
        content=b"x" * 65_537,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_body_too_large"


def test_operational_get_without_body_remains_available() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
