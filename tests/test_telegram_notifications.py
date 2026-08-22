"""Testes do consumidor proativo de alertas Telegram (TASK-036)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from app.authentication.models import CredentialAction, UserAuthSession
from app.collection.contracts import (
    InstallmentInterestKind,
    MarketplacePartyKind,
    OfferCondition,
)
from app.collection.models import OfferInstallmentOption, PriceObservation
from app.collection.normalization import Availability
from app.collection.relevance import OfferRelevance
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
    _installment_line,
    _marketplace_party_line,
    _rating_line,
    _render_prelist_v2_async,
    _select_installment_summary_option,
    _telegram_link,
    process_telegram_authentication_notifications,
    process_telegram_notifications,
    process_telegram_prelist_notifications,
    remember_private_notification_chat,
)
from app.users.models import User, UserRole
from pydantic import SecretStr

NOW = datetime(2026, 8, 8, 20, 0, tzinfo=UTC)


def test_rating_line_is_source_bound_and_omits_missing_snapshot() -> None:
    offer = Offer(
        id=uuid4(),
        product_id=uuid4(),
        store_id=uuid4(),
        url="https://example.test/offer",
        rating_average=Decimal("4.80"),
        review_count=2256,
        rating_observed_at=NOW,
    )

    assert _rating_line(offer) == "⭐ 4,8 · 2.256 avaliações\n"
    offer.review_count = None
    assert _rating_line(offer) == ""


def _fake_session_factory() -> tuple[MagicMock, MagicMock]:
    """Fábrica assíncrona falsa (TASK-080): devolve sempre a mesma sessão
    falsa a cada `factory()`, para que um único `session.get.side_effect`
    continue cobrindo, na ordem, todas as leituras feitas ao longo das
    fases A e C -- mesmo efeito prático de um teste que antes usava uma
    única `Session` síncrona para o lote inteiro."""
    session = MagicMock()
    session.get = AsyncMock()
    # `count_failed_attempts_async` usa `session.scalar` -- 0 tentativas
    # falhas anteriores por padrão (primeira tentativa), sobrescrito por
    # teste quando o cenário exigir um valor diferente.
    session.scalar = AsyncMock(return_value=0)
    # `_installment_options_for_observation_async` usa `session.scalars` --
    # sem parcelamento por padrão (nenhuma `OfferInstallmentOption`),
    # sobrescrito por teste quando o cenário exigir opções persistidas.
    session.scalars = AsyncMock(return_value=[])
    session.execute = AsyncMock()
    # `record_consumption_attempt_async` chama `session.flush()` -- `.add`
    # continua síncrono (mesmo em `AsyncSession` real).
    session.flush = AsyncMock()
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    transaction_cm = MagicMock()
    transaction_cm.__aenter__ = AsyncMock(return_value=None)
    transaction_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=transaction_cm)
    factory = MagicMock(return_value=session_cm)
    return factory, session


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


def _observation_for(
    event: Event,
    offer: Offer,
    *,
    field: str = "observation_id",
    kind: MarketplacePartyKind | None = None,
) -> PriceObservation:
    return PriceObservation(
        id=uuid4() if field not in event.payload else UUID(str(event.payload[field])),
        offer_id=offer.id,
        collection_run_id=uuid4(),
        amount="1.00",
        currency="BRL",
        total_amount="1.00",
        availability="available",
        observed_at=NOW,
        seller_kind=kind,
        fulfillment_kind=kind,
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


@pytest.mark.parametrize(
    ("kind", "expected"),
    (
        (MarketplacePartyKind.PLATFORM, "📦 Vendido e entregue por: KaBuM!"),
        (
            MarketplacePartyKind.MARKETPLACE_PARTNER,
            "📦 Vendido e entregue por: Loja parceira Kabum",
        ),
        (MarketplacePartyKind.UNKNOWN, "📦 Vendedor e entrega não identificados"),
        (None, ""),
    ),
)
def test_marketplace_party_line_uses_historical_classification(kind, expected) -> None:
    offer, _product, store = _offer_context()
    event = Event(
        payload={},
        id=uuid4(),
        event_type="x",
        aggregate_type="x",
        aggregate_id=uuid4(),
        occurred_at=NOW,
        recorded_at=NOW,
    )
    observation = _observation_for(event, offer, kind=kind)

    assert _marketplace_party_line(store, observation).strip() == expected


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
        ("price.target_reached.v1", "PREÇO-ALVO ENCONTRADO"),
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
    session_factory, session = _fake_session_factory()
    observation = _observation_for(event, offer, kind=MarketplacePartyKind.PLATFORM)
    session.get.side_effect = [mission, user, offer, product, store, observation, event]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events_async",
        AsyncMock(return_value=[event]),
    )
    sent: list[tuple[int, str]] = []

    async def _send(
        chat_id: int, text: str, *, bot_token: SecretStr, **kwargs: object
    ) -> None:
        sent.append((chat_id, text))

    monkeypatch.setattr("app.telegram.notifications.send_message", _send)

    result = await process_telegram_notifications(
        session_factory, bot_token=SecretStr("token")
    )

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
    session_factory, session = _fake_session_factory()
    session.get.side_effect = [mission, user]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events_async",
        AsyncMock(return_value=[event]),
    )
    send = MagicMock()
    monkeypatch.setattr("app.telegram.notifications.send_message", send)

    result = await process_telegram_notifications(
        session_factory, bot_token=SecretStr("token")
    )

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
    session_factory, session = _fake_session_factory()
    session.get.side_effect = [mission, user]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events_async",
        AsyncMock(return_value=[event]),
    )
    send = MagicMock()
    monkeypatch.setattr("app.telegram.notifications.send_message", send)

    result = await process_telegram_notifications(
        session_factory, bot_token=SecretStr("token")
    )

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
    session_factory, session = _fake_session_factory()
    observation = _observation_for(event, offer)
    session.get.side_effect = [mission, user, offer, product, store, observation, event]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events_async",
        AsyncMock(return_value=[event]),
    )

    async def _reject(*args: object, **kwargs: object) -> None:
        raise TelegramBotAPIError(403)

    monkeypatch.setattr("app.telegram.notifications.send_message", _reject)

    result = await process_telegram_notifications(
        session_factory, bot_token=SecretStr("token")
    )

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
    session_factory, session = _fake_session_factory()
    session.get.side_effect = [mission, user]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events_async",
        AsyncMock(return_value=[event]),
    )

    result = await process_telegram_notifications(
        session_factory, bot_token=SecretStr("token")
    )

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
    session_factory, session = _fake_session_factory()
    session.get = AsyncMock(side_effect=[user, event])
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events_async",
        AsyncMock(return_value=[event]),
    )
    sent: list[str] = []

    async def _send(chat_id: int, text: str, **kwargs: object) -> None:
        sent.append(text)

    monkeypatch.setattr("app.telegram.notifications.send_message", _send)

    result = await process_telegram_authentication_notifications(
        session_factory, bot_token=SecretStr("token")
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
    session_factory, session = _fake_session_factory()
    session.get.side_effect = [auth_session, user, event]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events_async",
        AsyncMock(return_value=[event]),
    )
    sent: list[str] = []

    async def _send(chat_id: int, text: str, **kwargs: object) -> None:
        sent.append(text)

    monkeypatch.setattr("app.telegram.notifications.send_message", _send)

    result = await process_telegram_authentication_notifications(
        session_factory, bot_token=SecretStr("token")
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
    session_factory, session = _fake_session_factory()
    session.get = AsyncMock(return_value=auth_session)
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events_async",
        AsyncMock(return_value=[event]),
    )
    send = MagicMock()
    monkeypatch.setattr("app.telegram.notifications.send_message", send)

    result = await process_telegram_authentication_notifications(
        session_factory, bot_token=SecretStr("token")
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
    session_factory, session = _fake_session_factory()
    session.get = AsyncMock(
        return_value=user if payload["action"] == "unknown" else None
    )
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events_async",
        AsyncMock(return_value=[event]),
    )

    result = await process_telegram_authentication_notifications(
        session_factory, bot_token=SecretStr("token")
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
async def test_prelist_v2_renderer_groups_one_message_per_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mission = _mission(_user())
    stores = [
        Store(id=uuid4(), code="amazon", name="Amazon", base_url="https://amazon.com.br"),
        Store(id=uuid4(), code="kabum", name="KaBuM!", base_url="https://kabum.com.br"),
    ]
    contexts = []
    payload_offers = []
    for index, store in enumerate((stores[0], stores[0], stores[1])):
        product = Product(id=uuid4(), name=f"Produto {index + 1}")
        offer = Offer(
            id=uuid4(),
            product_id=product.id,
            store_id=store.id,
            url=f"https://example.invalid/{index}",
            rating_average=Decimal("4.80") if index == 0 else None,
            review_count=2256 if index == 0 else None,
            rating_observed_at=NOW if index == 0 else None,
        )
        observation = PriceObservation(
            id=uuid4(),
            offer_id=offer.id,
            collection_run_id=uuid4(),
            amount=Decimal(str(100 + index)),
            total_amount=Decimal(str(100 + index)),
            currency="BRL",
            condition=(OfferCondition.NEW if index != 1 else OfferCondition.USED),
            seller_kind=(
                MarketplacePartyKind.PLATFORM
                if index == 0
                else MarketplacePartyKind.MARKETPLACE_PARTNER
            ),
            fulfillment_kind=None,
            availability=Availability.AVAILABLE,
            observed_at=NOW,
        )
        contexts.append((offer, product, store, observation))
        payload_offers.append(
            {
                "offer_id": str(offer.id),
                "observation_id": str(observation.id),
                "store_id": str(store.id),
                "amount": str(observation.amount),
                "total_amount": str(observation.total_amount),
                "currency": "BRL",
                "relevance": OfferRelevance.MATCH.value,
                "condition": observation.condition.value,
                "seller_kind": observation.seller_kind.value,
                "availability": observation.availability.value,
            }
        )
    objects = {
        (Offer, context[0].id): context[0]
        for context in contexts
    } | {
        (Product, context[1].id): context[1]
        for context in contexts
    } | {
        (Store, store.id): store
        for store in stores
    } | {
        (PriceObservation, context[3].id): context[3]
        for context in contexts
    }
    session = MagicMock()
    session.get = AsyncMock(side_effect=lambda model, key: objects.get((model, key)))
    session.scalars = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "app.telegram.notifications.get_or_create_offer_short_link",
        AsyncMock(return_value=MagicMock(token="token")),
    )
    event = Event(
        id=uuid4(),
        event_type="mission.prelist_ready.v2",
        aggregate_type="mission",
        aggregate_id=mission.id,
        mission_id=mission.id,
        payload={"mission_id": str(mission.id), "offers": payload_offers},
        occurred_at=NOW,
        recorded_at=NOW,
    )

    parts = await _render_prelist_v2_async(
        session,
        event,
        mission.title,
        public_base_url="https://agent.example",
        errata=False,
    )

    assert len(parts) == 2
    assert "Amazon — opções encontradas" in parts[0].text
    assert "Produto 1" in parts[0].text and "Produto 2" in parts[0].text
    assert "⭐ 4,8 · 2.256 avaliações" in parts[0].text
    assert "Usado" in parts[0].text
    assert "KaBuM! — opções encontradas" in parts[1].text


@pytest.mark.anyio
async def test_prelist_ready_sends_one_block_when_only_one_store_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _user()
    mission = _mission(user)
    offer, product, store = _offer_context()
    event = _ready_event(mission, offer)
    session_factory, session = _fake_session_factory()
    observation = _observation_for(event, offer, field="first_observation_id")
    session.get.side_effect = [mission, user, offer, product, store, observation, event]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events_async",
        AsyncMock(return_value=[event]),
    )
    sent: list[str] = []

    async def _send(chat_id: int, text: str, **kwargs: object) -> None:
        sent.append(text)

    monkeypatch.setattr("app.telegram.notifications.send_message", _send)

    result = await process_telegram_prelist_notifications(
        session_factory, bot_token=SecretStr("token")
    )

    assert result.succeeded == 1
    assert "MELHORES OFERTAS" in sent[0]
    assert "R$ 1.900,00" in sent[0]
    assert store.name in sent[0]
    assert sent[0].count("🏪") == 1  # só uma loja respondeu ainda
    assert "Frete não incluído. Consulte o valor na loja." in sent[0]
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
    first_observation = _observation_for(event, offer, field="first_observation_id")
    second_observation = _observation_for(
        event, second_offer, field="second_observation_id"
    )
    session_factory, session = _fake_session_factory()
    session.get.side_effect = [
        mission,
        user,
        offer,
        product,
        store,
        first_observation,
        second_offer,
        second_product,
        second_store,
        second_observation,
        event,
    ]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events_async",
        AsyncMock(return_value=[event]),
    )
    sent: list[str] = []

    async def _send(chat_id: int, text: str, **kwargs: object) -> None:
        sent.append(text)

    monkeypatch.setattr("app.telegram.notifications.send_message", _send)

    result = await process_telegram_prelist_notifications(
        session_factory, bot_token=SecretStr("token")
    )

    assert result.succeeded == 1
    assert len(sent) == 2
    assert sent[0].count("🏪") == sent[1].count("🏪") == 1
    assert "R$ 1.900,00" in sent[0]
    assert "R$ 2.100,00" in sent[1]


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
    session_factory, session = _fake_session_factory()
    observation = _observation_for(event, offer, field="first_observation_id")
    session.get.side_effect = [mission, user, offer, product, store, observation, event]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events_async",
        AsyncMock(return_value=[event]),
    )

    async def _send(chat_id: int, text: str, **kwargs: object) -> None:
        pass

    monkeypatch.setattr("app.telegram.notifications.send_message", _send)

    result = await process_telegram_prelist_notifications(
        session_factory, bot_token=SecretStr("token")
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
    session_factory, session = _fake_session_factory()
    observation = _observation_for(correction_event, offer)
    session.get.side_effect = [
        mission,
        user,
        offer,
        product,
        store,
        observation,
        correction_event,
    ]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events_async",
        AsyncMock(return_value=[correction_event]),
    )
    sent: list[str] = []

    async def _send(chat_id: int, text: str, **kwargs: object) -> None:
        sent.append(text)

    monkeypatch.setattr("app.telegram.notifications.send_message", _send)

    result = await process_telegram_prelist_notifications(
        session_factory, bot_token=SecretStr("token")
    )

    assert result.succeeded == 1
    assert "ATUALIZAÇÃO DA PRÉ-LISTA" in sent[0]
    assert "R$ 1.500,00" in sent[0]
    assert "Frete não incluído. Consulte o valor na loja." in sent[0]


@pytest.mark.anyio
async def test_prelist_errata_frames_first_find_without_previous_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _user()
    mission = _mission(user)
    offer, product, store = _offer_context()
    first_find_event = _errata_event(mission, offer, previous_lowest_amount=None)
    session_factory, session = _fake_session_factory()
    observation = _observation_for(first_find_event, offer)
    session.get.side_effect = [
        mission,
        user,
        offer,
        product,
        store,
        observation,
        first_find_event,
    ]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events_async",
        AsyncMock(return_value=[first_find_event]),
    )
    sent: list[str] = []

    async def _send(chat_id: int, text: str, **kwargs: object) -> None:
        sent.append(text)

    monkeypatch.setattr("app.telegram.notifications.send_message", _send)

    result = await process_telegram_prelist_notifications(
        session_factory, bot_token=SecretStr("token")
    )

    assert result.succeeded == 1
    assert "PRIMEIRA OFERTA" in sent[0]
    assert "CORREÇÃO" not in sent[0]
    assert "Frete não incluído. Consulte o valor na loja." in sent[0]


@pytest.mark.anyio
async def test_prelist_notification_fails_closed_on_missing_recipient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _user(chat_id=None)
    mission = _mission(user)
    offer, _product, _store = _offer_context()
    event = _ready_event(mission, offer)
    session_factory, session = _fake_session_factory()
    session.get.side_effect = [mission, user]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events_async",
        AsyncMock(return_value=[event]),
    )
    send = MagicMock()
    monkeypatch.setattr("app.telegram.notifications.send_message", send)

    result = await process_telegram_prelist_notifications(
        session_factory, bot_token=SecretStr("token")
    )

    assert result.failed == 1
    send.assert_not_called()
    assert (
        session.add.call_args.args[0].failure_code == "notification_recipient_missing"
    )


# --- TASK-089 (extensão Telegram): 💰 À vista / 💳 Parcelado ---


def _installment_option(
    *,
    count: int,
    amount: str,
    total: str | None = None,
    discount: str | None = None,
    interest: InstallmentInterestKind = InstallmentInterestKind.UNKNOWN,
    highlighted: bool = False,
) -> OfferInstallmentOption:
    return OfferInstallmentOption(
        id=uuid4(),
        price_observation_id=uuid4(),
        installment_count=count,
        installment_amount=Decimal(amount),
        installment_total_amount=Decimal(total) if total is not None else None,
        discount_percent=Decimal(discount) if discount is not None else None,
        interest_kind=interest,
        is_highlighted=highlighted,
    )


def test_installment_line_is_empty_without_any_option() -> None:
    assert _installment_line((), "BRL") == ""


def test_installment_line_interest_free() -> None:
    option = _installment_option(
        count=12, amount="421.57", interest=InstallmentInterestKind.INTEREST_FREE
    )

    line = _installment_line((option,), "BRL")

    assert line == "💳 Parcelado: 12x de R$ 421,57 sem juros\n"


def test_installment_line_with_interest() -> None:
    option = _installment_option(
        count=6, amount="550.00", interest=InstallmentInterestKind.WITH_INTEREST
    )

    line = _installment_line((option,), "BRL")

    assert line == "💳 Parcelado: 6x de R$ 550,00 com juros\n"


def test_installment_line_unknown_interest_never_shows_the_word_unknown() -> None:
    option = _installment_option(
        count=3, amount="1433.33", interest=InstallmentInterestKind.UNKNOWN
    )

    line = _installment_line((option,), "BRL")

    assert line == "💳 Parcelado: 3x de R$ 1.433,33\n"
    assert "unknown" not in line.lower()
    assert "none" not in line.lower()
    assert "null" not in line.lower()


def test_installment_line_shows_explicit_total() -> None:
    option = _installment_option(
        count=12,
        amount="421.57",
        total="5058.81",
        interest=InstallmentInterestKind.INTEREST_FREE,
    )

    line = _installment_line((option,), "BRL")

    assert line == "💳 Parcelado: 12x de R$ 421,57 sem juros — total R$ 5.058,81\n"


def test_installment_line_omits_total_when_store_never_declared_one() -> None:
    option = _installment_option(
        count=12, amount="421.57", interest=InstallmentInterestKind.INTEREST_FREE
    )

    line = _installment_line((option,), "BRL")

    assert "total" not in line
    assert line == "💳 Parcelado: 12x de R$ 421,57 sem juros\n"


def test_select_installment_summary_prefers_the_option_highlighted_by_the_card() -> (
    None
):
    """TASK-089 (regra do resumo): a opção destacada pela própria loja no
    card da busca vence qualquer outro critério de desempate."""
    highlighted = _installment_option(
        count=3,
        amount="1433.33",
        interest=InstallmentInterestKind.INTEREST_FREE,
        highlighted=True,
    )
    longer_interest_free = _installment_option(
        count=12, amount="421.57", interest=InstallmentInterestKind.INTEREST_FREE
    )

    selected = _select_installment_summary_option((longer_interest_free, highlighted))

    assert selected is highlighted


def test_select_installment_summary_falls_back_to_longest_interest_free() -> None:
    """Sem opção destacada conhecida, usa a maior quantidade de parcelas
    SEM JUROS -- nunca a de maior parcela absoluta nem a com juros."""
    short_interest_free = _installment_option(
        count=3, amount="1433.33", interest=InstallmentInterestKind.INTEREST_FREE
    )
    long_interest_free = _installment_option(
        count=12, amount="421.57", interest=InstallmentInterestKind.INTEREST_FREE
    )
    longest_with_interest = _installment_option(
        count=18, amount="300.00", interest=InstallmentInterestKind.WITH_INTEREST
    )

    selected = _select_installment_summary_option(
        (short_interest_free, longest_with_interest, long_interest_free)
    )

    assert selected is long_interest_free


def test_select_installment_summary_falls_back_to_longest_overall_without_interest_free() -> (
    None
):
    with_interest = _installment_option(
        count=6, amount="550.00", interest=InstallmentInterestKind.WITH_INTEREST
    )
    unknown = _installment_option(
        count=10, amount="330.00", interest=InstallmentInterestKind.UNKNOWN
    )

    selected = _select_installment_summary_option((with_interest, unknown))

    assert selected is unknown


def test_select_installment_summary_returns_none_without_options() -> None:
    assert _select_installment_summary_option(()) is None


@pytest.mark.anyio
async def test_alert_shows_cash_and_installment_lines_from_real_persisted_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _user()
    mission = _mission(user)
    event = _event(mission, event_type="price.decreased.v1")
    offer, product, store = _offer_context()
    session_factory, session = _fake_session_factory()
    observation = _observation_for(event, offer, kind=MarketplacePartyKind.PLATFORM)
    session.get.side_effect = [mission, user, offer, product, store, observation, event]
    session.scalars = AsyncMock(
        return_value=[
            _installment_option(
                count=12,
                amount="421.57",
                total="5058.81",
                interest=InstallmentInterestKind.INTEREST_FREE,
                highlighted=True,
            )
        ]
    )
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events_async",
        AsyncMock(return_value=[event]),
    )
    sent: list[str] = []

    async def _send(chat_id: int, text: str, *, bot_token, **kwargs: object) -> None:
        sent.append(text)

    monkeypatch.setattr("app.telegram.notifications.send_message", _send)

    result = await process_telegram_notifications(
        session_factory, bot_token=SecretStr("token")
    )

    assert result.succeeded == 1
    text = sent[0]
    assert "💰 À vista: R$ 4.499,90" in text
    assert "💳 Parcelado: 12x de R$ 421,57 sem juros — total R$ 5.058,81" in text
    # sem linha vazia entre "À vista" e "Parcelado", nem entre "Parcelado"
    # e a linha seguinte (nunca duas quebras seguidas fora dos separadores
    # de bloco intencionais do template).
    assert "\n\n" not in text.split("💰 À vista")[1].split("🔎 Missão")[0]


@pytest.mark.anyio
async def test_alert_omits_installment_line_when_offer_has_no_confirmed_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _user()
    mission = _mission(user)
    event = _event(mission, event_type="price.decreased.v1")
    offer, product, store = _offer_context()
    session_factory, session = _fake_session_factory()
    observation = _observation_for(event, offer, kind=MarketplacePartyKind.PLATFORM)
    session.get.side_effect = [mission, user, offer, product, store, observation, event]
    session.scalars = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events_async",
        AsyncMock(return_value=[event]),
    )
    sent: list[str] = []

    async def _send(chat_id: int, text: str, *, bot_token, **kwargs: object) -> None:
        sent.append(text)

    monkeypatch.setattr("app.telegram.notifications.send_message", _send)

    result = await process_telegram_notifications(
        session_factory, bot_token=SecretStr("token")
    )

    assert result.succeeded == 1
    text = sent[0]
    assert "💳" not in text
    assert "💰 À vista: R$ 4.499,90\n↘️ Preço anterior" in text


@pytest.mark.anyio
async def test_prelist_ready_shows_only_the_highlighted_option_among_many(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pichau/Terabyte podem persistir muitas opções para a mesma
    observação -- o card deve mostrar só o resumo, nunca a tabela
    inteira."""
    user = _user()
    mission = _mission(user)
    offer, product, store = _offer_context()
    event = _ready_event(mission, offer)
    session_factory, session = _fake_session_factory()
    observation = _observation_for(event, offer, field="first_observation_id")
    session.get.side_effect = [mission, user, offer, product, store, observation, event]
    session.scalars = AsyncMock(
        return_value=[
            _installment_option(count=1, amount="1900.00"),
            _installment_option(
                count=6,
                amount="331.90",
                interest=InstallmentInterestKind.INTEREST_FREE,
                highlighted=True,
            ),
            _installment_option(
                count=12,
                amount="180.00",
                interest=InstallmentInterestKind.WITH_INTEREST,
            ),
        ]
    )
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events_async",
        AsyncMock(return_value=[event]),
    )
    sent: list[str] = []

    async def _send(chat_id: int, text: str, **kwargs: object) -> None:
        sent.append(text)

    monkeypatch.setattr("app.telegram.notifications.send_message", _send)

    result = await process_telegram_prelist_notifications(
        session_factory, bot_token=SecretStr("token")
    )

    assert result.succeeded == 1
    text = sent[0]
    assert text.count("💳") == 1
    assert "💳 Parcelado: 6x de R$ 331,90 sem juros" in text
    assert "12x de R$ 180,00" not in text
    assert "1x de R$ 1.900,00" not in text


