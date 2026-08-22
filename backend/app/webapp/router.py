"""Endpoints de sessão da aplicação web (TASK-091, item 1 da V1.2).

Login/logout/sessão atual sob `/api/v1` (`docs/development/api-conventions.md`).
Autenticação de sessão de navegador própria (`WebSession`), reaproveitando
a senha já existente (`UserCredential`, Argon2id, TASK-061) -- a mesma
senha continua autenticando pelo link emitido via Telegram.

CSRF não é uma dependência deste router -- é propriedade da autenticação
por `WebSession` (`app.webapp.dependency.require_web_session`), válida em
qualquer módulo. Login é a única exceção: ainda não existe `WebSession`
nesse ponto, então `create_web_session` valida CSRF explicitamente via
`Depends(validate_csrf)`, usando o cookie anônimo emitido pela casca da
SPA antes do login. Logout e a consulta de sessão atual dependem de
`require_web_session` como qualquer outro endpoint do canal web -- nada
específico deste router."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy.orm import Session

from app.authentication.service import (
    SESSION_TTL,
    AuthenticationError,
    AuthenticationRateLimited,
    authenticate_web_login,
    issue_web_session,
    revoke_web_session,
)
from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.database.dependency import get_session
from app.users.models import User, UserRole
from app.webapp.csrf import CSRF_COOKIE_NAME, new_csrf_token, validate_csrf
from app.webapp.dependency import WEB_SESSION_COOKIE_NAME, require_web_session

router = APIRouter(prefix="/api/v1", tags=["webapp"])

_SESSION_TTL_SECONDS = int(SESSION_TTL.total_seconds())


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: Annotated[str, Field(min_length=1, max_length=32)]
    password: Annotated[SecretStr, Field(min_length=1, max_length=128)]


class WebSessionUser(BaseModel):
    id: UUID
    display_name: str
    username: str | None
    role: UserRole


def _set_session_cookie(
    response: Response, *, raw_token: str, settings: Settings
) -> None:
    response.set_cookie(
        WEB_SESSION_COOKIE_NAME,
        raw_token,
        max_age=_SESSION_TTL_SECONDS,
        httponly=True,
        secure=settings.environment == "production",
        samesite="lax",
        path="/",
    )


def _set_csrf_cookie(response: Response, *, settings: Settings) -> str:
    """Gira o cookie CSRF na fronteira de login (mesmo princípio da
    rotação de `WebSession`: nunca reaproveitar um identificador através de
    uma troca de privilégio). Não é `httpOnly` -- a SPA precisa ler o valor
    para ecoar no header `X-CSRF-Token`."""
    raw_csrf_token = new_csrf_token()
    response.set_cookie(
        CSRF_COOKIE_NAME,
        raw_csrf_token,
        max_age=_SESSION_TTL_SECONDS,
        httponly=False,
        secure=settings.environment == "production",
        samesite="lax",
        path="/",
    )
    return raw_csrf_token


def _as_web_session_user(user: User) -> WebSessionUser:
    return WebSessionUser(
        id=user.id,
        display_name=user.display_name,
        username=user.username,
        role=user.role,
    )


@router.post(
    "/web-sessions",
    status_code=status.HTTP_201_CREATED,
    operation_id="create_web_session",
    summary="Autenticar na aplicação web",
    response_description="Sessão criada; cookie httpOnly emitido.",
    dependencies=[Depends(validate_csrf)],
)
def create_web_session(
    payload: LoginRequest,
    response: Response,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> WebSessionUser:
    try:
        user = authenticate_web_login(
            session,
            username=payload.username,
            password=payload.password.get_secret_value(),
        )
    except AuthenticationRateLimited as error:
        # Commit explícito: a tentativa/bloqueio já foi registrado em
        # UserCredential por authenticate_web_login e não pode ser
        # desfeito pelo rollback que o ApiError provocaria no get_session.
        session.commit()
        raise ApiError(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code="login_rate_limited",
            message="Muitas tentativas. Tente novamente mais tarde.",
        ) from error
    except AuthenticationError as error:
        session.commit()
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="invalid_credentials",
            message="Usuário ou senha inválidos.",
        ) from error
    raw_token = issue_web_session(session, user=user)
    _set_session_cookie(response, raw_token=raw_token, settings=settings)
    _set_csrf_cookie(response, settings=settings)
    return _as_web_session_user(user)


@router.delete(
    "/web-sessions/current",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="delete_current_web_session",
    summary="Encerrar a sessão atual da aplicação web",
)
def delete_current_web_session(
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
    _user: User = Depends(
        require_web_session
    ),  # autenticação + CSRF (DELETE é mutável)
) -> None:
    # A dependência já garantiu que o cookie corresponde a uma sessão
    # válida; ler de novo aqui não repete validação nenhuma, só recupera
    # o valor para a revogação por hash.
    raw_token = request.cookies.get(WEB_SESSION_COOKIE_NAME)
    if raw_token:
        revoke_web_session(session, raw_token=raw_token)
    response.delete_cookie(WEB_SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")


@router.get(
    "/web-sessions/current",
    operation_id="get_current_web_session",
    summary="Consultar a sessão atual da aplicação web",
)
def get_current_web_session(
    user: User = Depends(
        require_web_session
    ),  # GET é seguro, CSRF é pulado internamente
) -> WebSessionUser:
    return _as_web_session_user(user)
