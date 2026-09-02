"""Contrato HTTP de recuperação de senha e verificação de e-mail
(Subtask 9, auditoria GG Oferta) -- mesmo padrão de
`tests/test_webapp_feedback_router.py`: app FastAPI mínima,
`get_web_async_session` sobreposto, `require_web_session` exercitado de
verdade via mock de `get_web_session_user` (só nos endpoints
autenticados de verificação de e-mail). A mecânica real de
`VerificationChallenge` (FOR UPDATE, corrida) é provada em
`tests/integration/test_registration_and_recovery.py`; aqui o alvo é o
contrato HTTP (status/códigos de erro, nunca 500, anti-enumeração)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.authentication.models import (
    VerificationChallenge,
    VerificationChannel,
    VerificationPurpose,
)
from app.authentication.passwords import PasswordPolicyError
from app.authentication.verification import ChallengeInvalid, ChallengeRateLimited
from app.core.config import Settings, get_settings
from app.core.errors import register_api_error_handler
from app.database.dependency import get_web_async_session
from app.users.models import User, UserRole
from app.webapp.auth_router import router
from app.webapp.csrf import CSRF_COOKIE_NAME
from app.webapp.dependency import WEB_SESSION_COOKIE_NAME
from fastapi import FastAPI
from fastapi.testclient import TestClient

_CSRF_TOKEN = "csrf-canary-token"
_TEST_PEPPER = "pepper-de-teste-nao-real"


def _authenticated_cookies() -> dict[str, str]:
    return {WEB_SESSION_COOKIE_NAME: "token", CSRF_COOKIE_NAME: _CSRF_TOKEN}


def _csrf_headers() -> dict[str, str]:
    return {"X-CSRF-Token": _CSRF_TOKEN}


def _build_app() -> FastAPI:
    app = FastAPI()
    register_api_error_handler(app)
    app.include_router(router)
    app.dependency_overrides[get_settings] = lambda: Settings(
        verification_code_pepper=_TEST_PEPPER,
        telegram_bot_token="test-telegram-bot-token",
        _env_file=None,
    )
    return app


def _user(**overrides: object) -> User:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        display_name="Cliente Teste",
        role=UserRole.USER,
        username="cliente",
        email="cliente@example.com",
        is_active=True,
    )
    defaults.update(overrides)
    return User(**defaults)


def _challenge(**overrides: object) -> VerificationChallenge:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        user_id=uuid4(),
        purpose=VerificationPurpose.PASSWORD_RESET,
        channel=VerificationChannel.EMAIL,
        code_hash="irrelevante",
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        used_at=None,
        attempts=0,
        created_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return VerificationChallenge(**defaults)


@pytest.fixture
def async_session() -> MagicMock:
    session = MagicMock()
    session.scalar = AsyncMock(return_value=None)
    session.get = AsyncMock(return_value=None)
    session.commit = AsyncMock()
    return session


@pytest.fixture
def client(async_session: MagicMock) -> TestClient:
    app = _build_app()
    app.dependency_overrides[get_web_async_session] = lambda: async_session
    return TestClient(app)


# --- Recuperação de senha ------------------------------------------------


def test_recovery_channels_for_nonexistent_identifier_is_empty(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/auth/password-recovery/channels", json={"identifier": "ninguem"}
    )
    assert response.status_code == 200
    assert response.json() == {"channels": []}


def test_recovery_channels_for_existing_user_lists_available_channels(
    client: TestClient, async_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user(telegram_user_id=555, telegram_chat_id=555)
    async_session.scalar = AsyncMock(return_value=user)

    response = client.post(
        "/api/v1/auth/password-recovery/channels", json={"identifier": "cliente"}
    )
    assert response.status_code == 200
    assert response.json() == {"channels": ["telegram"]}


def test_recovery_request_for_nonexistent_identifier_returns_decoy_shape(
    client: TestClient,
) -> None:
    """Nunca revela se a conta existe -- resposta com o mesmo formato de
    uma solicitação real, mas com um `challenge_id` nunca persistido."""
    response = client.post(
        "/api/v1/auth/password-recovery/request",
        json={"identifier": "ninguem", "channel": "email"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "challenge_id" in body and "expires_at" in body


def test_recovery_request_for_unavailable_channel_returns_decoy_shape(
    client: TestClient, async_session: MagicMock
) -> None:
    user = _user()  # sem telegram vinculado, sem provider de e-mail
    async_session.scalar = AsyncMock(return_value=user)

    response = client.post(
        "/api/v1/auth/password-recovery/request",
        json={"identifier": "cliente", "channel": "telegram"},
    )
    assert response.status_code == 200
    assert "challenge_id" in response.json()


def test_recovery_request_success_delivers_via_telegram(
    client: TestClient, async_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user(telegram_user_id=555, telegram_chat_id=555)
    async_session.scalar = AsyncMock(return_value=user)
    challenge = _challenge(user_id=user.id, channel=VerificationChannel.TELEGRAM)
    monkeypatch.setattr(
        "app.webapp.auth_router.create_challenge_async",
        AsyncMock(return_value=(challenge, "482913")),
    )
    delivered: list[str] = []

    async def _fake_deliver(self, *, user, code, purpose):
        delivered.append(code)

    monkeypatch.setattr(
        "app.authentication.delivery.TelegramDeliveryProvider.deliver", _fake_deliver
    )

    response = client.post(
        "/api/v1/auth/password-recovery/request",
        json={"identifier": "cliente", "channel": "telegram"},
    )
    assert response.status_code == 200
    assert response.json()["challenge_id"] == str(challenge.id)
    assert delivered == ["482913"]


def test_recovery_request_delivery_failure_is_503_and_never_500(
    client: TestClient, async_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.authentication.delivery import DeliveryUnavailable

    user = _user(telegram_user_id=555, telegram_chat_id=555)
    async_session.scalar = AsyncMock(return_value=user)
    challenge = _challenge(user_id=user.id, channel=VerificationChannel.TELEGRAM)
    monkeypatch.setattr(
        "app.webapp.auth_router.create_challenge_async",
        AsyncMock(return_value=(challenge, "482913")),
    )

    async def _fail(self, *, user, code, purpose):
        raise DeliveryUnavailable("falha simulada")

    monkeypatch.setattr(
        "app.authentication.delivery.TelegramDeliveryProvider.deliver", _fail
    )

    response = client.post(
        "/api/v1/auth/password-recovery/request",
        json={"identifier": "cliente", "channel": "telegram"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "delivery_unavailable"


def test_recovery_request_rate_limited_is_429(
    client: TestClient, async_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user(telegram_user_id=555, telegram_chat_id=555)
    async_session.scalar = AsyncMock(return_value=user)

    async def _fail(*a: object, **k: object) -> None:
        raise ChallengeRateLimited("Tente novamente mais tarde.")

    monkeypatch.setattr("app.webapp.auth_router.create_challenge_async", _fail)

    response = client.post(
        "/api/v1/auth/password-recovery/request",
        json={"identifier": "cliente", "channel": "telegram"},
    )
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "recovery_rate_limited"


def test_recovery_confirm_password_mismatch_is_422(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/password-recovery/confirm",
        json={
            "challenge_id": str(uuid4()),
            "code": "482913",
            "new_password": "Senha#Nova123",
            "new_password_confirmation": "Outra#Coisa123",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "password_confirmation_mismatch"


def test_recovery_confirm_invalid_code_is_422_never_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fail(*a: object, **k: object) -> None:
        raise ChallengeInvalid("Código inválido ou expirado.")

    monkeypatch.setattr("app.webapp.auth_router.confirm_challenge_async", _fail)

    response = client.post(
        "/api/v1/auth/password-recovery/confirm",
        json={
            "challenge_id": str(uuid4()),
            "code": "000000",
            "new_password": "Senha#Nova123",
            "new_password_confirmation": "Senha#Nova123",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "challenge_invalid"


def test_recovery_confirm_success_sets_new_password(
    client: TestClient, async_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user()
    challenge = _challenge(user_id=user.id)
    monkeypatch.setattr(
        "app.webapp.auth_router.confirm_challenge_async",
        AsyncMock(return_value=challenge),
    )
    async_session.get = AsyncMock(return_value=user)
    set_password = AsyncMock()
    monkeypatch.setattr("app.webapp.auth_router.set_new_password_async", set_password)

    response = client.post(
        "/api/v1/auth/password-recovery/confirm",
        json={
            "challenge_id": str(challenge.id),
            "code": "482913",
            "new_password": "Senha#Nova123",
            "new_password_confirmation": "Senha#Nova123",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    set_password.assert_awaited_once()


def test_recovery_confirm_weak_password_is_422_and_never_burns_challenge(
    client: TestClient, async_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user()
    challenge = _challenge(user_id=user.id)
    monkeypatch.setattr(
        "app.webapp.auth_router.confirm_challenge_async",
        AsyncMock(return_value=challenge),
    )
    async_session.get = AsyncMock(return_value=user)

    async def _fail(*a: object, **k: object) -> None:
        raise PasswordPolicyError("Essa senha é muito comum ou previsível.")

    monkeypatch.setattr("app.webapp.auth_router.set_new_password_async", _fail)

    response = client.post(
        "/api/v1/auth/password-recovery/confirm",
        json={
            "challenge_id": str(challenge.id),
            "code": "482913",
            "new_password": "senha123",
            "new_password_confirmation": "senha123",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "weak_password"
    async_session.commit.assert_not_awaited()  # nunca comita o used_at aqui


# --- Verificação de e-mail (autenticada) ----------------------------------


def test_verification_request_requires_authentication(client: TestClient) -> None:
    response = client.post("/api/v1/auth/verification/request")
    assert response.status_code == 401


def test_verification_request_without_email_is_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user",
        lambda *a, **k: _user(email=None),
    )
    response = client.post(
        "/api/v1/auth/verification/request",
        cookies=_authenticated_cookies(),
        headers=_csrf_headers(),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "email_missing"


def test_verification_request_without_provider_is_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user", lambda *a, **k: _user()
    )
    response = client.post(
        "/api/v1/auth/verification/request",
        cookies=_authenticated_cookies(),
        headers=_csrf_headers(),
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "email_delivery_unavailable"


def test_verification_confirm_wrong_owner_is_422(
    client: TestClient, async_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = _user()
    challenge = _challenge(
        user_id=uuid4(),  # dono diferente de quem está autenticado
        purpose=VerificationPurpose.EMAIL_VERIFICATION,
        channel=VerificationChannel.EMAIL,
    )
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user", lambda *a, **k: actor
    )
    monkeypatch.setattr(
        "app.webapp.auth_router.confirm_challenge_async",
        AsyncMock(return_value=challenge),
    )

    response = client.post(
        "/api/v1/auth/verification/confirm",
        json={"challenge_id": str(challenge.id), "code": "482913"},
        cookies=_authenticated_cookies(),
        headers=_csrf_headers(),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "challenge_invalid"


def test_verification_confirm_success_marks_email_verified(
    client: TestClient, async_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = _user()
    challenge = _challenge(
        user_id=actor.id,
        purpose=VerificationPurpose.EMAIL_VERIFICATION,
        channel=VerificationChannel.EMAIL,
        used_at=datetime.now(UTC),
    )
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user", lambda *a, **k: actor
    )
    monkeypatch.setattr(
        "app.webapp.auth_router.confirm_challenge_async",
        AsyncMock(return_value=challenge),
    )

    response = client.post(
        "/api/v1/auth/verification/confirm",
        json={"challenge_id": str(challenge.id), "code": "482913"},
        cookies=_authenticated_cookies(),
        headers=_csrf_headers(),
    )
    assert response.status_code == 200
    assert actor.email_verified_at == challenge.used_at


# --- Pepper de HMAC ausente (validação de segurança) ----------------------


def _client_without_pepper() -> TestClient:
    """`Settings.verification_code_pepper` não configurado -- nunca pode
    cair para um hash sem segredo; precisa recusar com 503 claro."""
    app = _build_app()
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    app.dependency_overrides[get_web_async_session] = lambda: MagicMock()
    return TestClient(app)


def test_recovery_request_without_pepper_is_503_for_real_and_fake_identifier() -> None:
    """Mesmo status para identificador real e inexistente -- a checagem do
    pepper roda ANTES do branch de decoy, então uma má configuração nunca
    vira um oráculo de enumeração (200 decoy vs. 503 real)."""
    client = _client_without_pepper()

    real = client.post(
        "/api/v1/auth/password-recovery/request",
        json={"identifier": "cliente", "channel": "email"},
    )
    fake = client.post(
        "/api/v1/auth/password-recovery/request",
        json={"identifier": "ninguem-existe", "channel": "email"},
    )

    assert real.status_code == fake.status_code == 503
    assert (
        real.json()["error"]["code"]
        == fake.json()["error"]["code"]
        == "verification_unavailable"
    )


def test_recovery_confirm_without_pepper_is_503() -> None:
    client = _client_without_pepper()

    response = client.post(
        "/api/v1/auth/password-recovery/confirm",
        json={
            "challenge_id": str(uuid4()),
            "code": "482913",
            "new_password": "Senha#Nova123",
            "new_password_confirmation": "Senha#Nova123",
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "verification_unavailable"
