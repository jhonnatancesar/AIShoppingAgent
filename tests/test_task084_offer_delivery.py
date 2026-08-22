"""Contratos de segurança e persistência introduzidos pela TASK-084."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.collection.contracts import RawCollectedOffer
from app.collection.errors import CollectionContractError
from app.collection.providers.stores import PichauProvider
from app.core.urls import is_url_compatible_with_store, normalize_http_url
from app.events.models import Event, EventDeliveryCheckpoint
from app.offers.models import Offer, OfferShortLink
from app.offers.router import redirect_offer
from app.offers.short_links import build_offer_short_url
from app.stores.models import Store
from app.telegram.bot_api import TelegramMediaRejected
from app.telegram.notifications import (
    TELEGRAM_PRELIST_CONSUMER,
    _PreparedMessagePart,
    _PreparedNotification,
    _send_and_record,
    _send_part,
)
from pydantic import SecretStr

NOW = datetime(2026, 8, 16, tzinfo=UTC)


def test_http_url_validation_and_store_host_compatibility() -> None:
    assert normalize_http_url("https://cdn.example.test/a.jpg")
    assert normalize_http_url("javascript:alert(1)") is None
    assert normalize_http_url("https://user:pass@example.test/a") is None
    assert is_url_compatible_with_store(
        "https://www.amazon.com.br/dp/ABC", "https://amazon.com.br"
    )
    assert not is_url_compatible_with_store(
        "https://amazon.com.br.evil.test/dp/ABC", "https://amazon.com.br"
    )


def test_raw_offer_rejects_invalid_image_and_provider_ignores_it() -> None:
    with pytest.raises(CollectionContractError, match="image_url"):
        RawCollectedOffer(
            source_code="pichau",
            url="https://www.pichau.com.br/item",
            title="Item",
            collected_at=NOW,
            raw_price="R$ 10,00",
            image_url="data:image/png;base64,abc",
        )
    rows = [
        {
            "url": "https://www.pichau.com.br/item",
            "title": "Item",
            "price": "R$ 10,00",
            "image": "javascript:alert(1)",
        }
    ]
    offer = PichauProvider().offers_from_rows(rows, NOW)[0]
    assert offer.image_url is None


def test_short_url_contains_only_opaque_token_path() -> None:
    assert (
        build_offer_short_url("https://agent.example.test/", "opaque_token")
        == "https://agent.example.test/r/opaque_token"
    )


def test_redirect_resolves_offer_url_and_rejects_arbitrary_query() -> None:
    offer_id, store_id = uuid4(), uuid4()
    link = OfferShortLink(token="opaque", offer_id=offer_id)
    offer = Offer(
        id=offer_id,
        product_id=uuid4(),
        store_id=store_id,
        url="https://www.amazon.com.br/dp/ABC",
    )
    store = Store(
        id=store_id,
        code="amazon",
        name="Amazon",
        base_url="https://www.amazon.com.br",
    )
    session = MagicMock()
    session.get.side_effect = [link, offer, store]
    response = redirect_offer(
        "opaque", SimpleNamespace(query_params={}), session=session
    )
    assert response.status_code == 302
    assert response.headers["location"] == offer.url

    rejected = redirect_offer(
        "opaque",
        SimpleNamespace(query_params={"url": "https://evil.test"}),
        session=session,
    )
    assert rejected.status_code == 400


@pytest.mark.anyio
async def test_media_rejection_falls_back_to_text(monkeypatch) -> None:
    photo = AsyncMock(side_effect=TelegramMediaRejected())
    text = AsyncMock()
    monkeypatch.setattr("app.telegram.notifications.send_photo", photo)
    monkeypatch.setattr("app.telegram.notifications.send_message", text)
    part = _PreparedMessagePart(
        uuid4(), 0, "Oferta completa", "https://images.example.test/item.jpg"
    )

    await _send_part(
        123,
        part,
        bot_token=SecretStr("token"),
        timeout_seconds=10,
        retry_after_cap_seconds=30,
        circuit_failure_threshold=5,
        circuit_open_seconds=30,
    )

    photo.assert_awaited_once()
    text.assert_awaited_once()


def test_checkpoint_identity_includes_event_offer_and_part() -> None:
    primary_keys = {
        column.name for column in EventDeliveryCheckpoint.__table__.primary_key.columns
    }
    assert primary_keys == {"consumer_name", "event_id", "offer_id", "message_part"}


@pytest.mark.anyio
async def test_retry_skips_confirmed_part_and_resumes_pending_part(
    monkeypatch,
) -> None:
    event_id, first_offer_id, second_offer_id = uuid4(), uuid4(), uuid4()
    checkpoint = EventDeliveryCheckpoint(
        consumer_name=TELEGRAM_PRELIST_CONSUMER,
        event_id=event_id,
        offer_id=first_offer_id,
        message_part=0,
    )
    event = Event(
        id=event_id,
        event_type="mission.prelist_ready.v1",
        aggregate_type="mission",
        aggregate_id=uuid4(),
        mission_id=uuid4(),
        payload={},
        occurred_at=NOW,
    )
    session = MagicMock()
    session.scalar = AsyncMock(side_effect=[checkpoint, None])
    session.get = AsyncMock(return_value=event)
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=None)
    transaction.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=transaction)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=context)
    sent = AsyncMock()
    monkeypatch.setattr("app.telegram.notifications.send_message", sent)
    prepared = _PreparedNotification(
        event_id,
        123,
        (
            _PreparedMessagePart(first_offer_id, 0, "já enviada"),
            _PreparedMessagePart(second_offer_id, 1, "pendente"),
        ),
    )

    outcome = await _send_and_record(
        factory,
        prepared,
        bot_token=SecretStr("token"),
        consumer_name=TELEGRAM_PRELIST_CONSUMER,
        max_attempts=5,
        retry_base_seconds=60,
        retry_cap_seconds=900,
        timeout_seconds=10,
        retry_after_cap_seconds=30,
        circuit_failure_threshold=5,
        circuit_open_seconds=30,
    )

    assert outcome.value == "succeeded"
    sent.assert_awaited_once()
    assert sent.await_args.args[1] == "pendente"
    values = session.execute.await_args.args[0].compile().params
    assert values["offer_id"] == second_offer_id
