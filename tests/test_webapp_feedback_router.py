"""Contrato HTTP do envio de bug/suporte/sugestão de loja pela Web
(subtask 7 da auditoria GG Oferta) -- mesmo padrão de
`tests/test_webapp_missions_router.py`: app FastAPI mínima, sessão/CSRF
herdados de verdade via `require_web_session`, só `get_web_async_session`
sobreposto."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.core.errors import register_api_error_handler
from app.database.dependency import get_web_async_session
from app.feedback.models import (
    FeedbackChannel,
    FeedbackKind,
    FeedbackStatus,
    UserFeedback,
)
from app.users.models import User, UserRole
from app.webapp.csrf import CSRF_COOKIE_NAME
from app.webapp.dependency import WEB_SESSION_COOKIE_NAME
from app.webapp.feedback_router import router
from fastapi import FastAPI
from fastapi.testclient import TestClient

_CSRF_TOKEN = "csrf-canary-token"


def _user() -> User:
    return User(
        id=uuid4(), display_name="Cliente Teste", role=UserRole.USER, username="cliente"
    )


def _build_app() -> FastAPI:
    app = FastAPI()
    register_api_error_handler(app)
    app.include_router(router)
    return app


@pytest.fixture
def owner() -> User:
    return _user()


@pytest.fixture
def async_session() -> MagicMock:
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    return session


@pytest.fixture
def client(
    async_session: MagicMock, owner: User, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    app = _build_app()
    app.dependency_overrides[get_web_async_session] = lambda: async_session
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user", lambda *a, **k: owner
    )
    return TestClient(app)


def _cookies(**extra: str) -> dict[str, str]:
    return {
        WEB_SESSION_COOKIE_NAME: "raw-token-canary",
        CSRF_COOKIE_NAME: _CSRF_TOKEN,
        **extra,
    }


def _csrf_headers() -> dict[str, str]:
    return {"X-CSRF-Token": _CSRF_TOKEN}


def test_submit_feedback_without_session_is_401(client: TestClient) -> None:
    response = client.post("/api/v1/feedback", json={"kind": "bug", "message": "erro"})
    assert response.status_code == 401


def test_submit_feedback_without_csrf_is_403(client: TestClient) -> None:
    response = client.post(
        "/api/v1/feedback",
        json={"kind": "bug", "message": "erro"},
        cookies={WEB_SESSION_COOKIE_NAME: "raw-token-canary"},
    )
    assert response.status_code == 403


def _created_feedback(**overrides: object) -> UserFeedback:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        user_id=uuid4(),
        kind=FeedbackKind.BUG,
        channel=FeedbackChannel.WEB,
        message="O gráfico não carrega.",
        store_name=None,
        store_url=None,
        status=FeedbackStatus.NEW,
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    defaults.update(overrides)
    return UserFeedback(**defaults)


def test_submit_bug_report_persists_and_returns_new_status(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = _created_feedback()
    monkeypatch.setattr(
        "app.webapp.feedback_router.create_feedback_async",
        AsyncMock(return_value=created),
    )

    response = client.post(
        "/api/v1/feedback",
        json={"kind": "bug", "message": "O gráfico não carrega."},
        cookies=_cookies(),
        headers=_csrf_headers(),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "new"
    assert body["id"] == str(created.id)


def test_submit_bug_report_without_message_is_422(client: TestClient) -> None:
    response = client.post(
        "/api/v1/feedback",
        json={"kind": "bug", "message": "  "},
        cookies=_cookies(),
        headers=_csrf_headers(),
    )
    assert response.status_code == 422


def test_submit_support_without_message_is_422(client: TestClient) -> None:
    response = client.post(
        "/api/v1/feedback",
        json={"kind": "support"},
        cookies=_cookies(),
        headers=_csrf_headers(),
    )
    assert response.status_code == 422


def test_submit_store_suggestion_without_store_name_is_422(client: TestClient) -> None:
    response = client.post(
        "/api/v1/feedback",
        json={"kind": "store_suggestion", "message": "sem nome"},
        cookies=_cookies(),
        headers=_csrf_headers(),
    )
    assert response.status_code == 422


def test_submit_store_suggestion_without_comment_succeeds(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = _created_feedback(
        kind=FeedbackKind.STORE_SUGGESTION, message=None, store_name="Loja Nova"
    )
    monkeypatch.setattr(
        "app.webapp.feedback_router.create_feedback_async",
        AsyncMock(return_value=created),
    )

    response = client.post(
        "/api/v1/feedback",
        json={"kind": "store_suggestion", "store_name": "Loja Nova"},
        cookies=_cookies(),
        headers=_csrf_headers(),
    )
    assert response.status_code == 201


def test_submit_feedback_rejects_unknown_kind(client: TestClient) -> None:
    response = client.post(
        "/api/v1/feedback",
        json={"kind": "not_a_real_kind", "message": "erro"},
        cookies=_cookies(),
        headers=_csrf_headers(),
    )
    assert response.status_code == 422


# --- Segurança: origem/dono/status nunca vêm do cliente -------------------


def test_submit_feedback_rejects_spoofed_user_id_channel_and_status(
    client: TestClient,
) -> None:
    """`FeedbackCreateRequest` usa `extra=\"forbid\"` -- um payload tentando
    definir dono, canal ou status é rejeitado inteiro (422), nunca
    silenciosamente aceito com esses campos ignorados."""
    response = client.post(
        "/api/v1/feedback",
        json={
            "kind": "bug",
            "message": "erro",
            "user_id": str(uuid4()),
            "channel": "telegram",
            "status": "closed",
        },
        cookies=_cookies(),
        headers=_csrf_headers(),
    )
    assert response.status_code == 422


def test_submit_feedback_persists_server_assigned_user_channel_and_status(
    client: TestClient, owner: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sem os campos extras, o backend usa a sessão autenticada (`user_id`),
    fixa `channel=web` e nasce sempre `status=new` -- nunca o que o
    cliente enviaria se pudesse."""
    captured: dict[str, object] = {}

    async def _fake_create(session, **kwargs):
        captured.update(kwargs)
        return _created_feedback(user_id=kwargs["user_id"])

    monkeypatch.setattr(
        "app.webapp.feedback_router.create_feedback_async", _fake_create
    )

    response = client.post(
        "/api/v1/feedback",
        json={"kind": "bug", "message": "erro real"},
        cookies=_cookies(),
        headers=_csrf_headers(),
    )

    assert response.status_code == 201
    assert captured["user_id"] == owner.id
    assert captured["channel"] == FeedbackChannel.WEB
    assert "status" not in captured


