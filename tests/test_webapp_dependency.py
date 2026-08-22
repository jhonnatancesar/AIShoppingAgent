"""Testes de `app.webapp.dependency` (TASK-091, endurecimento -- 3ª
rodada de revisão do usuário).

Duas versões anteriores do CSRF foram corrigidas antes desta: um
middleware genérico sobre `/api/v1/*` (amplo demais) e uma dependência no
nível do router de `web-sessions` (estreita demais -- só protegeria
endpoints daquele router). O desenho final amarra CSRF a
`require_web_session`, a dependência pública de autenticação por cookie
-- qualquer endpoint, de qualquer router, que dependa dela herda a
proteção. Esta suíte prova exatamente isso, montando um router
propositalmente sem relação nenhuma com `app.webapp.router`."""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.core.errors import register_api_error_handler
from app.database.dependency import get_session
from app.users.models import User, UserRole
from app.webapp.csrf import CSRF_COOKIE_NAME
from app.webapp.dependency import (
    WEB_SESSION_COOKIE_NAME,
    require_admin_web_session,
    require_web_session,
)
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

_CSRF_TOKEN = "csrf-canary-token"


def _user(*, role: UserRole = UserRole.USER) -> User:
    return User(id=uuid4(), display_name="Cliente", role=role, username="cliente")


def _csrf_cookies(**extra: str) -> dict[str, str]:
    return {CSRF_COOKIE_NAME: _CSRF_TOKEN, **extra}


def _csrf_headers() -> dict[str, str]:
    return {"X-CSRF-Token": _CSRF_TOKEN}


def _build_missions_like_app() -> FastAPI:
    """Router B: um domínio completamente diferente (missões), só para
    provar que a proteção não depende de estar no router de sessão."""
    app = FastAPI()
    register_api_error_handler(app)
    app.dependency_overrides[get_session] = lambda: MagicMock()
    missions_like_router = APIRouter()

    @missions_like_router.get("/missions-like/{mission_id}")
    def _read_mission_like(
        mission_id: str, _user: User = Depends(require_web_session)
    ) -> dict[str, str]:
        return {"mission_id": mission_id, "action": "read"}

    @missions_like_router.patch("/missions-like/{mission_id}")
    def _update_mission_like(
        mission_id: str, _user: User = Depends(require_web_session)
    ) -> dict[str, str]:
        return {"mission_id": mission_id, "action": "update"}

    @missions_like_router.post("/missions-like")
    def _create_mission_like(
        _user: User = Depends(require_web_session),
    ) -> dict[str, str]:
        return {"action": "create"}

    @missions_like_router.put("/missions-like/{mission_id}")
    def _replace_mission_like(
        mission_id: str, _user: User = Depends(require_web_session)
    ) -> dict[str, str]:
        return {"mission_id": mission_id, "action": "replace"}

    @missions_like_router.delete("/missions-like/{mission_id}")
    def _delete_mission_like(
        mission_id: str, _user: User = Depends(require_web_session)
    ) -> dict[str, str]:
        return {"mission_id": mission_id, "action": "delete"}

    @missions_like_router.patch("/admin-like/{resource_id}")
    def _update_admin_like(
        resource_id: str, _user: User = Depends(require_admin_web_session)
    ) -> dict[str, str]:
        return {"resource_id": resource_id}

    app.include_router(missions_like_router)
    return app


@pytest.fixture
def missions_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user", lambda *a, **k: _user()
    )
    return TestClient(_build_missions_like_app())


