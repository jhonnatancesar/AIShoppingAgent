"""Publicação idempotente dos avisos de expiração de sessão."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.authentication.models import UserAuthSession
from app.events import (
    AggregateType,
    AuthenticationSessionPayload,
    EventType,
    publish_event,
)
from app.events.service import publish_event_async

SESSION_EXPIRY_WARNING_LEAD = timedelta(minutes=30)


@dataclass(frozen=True, slots=True)
class AuthenticationNotificationPublication:
    expiring: int
    expired: int

    @property
    def total(self) -> int:
        return self.expiring + self.expired


def publish_due_authentication_notifications(
    session: Session,
    *,
    now: datetime | None = None,
    limit: int = 50,
    warning_lead: timedelta = SESSION_EXPIRY_WARNING_LEAD,
) -> AuthenticationNotificationPublication:
    """Publica fatos de sessão na mesma transação dos marcadores de deduplicação."""
    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("authentication notification time must be timezone-aware")
    if limit < 1:
        raise ValueError("limit must be positive")
    if warning_lead <= timedelta(0):
        raise ValueError("warning_lead must be positive")

    expired_rows = list(
        session.scalars(
            select(UserAuthSession)
            .where(
                UserAuthSession.revoked_at.is_(None),
                UserAuthSession.expiry_event_published.is_(False),
                UserAuthSession.expires_at <= current,
            )
            .order_by(UserAuthSession.expires_at, UserAuthSession.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
    )
    for auth_session in expired_rows:
        auth_session.expiry_warning_event_published = True
        auth_session.expiry_event_published = True
        _publish_session_event(
            session,
            auth_session=auth_session,
            event_type=EventType.AUTHENTICATION_SESSION_EXPIRED_V1,
            occurred_at=current,
        )

    remaining = limit - len(expired_rows)
    expiring_rows: list[UserAuthSession] = []
    if remaining:
        expiring_rows = list(
            session.scalars(
                select(UserAuthSession)
                .where(
                    UserAuthSession.revoked_at.is_(None),
                    UserAuthSession.expiry_warning_event_published.is_(False),
                    UserAuthSession.expires_at > current,
                    UserAuthSession.expires_at <= current + warning_lead,
                )
                .order_by(UserAuthSession.expires_at, UserAuthSession.id)
                .with_for_update(skip_locked=True)
                .limit(remaining)
            )
        )
        for auth_session in expiring_rows:
            auth_session.expiry_warning_event_published = True
            _publish_session_event(
                session,
                auth_session=auth_session,
                event_type=EventType.AUTHENTICATION_SESSION_EXPIRING_V1,
                occurred_at=current,
            )

    return AuthenticationNotificationPublication(
        expiring=len(expiring_rows), expired=len(expired_rows)
    )


async def publish_due_authentication_notifications_async(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 50,
    warning_lead: timedelta = SESSION_EXPIRY_WARNING_LEAD,
) -> AuthenticationNotificationPublication:
    """Equivalente assíncrono de `publish_due_authentication_notifications`
    (TASK-080) -- usado pelo caminho async do `telegram_notifier`."""
    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("authentication notification time must be timezone-aware")
    if limit < 1:
        raise ValueError("limit must be positive")
    if warning_lead <= timedelta(0):
        raise ValueError("warning_lead must be positive")

    expired_result = await session.scalars(
        select(UserAuthSession)
        .where(
            UserAuthSession.revoked_at.is_(None),
            UserAuthSession.expiry_event_published.is_(False),
            UserAuthSession.expires_at <= current,
        )
        .order_by(UserAuthSession.expires_at, UserAuthSession.id)
        .with_for_update(skip_locked=True)
        .limit(limit)
    )
    expired_rows = list(expired_result)
    for auth_session in expired_rows:
        auth_session.expiry_warning_event_published = True
        auth_session.expiry_event_published = True
        await _publish_session_event_async(
            session,
            auth_session=auth_session,
            event_type=EventType.AUTHENTICATION_SESSION_EXPIRED_V1,
            occurred_at=current,
        )

    remaining = limit - len(expired_rows)
    expiring_rows: list[UserAuthSession] = []
    if remaining:
        expiring_result = await session.scalars(
            select(UserAuthSession)
            .where(
                UserAuthSession.revoked_at.is_(None),
                UserAuthSession.expiry_warning_event_published.is_(False),
                UserAuthSession.expires_at > current,
                UserAuthSession.expires_at <= current + warning_lead,
            )
            .order_by(UserAuthSession.expires_at, UserAuthSession.id)
            .with_for_update(skip_locked=True)
            .limit(remaining)
        )
        expiring_rows = list(expiring_result)
        for auth_session in expiring_rows:
            auth_session.expiry_warning_event_published = True
            await _publish_session_event_async(
                session,
                auth_session=auth_session,
                event_type=EventType.AUTHENTICATION_SESSION_EXPIRING_V1,
                occurred_at=current,
            )

    return AuthenticationNotificationPublication(
        expiring=len(expiring_rows), expired=len(expired_rows)
    )


def _publish_session_event(
    session: Session,
    *,
    auth_session: UserAuthSession,
    event_type: EventType,
    occurred_at: datetime,
) -> None:
    publish_event(
        session,
        event_type=event_type,
        aggregate_type=AggregateType.AUTH_SESSION,
        aggregate_id=auth_session.id,
        payload=AuthenticationSessionPayload(
            session_id=auth_session.id,
            user_id=auth_session.user_id,
            expires_at=auth_session.expires_at,
        ),
        occurred_at=occurred_at,
    )


async def _publish_session_event_async(
    session: AsyncSession,
    *,
    auth_session: UserAuthSession,
    event_type: EventType,
    occurred_at: datetime,
) -> None:
    await publish_event_async(
        session,
        event_type=event_type,
        aggregate_type=AggregateType.AUTH_SESSION,
        aggregate_id=auth_session.id,
        payload=AuthenticationSessionPayload(
            session_id=auth_session.id,
            user_id=auth_session.user_id,
            expires_at=auth_session.expires_at,
        ),
        occurred_at=occurred_at,
    )
