"""Dependências FastAPI de sessão/autorização da aplicação web (TASK-091).

`require_web_session` é a dependência pública para QUALQUER endpoint
autenticado por `WebSession`, em qualquer router/módulo -- resolve a
sessão e, só para métodos mutáveis, exige CSRF automaticamente
(`app.webapp.csrf.validate_csrf`). A proteção acompanha a autenticação,
não o router: um endpoint futuro de missões/ofertas/admin que dependa de
`require_web_session` herda CSRF sem nenhuma configuração extra, em
qualquer módulo onde for declarado.

`_resolve_web_session` é interno -- só resolve a sessão (cookie -> hash
-> `WebSession` -> `User`), nunca aplica CSRF. Nunca importar/usar fora
deste módulo; todo endpoint deve depender de `require_web_session` (ou
`require_admin_web_session`), nunca do helper interno -- a dependência
pública é segura por padrão, o helper sozinho não é.

Ordem das validações em `require_web_session`: sessão primeiro (`401` se
ausente/inválida/expirada/revogada), CSRF depois, só se a sessão for
válida e o método for mutável (`403`). Nunca o inverso -- um request sem
sessão nenhuma nunca deve ser confundido com falha de CSRF."""

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.authentication.service import get_web_session_user
from app.authorization import AuthorizationDenied, Permission, authorize
from app.core.errors import ApiError
from app.database.dependency import get_session
from app.users.models import User
from app.webapp.csrf import validate_csrf

WEB_SESSION_COOKIE_NAME = "aishopping_session"

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _resolve_web_session(
    request: Request, session: Session = Depends(get_session)
) -> User:
    """Helper interno: só resolve e valida a sessão. `401` se ausente,
    inválida, revogada ou expirada -- nunca distingue os casos na
    resposta (evita enumeração). Nunca aplica CSRF -- não usar fora deste
    módulo; endpoints sempre dependem de `require_web_session`."""
    raw_token = request.cookies.get(WEB_SESSION_COOKIE_NAME)
    user = get_web_session_user(session, raw_token=raw_token) if raw_token else None
    if user is None:
        raise ApiError(
            status_code=401,
            code="not_authenticated",
            message="Sessão inválida ou expirada. Faça login novamente.",
        )
    return user


def require_web_session(
    request: Request, user: User = Depends(_resolve_web_session)
) -> User:
    """Dependência pública do canal WebSession -- usar em qualquer
    endpoint (de qualquer router) que precise de autenticação por cookie.
    `_resolve_web_session` já rodou (e já teria levantado `401`) antes
    desta função executar, já que é uma sub-dependência: a ordem
    sessão-depois-CSRF vem de graça da árvore de dependências do
    FastAPI, não de um `if` explícito aqui."""
    if request.method not in _SAFE_METHODS:
        validate_csrf(request)
    return user


def require_admin_web_session(
    user: User = Depends(require_web_session),
    session: Session = Depends(get_session),
) -> User:
    """Exige `Permission.ADMIN_PANEL_ACCESS` (DEV/ADMIN exclusivo,
    `DEC-073`) -- reaproveita a matriz fail-closed já existente
    (`app.authorization`), nunca uma checagem de papel paralela. Compõe
    sobre `require_web_session`: autenticação -> CSRF (se mutável) ->
    autorização ADMIN, nessa ordem -- qualquer endpoint administrativo
    mutável futuro herda as três camadas só por declarar esta
    dependência."""
    try:
        authorize(session, user, Permission.ADMIN_PANEL_ACCESS)
    except AuthorizationDenied as error:
        # Mesma armadilha já resolvida no webhook Telegram (TASK-079): sem
        # commit explícito aqui, a auditoria de negação que `authorize`
        # acabou de adicionar seria desfeita pelo rollback do `get_session`
        # ao ver esta exceção propagar.
        session.commit()
        raise ApiError(
            status_code=403,
            code="admin_access_denied",
            message="Você não tem acesso a esta área.",
        ) from error
    return user
