"""Testes do consumidor proativo de alertas Telegram (TASK-036)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.authentication.models import CredentialAction, UserAuthSession
from app.events import ConsumptionOutcome, Event
from app.missions.models import Mission, MissionStatus
from app.offers.models import Offer
from app.products.models import Product
from app.stores.models import Store
from app.telegram.bot_api import TelegramBotAPIError
from app.telegram.contracts import TelegramChatType, TelegramMessage
from app.telegram.notifications import (
    TELEGRAM_AUTH_NOTIFICATION_CONSUMER,
    TELEGRAM_NOTIFICATION_CONSUMER,
    TELEGRAM_PRELIST_CONSUMER,
    TelegramNotificationError,
    process_telegram_authentication_notifications,
    process_telegram_notifications,
    process_telegram_prelist_notifications,
    remember_private_notification_chat,
)
from app.users.models import User, UserRole
from pydantic import SecretStr

NOW = datetime(2026, 8, 8, 20, 0, tzinfo=UTC)


def _user(
    *,
    chat_id: int | None = 123,
    notify_price_decreases: bool = True,
    notify_target_reached: bool = True,
) -> User:
    return User(
        id=uuid4(),
        display_name="Cliente",
        role=UserRole.USER,
        is_active=True,
        telegram_user_id=123,
        telegram_chat_id=chat_id,
        notify_price_decreases=notify_price_decreases,
        notify_target_reached=notify_target_reached,
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


def _offer_context() -> tuple[Offer, Product, Store]:
    product_id, store_id = uuid4(), uuid4()
    offer = Offer(
        id=uuid4(),
        product_id=product_id,
        store_id=store_id,
        url="https://example.invalid/anuncio-real",
    )
    product = Product(id=product_id, name="Título bruto do anúncio")
    store = Store(
        id=store_id,
        code="kabum",
        name="Kabum",
        base_url="https://kabum.example.invalid",
    )
    return offer, product, store


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
        ("price.target_reached.v1", "PREÇO ENCONTRADO"),
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
    offer, product, store = _offer_context()
    session = MagicMock()
    session.get.side_effect = [mission, user, offer, product, store]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events",
        lambda *args, **kwargs: [event],
    )
    sent: list[tuple[int, str]] = []

    async def _send(
        chat_id: int, text: str, *, bot_token: SecretStr, **kwargs: object
    ) -> None:
        sent.append((chat_id, text))

    monkeypatch.setattr("app.telegram.notifications.send_message", _send)

    result = await process_telegram_notifications(session, bot_token=SecretStr("token"))

    assert result.claimed == result.succeeded == 1
    assert result.failed == 0
    assert result.skipped == 0
    assert sent[0][0] == 123
    assert expected_fragment in sent[0][1]
    attempt = session.add.call_args.args[0]
    assert attempt.consumer_name == TELEGRAM_NOTIFICATION_CONSUMER
    assert attempt.outcome is ConsumptionOutcome.SUCCEEDED
    assert attempt.failure_code is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("event_type", "user_overrides"),
    [
        ("price.decreased.v1", {"notify_price_decreases": False}),
        ("price.target_reached.v1", {"notify_target_reached": False}),
    ],
)
async def test_process_records_disabled_preference_as_terminal_skipped(
    monkeypatch: pytest.MonkeyPatch,
    event_type: str,
    user_overrides: dict[str, bool],
) -> None:
    user = _user(**user_overrides)
    mission = _mission(user)
    event = _event(mission, event_type=event_type)
    session = MagicMock()
    session.get.side_effect = [mission, user]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events",
        lambda *args, **kwargs: [event],
    )
    send = MagicMock()
    monkeypatch.setattr("app.telegram.notifications.send_message", send)

    result = await process_telegram_notifications(session, bot_token=SecretStr("token"))

    assert result.claimed == result.skipped == 1
    assert result.succeeded == result.failed == 0
    send.assert_not_called()
    attempt = session.add.call_args.args[0]
    assert attempt.outcome is ConsumptionOutcome.SKIPPED
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
    offer, product, store = _offer_context()
    session = MagicMock()
    session.get.side_effect = [mission, user, offer, product, store]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events",
        lambda *args, **kwargs: [event],
    )

    async def _reject(*args: object, **kwargs: object) -> None:
        raise TelegramBotAPIError(403)

    monkeypatch.setattr("app.telegram.notifications.send_message", _reject)

    result = await process_telegram_notifications(session, bot_token=SecretStr("token"))

    assert result.dead_lettered == 1
    assert session.add.call_args.args[0].failure_code == "telegram_api_rejected"


@pytest.mark.anyio
async def test_process_records_invalid_payload_as_permanent_dead_letter(
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

    assert result.dead_lettered == 1
    assert session.add.call_args.args[0].failure_code == "notification_payload_invalid"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (CredentialAction.SET_PASSWORD, "Senha criada"),
        (CredentialAction.LOGIN, "Login realizado"),
        (CredentialAction.CHANGE_PASSWORD, "Senha alterada"),
        (CredentialAction.RECOVER_PASSWORD, "Senha recuperada"),
    ],
)
async def test_authentication_completion_is_sent_despite_price_preferences(
    monkeypatch: pytest.MonkeyPatch,
    action: CredentialAction,
    expected: str,
) -> None:
    user = _user(notify_price_decreases=False, notify_target_reached=False)
    event = Event(
        id=uuid4(),
        event_type="authentication.completed.v1",
        aggregate_type="user",
        aggregate_id=user.id,
        mission_id=None,
        payload={"user_id": str(user.id), "action": action.value},
        occurred_at=NOW,
        recorded_at=NOW,
    )
    session = MagicMock()
    session.get.return_value = user
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events",
        lambda *args, **kwargs: [event],
    )
    sent: list[str] = []

    async def _send(chat_id: int, text: str, **kwargs: object) -> None:
        sent.append(text)

    monkeypatch.setattr("app.telegram.notifications.send_message", _send)

    result = await process_telegram_authentication_notifications(
        session, bot_token=SecretStr("token")
    )

    assert result.succeeded == 1
    assert expected in sent[0]
    attempt = session.add.call_args.args[0]
    assert attempt.consumer_name == TELEGRAM_AUTH_NOTIFICATION_CONSUMER
    assert attempt.outcome is ConsumptionOutcome.SUCCEEDED


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("event_type", "expected"),
    [
        ("authentication.session_expiring.v1", "expira em breve"),
        ("authentication.session_expired.v1", "sessão expirou"),
    ],
)
async def test_session_lifecycle_message_uses_exact_persisted_session(
    monkeypatch: pytest.MonkeyPatch, event_type: str, expected: str
) -> None:
    user = _user()
    expires_at = (
        datetime(2030, 8, 9, 18, 30, tzinfo=UTC)
        if event_type == "authentication.session_expiring.v1"
        else NOW - timedelta(minutes=1)
    )
    auth_session = UserAuthSession(
        id=uuid4(),
        user_id=user.id,
        telegram_user_id=123,
        authenticated_at=expires_at - timedelta(hours=12),
        expires_at=expires_at,
        revoked_at=None,
    )
    event = Event(
        id=uuid4(),
        event_type=event_type,
        aggregate_type="auth_session",
        aggregate_id=auth_session.id,
        mission_id=None,
        payload={
            "session_id": str(auth_session.id),
            "user_id": str(user.id),
            "expires_at": expires_at.isoformat(),
        },
        occurred_at=NOW,
        recorded_at=NOW,
    )
    session = MagicMock()
    session.get.side_effect = [auth_session, user]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events",
        lambda *args, **kwargs: [event],
    )
    sent: list[str] = []

    async def _send(chat_id: int, text: str, **kwargs: object) -> None:
        sent.append(text)

    monkeypatch.setattr("app.telegram.notifications.send_message", _send)

    result = await process_telegram_authentication_notifications(
        session, bot_token=SecretStr("token")
    )

    assert result.succeeded == 1
    assert expected in sent[0]


@pytest.mark.anyio
async def test_revoked_session_warning_is_terminal_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _user()
    expires_at = datetime(2030, 8, 9, 18, 30, tzinfo=UTC)
    auth_session = UserAuthSession(
        id=uuid4(),
        user_id=user.id,
        telegram_user_id=123,
        authenticated_at=expires_at - timedelta(hours=12),
        expires_at=expires_at,
        revoked_at=NOW,
    )
    event = Event(
        id=uuid4(),
        event_type="authentication.session_expiring.v1",
        aggregate_type="auth_session",
        aggregate_id=auth_session.id,
        payload={
            "session_id": str(auth_session.id),
            "user_id": str(user.id),
            "expires_at": expires_at.isoformat(),
        },
        occurred_at=NOW,
        recorded_at=NOW,
    )
    session = MagicMock()
    session.get.return_value = auth_session
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events",
        lambda *args, **kwargs: [event],
    )
    send = MagicMock()
    monkeypatch.setattr("app.telegram.notifications.send_message", send)

    result = await process_telegram_authentication_notifications(
        session, bot_token=SecretStr("token")
    )

    assert result.skipped == 1
    send.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize("payload", [{"action": "unknown"}, {"action": "login"}])
async def test_authentication_event_fails_closed_for_invalid_identity_or_action(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, str]
) -> None:
    user = _user()
    event_payload = {"user_id": str(user.id), **payload}
    event = Event(
        id=uuid4(),
        event_type="authentication.completed.v1",
        aggregate_type="user",
        aggregate_id=user.id,
        payload=event_payload,
        occurred_at=NOW,
        recorded_at=NOW,
    )
    session = MagicMock()
    session.get.return_value = user if payload["action"] == "unknown" else None
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events",
        lambda *args, **kwargs: [event],
    )

    result = await process_telegram_authentication_notifications(
        session, bot_token=SecretStr("token")
    )

    assert result.dead_lettered == 1
    assert session.add.call_args.args[0].failure_code in {
        "notification_payload_invalid",
        "notification_recipient_missing",
    }


def _second_offer_context() -> tuple[Offer, Product, Store]:
    product_id, store_id = uuid4(), uuid4()
    offer = Offer(
        id=uuid4(),
        product_id=product_id,
        store_id=store_id,
        url="https://example.invalid/segundo-anuncio",
    )
    product = Product(id=product_id, name="Segundo produto")
    store = Store(
        id=store_id,
        code="pichau",
        name="Pichau",
        base_url="https://pichau.example.invalid",
    )
    return offer, product, store


def _ready_event(
    mission: Mission,
    offer: Offer,
    *,
    second_offer: Offer | None = None,
) -> Event:
    payload: dict[str, object] = {
        "mission_id": str(mission.id),
        "first_offer_id": str(offer.id),
        "first_observation_id": str(uuid4()),
        "first_amount": "1900.00",
        "first_currency": "BRL",
        "second_offer_id": None,
        "second_observation_id": None,
        "second_amount": None,
        "second_currency": None,
    }
    if second_offer is not None:
        payload.update(
            second_offer_id=str(second_offer.id),
            second_observation_id=str(uuid4()),
            second_amount="2100.00",
            second_currency="BRL",
        )
    return Event(
        id=uuid4(),
        event_type="mission.prelist_ready.v1",
        aggregate_type="mission",
        aggregate_id=mission.id,
        mission_id=mission.id,
        payload=payload,
        occurred_at=NOW,
        recorded_at=NOW,
    )


def _errata_event(
    mission: Mission, offer: Offer, *, previous_lowest_amount: str | None
) -> Event:
    return Event(
        id=uuid4(),
        event_type="mission.prelist_errata.v1",
        aggregate_type="mission",
        aggregate_id=mission.id,
        mission_id=mission.id,
        payload={
            "mission_id": str(mission.id),
            "offer_id": str(offer.id),
            "observation_id": str(uuid4()),
            "current_amount": "1500.00",
            "currency": "BRL",
            "previous_lowest_amount": previous_lowest_amount,
        },
        occurred_at=NOW,
        recorded_at=NOW,
    )


@pytest.mark.anyio
async def test_prelist_ready_sends_one_block_when_only_one_store_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _user()
    mission = _mission(user)
    offer, product, store = _offer_context()
    event = _ready_event(mission, offer)
    session = MagicMock()
    session.get.side_effect = [mission, user, offer, product, store]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events",
        lambda *args, **kwargs: [event],
    )
    sent: list[str] = []

    async def _send(chat_id: int, text: str, **kwargs: object) -> None:
        sent.append(text)

    monkeypatch.setattr("app.telegram.notifications.send_message", _send)

    result = await process_telegram_prelist_notifications(
        session, bot_token=SecretStr("token")
    )

    assert result.succeeded == 1
    assert "MELHORES OFERTAS" in sent[0]
    assert "R$ 1.900,00" in sent[0]
    assert store.name in sent[0]
    assert sent[0].count("🏪") == 1  # só uma loja respondeu ainda
    assert "sem frete" in sent[0].lower()
    attempt = session.add.call_args.args[0]
    assert attempt.consumer_name == TELEGRAM_PRELIST_CONSUMER


@pytest.mark.anyio
async def test_prelist_ready_sends_two_blocks_cheapest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _user()
    mission = _mission(user)
    offer, product, store = _offer_context()
    second_offer, second_product, second_store = _second_offer_context()
    event = _ready_event(mission, offer, second_offer=second_offer)
    session = MagicMock()
    session.get.side_effect = [
        mission,
        user,
        offer,
        product,
        store,
        second_offer,
        second_product,
        second_store,
    ]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events",
        lambda *args, **kwargs: [event],
    )
    sent: list[str] = []

    async def _send(chat_id: int, text: str, **kwargs: object) -> None:
        sent.append(text)

    monkeypatch.setattr("app.telegram.notifications.send_message", _send)

    result = await process_telegram_prelist_notifications(
        session, bot_token=SecretStr("token")
    )

    assert result.succeeded == 1
    assert sent[0].count("🏪") == 2
    assert sent[0].index("R$ 1.900,00") < sent[0].index("R$ 2.100,00")


@pytest.mark.anyio
async def test_prelist_ready_ignores_price_preferences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TASK-068: a pré-lista nunca é bloqueada por notify_price_decreases/
    notify_target_reached -- essas preferências são só da TASK-037."""
    user = _user(notify_price_decreases=False, notify_target_reached=False)
    mission = _mission(user)
    offer, product, store = _offer_context()
    event = _ready_event(mission, offer)
    session = MagicMock()
    session.get.side_effect = [mission, user, offer, product, store]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events",
        lambda *args, **kwargs: [event],
    )

    async def _send(chat_id: int, text: str, **kwargs: object) -> None:
        pass

    monkeypatch.setattr("app.telegram.notifications.send_message", _send)

    result = await process_telegram_prelist_notifications(
        session, bot_token=SecretStr("token")
    )

    assert result.succeeded == 1
    assert result.skipped == 0


