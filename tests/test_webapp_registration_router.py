"""Contrato HTTP do cadastro Web self-service (Subtask 9, auditoria GG
Oferta) -- mesmo padrão de `tests/test_webapp_router.py`: app FastAPI
mínima, `get_session`/`get_settings` substituídos, CSRF exercitado de
verdade (`dependencies=[Depends(validate_csrf)]`, mesmo mecanismo do
login)."""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.authentication.passwords import PasswordPolicyError
from app.core.config import Settings, get_settings
from app.core.errors import register_api_error_handler
from app.database.dependency import get_session
from app.users.models import User, UserRole
from app.users.registration import RegistrationError
from app.users.service import UserAlreadyExistsError
from app.webapp.csrf import CSRF_COOKIE_NAME
from app.webapp.dependency import WEB_SESSION_COOKIE_NAME
from app.webapp.registration_router import router
from fastapi import FastAPI
from fastapi.testclient import TestClient

_CSRF_TOKEN = "csrf-canary-token"


@pytest.fixture(autouse=True)
def _reset_registration_rate_limit() -> None:
    """O limitador é um singleton por processo (mesma filosofia de
    `app.core.resilience.CIRCUITS`) -- sem reset, todos os testes deste
    arquivo compartilhariam a mesma janela (o `TestClient` usa sempre o
    mesmo IP fixo), e os últimos passariam a receber 429 dos anteriores."""
    from app.webapp.registration_router import _registration_rate_limiter

    with _registration_rate_limiter._lock:
        _registration_rate_limiter._attempts.clear()


def _build_app() -> FastAPI:
    app = FastAPI()
    register_api_error_handler(app)
    app.include_router(router)
    return app


def _user(**overrides: object) -> User:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        display_name="clientenovo",
        role=UserRole.USER,
        username="clientenovo",
        email="cliente@example.com",
    )
    defaults.update(overrides)
    return User(**defaults)


@pytest.fixture
def client() -> TestClient:
    app = _build_app()
    app.dependency_overrides[get_session] = lambda: MagicMock()
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_password="unused", _env_file=None
    )
    return TestClient(app)


def _cookies(**extra: str) -> dict[str, str]:
    return {CSRF_COOKIE_NAME: _CSRF_TOKEN, **extra}


def _csrf_headers() -> dict[str, str]:
    return {"X-CSRF-Token": _CSRF_TOKEN}


def _payload(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "username": "clientenovo",
        "email": "cliente@example.com",
        "password": "Senha#Forte123",
        "password_confirmation": "Senha#Forte123",
    }
    defaults.update(overrides)
    return defaults


def test_register_without_csrf_is_403(client: TestClient) -> None:
    response = client.post("/api/v1/users/register", json=_payload())
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_invalid"