# --- correção: links devem chegar como entidade HTML clicável, não texto ---


def test_telegram_link_wraps_url_in_anchor_tag() -> None:
    link = _telegram_link("https://exemplo.com/o/ab12cd34")

    assert (
        link
        == '<a href="https://exemplo.com/o/ab12cd34">https://exemplo.com/o/ab12cd34</a>'
    )


def test_telegram_link_escapes_special_characters() -> None:
    link = _telegram_link('https://exemplo.com/o/ab?x=1&y="2"')

    assert "&amp;" in link
    assert "&quot;" in link
    assert '"2"' not in link  # aspas cruas quebrariam o atributo href


@pytest.mark.anyio
async def test_alert_sends_html_parse_mode_with_clickable_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Bot API não garante auto-detecção de URL em texto plano -- o
    link só fica clicável com `parse_mode="HTML"` e uma entidade
    `<a href="...">` explícita."""
    user = _user()
    mission = _mission(user)
    event = _event(mission, event_type="price.decreased.v1")
    offer, product, store = _offer_context()
    session_factory, session = _fake_session_factory()
    observation = _observation_for(event, offer, kind=MarketplacePartyKind.PLATFORM)
    session.get.side_effect = [mission, user, offer, product, store, observation, event]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events_async",
        AsyncMock(return_value=[event]),
    )
    captured: dict[str, object] = {}

    async def _send(chat_id: int, text: str, *, bot_token, **kwargs: object) -> None:
        captured["text"] = text
        captured["parse_mode"] = kwargs.get("parse_mode")

    monkeypatch.setattr("app.telegram.notifications.send_message", _send)

    result = await process_telegram_notifications(
        session_factory, bot_token=SecretStr("token")
    )

    assert result.succeeded == 1
    assert captured["parse_mode"] == "HTML"
    text = captured["text"]
    assert '🔗 Ver anúncio\n<a href="' in text
    assert text.count("<a href=") == 1
    assert text.count("</a>") == 1


@pytest.mark.anyio
async def test_alert_escapes_special_characters_in_dynamic_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Título de produto/nome de loja vêm de texto raspado de terceiros --
    precisam ser escapados para HTML antes do envio com `parse_mode="HTML"`,
    senão `<`/`&` quebram o parser da Bot API ou são interpretados como
    marcação indevida."""
    user = _user()
    mission = _mission(user)
    event = _event(mission, event_type="price.decreased.v1")
    offer, product, store = _offer_context()
    product.name = 'Placa <RTX 4070> & "Super" 12GB'
    store.name = "Loja & Cia"
    session_factory, session = _fake_session_factory()
    observation = _observation_for(event, offer, kind=MarketplacePartyKind.PLATFORM)
    session.get.side_effect = [mission, user, offer, product, store, observation, event]
    monkeypatch.setattr(
        "app.telegram.notifications.claim_unconsumed_events_async",
        AsyncMock(return_value=[event]),
    )
    sent: list[str] = []

    async def _send(chat_id: int, text: str, *, bot_token, **kwargs: object) -> None:
        sent.append(text)

    monkeypatch.setattr("app.telegram.notifications.send_message", _send)

    result = await process_telegram_notifications(
        session_factory, bot_token=SecretStr("token")
    )

    assert result.succeeded == 1
    text = sent[0]
    assert "Placa &lt;RTX 4070&gt; &amp; &quot;Super&quot; 12GB" in text
    assert "Placa <RTX 4070>" not in text
    assert "Loja &amp; Cia" in text
    assert "Loja & Cia\n" not in text
