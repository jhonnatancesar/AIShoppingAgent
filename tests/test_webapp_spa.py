"""Fronteira de `/admin` no backend, servindo o build da SPA (TASK-091).

`register_spa` é o catch-all client-side de `/app`/`/admin`; o ponto que
esta suíte comprova é que `/admin` nunca é servido sem
`Permission.ADMIN_PANEL_ACCESS` checada no backend (`DEC-073`) -- nunca só
o React redirecionando no cliente."""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from app.core.config import Settings
from app.database.dependency import get_session
from app.users.models import User, UserRole
from app.webapp.csrf import CSRF_COOKIE_NAME
from app.webapp.dependency import WEB_SESSION_COOKIE_NAME
from app.webapp.spa import register_spa
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def dist_dir():
    # `tempfile.mkdtemp` em vez do `tmp_path` do pytest -- o `tmp_path`
    # escaneia `%TEMP%\pytest-of-User`, que pode ficar bloqueado por ACL
    # nesta máquina Windows (achado documentado, não é bug de código).
    root = Path(tempfile.mkdtemp(prefix="aishopping-webapp-spa-"))
    try:
        dist = root / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html><body>spa shell</body></html>")
        assets = dist / "assets"
        assets.mkdir()
        (assets / "app.js").write_text("console.log('spa')")
        yield dist
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _build_app(dist_dir: Path) -> FastAPI:
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: MagicMock()
    register_spa(
        app, Settings(spa_dist_dir=dist_dir, database_password="unused", _env_file=None)
    )
    return app


def test_spa_not_built_returns_404_without_crashing() -> None:
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: MagicMock()
    register_spa(
        app,
        Settings(
            spa_dist_dir=Path(tempfile.gettempdir()) / "aishopping-spa-never-built",
            database_password="unused",
            _env_file=None,
        ),
    )
    client = TestClient(app)

    response = client.get("/app")

    assert response.status_code == 404


def test_app_shell_served_without_any_session(dist_dir: Path) -> None:
    client = TestClient(_build_app(dist_dir))

    response = client.get("/app")

    assert response.status_code == 200
    assert "spa shell" in response.text


def test_login_shell_served_without_any_session(dist_dir: Path) -> None:
    client = TestClient(_build_app(dist_dir))

    response = client.get("/login")

    assert response.status_code == 200


def test_csp_img_src_allowlists_real_store_hosts_without_wildcard(dist_dir: Path) -> None:
    """TASK-115 (v1.2.5) + subtask 4 (auditoria GG Oferta, 2026-08-30): hosts
    reais das 6 lojas com Offer.image_url, nunca 'img-src *'/https: genérico."""
    client = TestClient(_build_app(dist_dir))

    response = client.get("/app")

    csp = response.headers["content-security-policy"]
    img_src = next(part for part in csp.split(";") if part.strip().startswith("img-src"))
    tokens = img_src.split()[1:]  # remove "img-src"
    assert "*" not in tokens
    assert "https:" not in tokens  # nunca https: genérico (bare scheme) em img-src
    assert "'self'" in tokens
    assert "data:" in tokens
    for host in (
        "https://m.media-amazon.com",
        "https://images.kabum.com.br",
        "https://media.pichau.com.br",
        "https://img.terabyteshop.com.br",
        "https://a-static.mlcdn.com.br",
        "https://http2.mlstatic.com",
    ):
        assert host in img_src


def test_static_asset_is_served_directly(dist_dir: Path) -> None:
    client = TestClient(_build_app(dist_dir))

    response = client.get("/assets/app.js")

    assert response.status_code == 200
    assert "console.log" in response.text


def test_admin_shell_denied_without_any_session_redirects_to_login(
    dist_dir: Path,
) -> None:
    client = TestClient(_build_app(dist_dir), follow_redirects=False)

    response = client.get("/admin")

    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/login"


def test_admin_shell_denied_for_plain_user(
    dist_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = User(display_name="Cliente", role=UserRole.USER, username="cliente")
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user", lambda *a, **k: user
    )
    client = TestClient(_build_app(dist_dir), follow_redirects=False)

    response = client.get(
        "/admin/qualquer-coisa", cookies={WEB_SESSION_COOKIE_NAME: "raw-token"}
    )

    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/login"


def test_admin_shell_served_for_dev_user(
    dist_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = User(display_name="Dev", role=UserRole.DEV, username="dev")
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user", lambda *a, **k: user
    )
    client = TestClient(_build_app(dist_dir))

    response = client.get("/admin", cookies={WEB_SESSION_COOKIE_NAME: "raw-token"})

    assert response.status_code == 200
    assert "spa shell" in response.text


def test_admin_shell_served_for_admin_user(
    dist_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = User(display_name="Admin", role=UserRole.ADMIN, username="admin")
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user", lambda *a, **k: user
    )
    client = TestClient(_build_app(dist_dir))

    response = client.get("/admin", cookies={WEB_SESSION_COOKIE_NAME: "raw-token"})

    assert response.status_code == 200


