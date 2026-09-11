"""Serve o build estático da SPA (TASK-091, item 1 da V1.2).

Registrado por último em `app.main` -- depois de todos os routers de API
-- para que `/health`, `/ready`, `/api/v1/...`, `/auth`, `/telegram/webhook`
etc. continuem tendo prioridade sobre o catch-all de roteamento
client-side. Se o build não existir (frontend não compilado), a rota
responde `404` em vez de derrubar a aplicação -- API e Telegram continuam
funcionando normalmente sem a SPA.
"""

import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.authorization import AuthorizationDenied, Permission, authorize
from app.core.config import Settings
from app.core.errors import ApiError
from app.database.dependency import get_session
from app.webapp.csrf import CSRF_COOKIE_NAME, new_csrf_token
from app.webapp.dependency import _resolve_web_session

logger = logging.getLogger("app.webapp")

# Cookie CSRF anônimo (pré-login): vida curta, só precisa sobreviver do
# carregamento da casca até o clique em "Entrar" -- não é a sessão em si.
_CSRF_ANONYMOUS_MAX_AGE_SECONDS = 3600

_HASHED_ASSET_CACHE_HEADERS = {
    # Achado real (2026-09-11): o deploy da v1.3.13 ficou de pé no
    # servidor, mas usuarios com uma visita anterior continuaram vendo a
    # casca antiga -- FileResponse nao manda Cache-Control nenhum, entao
    # o navegador aplica heuristica propria (RFC 7234) e reaproveita o
    # index.html velho sem revalidar. Só `/assets/*` (nome com hash de
    # conteudo, gerado pelo Vite -- ex. `index-D0IK8e0t.js`) pode cachear
    # longo, porque cada build muda o nome do arquivo. `index.html` (essa
    # casca aponta pro hash certo) e os estaticos SEM hash copiados de
    # `frontend/public/` (favicon, logos, `store-logos/`) nunca podem
    # levar `immutable`, senao reproduzem o mesmo bug pra eles.
    "Cache-Control": "public, max-age=31536000, immutable",
}
_UNHASHED_STATIC_CACHE_HEADERS = {"Cache-Control": "no-cache"}
_INDEX_CACHE_HEADERS = {"Cache-Control": "no-cache"}

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
        "connect-src 'self'; "
        # TASK-115 (v1.2.5, achado real): hosts levantados diretamente de
        # Offer.image_url, um por loja -- nunca 'img-src *'/https: genérico.
        # Magalu e Mercado Livre (subtask 4, auditoria GG Oferta) usam CDNs
        # próprias e sempre bloqueadas até aqui: `a-static.mlcdn.com.br`
        # (Magalu) e `http2.mlstatic.com` (Mercado Livre), ambos já
        # confirmados no código dos providers e nos fixtures reais de
        # `tests/test_store_providers.py`.
        "img-src 'self' data: "
        "https://m.media-amazon.com "
        "https://images.kabum.com.br "
        "https://media.pichau.com.br "
        "https://img.terabyteshop.com.br "
        "https://a-static.mlcdn.com.br "
        "https://http2.mlstatic.com; "
        # `'unsafe-inline'` (Subtask 13, achado real): Radix Popper posiciona
        # Select/Popover/Tooltip via `style` inline calculado em runtime
        # (depende da posição real do gatilho na tela) -- impossível de
        # prever em build-time para nonce/hash. Só afasta `style-src`; script
        # continua estritamente `'self'`, sem relaxar XSS.
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self'"
    ),
}


def _default_dist_dir() -> Path:
    # backend/app/webapp/spa.py -> backend/app -> backend -> repo root -> frontend/dist
    return Path(__file__).resolve().parents[3] / "frontend" / "dist"


_CATCH_ALL_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]


# Whitelist do que pertence à SPA (endurecimento de 2026-08-21/22,
# corrigindo o desenho original que usava blacklist de prefixos de
# backend). Uma blacklist depende de lembrar de listar cada rota de
# backend nova; uma rota esquecida vira silenciosamente `200 index.html`
# em vez de `404`. Com whitelist o padrão é o oposto e seguro por
# construção: só o que está aqui vira casca da SPA -- qualquer caminho não
# listado (rota de API futura, typo, o que for) é `404` de verdade, mesmo
# que ninguém tenha atualizado esta lista. Precisa espelhar exatamente as
# rotas de `frontend/src/App.tsx` (`/`, `/login`, `/cadastro`, `/recuperar`,
# `/app`, `/admin`, e seus descendentes via roteamento client-side do React
# Router).
_SPA_OWNED_TOP_LEVEL_SEGMENTS = frozenset(
    {"", "login", "cadastro", "recuperar", "app", "admin"}
)


def _is_spa_owned_path(full_path: str) -> bool:
    first_segment = full_path.split("/", 1)[0]
    return first_segment in _SPA_OWNED_TOP_LEVEL_SEGMENTS