# --- Invariantes de domínio aplicadas também na borda Web ------------------


def test_submit_store_suggestion_rejects_javascript_url_scheme(
    client: TestClient,
) -> None:
    """`store_url` não tem validação de esquema no Pydantic -- prova que
    quem barra `javascript:` é o validador compartilhado
    (`app.feedback.service.validate_store_url`), não um acaso da forma."""
    response = client.post(
        "/api/v1/feedback",
        json={
            "kind": "store_suggestion",
            "store_name": "Loja Nova",
            "store_url": "javascript:alert(1)",
        },
        cookies=_cookies(),
        headers=_csrf_headers(),
    )
    assert response.status_code == 422


def test_submit_store_suggestion_accepts_https_url(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = _created_feedback(
        kind=FeedbackKind.STORE_SUGGESTION,
        message=None,
        store_name="Loja Nova",
        store_url="https://lojanova.example.com",
    )
    monkeypatch.setattr(
        "app.webapp.feedback_router.create_feedback_async",
        AsyncMock(return_value=created),
    )

    response = client.post(
        "/api/v1/feedback",
        json={
            "kind": "store_suggestion",
            "store_name": "Loja Nova",
            "store_url": "https://lojanova.example.com",
        },
        cookies=_cookies(),
        headers=_csrf_headers(),
    )
    assert response.status_code == 201