def test_register_success_creates_session_and_returns_user(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = _user()
    monkeypatch.setattr(
        "app.webapp.registration_router.create_user_with_password",
        lambda *a, **k: created,
    )
    monkeypatch.setattr(
        "app.webapp.registration_router.issue_web_session",
        lambda *a, **k: "raw-token-canary",
    )

    response = client.post(
        "/api/v1/users/register", json=_payload(), cookies=_cookies(), headers=_csrf_headers()
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == str(created.id)
    assert body["role"] == "USER"
    assert response.cookies.get(WEB_SESSION_COOKIE_NAME) == "raw-token-canary"
    assert "raw-token-canary" not in response.text


def test_register_role_is_always_user_regardless_of_payload(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`extra=\"forbid\"` rejeita o campo antes mesmo do handler rodar --
    o cliente não consegue nem tentar influenciar o role."""
    response = client.post(
        "/api/v1/users/register",
        json=_payload(role="ADMIN"),
        cookies=_cookies(),
        headers=_csrf_headers(),
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "extra_field", ["role", "permissions", "user_id", "status", "is_active"]
)
def test_register_rejects_any_administrative_field(
    client: TestClient, extra_field: str
) -> None:
    response = client.post(
        "/api/v1/users/register",
        json=_payload(**{extra_field: "qualquer_valor"}),
        cookies=_cookies(),
        headers=_csrf_headers(),
    )
    assert response.status_code == 422


def test_register_password_confirmation_mismatch_is_422(client: TestClient) -> None:
    response = client.post(
        "/api/v1/users/register",
        json=_payload(password_confirmation="Outra#Senha123"),
        cookies=_cookies(),
        headers=_csrf_headers(),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "password_confirmation_mismatch"


def test_register_invalid_email_is_422(client: TestClient) -> None:
    response = client.post(
        "/api/v1/users/register",
        json=_payload(email="nao-e-email"),
        cookies=_cookies(),
        headers=_csrf_headers(),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_email"


def test_register_weak_password_is_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(*a: object, **k: object) -> None:
        raise PasswordPolicyError("Essa senha é muito comum ou previsível.")

    monkeypatch.setattr(
        "app.webapp.registration_router.create_user_with_password", _fail
    )

    response = client.post(
        "/api/v1/users/register",
        json=_payload(password="senha123", password_confirmation="senha123"),
        cookies=_cookies(),
        headers=_csrf_headers(),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "registration_invalid"


def test_register_invalid_username_is_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(*a: object, **k: object) -> None:
        raise RegistrationError("O nome de usuário não pode conter espaços.")

    monkeypatch.setattr(
        "app.webapp.registration_router.create_user_with_password", _fail
    )

    response = client.post(
        "/api/v1/users/register", json=_payload(), cookies=_cookies(), headers=_csrf_headers()
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "registration_invalid"


@pytest.mark.parametrize("field", ["username", "email"])
def test_register_duplicate_returns_conflict_never_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    def _fail(*a: object, **k: object) -> None:
        raise UserAlreadyExistsError(field)

    monkeypatch.setattr(
        "app.webapp.registration_router.create_user_with_password", _fail
    )

    response = client.post(
        "/api/v1/users/register", json=_payload(), cookies=_cookies(), headers=_csrf_headers()
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == f"{field}_taken"


# --- Rate limit (validação de segurança) ----------------------------------


def test_register_under_the_limit_keeps_working_normally(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.webapp.registration_router.create_user_with_password",
        lambda *a, **k: _user(),
    )
    monkeypatch.setattr(
        "app.webapp.registration_router.issue_web_session",
        lambda *a, **k: "raw-token-canary",
    )

    for _ in range(5):  # LOGIN_FAILURE_LIMIT, mesmo limite reaproveitado
        response = client.post(
            "/api/v1/users/register",
            json=_payload(),
            cookies=_cookies(),
            headers=_csrf_headers(),
        )
        assert response.status_code == 201


def test_register_exceeding_the_limit_is_429_never_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.webapp.registration_router.create_user_with_password",
        lambda *a, **k: _user(),
    )
    monkeypatch.setattr(
        "app.webapp.registration_router.issue_web_session",
        lambda *a, **k: "raw-token-canary",
    )

    for _ in range(5):
        client.post(
            "/api/v1/users/register",
            json=_payload(),
            cookies=_cookies(),
            headers=_csrf_headers(),
        )
    response = client.post(
        "/api/v1/users/register", json=_payload(), cookies=_cookies(), headers=_csrf_headers()
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "registration_rate_limited"


def test_register_rate_limit_counts_failed_attempts_too(client: TestClient) -> None:
    """Conta toda tentativa (sucesso ou falha) -- senão bastaria variar um
    campo inválido entre tentativas reais para nunca esgotar a janela."""
    for _ in range(5):
        client.post(
            "/api/v1/users/register",
            json=_payload(email="nao-e-email"),
            cookies=_cookies(),
            headers=_csrf_headers(),
        )
    response = client.post(
        "/api/v1/users/register",
        json=_payload(email="nao-e-email"),
        cookies=_cookies(),
        headers=_csrf_headers(),
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "registration_rate_limited"


def test_register_rate_limit_is_per_ip_not_global(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Duas origens diferentes não compartilham a mesma janela."""
    from app.webapp.registration_router import _registration_rate_limiter

    monkeypatch.setattr(
        "app.webapp.registration_router.create_user_with_password",
        lambda *a, **k: _user(),
    )
    monkeypatch.setattr(
        "app.webapp.registration_router.issue_web_session",
        lambda *a, **k: "raw-token-canary",
    )
    for _ in range(5):
        _registration_rate_limiter.check("203.0.113.10")

    response = client.post(
        "/api/v1/users/register", json=_payload(), cookies=_cookies(), headers=_csrf_headers()
    )

    assert response.status_code == 201


# --- Rate limit atrás do Cloudflare Tunnel (validação de segurança) -------
#
# PROD roda atrás de um Cloudflare Tunnel: `request.client.host` chega
# sempre como `127.0.0.1` (confirmado ao vivo contra um túnel `cloudflared`
# real -- ver `app.webapp.client_ip`). Os testes abaixo simulam esse peer
# via `TestClient(client=("127.0.0.1", ...))` e enviam `CF-Connecting-IP`
# como o Cloudflare de fato envia, provando que o rate limit volta a ser
# por-visitante-real nesse caminho.


def _client_from(peer_host: str) -> TestClient:
    app = _build_app()
    app.dependency_overrides[get_session] = lambda: MagicMock()
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_password="unused", _env_file=None
    )
    return TestClient(app, client=(peer_host, 0))


def _mock_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.webapp.registration_router.create_user_with_password",
        lambda *a, **k: _user(),
    )
    monkeypatch.setattr(
        "app.webapp.registration_router.issue_web_session",
        lambda *a, **k: "raw-token-canary",
    )


def test_registration_via_tunnel_same_visitor_shares_one_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mesmo visitante real (mesmo CF-Connecting-IP), várias requisições
    através do túnel -- todas caem no mesmo bucket, então a 6ª (acima do
    limite reaproveitado de LOGIN_FAILURE_LIMIT=5) é 429."""
    _mock_registration(monkeypatch)
    client = _client_from("127.0.0.1")
    headers = {**_csrf_headers(), "CF-Connecting-IP": "198.51.100.7"}

    for _ in range(5):
        response = client.post(
            "/api/v1/users/register", json=_payload(), cookies=_cookies(), headers=headers
        )
        assert response.status_code == 201

    sixth = client.post(
        "/api/v1/users/register", json=_payload(), cookies=_cookies(), headers=headers
    )
    assert sixth.status_code == 429
    assert sixth.json()["error"]["code"] == "registration_rate_limited"


def test_registration_via_tunnel_different_visitors_get_different_buckets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O requisito central desta correção: um visitante esgotando seu
    próprio bucket nunca impede outro visitante real de se cadastrar --
    nunca derruba o endpoint inteiro para 429."""
    _mock_registration(monkeypatch)
    client = _client_from("127.0.0.1")
    attacker_headers = {**_csrf_headers(), "CF-Connecting-IP": "198.51.100.66"}
    victim_headers = {**_csrf_headers(), "CF-Connecting-IP": "198.51.100.77"}

    for _ in range(5):
        client.post(
            "/api/v1/users/register",
            json=_payload(),
            cookies=_cookies(),
            headers=attacker_headers,
        )
    blocked = client.post(
        "/api/v1/users/register", json=_payload(), cookies=_cookies(), headers=attacker_headers
    )
    assert blocked.status_code == 429

    still_works = client.post(
        "/api/v1/users/register", json=_payload(), cookies=_cookies(), headers=victim_headers
    )
    assert still_works.status_code == 201


def test_registration_via_tunnel_spoofed_x_forwarded_for_does_not_bypass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cadeia igual à observada ao vivo contra o Cloudflare real: o valor
    que o cliente manda é preservado e o Cloudflare ANEXA o IP real ao
    final -- só o último elemento conta, então forjar o primeiro não
    troca de bucket nem burla o limite."""
    _mock_registration(monkeypatch)
    client = _client_from("127.0.0.1")

    for spoofed_prefix in range(5):
        headers = {
            **_csrf_headers(),
            "X-Forwarded-For": f"{spoofed_prefix}.0.0.1, 198.51.100.200",
        }
        response = client.post(
            "/api/v1/users/register", json=_payload(), cookies=_cookies(), headers=headers
        )
        assert response.status_code == 201

    blocked = client.post(
        "/api/v1/users/register",
        json=_payload(),
        cookies=_cookies(),
        headers={**_csrf_headers(), "X-Forwarded-For": "999.0.0.1, 198.51.100.200"},
    )
    assert blocked.status_code == 429


def test_registration_direct_non_loopback_peer_ignores_forwarded_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Se a conexão não vier do loopback, o CF-Connecting-IP forjado é
    ignorado -- todas as tentativas contam para o mesmo peer bruto, nunca
    permitindo trocar de bucket só enviando um header diferente."""
    _mock_registration(monkeypatch)
    client = _client_from("203.0.113.55")

    for _ in range(5):
        response = client.post(
            "/api/v1/users/register",
            json=_payload(),
            cookies=_cookies(),
            headers={**_csrf_headers(), "CF-Connecting-IP": "1.1.1.1"},
        )
        assert response.status_code == 201

    blocked = client.post(
        "/api/v1/users/register",
        json=_payload(),
        cookies=_cookies(),
        headers={**_csrf_headers(), "CF-Connecting-IP": "2.2.2.2"},
    )
    assert blocked.status_code == 429
