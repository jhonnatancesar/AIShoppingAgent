"""Contrato HTTP dos endpoints de sessão web (TASK-091, item 1 da V1.2).

Monta uma app FastAPI mínima (só o router + o handler de erro) para
testar o contrato real via `TestClient`, sem depender de segredos/config
completos da aplicação inteira -- `get_session`/`get_settings` são
substituídos por dependências de teste."""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.authentication.service import AuthenticationError, AuthenticationRateLimited
from app.core.config import Settings, get_settings
from app.core.errors import register_api_error_handler
from app.database.dependency import get_session
from app.users.models import User, UserRole
from app.webapp.csrf import CSRF_COOKIE_NAME
from app.webapp.dependency import WEB_SESSION_COOKIE_NAME
from app.webapp.router import _SESSION_TTL_SECONDS, router
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

_CSRF_TOKEN = "csrf-canary-token"


def _build_app() -> FastAPI:
    app = FastAPI()
    register_api_error_handler(app)
    app.include_router(router)
    return app


def _user() -> User:
    return User(
        id=uuid4(),
        display_name="Cliente Teste",
        role=UserRole.USER,
        username="cliente",
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = _build_app()
    app.dependency_overrides[get_session] = lambda: MagicMock()
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_password="unused", _env_file=None
    )
    return TestClient(app)


def _csrf_cookies(**extra: str) -> dict[str, str]:
    return {CSRF_COOKIE_NAME: _CSRF_TOKEN, **extra}


def _csrf_headers() -> dict[str, str]:
    return {"X-CSRF-Token": _CSRF_TOKEN}


