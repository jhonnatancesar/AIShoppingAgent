"""Testes do consumidor proativo de alertas Telegram (TASK-036)."""

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.events import ConsumptionOutcome, Event
from app.missions.models import Mission, MissionStatus
from app.telegram.bot_api import TelegramBotAPIError
from app.telegram.contracts import TelegramChatType, TelegramMessage
from app.telegram.notifications import (
    TELEGRAM_NOTIFICATION_CONSUMER,
    TelegramNotificationError,
    process_telegram_notifications,
    remember_private_notification_chat,
)
from app.users.models import User, UserRole
from pydantic import SecretStr

NOW = datetime(2026, 8, 8, 20, 0, tzinfo=UTC)


def _user(*, chat_id: int | None = 123) -> User:
    return User(
        id=uuid4(),
        display_name="Cliente",
        role=UserRole.USER,
        is_active=True,
        telegram_user_id=123,
        telegram_chat_id=chat_id,
    )


def _mission(user: User) -> Mission:
    return Mission(
        id=uuid4(),
        user_id=user.id,
        title="notebook gamer",
        status=MissionStatus.ACTIVE,
        state_version=1,
    )


def _event(mission: Mission, *, event_type: str = "price.decreased.v1") -> Event:
    payload = {
        "offer_id": str(uuid4()),
        "observation_id": str(uuid4()),
        "previous_observation_id": str(uuid4()),
        "previous_total": "5000.00",
        "current_total": "4499.90",
        "currency": "BRL",
    }
    if event_type == "price.target_reached.v1":
        payload = {
            "mission_id": str(mission.id),
            "offer_id": str(uuid4()),
            "observation_id": str(uuid4()),
            "target_total": "4500.00",
            "current_total": "4499.90",
            "currency": "BRL",
        }
    return Event(
        id=uuid4(),
        event_type=event_type,
        aggregate_type="offer",
        aggregate_id=uuid4(),
        mission_id=mission.id,
        payload=payload,
        occurred_at=NOW,
        recorded_at=NOW,
    )


def test_remember_private_chat_only_for_the_same_person() -> None:
    user = _user(chat_id=None)
    private_message = TelegramMessage(
        chat_id=123,
        chat_type=TelegramChatType.PRIVATE,
        user_id=123,
        text="oi",
        received_at=NOW,
    )

    assert remember_private_notification_chat(user, private_message) is True
    assert user.telegram_chat_id == 123


def test_group_chat_is_never_saved_as_notification_target() -> None:
    user = _user(chat_id=None)
    group_message = TelegramMessage(
        chat_id=-999,
        chat_type=TelegramChatType.GROUP,
        user_id=123,
        text="oi",
        received_at=NOW,
    )

    assert remember_private_notification_chat(user, group_message) is False
    assert user.telegram_chat_id is None


def test_private_chat_rejects_mismatched_identity() -> None:
    user = _user(chat_id=None)
    message = TelegramMessage(
        chat_id=999,
        chat_type=TelegramChatType.PRIVATE,
        user_id=123,
        text="oi",
        received_at=NOW,
    )

    with pytest.raises(
        TelegramNotificationError, match="private_chat_identity_mismatch"
    ):
        remember_private_notification_chat(user, message)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("event_type", "expected_fragment"),
    [
        ("price.decreased.v1", "R$ 4.499,90"),
        ("price.target_reached.v1", "Preço-alvo atingido"),
    ],
)
async def test_process_sends_alert_and_records_success(
    monkeypatch: pytest.MonkeyPatch,
    event_type: str,
    expected_fragment: str,
) -> None:
    user = _user()
    mission = _mission(user)
    event = _event(mission, event_type=event_type)
    session = MagicMock()
    session.get.side_effect = [mission, user]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events",
        lambda *args, **kwargs: [event],
    )
    sent: list[tuple[int, str]] = []

    async def _send(chat_id: int, text: str, *, bot_token: SecretStr) -> None:
        sent.append((chat_id, text))

    monkeypatch.setattr("app.telegram.notifications.send_message", _send)

    result = await process_telegram_notifications(session, bot_token=SecretStr("token"))

    assert result.claimed == result.succeeded == 1
    assert result.failed == 0
    assert sent[0][0] == 123
    assert expected_fragment in sent[0][1]
    attempt = session.add.call_args.args[0]
    assert attempt.consumer_name == TELEGRAM_NOTIFICATION_CONSUMER
    assert attempt.outcome is ConsumptionOutcome.SUCCEEDED
    assert attempt.failure_code is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("chat_id", "is_active", "failure_code"),
    [
        (None, True, "notification_recipient_missing"),
        (123, False, "notification_recipient_inactive"),
    ],
)
async def test_process_records_known_recipient_failure_for_retry(
    monkeypatch: pytest.MonkeyPatch,
    chat_id: int | None,
    is_active: bool,
    failure_code: str,
) -> None:
    user = _user(chat_id=chat_id)
    user.is_active = is_active
    mission = _mission(user)
    event = _event(mission)
    session = MagicMock()
    session.get.side_effect = [mission, user]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events",
        lambda *args, **kwargs: [event],
    )
    send = MagicMock()
    monkeypatch.setattr("app.telegram.notifications.send_message", send)

    result = await process_telegram_notifications(session, bot_token=SecretStr("token"))

    assert result.failed == 1
    send.assert_not_called()
    attempt = session.add.call_args.args[0]
    assert attempt.outcome is ConsumptionOutcome.FAILED
    assert attempt.failure_code == failure_code


@pytest.mark.anyio
async def test_process_records_api_rejection_without_leaking_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _user()
    mission = _mission(user)
    event = _event(mission)
    session = MagicMock()
    session.get.side_effect = [mission, user]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events",
        lambda *args, **kwargs: [event],
    )

    async def _reject(*args: object, **kwargs: object) -> None:
        raise TelegramBotAPIError(403)

    monkeypatch.setattr("app.telegram.notifications.send_message", _reject)

    result = await process_telegram_notifications(session, bot_token=SecretStr("token"))

    assert result.failed == 1
    assert session.add.call_args.args[0].failure_code == "telegram_delivery_failed"


@pytest.mark.anyio
async def test_process_records_invalid_payload_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _user()
    mission = _mission(user)
    event = _event(mission)
    event.payload = {"currency": "BRL", "current_total": "invalid"}
    session = MagicMock()
    session.get.side_effect = [mission, user]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events",
        lambda *args, **kwargs: [event],
    )

    result = await process_telegram_notifications(session, bot_token=SecretStr("token"))

    assert result.failed == 1
    assert session.add.call_args.args[0].failure_code == "notification_payload_invalid"