def register_spa(app: FastAPI, settings: Settings) -> None:
    dist_dir = settings.spa_dist_dir or _default_dist_dir()
    index_file = dist_dir / "index.html"
    if not index_file.is_file():
        logger.warning(
            "spa_build_not_found",
            extra={"spa_dist_dir": str(dist_dir)},
        )

        @app.api_route(
            "/{full_path:path}", methods=_CATCH_ALL_METHODS, include_in_schema=False
        )
        async def _spa_not_built(full_path: str) -> Response:
            return Response(status_code=404)

        return

    @app.api_route(
        "/{full_path:path}", methods=_CATCH_ALL_METHODS, include_in_schema=False
    )
    async def _serve_spa(
        full_path: str,
        request: Request,
        session: Session = Depends(get_session),
    ) -> Response:
        if request.method not in ("GET", "HEAD"):
            # O catch-all precisa casar com qualquer método (não só GET)
            # para que uma rota de API real sem correspondência responda
            # `404` -- sem isso, o Starlette veria o padrão de caminho
            # bater (registrado só para GET) e devolveria `405 Method Not
            # Allowed` em vez de `404` para `PATCH`/`PUT`/`DELETE` num
            # caminho inexistente. A SPA em si só serve navegação `GET`.
            return Response(status_code=404)
        candidate = (dist_dir / full_path).resolve()
        if full_path and candidate.is_file() and dist_dir in candidate.parents:
            # Arquivo real do build (`/assets/*.js`, `/favicon.svg`, ...) --
            # não é uma "rota" da SPA, é um asset estático; servido
            # independente da whitelist de rotas abaixo.
            is_hashed_asset = full_path.startswith("assets/")
            cache_headers = (
                _HASHED_ASSET_CACHE_HEADERS
                if is_hashed_asset
                else _UNHASHED_STATIC_CACHE_HEADERS
            )
            return FileResponse(
                candidate, headers={**_SECURITY_HEADERS, **cache_headers}
            )
        if not _is_spa_owned_path(full_path):
            # Nenhuma rota real de backend bateu (routers de API são
            # registrados antes deste catch-all e sempre têm prioridade) e
            # o caminho não pertence à SPA -- 404 de verdade, nunca
            # `index.html` (`docs/tasks/TASK-091.md`).
            return Response(status_code=404)
        is_admin_path = full_path == "admin" or full_path.startswith("admin/")
        if is_admin_path and not _has_admin_web_session(request, session):
            response = RedirectResponse("/login", headers=_SECURITY_HEADERS)
            _ensure_csrf_cookie(request, response, settings=settings)
            return response
        response = FileResponse(
            index_file, headers={**_SECURITY_HEADERS, **_INDEX_CACHE_HEADERS}
        )
        _ensure_csrf_cookie(request, response, settings=settings)
        return response


def _ensure_csrf_cookie(
    request: Request, response: Response, *, settings: Settings
) -> None:
    """Emite o cookie CSRF anônimo (não requer sessão) sempre que a casca da
    SPA é servida e o cliente ainda não tem um -- é o que protege o próprio
    `POST /api/v1/web-sessions` (login) contra CSRF: a SPA sempre carrega a
    casca antes de rodar qualquer JS, então o cookie já existe no momento do
    primeiro login. `create_web_session` gira este cookie de novo após
    autenticar (mesmo princípio de `WebSession`: nunca atravessar uma
    fronteira de privilégio com um identificador reaproveitado)."""
    if request.cookies.get(CSRF_COOKIE_NAME):
        return
    response.set_cookie(
        CSRF_COOKIE_NAME,
        new_csrf_token(),
        max_age=_CSRF_ANONYMOUS_MAX_AGE_SECONDS,
        httponly=False,
        secure=settings.environment == "production",
        samesite="lax",
        path="/",
    )


def _has_admin_web_session(request: Request, session: Session) -> bool:
    """Fecha a fronteira de `/admin` no backend (`DEC-073`): a casca
    estática em si não é sensível, mas nunca é servida pra quem não tem
    `Permission.ADMIN_PANEL_ACCESS` -- nunca depende só do React
    redirecionar no cliente. Reaproveita `_resolve_web_session` (mesmo
    helper interno usado por `require_web_session`) em vez de duplicar a
    resolução de sessão -- única fonte de verdade do que conta como uma
    `WebSession` válida. Não usa `require_web_session`/CSRF aqui: este
    caminho só serve HTML estático via `GET`, nunca muta nada."""
    try:
        user = _resolve_web_session(request, session)
    except ApiError:
        return False
    try:
        authorize(session, user, Permission.ADMIN_PANEL_ACCESS)
    except AuthorizationDenied:
        session.commit()
        return False
    return True