def test_login_success_sets_httponly_cookie_and_returns_user(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user()
    monkeypatch.setattr(
        "app.webapp.router.authenticate_web_login", lambda *a, **k: user
    )
    monkeypatch.setattr(
        "app.webapp.router.issue_web_session", lambda *a, **k: "raw-token-canary"
    )

    response = client.post(
        "/api/v1/web-sessions",
        json={"username": "cliente", "password": "Senha#123"},
        cookies=_csrf_cookies(),
        headers=_csrf_headers(),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == str(user.id)
    assert body["display_name"] == "Cliente Teste"
    assert body["role"] == "USER"
    cookie = response.cookies.get(WEB_SESSION_COOKIE_NAME)
    assert cookie == "raw-token-canary"
    set_cookie_header = response.headers.get("set-cookie", "")
    assert "HttpOnly" in set_cookie_header
    assert "samesite=lax" in set_cookie_header.lower()
    assert "path=/" in set_cookie_header.lower()
    assert f"max-age={_SESSION_TTL_SECONDS}" in set_cookie_header.lower()
    assert "raw-token-canary" not in response.text  # token não vaza no corpo JSON


def test_login_success_does_not_mark_session_cookie_secure_in_development(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Secure` é controlado por `settings.environment`, nunca por um valor
    fixo no código -- em `development` (HTTP local) o navegador descartaria
    silenciosamente um cookie `Secure` servido fora de HTTPS."""
    user = _user()
    monkeypatch.setattr(
        "app.webapp.router.authenticate_web_login", lambda *a, **k: user
    )
    monkeypatch.setattr(
        "app.webapp.router.issue_web_session", lambda *a, **k: "raw-token-canary"
    )

    response = client.post(
        "/api/v1/web-sessions",
        json={"username": "cliente", "password": "Senha#123"},
        cookies=_csrf_cookies(),
        headers=_csrf_headers(),
    )

    session_cookie_header = next(
        value
        for key, value in response.headers.multi_items()
        if key.lower() == "set-cookie"
        and value.startswith(f"{WEB_SESSION_COOKIE_NAME}=")
    )
    assert "secure" not in session_cookie_header.lower()


def test_login_success_marks_session_cookie_secure_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `tempfile.mkdtemp` em vez do `tmp_path` do pytest -- `pytest-of-User`
    # pode ficar bloqueado por ACL nesta máquina Windows (achado documentado,
    # não é bug de código; mesmo padrão de `tests/test_webapp_spa.py`).
    secret_dir = Path(tempfile.mkdtemp(prefix="aishopping-webapp-router-"))
    try:
        secret_file = secret_dir / "postgres_password"
        secret_file.write_text("producao-secreta\n", encoding="utf-8")
        app = _build_app()
        app.dependency_overrides[get_session] = lambda: MagicMock()
        app.dependency_overrides[get_settings] = lambda: Settings(
            database_password_file=secret_file,
            environment="production",
            auth_public_base_url="https://app.exemplo.com.br",
            _env_file=None,
        )
        client = TestClient(app)
        user = _user()
        monkeypatch.setattr(
            "app.webapp.router.authenticate_web_login", lambda *a, **k: user
        )
        monkeypatch.setattr(
            "app.webapp.router.issue_web_session", lambda *a, **k: "raw-token-canary"
        )

        response = client.post(
            "/api/v1/web-sessions",
            json={"username": "cliente", "password": "Senha#123"},
            cookies=_csrf_cookies(),
            headers=_csrf_headers(),
        )

        session_cookie_header = next(
            value
            for key, value in response.headers.multi_items()
            if key.lower() == "set-cookie"
            and value.startswith(f"{WEB_SESSION_COOKIE_NAME}=")
        )
        assert "secure" in session_cookie_header.lower()
    finally:
        shutil.rmtree(secret_dir, ignore_errors=True)


def test_login_success_rotates_csrf_cookie_and_it_is_not_httponly(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user()
    monkeypatch.setattr(
        "app.webapp.router.authenticate_web_login", lambda *a, **k: user
    )
    monkeypatch.setattr(
        "app.webapp.router.issue_web_session", lambda *a, **k: "raw-token-canary"
    )

    response = client.post(
        "/api/v1/web-sessions",
        json={"username": "cliente", "password": "Senha#123"},
        cookies=_csrf_cookies(),
        headers=_csrf_headers(),
    )

    csrf_cookie_header = next(
        value
        for key, value in response.headers.multi_items()
        if key.lower() == "set-cookie" and value.startswith(f"{CSRF_COOKIE_NAME}=")
    )
    new_csrf_value = response.cookies.get(CSRF_COOKIE_NAME)
    assert new_csrf_value is not None
    assert new_csrf_value != _CSRF_TOKEN  # girou, não reaproveitou o pré-login
    assert "httponly" not in csrf_cookie_header.lower()  # SPA precisa ler


def test_login_without_csrf_cookie_is_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.webapp.router.authenticate_web_login", lambda *a, **k: _user()
    )

    response = client.post(
        "/api/v1/web-sessions",
        json={"username": "cliente", "password": "Senha#123"},
        headers=_csrf_headers(),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_invalid"


def test_login_with_mismatched_csrf_header_is_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.webapp.router.authenticate_web_login", lambda *a, **k: _user()
    )

    response = client.post(
        "/api/v1/web-sessions",
        json={"username": "cliente", "password": "Senha#123"},
        cookies=_csrf_cookies(),
        headers={"X-CSRF-Token": "token-diferente"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_invalid"


def test_login_without_csrf_header_is_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.webapp.router.authenticate_web_login", lambda *a, **k: _user()
    )

    response = client.post(
        "/api/v1/web-sessions",
        json={"username": "cliente", "password": "Senha#123"},
        cookies=_csrf_cookies(),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_invalid"


def test_login_rejects_wrong_credentials_with_stable_envelope(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(*args: object, **kwargs: object) -> None:
        raise AuthenticationError("detalhe interno nunca exposto")

    monkeypatch.setattr("app.webapp.router.authenticate_web_login", _fail)

    response = client.post(
        "/api/v1/web-sessions",
        json={"username": "cliente", "password": "errada"},
        cookies=_csrf_cookies(),
        headers=_csrf_headers(),
    )

    assert response.status_code == 401
    body = response.json()
    assert body == {
        "error": {
            "code": "invalid_credentials",
            "message": "Usuário ou senha inválidos.",
            "details": None,
        }
    }
    assert "detalhe interno" not in response.text
    assert WEB_SESSION_COOKIE_NAME not in response.cookies


def test_login_rate_limited_returns_429(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(*args: object, **kwargs: object) -> None:
        raise AuthenticationRateLimited("bloqueado")

    monkeypatch.setattr("app.webapp.router.authenticate_web_login", _fail)

    response = client.post(
        "/api/v1/web-sessions",
        json={"username": "cliente", "password": "errada"},
        cookies=_csrf_cookies(),
        headers=_csrf_headers(),
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "login_rate_limited"


def test_login_rejects_unknown_extra_fields(client: TestClient) -> None:
    response = client.post(
        "/api/v1/web-sessions",
        json={"username": "cliente", "password": "Senha#123", "role": "dev"},
        cookies=_csrf_cookies(),
        headers=_csrf_headers(),
    )

    assert response.status_code == 422


def test_get_current_session_without_cookie_is_401(client: TestClient) -> None:
    response = client.get("/api/v1/web-sessions/current")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


def test_get_current_session_with_valid_cookie_returns_user(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user()
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user", lambda *a, **k: user
    )

    response = client.get(
        "/api/v1/web-sessions/current",
        cookies={WEB_SESSION_COOKIE_NAME: "raw-token-canary"},
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(user.id)


def test_logout_revokes_session_and_clears_cookie(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Logout depende de `require_web_session` como qualquer outro
    endpoint do canal web (`DEC-074`) -- precisa de sessão válida (aqui
    simulada via mock de `get_web_session_user`) para chegar ao handler."""
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user", lambda *a, **k: _user()
    )
    revoked: list[str] = []
    monkeypatch.setattr(
        "app.webapp.router.revoke_web_session",
        lambda session, *, raw_token, **k: revoked.append(raw_token),
    )

    response = client.delete(
        "/api/v1/web-sessions/current",
        cookies=_csrf_cookies(**{WEB_SESSION_COOKIE_NAME: "raw-token-canary"}),
        headers=_csrf_headers(),
    )

    assert response.status_code == 204
    assert revoked == ["raw-token-canary"]
    assert WEB_SESSION_COOKIE_NAME not in response.cookies
    assert CSRF_COOKIE_NAME not in response.cookies


def test_logout_without_a_valid_session_is_401_not_csrf_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Correção da 3ª rodada de endurecimento: logout sem sessão válida é
    erro de autenticação, nunca `csrf_invalid` -- a sessão é resolvida
    antes de qualquer checagem de CSRF (ponto 8)."""
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user", lambda *a, **k: None
    )
    revoked: list[str] = []
    monkeypatch.setattr(
        "app.webapp.router.revoke_web_session",
        lambda session, *, raw_token, **k: revoked.append(raw_token),
    )

    response = client.delete("/api/v1/web-sessions/current")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"
    assert revoked == []


def test_logout_without_csrf_is_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user", lambda *a, **k: _user()
    )
    revoked: list[str] = []
    monkeypatch.setattr(
        "app.webapp.router.revoke_web_session",
        lambda session, *, raw_token, **k: revoked.append(raw_token),
    )

    response = client.delete(
        "/api/v1/web-sessions/current",
        cookies={WEB_SESSION_COOKIE_NAME: "raw-token-canary"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_invalid"
    assert revoked == []  # nunca chega a revogar -- CSRF barra antes


def test_logout_with_invalid_csrf_is_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user", lambda *a, **k: _user()
    )
    revoked: list[str] = []
    monkeypatch.setattr(
        "app.webapp.router.revoke_web_session",
        lambda session, *, raw_token, **k: revoked.append(raw_token),
    )

    response = client.delete(
        "/api/v1/web-sessions/current",
        cookies=_csrf_cookies(**{WEB_SESSION_COOKIE_NAME: "raw-token-canary"}),
        headers={"X-CSRF-Token": "token-diferente"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_invalid"
    assert revoked == []


def test_get_current_session_does_not_require_csrf(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Método seguro (`GET`) nunca exige CSRF -- só operações mutáveis."""
    user = _user()
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user", lambda *a, **k: user
    )

    response = client.get(
        "/api/v1/web-sessions/current",
        cookies={WEB_SESSION_COOKIE_NAME: "raw-token-canary"},
    )

    assert response.status_code == 200


def test_csrf_pair_is_checked_before_authentication_is_attempted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CSRF é a primeira barreira: credenciais nunca são verificadas se o
    par CSRF já falhou (evita gastar trabalho de verificação de senha em
    requisições forjadas)."""
    called: list[bool] = []

    def _spy(*args: object, **kwargs: object) -> User:
        called.append(True)
        return _user()

    monkeypatch.setattr("app.webapp.router.authenticate_web_login", _spy)

    response = client.post(
        "/api/v1/web-sessions", json={"username": "cliente", "password": "Senha#123"}
    )

    assert response.status_code == 403
    assert called == []


def test_nonexistent_mutating_route_is_404_not_csrf_403(client: TestClient) -> None:
    """CSRF nunca é uma checagem global de `/api/v1` -- é propriedade da
    dependência `require_web_session`/`validate_csrf` de cada rota. Uma
    rota que não existe nunca chega a nenhuma dependência (FastAPI
    resolve `404` antes disso), então nunca pode ser mascarada como falha
    de CSRF."""
    response = client.patch("/api/v1/rota-inexistente", headers=_csrf_headers())

    assert response.status_code == 404


def test_mutations_outside_the_webapp_router_are_not_affected_by_csrf() -> None:
    """CSRF protege a autenticação por `WebSession` (`require_web_session`/
    `validate_csrf`), não é uma regra global deste router nem de
    `/api/v1` -- um endpoint mutável autenticado por outro mecanismo (aqui
    simulando Bearer/service token, como o webhook real do Telegram usa um
    segredo de header em vez de cookie) nunca é afetado, mesmo montado na
    mesma aplicação que `router`. Ver `tests/test_webapp_dependency.py`
    para a prova arquitetural completa (CSRF acompanha a dependência, não
    o router)."""
    app = FastAPI()
    register_api_error_handler(app)
    other_router = APIRouter()

    @other_router.post("/telegram/webhook")
    def _fake_bearer_authenticated_endpoint() -> dict[str, bool]:
        return {"ok": True}

    app.include_router(other_router)
    app.include_router(router)  # o router protegido continua montado junto
    client = TestClient(app)

    response = client.post("/telegram/webhook", json={})

    assert response.status_code == 200
    assert response.json() == {"ok": True}