def test_csrf_follows_require_web_session_regardless_of_router(
    missions_client: TestClient,
) -> None:
    """Ponto 12 do endurecimento: `PATCH` num router que nada tem a ver
    com sessão web, mas autenticado por `Depends(require_web_session)`,
    exige CSRF do mesmo jeito que o login exige."""
    without_csrf = missions_client.patch(
        "/missions-like/123", cookies={WEB_SESSION_COOKIE_NAME: "raw-token"}
    )
    with_csrf = missions_client.patch(
        "/missions-like/123",
        cookies=_csrf_cookies(**{WEB_SESSION_COOKIE_NAME: "raw-token"}),
        headers=_csrf_headers(),
    )

    assert without_csrf.status_code == 403
    assert without_csrf.json()["error"]["code"] == "csrf_invalid"
    assert with_csrf.status_code == 200
    assert with_csrf.json() == {"mission_id": "123", "action": "update"}


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_every_mutating_method_requires_csrf_when_web_session_authenticated(
    missions_client: TestClient, method: str
) -> None:
    path = "/missions-like" if method == "post" else "/missions-like/123"

    response = missions_client.request(
        method.upper(), path, cookies={WEB_SESSION_COOKIE_NAME: "raw-token"}
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_invalid"


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_every_mutating_method_is_accepted_with_valid_csrf(
    missions_client: TestClient, method: str
) -> None:
    path = "/missions-like" if method == "post" else "/missions-like/123"

    response = missions_client.request(
        method.upper(),
        path,
        cookies=_csrf_cookies(**{WEB_SESSION_COOKIE_NAME: "raw-token"}),
        headers=_csrf_headers(),
    )

    assert response.status_code == 200


def _fake_request(*, method: str) -> Request:
    """`GET /web-sessions/current` já prova `GET` via HTTP real; `HEAD` e
    `OPTIONS` não têm nenhuma rota real no app hoje (nenhum router declara
    `methods=["...", "HEAD"]`), então testados diretamente na função, sem
    inventar uma rota só para o teste."""
    scope = {
        "type": "http",
        "method": method,
        "headers": [],
        "path": "/missions-like/123",
        "query_string": b"",
    }
    return Request(scope)


def test_require_web_session_skips_csrf_for_head_and_options() -> None:
    user = _user()

    for method in ("HEAD", "OPTIONS"):
        result = require_web_session(_fake_request(method=method), user=user)
        assert result is user


def test_get_with_valid_session_never_requires_csrf(
    missions_client: TestClient,
) -> None:
    response = missions_client.get(
        "/missions-like/123", cookies={WEB_SESSION_COOKIE_NAME: "raw-token"}
    )

    assert response.status_code == 200


def test_invalid_session_returns_authentication_error_not_csrf_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ponto 8: a ordem é sessão primeiro, CSRF depois -- nunca o
    inverso. Sem sessão válida (e também sem CSRF nenhum), a resposta
    precisa ser `401 not_authenticated`, nunca `403 csrf_invalid`."""
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user", lambda *a, **k: None
    )
    client = TestClient(_build_missions_like_app())

    response = client.patch("/missions-like/123")  # sem cookie de sessão, sem CSRF

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


def test_invalid_session_is_401_even_with_a_valid_csrf_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reforça a ordem: mesmo com um par CSRF válido, sessão inválida
    ainda é `401`, não `200` nem `403`."""
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user", lambda *a, **k: None
    )
    client = TestClient(_build_missions_like_app())

    response = client.patch(
        "/missions-like/123",
        cookies=_csrf_cookies(**{WEB_SESSION_COOKIE_NAME: "raw-token"}),
        headers=_csrf_headers(),
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


def test_mutation_authenticated_by_non_cookie_mechanism_is_not_affected_by_csrf() -> (
    None
):
    """Ponto 9: um endpoint autenticado por outro mecanismo (aqui
    simulando Bearer/service token, como o webhook real do Telegram usa
    um segredo de header em vez de cookie) nunca depende de
    `require_web_session`, então nunca exige CSRF -- mesmo montado na
    mesma aplicação que endpoints protegidos."""
    app = FastAPI()
    register_api_error_handler(app)
    other_router = APIRouter()

    @other_router.post("/service-endpoint")
    def _bearer_authenticated_endpoint() -> dict[str, bool]:
        return {"ok": True}

    app.include_router(other_router)
    client = TestClient(app)

    response = client.post("/service-endpoint", json={})

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_require_admin_web_session_denies_plain_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user",
        lambda *a, **k: _user(role=UserRole.USER),
    )
    client = TestClient(_build_missions_like_app())

    response = client.patch(
        "/admin-like/1",
        cookies=_csrf_cookies(**{WEB_SESSION_COOKIE_NAME: "raw-token"}),
        headers=_csrf_headers(),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "admin_access_denied"


def test_require_admin_web_session_allows_dev_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user",
        lambda *a, **k: _user(role=UserRole.DEV),
    )
    client = TestClient(_build_missions_like_app())

    response = client.patch(
        "/admin-like/1",
        cookies=_csrf_cookies(**{WEB_SESSION_COOKIE_NAME: "raw-token"}),
        headers=_csrf_headers(),
    )

    assert response.status_code == 200


def test_require_admin_web_session_still_requires_csrf_for_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Composição completa (ponto 5): autenticação -> CSRF -> autorização
    ADMIN. Mesmo um DEV/ADMIN legítimo é barrado por CSRF antes de chegar
    à checagem de permissão, para método mutável."""
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user",
        lambda *a, **k: _user(role=UserRole.DEV),
    )
    client = TestClient(_build_missions_like_app())

    response = client.patch(
        "/admin-like/1", cookies={WEB_SESSION_COOKIE_NAME: "raw-token"}
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_invalid"
