"""Cadastro Web self-service (Subtask 9, auditoria GG Oferta).

Único caminho de criação de `User` com senha própria além do endpoint
ADMIN (`app.webapp.admin_router.create_user`) -- ambos reaproveitam
`app.users.service.create_user_with_password` (mesma validação de
username/senha, mesmo hashing Argon2id, mesma proteção real contra
corrida). Sempre cria `role=USER`; `extra="forbid"` garante que o
cliente não consegue sequer tentar enviar `role`/`permissions`/`user_id`/
status administrativo -- rejeição no nível do schema, antes do corpo do
endpoint rodar.

Sessão emitida automaticamente após o cadastro, pela mesma
infraestrutura do login (`issue_web_session`/cookies httpOnly/CSRF) --
nunca uma "sessão de cadastro" à parte. CSRF segue o mesmo padrão do
login (`Depends(validate_csrf)` direto, cookie anônimo emitido pela
casca da SPA antes do cadastro -- ainda não existe `WebSession` neste
ponto, então não há como passar por `require_web_session`).

Proteção contra abuso (validação de segurança): não existia nenhum rate
limit efetivo sobre este endpoint público antes desta correção (só
`MaxRequestBodyMiddleware`, que limita tamanho de corpo, não frequência).
`_RegistrationRateLimiter` é deliberadamente simples e em memória do
próprio processo -- mesma filosofia já aceita em
`app.core.resilience.CircuitRegistry` (best-effort, por processo, nunca
persistido -- reiniciar o processo apenas reabre a janela, o que é
aceitável para conter automação, não para auditoria). Reaproveita os
MESMOS números já calibrados de `LOGIN_WINDOW`/`LOGIN_FAILURE_LIMIT`
(login por senha), em vez de inventar um segundo limite arbitrário.

Chave é `app.webapp.client_ip.resolve_client_ip` (achado real da
segunda rodada de validação: PROD roda atrás de Cloudflare Tunnel, que
faz `request.client.host` chegar sempre como `127.0.0.1` -- usar isso
direto colapsaria TODO visitante público no mesmo bucket, então uma
única origem abusiva derrubaria o cadastro para todo mundo). O helper só
confia em `CF-Connecting-IP`/`X-Forwarded-For` quando a conexão chega do
loopback (única forma de o túnel alcançar esta porta); ver a docstring
daquele módulo para a prova empírica contra um túnel real."""

from collections import defaultdict, deque
from threading import Lock
from time import monotonic

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy.orm import Session

from app.authentication.passwords import PasswordPolicyError
from app.authentication.service import (
    LOGIN_FAILURE_LIMIT,
    LOGIN_WINDOW,
    issue_web_session,
)
from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.database.dependency import get_session
from app.users.models import UserRole
from app.users.registration import RegistrationError
from app.users.service import UserAlreadyExistsError, create_user_with_password
from app.webapp.client_ip import resolve_client_ip
from app.webapp.csrf import validate_csrf
from app.webapp.router import (
    WebSessionUser,
    as_web_session_user,
    set_csrf_cookie,
    set_session_cookie,
)

router = APIRouter(prefix="/api/v1/users", tags=["webapp-registration"])

_EMAIL_PATTERN_MESSAGE = "E-mail inválido."

_REGISTRATION_RATE_LIMIT_MAX_TRACKED_IPS = 10_000


class _RegistrationRateLimiter:
    """Janela deslizante por IP, em memória -- ver docstring do módulo."""

    def __init__(self, *, window_seconds: float, max_attempts: int) -> None:
        self._window_seconds = window_seconds
        self._max_attempts = max_attempts
        self._lock = Lock()
        self._attempts: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, *, now: float | None = None) -> None:
        current = monotonic() if now is None else now
        with self._lock:
            attempts = self._attempts[key]
            while attempts and current - attempts[0] > self._window_seconds:
                attempts.popleft()
            if len(attempts) >= self._max_attempts:
                raise ApiError(
                    status_code=429,
                    code="registration_rate_limited",
                    message="Muitas tentativas de cadastro. Tente novamente mais tarde.",
                )
            attempts.append(current)
            if len(self._attempts) > _REGISTRATION_RATE_LIMIT_MAX_TRACKED_IPS:
                self._evict_empty_locked()

    def _evict_empty_locked(self) -> None:
        for tracked_key in [k for k, v in self._attempts.items() if not v]:
            del self._attempts[tracked_key]


_registration_rate_limiter = _RegistrationRateLimiter(
    window_seconds=LOGIN_WINDOW.total_seconds(), max_attempts=LOGIN_FAILURE_LIMIT
)


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=32)
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=128)
    password_confirmation: SecretStr = Field(min_length=1, max_length=128)


_CONFLICT_MESSAGES = {
    "username": "Esse nome de usuário já está em uso.",
    "email": "Esse e-mail já está em uso.",
    "unknown": "Não foi possível concluir o cadastro.",
}


@router.post(
    "/register",
    response_model=WebSessionUser,
    status_code=status.HTTP_201_CREATED,
    operation_id="register_web_user",
    summary="Criar conta (cadastro Web self-service)",
    dependencies=[Depends(validate_csrf)],
)
def register_web_user(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> WebSessionUser:
    _registration_rate_limiter.check(resolve_client_ip(request))
    if payload.password.get_secret_value() != payload.password_confirmation.get_secret_value():
        raise ApiError(
            status_code=422,
            code="password_confirmation_mismatch",
            message="As senhas informadas não conferem.",
        )
    email = payload.email.strip()
    if "@" not in email or "." not in email.split("@")[-1] or " " in email:
        raise ApiError(
            status_code=422, code="invalid_email", message=_EMAIL_PATTERN_MESSAGE
        )
    try:
        user = create_user_with_password(
            session,
            username=payload.username,
            email=email,
            password=payload.password.get_secret_value(),
            role=UserRole.USER,
        )
    except (RegistrationError, PasswordPolicyError) as error:
        raise ApiError(
            status_code=422, code="registration_invalid", message=str(error)
        ) from error
    except UserAlreadyExistsError as error:
        raise ApiError(
            status_code=409,
            code=f"{error.field}_taken",
            message=_CONFLICT_MESSAGES.get(error.field, _CONFLICT_MESSAGES["unknown"]),
        ) from error
    raw_token = issue_web_session(session, user=user)
    set_session_cookie(response, raw_token=raw_token, settings=settings)
    set_csrf_cookie(response, settings=settings)
    return as_web_session_user(user)
