"""Vínculo opcional Web -> Telegram com prova privada temporária."""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.audit.models import AuditEntry
from app.authentication.models import (
    CredentialActionToken,
    TelegramLinkToken,
    UserAuthSession,
)
from app.authentication.service import LINK_LIMIT, LINK_WINDOW, token_digest
from app.users.models import User

TELEGRAM_LINK_TTL = timedelta(minutes=10)


class TelegramLinkStatus(StrEnum):
    NOT_LINKED = "not_linked"
    PENDING = "pending"
    LINKED = "linked"


class TelegramLinkError(RuntimeError):
    """Falha externa genérica, sem revelar conta ou Telegram envolvidos."""


class TelegramLinkRateLimited(TelegramLinkError):
    pass


@dataclass(frozen=True, slots=True)
class IssuedTelegramLink:
    raw_token: str
    command: str
    expires_at: datetime


def telegram_link_state(
    session: Session, *, user: User, now: datetime | None = None
) -> tuple[TelegramLinkStatus, datetime | None]:
    if user.telegram_user_id is not None:
        return TelegramLinkStatus.LINKED, None
    current = _aware_now(now)
    expires_at = session.scalar(
        select(TelegramLinkToken.expires_at)
        .where(
            TelegramLinkToken.user_id == user.id,
            TelegramLinkToken.consumed_at.is_(None),
            TelegramLinkToken.invalidated_at.is_(None),
            TelegramLinkToken.expires_at > current,
        )
        .order_by(TelegramLinkToken.created_at.desc(), TelegramLinkToken.id.desc())
        .limit(1)
    )
    if expires_at is not None:
        return TelegramLinkStatus.PENDING, expires_at
    return TelegramLinkStatus.NOT_LINKED, None


def issue_telegram_link(
    session: Session, *, user: User, now: datetime | None = None
) -> IssuedTelegramLink:
    current = _aware_now(now)
    if user.telegram_user_id is not None:
        raise TelegramLinkError("Operação indisponível.")
    issued = session.scalar(
        select(func.count())
        .select_from(TelegramLinkToken)
        .where(
            TelegramLinkToken.user_id == user.id,
            TelegramLinkToken.created_at >= current - LINK_WINDOW,
        )
    )
    if int(issued or 0) >= LINK_LIMIT:
        raise TelegramLinkRateLimited("Muitas tentativas. Tente novamente mais tarde.")
    session.execute(
        update(TelegramLinkToken)
        .where(
            TelegramLinkToken.user_id == user.id,
            TelegramLinkToken.consumed_at.is_(None),
            TelegramLinkToken.invalidated_at.is_(None),
        )
        .values(invalidated_at=current)
    )
    raw_token = secrets.token_urlsafe(32)
    expires_at = current + TELEGRAM_LINK_TTL
    session.add(
        TelegramLinkToken(
            token_hash=token_digest(raw_token),
            user_id=user.id,
            created_at=current,
            expires_at=expires_at,
        )
    )
    _audit(session, user=user, action="telegram.link_requested")
    return IssuedTelegramLink(
        raw_token=raw_token,
        command=f"/vincular {raw_token}",
        expires_at=expires_at,
    )


async def complete_telegram_link(
    session: AsyncSession,
    *,
    raw_token: str,
    telegram_user_id: int,
    telegram_chat_id: int,
    now: datetime | None = None,
) -> User:
    current = _aware_now(now)
    if (
        not raw_token
        or len(raw_token) > 128
        or telegram_user_id != telegram_chat_id
    ):
        raise TelegramLinkError("Código inválido ou expirado.")
    token = await session.scalar(
        select(TelegramLinkToken)
        .where(TelegramLinkToken.token_hash == token_digest(raw_token))
        .with_for_update()
    )
    if token is None or not _token_is_usable(token, current):
        raise TelegramLinkError("Código inválido ou expirado.")
    target = await session.get(User, token.user_id, with_for_update=True)
    linked_user = await session.scalar(
        select(User)
        .where(User.telegram_user_id == telegram_user_id)
        .with_for_update()
    )
    if (
        target is None
        or not target.is_active
        or target.telegram_user_id is not None
        or (linked_user is not None and linked_user.id != target.id)
    ):
        token.invalidated_at = current
        raise TelegramLinkError("Não foi possível vincular este Telegram.")
    target.telegram_user_id = telegram_user_id
    target.telegram_chat_id = telegram_chat_id
    token.consumed_at = current
    await session.execute(
        update(TelegramLinkToken)
        .where(
            TelegramLinkToken.user_id == target.id,
            TelegramLinkToken.id != token.id,
            TelegramLinkToken.consumed_at.is_(None),
            TelegramLinkToken.invalidated_at.is_(None),
        )
        .values(invalidated_at=current)
    )
    _audit(session, user=target, action="telegram.link_completed")
    return target


def unlink_telegram(
    session: Session, *, user: User, now: datetime | None = None
) -> None:
    current = _aware_now(now)
    had_link = user.telegram_user_id is not None
    user.telegram_user_id = None
    user.telegram_chat_id = None
    session.execute(
        update(UserAuthSession)
        .where(UserAuthSession.user_id == user.id, UserAuthSession.revoked_at.is_(None))
        .values(revoked_at=current)
    )
    session.execute(
        update(CredentialActionToken)
        .where(
            CredentialActionToken.user_id == user.id,
            CredentialActionToken.consumed_at.is_(None),
            CredentialActionToken.invalidated_at.is_(None),
        )
        .values(invalidated_at=current)
    )
    session.execute(
        update(TelegramLinkToken)
        .where(
            TelegramLinkToken.user_id == user.id,
            TelegramLinkToken.consumed_at.is_(None),
            TelegramLinkToken.invalidated_at.is_(None),
        )
        .values(invalidated_at=current)
    )
    if had_link:
        _audit(session, user=user, action="telegram.unlinked")


def _token_is_usable(token: TelegramLinkToken, now: datetime) -> bool:
    return (
        token.consumed_at is None
        and token.invalidated_at is None
        and now < token.expires_at
    )


def _audit(
    session: Session | AsyncSession, *, user: User, action: str
) -> None:
    session.add(
        AuditEntry(
            actor_type="user",
            actor_id=user.id,
            action=action,
            resource_type="user",
            resource_id=user.id,
            entry_metadata={},
        )
    )


def _aware_now(value: datetime | None) -> datetime:
    result = value or datetime.now(UTC)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("telegram link timestamp must be timezone-aware")
    return result
