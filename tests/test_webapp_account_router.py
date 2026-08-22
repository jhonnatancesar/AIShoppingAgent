"""Testes focados da área Minha conta (TASK-101)."""

from datetime import UTC, datetime
from uuid import uuid4

from app.database.dependency import get_session
from app.users.models import User, UserRole
from app.webapp.account_router import router
from app.webapp.dependency import require_web_session
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _user(*, telegram_linked: bool = True) -> User:
    return User(
        id=uuid4(),
        display_name="Cliente",
        username="cliente",
        email="cliente@example.com",
        role=UserRole.USER,
        telegram_user_id=123 if telegram_linked else None,
        telegram_chat_id=123 if telegram_linked else None,
        favorite_stores=["amazon"],
        preferred_categories=["celulares"],
        notify_price_decreases=True,
        notify_target_reached=False,
        created_at=datetime(2026, 8, 22, tzinfo=UTC),
        updated_at=datetime(2026, 8, 22, tzinfo=UTC),
    )


class _SyncSession:
    def scalar(self, _statement):
        return None

    def execute(self, _statement):
        return None

    def add(self, _value) -> None:
        return None

    def commit(self) -> None:
        return None


def _client(user: User) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    session = _SyncSession()
    app.dependency_overrides[require_web_session] = lambda: user
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


def test_user_reads_only_safe_account_fields() -> None:
    response = _client(_user()).get("/api/v1/account")

    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "cliente"
    assert body["telegram_linked"] is True
    assert "telegram_user_id" not in body
    assert "telegram_chat_id" not in body
    assert "pending_intent" not in body


def test_web_account_without_telegram_remains_fully_available() -> None:
    response = _client(_user(telegram_linked=False)).get("/api/v1/account")

    assert response.status_code == 200
    assert response.json()["telegram_link_status"] == "not_linked"
    assert response.json()["telegram_linked"] is False


def test_web_user_starts_optional_link_without_sending_telegram_identity() -> None:
    user = _user(telegram_linked=False)
    response = _client(user).post("/api/v1/account/telegram-link", json={})

    assert response.status_code == 200
    assert response.json()["command"].startswith("/vincular ")
    assert response.json()["account"]["telegram_link_status"] == "pending"
    assert user.telegram_user_id is None


def test_user_updates_profile_and_existing_preferences() -> None:
    user = _user()
    response = _client(user).put(
        "/api/v1/account/profile",
        json={
            "display_name": "  Cliente Atualizado  ",
            "email": "  novo@example.com  ",
            "favorite_stores": ["pichau", "amazon", "amazon"],
            "preferred_categories": ["hardware", "celulares"],
        },
    )

    assert response.status_code == 200
    assert user.display_name == "Cliente Atualizado"
    assert user.email == "novo@example.com"
    assert user.favorite_stores == ["amazon", "pichau"]
    assert user.preferred_categories == ["celulares", "hardware"]


def test_user_updates_notification_preferences_without_telegram_specific_rule() -> None:
    user = _user()
    response = _client(user).put(
        "/api/v1/account/notification-preferences",
        json={
            "notify_price_decreases": False,
            "notify_target_reached": True,
        },
    )

    assert response.status_code == 200
    assert user.notify_price_decreases is False
    assert user.notify_target_reached is True


def test_profile_rejects_unknown_store_without_mutating_user() -> None:
    user = _user()
    response = _client(user).put(
        "/api/v1/account/profile",
        json={
            "display_name": "Outro",
            "email": None,
            "favorite_stores": ["loja_inexistente"],
            "preferred_categories": [],
        },
    )

    assert response.status_code == 422
    assert user.display_name == "Cliente"
    assert user.favorite_stores == ["amazon"]
