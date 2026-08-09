"""Publicação dos avisos duráveis de expiração de sessão."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.authentication.models import UserAuthSession
from app.authentication.notifications import (
    SESSION_EXPIRY_WARNING_LEAD,
    publish_due_authentication_notifications,
)
from app.events import Event

NOW = datetime(2026, 8, 9, 18, 0, tzinfo=UTC)


def _auth_session(expires_at: datetime) -> UserAuthSession:
    return UserAuthSession(
        id=uuid4(),
        user_id=uuid4(),
        telegram_user_id=123,
        authenticated_at=expires_at - timedelta(hours=12),
        expires_at=expires_at,
        expiry_warning_event_published=False,
        expiry_event_published=False,
    )


def test_publishes_expired_before_expiring_with_atomic_markers() -> None:
    expired = _auth_session(NOW)
    expiring = _auth_session(NOW + timedelta(minutes=10))
    session = MagicMock()
    session.scalars.side_effect = [[expired], [expiring]]

    result = publish_due_authentication_notifications(session, now=NOW, limit=2)

    assert result.expired == result.expiring == 1
    assert result.total == 2
    assert expired.expiry_warning_event_published is True
    assert expired.expiry_event_published is True
    assert expiring.expiry_warning_event_published is True
    assert expiring.expiry_event_published is False
    events = [
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], Event)
    ]
    assert [event.event_type for event in events] == [
        "authentication.session_expired.v1",
        "authentication.session_expiring.v1",
    ]
    assert events[0].payload["session_id"] == str(expired.id)
    assert events[1].payload["expires_at"] == expiring.expires_at.isoformat()


def test_limit_is_shared_and_prevents_warning_query_when_full() -> None:
    expired = _auth_session(NOW - timedelta(seconds=1))
    session = MagicMock()
    session.scalars.return_value = [expired]

    result = publish_due_authentication_notifications(session, now=NOW, limit=1)

    assert result.expired == 1
    assert result.expiring == 0
    session.scalars.assert_called_once()


@pytest.mark.parametrize(
    ("now", "limit", "lead", "message"),
    [
        (datetime(2026, 8, 9, 18, 0), 1, SESSION_EXPIRY_WARNING_LEAD, "timezone"),
        (NOW, 0, SESSION_EXPIRY_WARNING_LEAD, "limit"),
        (NOW, 1, timedelta(0), "warning_lead"),
    ],
)
def test_publication_guards_fail_closed(
    now: datetime, limit: int, lead: timedelta, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        publish_due_authentication_notifications(
            MagicMock(), now=now, limit=limit, warning_lead=lead
        )
