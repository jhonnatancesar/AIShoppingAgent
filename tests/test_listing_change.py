"""TASK-132 (Parte A) -- máquina de estados da vigilância de título
(`app.offers.listing_change`), sem banco: a sessão é simulada e a consulta de
identidade é injetada. O comportamento contra Postgres real fica em
`tests/integration/test_listing_product_change.py`."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.offers import listing_change
from app.offers.listing_change import (
    LISTING_CHANGE_CONFIRMATIONS,
    apply_pending_identity,
    track_listing_identity,
)
from app.offers.models import Offer, OfferIdentityWatch
from app.products.identity import build_resolved_variant_from_fields
from app.products.identity_learning import TitleIdentity
from app.products.models import Product
from sqlalchemy.exc import SQLAlchemyError

NOW = datetime(2026, 9, 28, 10, 0, tzinfo=UTC)


def _identity(model: str):
    return build_resolved_variant_from_fields(
        category="gpu",
        brand="nvidia",
        family="geforce-rtx",
        model=model,
        variant="base",
        attributes={},
    )


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


def _session(product, watch):
    session = MagicMock()
    session.begin_nested = MagicMock(side_effect=lambda: _Nested())
    session.add = MagicMock()
    session.delete = AsyncMock()
    session.flush = AsyncMock()
    session.scalar = AsyncMock(return_value=0)

    async def _get(model, _key):
        return product if model is Product else watch

    session.get = AsyncMock(side_effect=_get)
    return session


def _product(identity):
    return Product(
        id=uuid4(),
        name="p",
        identity_key=identity.identity_key if identity else None,
    )


def _offer(product):
    return Offer(id=uuid4(), product_id=product.id, superseded_by_id=None)


def _lookup(monkeypatch, result):
    monkeypatch.setattr(
        listing_change, "lookup_title_identity", AsyncMock(return_value=result)
    )


def _run(session, offer, title="titulo novo", at=NOW):
    return asyncio.run(
        track_listing_identity(session, offer, raw_title=title, collected_at=at)
    )


def test_same_identity_clears_an_existing_watch(monkeypatch) -> None:
    current = _identity("4060")
    product = _product(current)
    watch = OfferIdentityWatch(offer_id=uuid4(), pending_title_hash="x")
    session = _session(product, watch)
    _lookup(monkeypatch, TitleIdentity("resolved", current))

    assert _run(session, _offer(product)) is False
    session.delete.assert_awaited_once_with(watch)


def test_first_sighting_of_a_different_identity_only_watches(monkeypatch) -> None:
    product = _product(_identity("4060"))
    session = _session(product, None)
    _lookup(monkeypatch, TitleIdentity("resolved", _identity("4070")))

    assert _run(session, _offer(product)) is False
    created = session.add.call_args.args[0]
    assert isinstance(created, OfferIdentityWatch)
    assert created.sightings == 1 and created.needs_ai is False


def test_second_consecutive_sighting_applies_the_change(monkeypatch) -> None:
    product = _product(_identity("4060"))
    new_identity = _identity("4070")
    first_seen = NOW - timedelta(hours=1)
    watch = OfferIdentityWatch(
        offer_id=uuid4(),
        pending_title="titulo novo",
        pending_title_hash=listing_change.normalized_title_hash("titulo novo"),
        first_seen_at=first_seen,
        sightings=1,
        needs_ai=False,
        ai_attempts=0,
    )
    session = _session(product, watch)
    _lookup(monkeypatch, TitleIdentity("resolved", new_identity))
    applied = AsyncMock(return_value=True)
    monkeypatch.setattr(listing_change, "apply_listing_change", applied)

    assert _run(session, _offer(product)) is True
    assert watch.sightings == LISTING_CHANGE_CONFIRMATIONS
    assert applied.await_args.kwargs["first_seen_at"] == first_seen


def test_a_different_title_restarts_the_count(monkeypatch) -> None:
    product = _product(_identity("4060"))
    watch = OfferIdentityWatch(
        offer_id=uuid4(),
        pending_title="antigo",
        pending_title_hash="outro-hash",
        first_seen_at=NOW - timedelta(hours=3),
        sightings=1,
        needs_ai=False,
        ai_attempts=2,
    )
    session = _session(product, watch)
    _lookup(monkeypatch, TitleIdentity("resolved", _identity("4070")))

    assert _run(session, _offer(product), at=NOW) is False
    assert watch.pending_title == "titulo novo"
    assert (watch.sightings, watch.ai_attempts, watch.first_seen_at) == (1, 0, NOW)


def test_unknown_title_marks_needs_ai_and_counts_repeats(monkeypatch) -> None:
    product = _product(_identity("4060"))
    session = _session(product, None)
    _lookup(monkeypatch, TitleIdentity("unknown"))

    assert _run(session, _offer(product)) is False
    created = session.add.call_args.args[0]
    assert created.needs_ai is True

    repeat = _session(product, created)
    created.pending_title_hash = listing_change.normalized_title_hash("titulo novo")
    assert _run(repeat, _offer(product)) is False
    assert created.sightings == 2


def test_cached_unresolved_title_is_not_evidence(monkeypatch) -> None:
    product = _product(_identity("4060"))
    session = _session(product, None)
    _lookup(monkeypatch, TitleIdentity("cached_unresolved"))

    assert _run(session, _offer(product)) is False
    session.add.assert_not_called()


def test_archived_and_adhoc_offers_are_never_watched(monkeypatch) -> None:
    _lookup(monkeypatch, TitleIdentity("unknown"))
    product = _product(_identity("4060"))
    archived = _offer(product)
    archived.superseded_by_id = uuid4()
    session = _session(product, None)
    assert _run(session, archived) is False

    adhoc = _product(None)
    session = _session(adhoc, None)
    assert _run(session, _offer(adhoc)) is False
    session.add.assert_not_called()


def test_database_failure_never_breaks_the_collection(monkeypatch) -> None:
    product = _product(_identity("4060"))
    session = _session(product, None)
    monkeypatch.setattr(
        listing_change,
        "lookup_title_identity",
        AsyncMock(side_effect=SQLAlchemyError("boom")),
    )

    assert _run(session, _offer(product)) is False


def test_apply_skips_when_the_offer_has_a_purchase_confirmation() -> None:
    product = _product(_identity("4060"))
    watch = OfferIdentityWatch(offer_id=uuid4())
    session = _session(product, watch)
    session.scalar = AsyncMock(return_value=1)
    offer = _offer(product)

    result = asyncio.run(
        listing_change.apply_listing_change(
            session, offer, _identity("4070"), first_seen_at=NOW, watch=watch
        )
    )

    assert result is False
    assert offer.product_id == product.id
    session.delete.assert_awaited_once_with(watch)


@pytest.mark.parametrize(
    ("offer_kind", "same", "sightings", "expected"),
    [
        ("missing", False, 2, False),
        ("archived", False, 2, False),
        ("adhoc", False, 2, False),
        ("live", True, 2, False),
        ("live", False, 1, False),
        ("live", False, 2, True),
    ],
)
def test_pending_identity_from_the_sweep(
    monkeypatch, offer_kind, same, sightings, expected
) -> None:
    current = _identity("4060")
    new_identity = current if same else _identity("4070")
    product = _product(None if offer_kind == "adhoc" else current)
    offer = _offer(product)
    if offer_kind == "archived":
        offer.superseded_by_id = uuid4()
    watch = OfferIdentityWatch(
        offer_id=offer.id, sightings=sightings, needs_ai=True, first_seen_at=NOW
    )
    session = MagicMock()
    session.delete = AsyncMock()

    async def _get(model, _key):
        if model is Offer:
            return None if offer_kind == "missing" else offer
        return product

    session.get = AsyncMock(side_effect=_get)
    monkeypatch.setattr(
        listing_change, "apply_listing_change", AsyncMock(return_value=True)
    )

    assert asyncio.run(apply_pending_identity(session, watch, new_identity)) is expected


def test_apply_listing_change_is_a_noop_when_product_is_already_the_target(
    monkeypatch,
) -> None:
    identity = _identity("4070")
    product = SimpleNamespace(id=uuid4())
    monkeypatch.setattr(
        listing_change, "_product_for_identity", AsyncMock(return_value=product)
    )
    session = _session(None, None)
    session.scalar = AsyncMock(return_value=0)
    offer = SimpleNamespace(id=uuid4(), product_id=product.id)
    watch = OfferIdentityWatch(offer_id=offer.id)

    result = asyncio.run(
        listing_change.apply_listing_change(
            session, offer, identity, first_seen_at=NOW, watch=watch
        )
    )

    assert result is False
    session.delete.assert_awaited_once_with(watch)