def test_deep_link_under_app_serves_the_spa_shell(dist_dir: Path) -> None:
    """Uma rota client-side qualquer (roteamento do React Router) precisa
    servir a mesma casca -- refresh/F5 numa rota profunda não pode dar 404
    só porque não existe um arquivo físico com esse nome."""
    client = TestClient(_build_app(dist_dir))

    response = client.get("/app/alguma-rota-valida")

    assert response.status_code == 200
    assert "spa shell" in response.text


def test_deep_link_under_admin_serves_the_shell_for_dev_user(
    dist_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = User(display_name="Dev", role=UserRole.DEV, username="dev")
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user", lambda *a, **k: user
    )
    client = TestClient(_build_app(dist_dir))

    response = client.get(
        "/admin/alguma-rota-valida", cookies={WEB_SESSION_COOKIE_NAME: "raw-token"}
    )

    assert response.status_code == 200
    assert "spa shell" in response.text


@pytest.mark.parametrize(
    "path",
    [
        "api/v1/rota-que-nao-existe",
        "api/v1/web-sessions/current/algo-a-mais",
        "health/algo",
        "ready",
        "metrics",
        "telegram/webhook-errado",
        "auth/rota-errada",
        "r/token-qualquer-sem-rota",
    ],
)
def test_api_like_paths_never_fall_back_to_the_spa_shell(
    dist_dir: Path, path: str
) -> None:
    """Ponto 8 do endurecimento: uma rota de API inexistente precisa
    continuar respondendo 404 de verdade -- nunca `index.html` (200) só
    porque o catch-all client-side casaria com qualquer string."""
    client = TestClient(_build_app(dist_dir))

    response = client.get(f"/{path}")

    assert response.status_code == 404
    assert "spa shell" not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "qualquer-coisa-desconhecida",
        "app-mas-nao-exatamente/rota",
        "administracao",
        "loginx",
        "app2",
    ],
)
def test_unknown_paths_outside_the_spa_whitelist_are_404(
    dist_dir: Path, path: str
) -> None:
    """Endurecimento (ponto 3 da segunda rodada): whitelist explícita de
    rotas da SPA (`/`, `/login`, `/app`, `/admin`), não blacklist de
    prefixos de backend -- qualquer caminho fora dela é `404` por padrão,
    mesmo que ninguém tenha listado esse caminho especificamente em lugar
    nenhum. Antes desta correção, qualquer um desses caminhos teria virado
    `200 index.html` silenciosamente."""
    client = TestClient(_build_app(dist_dir))

    response = client.get(f"/{path}")

    assert response.status_code == 404
    assert "spa shell" not in response.text


def test_shell_issues_anonymous_csrf_cookie_when_missing(dist_dir: Path) -> None:
    """Protege o próprio login contra CSRF: a casca precisa deixar o
    cookie CSRF pronto antes de qualquer JS rodar, sem exigir sessão."""
    client = TestClient(_build_app(dist_dir))

    response = client.get("/login")

    csrf_cookie = response.cookies.get(CSRF_COOKIE_NAME)
    assert csrf_cookie
    set_cookie_header = next(
        value
        for key, value in response.headers.multi_items()
        if key.lower() == "set-cookie" and value.startswith(f"{CSRF_COOKIE_NAME}=")
    )
    assert "httponly" not in set_cookie_header.lower()  # SPA precisa ler


def test_shell_does_not_overwrite_an_existing_csrf_cookie(dist_dir: Path) -> None:
    client = TestClient(_build_app(dist_dir))

    response = client.get("/login", cookies={CSRF_COOKIE_NAME: "ja-existente"})

    assert CSRF_COOKIE_NAME not in response.cookies


def test_non_whitelisted_path_404_takes_priority_over_admin_gating(
    dist_dir: Path,
) -> None:
    """`/api/v1/admin` não é `/admin` -- o primeiro segmento (`api`) não
    está na whitelist da SPA, então nunca chega à checagem de admin."""
    client = TestClient(_build_app(dist_dir), follow_redirects=False)

    response = client.get("/api/v1/admin")

    assert response.status_code == 404


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_mutating_request_to_nonexistent_route_is_404_not_405(
    dist_dir: Path, method: str
) -> None:
    """Correção do endurecimento (2026-08-22): o catch-all da SPA precisa
    casar com qualquer método, não só `GET` -- senão o Starlette veria o
    padrão de caminho bater (registrado só para `GET`) e devolveria `405`
    em vez de `404` para uma rota de API inexistente atingida por um
    método mutável. A SPA em si nunca serve nada fora de `GET`/`HEAD`."""
    client = TestClient(_build_app(dist_dir))

    response = client.request(method, "/api/v1/rota-que-nao-existe")

    assert response.status_code == 404