@pytest.mark.anyio
async def test_prelist_errata_message_frames_correction_vs_first_find(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _user()
    mission = _mission(user)
    offer, product, store = _offer_context()
    correction_event = _errata_event(mission, offer, previous_lowest_amount="1900.00")
    session = MagicMock()
    session.get.side_effect = [mission, user, offer, product, store]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events",
        lambda *args, **kwargs: [correction_event],
    )
    sent: list[str] = []

    async def _send(chat_id: int, text: str, **kwargs: object) -> None:
        sent.append(text)

    monkeypatch.setattr("app.telegram.notifications.send_message", _send)

    result = await process_telegram_prelist_notifications(
        session, bot_token=SecretStr("token")
    )

    assert result.succeeded == 1
    assert "CORREÇÃO" in sent[0]
    assert "R$ 1.500,00" in sent[0]
    assert "sem frete" in sent[0].lower()


@pytest.mark.anyio
async def test_prelist_errata_frames_first_find_without_previous_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _user()
    mission = _mission(user)
    offer, product, store = _offer_context()
    first_find_event = _errata_event(mission, offer, previous_lowest_amount=None)
    session = MagicMock()
    session.get.side_effect = [mission, user, offer, product, store]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events",
        lambda *args, **kwargs: [first_find_event],
    )
    sent: list[str] = []

    async def _send(chat_id: int, text: str, **kwargs: object) -> None:
        sent.append(text)

    monkeypatch.setattr("app.telegram.notifications.send_message", _send)

    result = await process_telegram_prelist_notifications(
        session, bot_token=SecretStr("token")
    )

    assert result.succeeded == 1
    assert "PRIMEIRA OFERTA" in sent[0]
    assert "CORREÇÃO" not in sent[0]
    assert "sem frete" in sent[0].lower()


@pytest.mark.anyio
async def test_prelist_notification_fails_closed_on_missing_recipient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _user(chat_id=None)
    mission = _mission(user)
    offer, _product, _store = _offer_context()
    event = _ready_event(mission, offer)
    session = MagicMock()
    session.get.side_effect = [mission, user]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events",
        lambda *args, **kwargs: [event],
    )
    send = MagicMock()
    monkeypatch.setattr("app.telegram.notifications.send_message", send)

    result = await process_telegram_prelist_notifications(
        session, bot_token=SecretStr("token")
    )

    assert result.failed == 1
    send.assert_not_called()
    assert (
        session.add.call_args.args[0].failure_code == "notification_recipient_missing"
    )
