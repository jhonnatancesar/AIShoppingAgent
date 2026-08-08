"""Serviço transacional para tokens, senhas e sessões autenticadas."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.audit.models import AuditEntry
from app.authentication.models import (
    CredentialAction,
    CredentialActionToken,
    UserAuthSession,
    UserCredential,
)
from app.authentication.passwords import (
    PasswordPolicyError,
    hash_password,
    needs_rehash,
    validate_password,
    verify_password,
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
        raise AuthenticationError("Link inválido ou expirado.")
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


def logout(
    session: Session,
    *,
    user: User,
    now: datetime | None = None,
) -> None:
    current = _aware_now(now)
    _revoke_sessions(session, user_id=user.id, now=current)
    _audit(session, user.id, "authentication.logged_out")


def _complete_login(
    session: Session,
    *,
    token: CredentialActionToken,
    user: User,
    credential: UserCredential | None,
    password: str,
    now: datetime,
) -> None:
    if credential is None:
        _fail_token(token, now)
        raise AuthenticationError("Credenciais inválidas.")
    if credential.login_locked_until and now < credential.login_locked_until:
        raise AuthenticationRateLimited("Tente novamente mais tarde.")
    if not verify_password(credential.password_hash, password):
        _record_login_failure(credential, now)
        _fail_token(token, now)
        _audit(session, user.id, "authentication.login_failed")
        raise AuthenticationError("Credenciais inválidas.")
    credential.failed_login_attempts = 0
    credential.login_window_started_at = None
    credential.login_locked_until = None
    if needs_rehash(credential.password_hash):
        credential.password_hash = hash_password(password)
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
        _revoke_sessions(session, user_id=user.id, now=now)
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


def _locked_token(session: Session, raw_token: str) -> CredentialActionToken:
    if not raw_token or len(raw_token) > 128:
        raise AuthenticationError("Link inválido ou expirado.")
    token = session.execute(
        select(CredentialActionToken)
        .where(CredentialActionToken.token_hash == token_digest(raw_token))
        .with_for_update()
    ).scalar_one_or_none()
    if token is None:
        raise AuthenticationError("Link inválido ou expirado.")
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


def _audit(
    session: Session,
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
