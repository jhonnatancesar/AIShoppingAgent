"""Serviço transacional para tokens, senhas e sessões autenticadas.

`issue_action_link`/`has_active_session`/`logout` têm uma versão `_async`
(extensão da TASK-079, usada pelo webhook Telegram) além da síncrona
(usada pela página `/auth` e por `scripts/validate_password_authentication.py`).
`complete_action` (fluxo da página `/auth`, fora do webhook) permanece só
síncrona.

`authenticate_web_login`/`issue_web_session`/`get_web_session_user`/
`revoke_web_session` (TASK-091, item 1 da V1.2) autenticam e gerenciam a
sessão de navegador da aplicação web -- usuário + senha direto, sem token
nem Telegram, sessão própria (`WebSession`), nunca `UserAuthSession`."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.audit.models import AuditEntry
from app.authentication.models import (
    CredentialAction,
    CredentialActionToken,
    UserAuthSession,
    UserCredential,
    WebSession,
)
from app.authentication.passwords import (
    PasswordPolicyError,
    hash_password,
    needs_rehash,
    validate_password,
    verify_password,
)
from app.events import (
    AggregateType,
    AuthenticationCompletedPayload,
    EventType,
    publish_event,
)
from app.users.models import User

SESSION_TTL = timedelta(hours=12)
TOKEN_TTL = timedelta(minutes=10)
LOGIN_WINDOW = timedelta(minutes=15)
LOGIN_COOLDOWN = timedelta(minutes=15)
LOGIN_FAILURE_LIMIT = 5
TOKEN_FAILURE_LIMIT = 5
LINK_WINDOW = timedelta(minutes=15)
LINK_LIMIT = 5
RECOVERY_WINDOW = timedelta(hours=1)
RECOVERY_LIMIT = 3
RECOVERY_MIN_INTERVAL = timedelta(minutes=5)


class AuthenticationError(RuntimeError):
    """Falha externa genérica, sem enumeração ou material sensível."""


class AuthenticationRateLimited(AuthenticationError):
    pass


@dataclass(frozen=True)
class IssuedActionLink:
    url: str
    expires_at: datetime


def token_digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def issue_action_link(
    session: Session,
    *,
    user: User,
    action: CredentialAction,
    public_base_url: str,
    now: datetime | None = None,
) -> IssuedActionLink:
    current = _aware_now(now)
    if user.telegram_user_id is None:
        raise AuthenticationError("Operação indisponível.")
    _validate_action_state(session, user=user, action=action)
    _enforce_issuance_limit(session, user_id=user.id, action=action, now=current)
    session.execute(
        update(CredentialActionToken)
        .where(
            CredentialActionToken.user_id == user.id,
            CredentialActionToken.action == action,
            CredentialActionToken.consumed_at.is_(None),
            CredentialActionToken.invalidated_at.is_(None),
            CredentialActionToken.expires_at > current,
        )
        .values(invalidated_at=current)
    )
    raw_token = secrets.token_urlsafe(32)
    expires_at = current + TOKEN_TTL
    session.add(
        CredentialActionToken(
            token_hash=token_digest(raw_token),
            action=action,
            user_id=user.id,
            telegram_user_id=user.telegram_user_id,
            created_at=current,
            expires_at=expires_at,
        )
    )
    _audit(session, user.id, "authentication.action_requested", action=action)
    base = _validated_base_url(public_base_url)
    hint = "login" if action is CredentialAction.LOGIN else "password"
    return IssuedActionLink(f"{base}/auth#{hint}:{raw_token}", expires_at)


async def issue_action_link_async(
    session: AsyncSession,
    *,
    user: User,
    action: CredentialAction,
    public_base_url: str,
    now: datetime | None = None,
) -> IssuedActionLink:
    """Equivalente assíncrono de `issue_action_link` (extensão da
    TASK-079). Usado pelo webhook Telegram; a página `/auth` (fora deste
    caminho) continua na versão síncrona."""
    current = _aware_now(now)
    if user.telegram_user_id is None:
        raise AuthenticationError("Operação indisponível.")
    await _validate_action_state_async(session, user=user, action=action)
    await _enforce_issuance_limit_async(
        session, user_id=user.id, action=action, now=current
    )
    await session.execute(
        update(CredentialActionToken)
        .where(
            CredentialActionToken.user_id == user.id,
            CredentialActionToken.action == action,
            CredentialActionToken.consumed_at.is_(None),
            CredentialActionToken.invalidated_at.is_(None),
            CredentialActionToken.expires_at > current,
        )
        .values(invalidated_at=current)
    )
    raw_token = secrets.token_urlsafe(32)
    expires_at = current + TOKEN_TTL
    session.add(
        CredentialActionToken(
            token_hash=token_digest(raw_token),
            action=action,
            user_id=user.id,
            telegram_user_id=user.telegram_user_id,
            created_at=current,
            expires_at=expires_at,
        )
    )
    _audit(session, user.id, "authentication.action_requested", action=action)
    base = _validated_base_url(public_base_url)
    hint = "login" if action is CredentialAction.LOGIN else "password"
    return IssuedActionLink(f"{base}/auth#{hint}:{raw_token}", expires_at)


def complete_action(
    session: Session,
    *,
    raw_token: str,
    password: str,
    password_confirmation: str | None,
    now: datetime | None = None,
) -> CredentialAction:
    current = _aware_now(now)
    token = _locked_token(session, raw_token)
    if not _token_is_usable(token, current):
        raise AuthenticationError("Este link é inválido ou já expirou.")
    user = session.get(User, token.user_id)
    if (
        user is None
        or not user.is_active
        or user.telegram_user_id != token.telegram_user_id
    ):
        _fail_token(token, current)
        raise AuthenticationError("Não foi possível concluir a operação.")
    credential = session.execute(
        select(UserCredential)
        .where(UserCredential.user_id == user.id)
        .with_for_update()
    ).scalar_one_or_none()
    if token.action is CredentialAction.LOGIN:
        _complete_login(
            session,
            token=token,
            user=user,
            credential=credential,
            password=password,
            now=current,
        )
    else:
        _complete_password_change(
            session,
            token=token,
            user=user,
            credential=credential,
            password=password,
            confirmation=password_confirmation,
            now=current,
        )
    token.consumed_at = current
    publish_event(
        session,
        event_type=EventType.AUTHENTICATION_COMPLETED_V1,
        aggregate_type=AggregateType.USER,
        aggregate_id=user.id,
        payload=AuthenticationCompletedPayload(user_id=user.id, action=token.action),
        occurred_at=current,
    )
    return token.action


def has_active_session(
    session: Session,
    *,
    user_id: UUID,
    telegram_user_id: int,
    now: datetime | None = None,
) -> bool:
    current = _aware_now(now)
    statement = select(UserAuthSession.id).where(
        UserAuthSession.user_id == user_id,
        UserAuthSession.telegram_user_id == telegram_user_id,
        UserAuthSession.revoked_at.is_(None),
        UserAuthSession.expires_at > current,
    )
    return session.execute(statement).scalar_one_or_none() is not None


async def has_active_session_async(
    session: AsyncSession,
    *,
    user_id: UUID,
    telegram_user_id: int,
    now: datetime | None = None,
) -> bool:
    """Equivalente assíncrono de `has_active_session` (extensão da
    TASK-079)."""
    current = _aware_now(now)
    statement = select(UserAuthSession.id).where(
        UserAuthSession.user_id == user_id,
        UserAuthSession.telegram_user_id == telegram_user_id,
        UserAuthSession.revoked_at.is_(None),
        UserAuthSession.expires_at > current,
    )
    return (await session.execute(statement)).scalar_one_or_none() is not None


def logout(
    session: Session,
    *,
    user: User,
    now: datetime | None = None,
) -> None:
    current = _aware_now(now)
    _revoke_sessions(session, user_id=user.id, now=current)
    _audit(session, user.id, "authentication.logged_out")


async def logout_async(
    session: AsyncSession,
    *,
    user: User,
    now: datetime | None = None,
) -> None:
    """Equivalente assíncrono de `logout` (extensão da TASK-079)."""
    current = _aware_now(now)
    await _revoke_sessions_async(session, user_id=user.id, now=current)
    _audit(session, user.id, "authentication.logged_out")


def authenticate_web_login(
    session: Session,
    *,
    username: str,
    password: str,
    now: datetime | None = None,
) -> User:
    """Autentica login pela aplicação web (TASK-091): usuário + senha
    diretos, sem token/Telegram envolvido. Reaproveita o mesmo núcleo de
    verificação de senha do login por link (`_verify_login_password`) --
    mesma política de bloqueio/tentativas/rehash, nunca uma segunda regra
    paralela. Não emite sessão nenhuma; o chamador decide se/como emitir
    (`issue_web_session`)."""
    current = _aware_now(now)
    user = session.scalar(select(User).where(User.username == username))
    if user is None or not user.is_active:
        raise AuthenticationError("As credenciais informadas são inválidas.")
    credential = session.execute(
        select(UserCredential)
        .where(UserCredential.user_id == user.id)
        .with_for_update()
    ).scalar_one_or_none()
    _verify_login_password(
        session, user_id=user.id, credential=credential, password=password, now=current
    )
    _audit(session, user.id, "authentication.web_logged_in")
    return user


def issue_web_session(
    session: Session,
    *,
    user: User,
    now: datetime | None = None,
) -> str:
    """Emite uma sessão de navegador nova, revogando qualquer sessão web
    anterior do usuário (mesma política de "uma sessão ativa por vez" já
    usada para `UserAuthSession`). Devolve o token bruto -- só ele vai para
    o cookie; o banco guarda apenas o hash (`token_digest`)."""
    current = _aware_now(now)
    _revoke_web_sessions(session, user_id=user.id, now=current)
    raw_token = secrets.token_urlsafe(32)
    session.add(
        WebSession(
            user_id=user.id,
            token_hash=token_digest(raw_token),
            authenticated_at=current,
            expires_at=current + SESSION_TTL,
        )
    )
    return raw_token


def get_web_session_user(
    session: Session,
    *,
    raw_token: str,
    now: datetime | None = None,
) -> User | None:
    """Resolve o `User` de uma sessão web ativa, ou `None` se o token for
    inválido, revogado ou expirado -- nunca distingue os três casos para o
    chamador (evita enumeração)."""
    if not raw_token or len(raw_token) > 128:
        return None
    current = _aware_now(now)
    web_session = session.scalar(
        select(WebSession).where(WebSession.token_hash == token_digest(raw_token))
    )
    if (
        web_session is None
        or web_session.revoked_at is not None
        or web_session.expires_at <= current
    ):
        return None
    user = session.get(User, web_session.user_id)
    if user is None or not user.is_active:
        return None
    return user


def revoke_web_session(
    session: Session,
    *,
    raw_token: str,
    now: datetime | None = None,
) -> None:
    """Revoga uma sessão web pelo token bruto (logout). Sem efeito se o
    token não corresponder a nenhuma sessão ativa."""
    if not raw_token:
        return
    current = _aware_now(now)
    session.execute(
        update(WebSession)
        .where(
            WebSession.token_hash == token_digest(raw_token),
            WebSession.revoked_at.is_(None),
        )
        .values(revoked_at=current)
    )


def _revoke_web_sessions(session: Session, *, user_id: UUID, now: datetime) -> None:
    session.execute(
        update(WebSession)
        .where(
            WebSession.user_id == user_id,
            WebSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )


async def _revoke_web_sessions_async(
    session: AsyncSession, *, user_id: UUID, now: datetime
) -> None:
    await session.execute(
        update(WebSession)
        .where(
            WebSession.user_id == user_id,
            WebSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )


def change_password_with_current(
    session: Session,
    *,
    user: User,
    current_password: str,
    new_password: str,
    now: datetime | None = None,
) -> str:
    """Troca de senha autenticada (Web, "Minha conta") -- Subtask 9.
    Verifica a senha atual antes de aceitar a nova (por isso não
    reaproveita `set_new_password_async`, que é para os fluxos de
    challenge/código, sem esse passo). Rotaciona a sessão Web atual
    (`issue_web_session` já revoga todas e emite uma nova -- "sessão
    atual preservada/rotacionada com segurança", nunca uma segunda
    implementação) e revoga as sessões do Telegram. Devolve o novo token
    bruto para o chamador setar o cookie."""
    current = _aware_now(now)
    credential = session.get(UserCredential, user.id)
    if credential is None or not verify_password(credential.password_hash, current_password):
        raise AuthenticationError("A senha atual informada está incorreta.")
    normalized = validate_password(new_password, username=user.username)
    credential.password_hash = hash_password(normalized)
    credential.password_changed_at = current
    credential.failed_login_attempts = 0
    credential.login_window_started_at = None
    credential.login_locked_until = None
    _revoke_sessions(session, user_id=user.id, now=current)
    raw_token = issue_web_session(session, user=user, now=current)
    _audit(session, user.id, "authentication.password_changed")
    return raw_token


async def set_new_password_async(
    session: AsyncSession,
    *,
    user: User,
    new_password: str,
    now: datetime | None = None,
) -> None:
    """Núcleo assíncrono compartilhado de "definir/trocar a senha e
    invalidar tudo" (Subtask 9) -- usado pelos fluxos novos de
    `VerificationChallenge` (recuperação Web, recuperação/alteração
    iniciadas pelo Telegram), nunca uma segunda política de senha:
    reaproveita `validate_password`/`hash_password` (`passwords.py`),
    mesmas regras do `/auth` e do login Web. Revoga incondicionalmente
    todas as sessões Web e Telegram -- diferente de
    `_complete_password_change` (que preserva nada porque não há sessão
    "atual" nesses fluxos: quem confirma o código ainda não está
    autenticado em lugar nenhum)."""
    current = _aware_now(now)
    normalized = validate_password(new_password, username=user.username)
    encoded = hash_password(normalized)
    credential = await session.get(UserCredential, user.id)
    if credential is None:
        session.add(
            UserCredential(user_id=user.id, password_hash=encoded, password_changed_at=current)
        )
    else:
        credential.password_hash = encoded
        credential.password_changed_at = current
        credential.failed_login_attempts = 0
        credential.login_window_started_at = None
        credential.login_locked_until = None
    await _revoke_sessions_async(session, user_id=user.id, now=current)
    await _revoke_web_sessions_async(session, user_id=user.id, now=current)
    _audit(session, user.id, "authentication.password_reset")


def _verify_login_password(
    session: Session,
    *,
    user_id: UUID,
    credential: UserCredential | None,
    password: str,
    now: datetime,
) -> None:
    """Núcleo compartilhado da verificação de login: checa bloqueio,
    verifica a senha, registra falha/rehash. Levanta em caso de falha;
    não decide o que fazer com o token (`_complete_login`) nem com sessão
    nenhuma -- cada chamador aplica seu próprio efeito de sucesso."""
    if credential is None:
        raise AuthenticationError("As credenciais informadas são inválidas.")
    if credential.login_locked_until and now < credential.login_locked_until:
        raise AuthenticationRateLimited("Tente novamente mais tarde.")
    if not verify_password(credential.password_hash, password):
        _record_login_failure(credential, now)
        _audit(session, user_id, "authentication.login_failed")
        raise AuthenticationError("As credenciais informadas são inválidas.")
    credential.failed_login_attempts = 0
    credential.login_window_started_at = None
    credential.login_locked_until = None
    if needs_rehash(credential.password_hash):
        credential.password_hash = hash_password(password)


def _complete_login(
    session: Session,
    *,
    token: CredentialActionToken,
    user: User,
    credential: UserCredential | None,
    password: str,
    now: datetime,
) -> None:
    try:
        _verify_login_password(
            session, user_id=user.id, credential=credential, password=password, now=now
        )
    except AuthenticationRateLimited:
        raise
    except AuthenticationError:
        _fail_token(token, now)
        raise
    # O bot possui uma única identidade autenticada por usuário/Telegram.
    # Substituir a sessão anterior evita avisos contraditórios de expiração.
    _revoke_sessions(session, user_id=user.id, now=now)
    session.add(
        UserAuthSession(
            user_id=user.id,
            telegram_user_id=token.telegram_user_id,
            authenticated_at=now,
            expires_at=now + SESSION_TTL,
        )
    )
    _audit(session, user.id, "authentication.logged_in")


def _complete_password_change(
    session: Session,
    *,
    token: CredentialActionToken,
    user: User,
    credential: UserCredential | None,
    password: str,
    confirmation: str | None,
    now: datetime,
) -> None:
    if confirmation is None or password != confirmation:
        _fail_token(token, now)
        raise PasswordPolicyError("As senhas informadas não conferem.")
    try:
        normalized = validate_password(password, username=user.username)
    except PasswordPolicyError:
        _fail_token(token, now)
        raise
    if token.action is CredentialAction.SET_PASSWORD and credential is not None:
        _fail_token(token, now)
        raise AuthenticationError("Não foi possível concluir a operação.")
    if token.action is not CredentialAction.SET_PASSWORD and credential is None:
        _fail_token(token, now)
        raise AuthenticationError("Não foi possível concluir a operação.")
    encoded = hash_password(normalized)
    if credential is None:
        session.add(
            UserCredential(
                user_id=user.id, password_hash=encoded, password_changed_at=now
            )
        )
    else:
        credential.password_hash = encoded
        credential.password_changed_at = now
        credential.failed_login_attempts = 0
        credential.login_window_started_at = None
        credential.login_locked_until = None
        # Subtask 9 (correção de gap encontrado no preflight): esta troca
        # já revogava as sessões do Telegram (`UserAuthSession`), mas
        # nunca as sessões Web (`WebSession`) -- uma sessão de navegador
        # roubada continuava válida indefinidamente após troca/recuperação
        # de senha feita pelo link `/auth`. Mesma política now aplicada
        # de forma uniforme nos dois canais.
        _revoke_sessions(session, user_id=user.id, now=now)
        _revoke_web_sessions(session, user_id=user.id, now=now)
    action_name = {
        CredentialAction.SET_PASSWORD: "authentication.password_set",
        CredentialAction.CHANGE_PASSWORD: "authentication.password_changed",
        CredentialAction.RECOVER_PASSWORD: "authentication.password_recovered",
    }[token.action]
    _audit(session, user.id, action_name)


def _validate_action_state(
    session: Session, *, user: User, action: CredentialAction
) -> None:
    exists = session.get(UserCredential, user.id) is not None
    valid = {
        CredentialAction.SET_PASSWORD: not exists,
        CredentialAction.CHANGE_PASSWORD: exists,
        CredentialAction.LOGIN: exists,
        CredentialAction.RECOVER_PASSWORD: exists,
    }[action]
    if not valid:
        raise AuthenticationError("Operação indisponível.")


async def _validate_action_state_async(
    session: AsyncSession, *, user: User, action: CredentialAction
) -> None:
    exists = await session.get(UserCredential, user.id) is not None
    valid = {
        CredentialAction.SET_PASSWORD: not exists,
        CredentialAction.CHANGE_PASSWORD: exists,
        CredentialAction.LOGIN: exists,
        CredentialAction.RECOVER_PASSWORD: exists,
    }[action]
    if not valid:
        raise AuthenticationError("Operação indisponível.")


def _enforce_issuance_limit(
    session: Session, *, user_id: UUID, action: CredentialAction, now: datetime
) -> None:
    window = (
        RECOVERY_WINDOW if action is CredentialAction.RECOVER_PASSWORD else LINK_WINDOW
    )
    limit = (
        RECOVERY_LIMIT if action is CredentialAction.RECOVER_PASSWORD else LINK_LIMIT
    )
    recent = session.execute(
        select(func.count(CredentialActionToken.id)).where(
            CredentialActionToken.user_id == user_id,
            CredentialActionToken.action == action,
            CredentialActionToken.created_at > now - window,
        )
    ).scalar_one()
    if recent >= limit:
        raise AuthenticationRateLimited("Tente novamente mais tarde.")
    if action is CredentialAction.RECOVER_PASSWORD:
        latest = session.execute(
            select(CredentialActionToken.created_at)
            .where(
                CredentialActionToken.user_id == user_id,
                CredentialActionToken.action == action,
            )
            .order_by(CredentialActionToken.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if latest is not None and latest > now - RECOVERY_MIN_INTERVAL:
            raise AuthenticationRateLimited("Tente novamente mais tarde.")


async def _enforce_issuance_limit_async(
    session: AsyncSession, *, user_id: UUID, action: CredentialAction, now: datetime
) -> None:
    window = (
        RECOVERY_WINDOW if action is CredentialAction.RECOVER_PASSWORD else LINK_WINDOW
    )
    limit = (
        RECOVERY_LIMIT if action is CredentialAction.RECOVER_PASSWORD else LINK_LIMIT
    )
    recent = await session.scalar(
        select(func.count(CredentialActionToken.id)).where(
            CredentialActionToken.user_id == user_id,
            CredentialActionToken.action == action,
            CredentialActionToken.created_at > now - window,
        )
    )
    if recent >= limit:
        raise AuthenticationRateLimited("Tente novamente mais tarde.")
    if action is CredentialAction.RECOVER_PASSWORD:
        latest = await session.scalar(
            select(CredentialActionToken.created_at)
            .where(
                CredentialActionToken.user_id == user_id,
                CredentialActionToken.action == action,
            )
            .order_by(CredentialActionToken.created_at.desc())
            .limit(1)
        )
        if latest is not None and latest > now - RECOVERY_MIN_INTERVAL:
            raise AuthenticationRateLimited("Tente novamente mais tarde.")


def _locked_token(session: Session, raw_token: str) -> CredentialActionToken:
    if not raw_token or len(raw_token) > 128:
        raise AuthenticationError("Este link é inválido ou já expirou.")
    token = session.execute(
        select(CredentialActionToken)
        .where(CredentialActionToken.token_hash == token_digest(raw_token))
        .with_for_update()
    ).scalar_one_or_none()
    if token is None:
        raise AuthenticationError("Este link é inválido ou já expirou.")
    return token


def _token_is_usable(token: CredentialActionToken, now: datetime) -> bool:
    return (
        token.consumed_at is None
        and token.invalidated_at is None
        and token.failed_attempts < TOKEN_FAILURE_LIMIT
        and now < token.expires_at
    )


def _fail_token(token: CredentialActionToken, now: datetime) -> None:
    token.failed_attempts = min(token.failed_attempts + 1, TOKEN_FAILURE_LIMIT)
    if token.failed_attempts >= TOKEN_FAILURE_LIMIT and token.invalidated_at is None:
        token.invalidated_at = now


def _record_login_failure(credential: UserCredential, now: datetime) -> None:
    if (
        credential.login_window_started_at is None
        or now >= credential.login_window_started_at + LOGIN_WINDOW
    ):
        credential.login_window_started_at = now
        credential.failed_login_attempts = 1
    else:
        credential.failed_login_attempts += 1
    if credential.failed_login_attempts >= LOGIN_FAILURE_LIMIT:
        credential.login_locked_until = now + LOGIN_COOLDOWN


def _revoke_sessions(session: Session, *, user_id: UUID, now: datetime) -> None:
    session.execute(
        update(UserAuthSession)
        .where(
            UserAuthSession.user_id == user_id,
            UserAuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )


async def _revoke_sessions_async(
    session: AsyncSession, *, user_id: UUID, now: datetime
) -> None:
    await session.execute(
        update(UserAuthSession)
        .where(
            UserAuthSession.user_id == user_id,
            UserAuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )


def _audit(
    session: Session | AsyncSession,
    user_id: UUID,
    action_name: str,
    *,
    action: CredentialAction | None = None,
) -> None:
    metadata = {"action": action.value} if action is not None else {}
    session.add(
        AuditEntry(
            actor_type="authentication",
            actor_id=user_id,
            action=action_name,
            resource_type="user",
            resource_id=user_id,
            entry_metadata=metadata,
        )
    )


def _validated_base_url(value: str) -> str:
    normalized = value.rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("auth public base URL must be absolute HTTP(S)")
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("auth public base URL must contain only a clean origin")
    if parsed.scheme != "https" and parsed.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ValueError("auth public base URL must use HTTPS outside localhost")
    return normalized


def _aware_now(value: datetime | None) -> datetime:
    result = value or datetime.now(UTC)
    if result.tzinfo is None:
        raise ValueError("authentication timestamps must be timezone-aware")
    return result
