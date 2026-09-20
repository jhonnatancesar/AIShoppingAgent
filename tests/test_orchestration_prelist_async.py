"""Testes com `AsyncSession` mockada para o cluster de pré-lista/variantes
do `collection_worker` (`app.collection.orchestration`):
`_current_prelist_candidates`, `_available_family_variants`,
`_maybe_publish_variant_choices` e `_previous_prelist_best_by_store`.

Mesmo papel de `tests/test_collection_orchestration_async.py`: sinal de
regressão rápido, não prova de corretude sob concorrência real (essa
prova é `tests/integration/test_unified_fair_queue.py` e correlatos).
"""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.collection.contracts import MarketplacePartyKind, OfferCondition
from app.collection.normalization import Availability
from app.collection.orchestration import (
    _available_family_variants,
    _current_prelist_candidates,
    _maybe_publish_variant_choices,
    _PrelistCandidate,
    _previous_prelist_best_by_store,
)
from app.collection.relevance import OfferRelevance
from app.events.catalog import EventType
from app.missions.models import VariantSelectionMode
from app.products.identity import ProductRequestKind

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def _mock_async_session() -> MagicMock:
    session = MagicMock()
    session.scalar = AsyncMock()
    session.scalars = AsyncMock()
    session.execute = AsyncMock()
    session.get = AsyncMock()
    return session


def _rows_result(rows: list) -> MagicMock:
    result = MagicMock()
    result.all.return_value = rows
    return result


def _offer(*, offer_id=None, product_id=None, store_id=None, last_seen_at=NOW):
    return SimpleNamespace(
        id=offer_id or uuid4(),
        product_id=product_id or uuid4(),
        store_id=store_id or uuid4(),
        last_seen_at=last_seen_at,
    )


def _observation(*, observation_id=None, amount=Decimal("100.00")):
    return SimpleNamespace(
        id=observation_id or uuid4(),
        observed_at=NOW,
        amount=amount,
        total_amount=amount,
        currency="BRL",
        condition=OfferCondition.NEW,
        seller_kind=MarketplacePartyKind.PLATFORM,
        availability=Availability.AVAILABLE,
    )


def _store(store_id=None, code="KABUM"):
    return SimpleNamespace(id=store_id or uuid4(), code=code)


def _relevance(classification=OfferRelevance.MATCH):
    return SimpleNamespace(classification=classification)


# ---------------------------------------------------------------------------
# _current_prelist_candidates
# ---------------------------------------------------------------------------


def test_current_prelist_candidates_dedupes_offer_keeping_latest_observation() -> None:
    session = _mock_async_session()
    offer = _offer()
    store = _store()
    newest = _observation(amount=Decimal("90.00"))
    older = _observation(amount=Decimal("120.00"))
    # A query já ordena por observed_at desc -- a mais nova vem primeiro.
    session.execute.return_value = _rows_result(
        [
            (_relevance(), newest, offer, store),
            (_relevance(), older, offer, store),
        ]
    )
    session.scalar.return_value = None

    result = asyncio.run(_current_prelist_candidates(session, uuid4()))

    assert len(result) == 1
    assert result[0].observation is newest


def test_current_prelist_candidates_excludes_offers_not_seen_in_current_sweep() -> None:
    session = _mock_async_session()
    store = _store()
    fresh_offer = _offer(store_id=store.id, last_seen_at=NOW)
    stale_offer = _offer(store_id=store.id, last_seen_at=NOW - timedelta(hours=6))
    session.execute.return_value = _rows_result(
        [
            (_relevance(), _observation(), fresh_offer, store),
            (_relevance(), _observation(), stale_offer, store),
        ]
    )
    session.scalar.return_value = None

    result = asyncio.run(_current_prelist_candidates(session, uuid4()))

    assert len(result) == 1
    assert result[0].offer is fresh_offer


def test_current_prelist_candidates_without_family_criteria_skips_variant_filters() -> (
    None
):
    session = _mock_async_session()
    offer = _offer()
    store = _store()
    session.execute.return_value = _rows_result(
        [(_relevance(), _observation(), offer, store)]
    )
    session.scalar.return_value = SimpleNamespace(
        request_kind=ProductRequestKind.SPECIFIC_PRODUCT.value,
        variant_selection_mode=VariantSelectionMode.PENDING,
        requested_family_key=None,
        requested_variant=None,
    )

    result = asyncio.run(_current_prelist_candidates(session, uuid4()))

    assert len(result) == 1
    session.scalars.assert_not_awaited()


def test_current_prelist_candidates_returns_empty_while_variant_pending() -> None:
    session = _mock_async_session()
    offer = _offer()
    store = _store()
    session.execute.return_value = _rows_result(
        [(_relevance(), _observation(), offer, store)]
    )
    session.scalar.return_value = SimpleNamespace(
        request_kind=ProductRequestKind.PRODUCT_FAMILY.value,
        variant_selection_mode=VariantSelectionMode.PENDING,
        requested_family_key="ryzen-9",
        requested_variant=None,
    )

    result = asyncio.run(_current_prelist_candidates(session, uuid4()))

    assert result == ()


def test_current_prelist_candidates_any_mode_filters_by_eligible_products() -> None:
    session = _mock_async_session()
    store = _store()
    eligible_offer = _offer(store_id=store.id)
    other_offer = _offer(store_id=store.id)
    session.execute.return_value = _rows_result(
        [
            (_relevance(), _observation(), eligible_offer, store),
            (_relevance(), _observation(), other_offer, store),
        ]
    )
    session.scalar.return_value = SimpleNamespace(
        request_kind=ProductRequestKind.PRODUCT_FAMILY.value,
        variant_selection_mode=VariantSelectionMode.ALL,
        requested_family_key="ryzen-9",
        requested_variant=None,
    )
    session.scalars.return_value = [eligible_offer.product_id]

    result = asyncio.run(_current_prelist_candidates(session, uuid4()))

    assert len(result) == 1
    assert result[0].offer is eligible_offer


def test_current_prelist_candidates_selected_mode_filters_by_selection() -> None:
    session = _mock_async_session()
    store = _store()
    selected_offer = _offer(store_id=store.id)
    unselected_offer = _offer(store_id=store.id)
    session.execute.return_value = _rows_result(
        [
            (_relevance(), _observation(), selected_offer, store),
            (_relevance(), _observation(), unselected_offer, store),
        ]
    )
    session.scalar.return_value = SimpleNamespace(
        request_kind=ProductRequestKind.PRODUCT_FAMILY.value,
        variant_selection_mode=VariantSelectionMode.SELECTED,
        requested_family_key="ryzen-9",
        requested_variant=None,
    )
    # 1a chamada: eligible_product_ids (todos elegíveis por família);
    # 2a chamada: selected_ids (só o produto escolhido pelo usuário).
    session.scalars.side_effect = [
        [selected_offer.product_id, unselected_offer.product_id],
        [selected_offer.product_id],
    ]

    result = asyncio.run(_current_prelist_candidates(session, uuid4()))

    assert len(result) == 1
    assert result[0].offer is selected_offer


# ---------------------------------------------------------------------------
# _available_family_variants
# ---------------------------------------------------------------------------


def test_available_family_variants_without_requested_variant_does_not_filter(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    captured_statement = {}

    async def _fake_scalars(statement):
        captured_statement["value"] = statement
        return [SimpleNamespace(id=uuid4())]

    session.scalars.side_effect = _fake_scalars

    result = asyncio.run(
        _available_family_variants(
            session,
            mission_id=uuid4(),
            family_key="ryzen-9",
            requested_variant=None,
        )
    )

    assert len(result) == 1


def test_available_family_variants_with_requested_variant_adds_filter() -> None:
    session = _mock_async_session()
    session.scalars.return_value = []

    result = asyncio.run(
        _available_family_variants(
            session,
            mission_id=uuid4(),
            family_key="ryzen-9",
            requested_variant="x3d",
        )
    )

    assert result == ()
    session.scalars.assert_awaited_once()


# ---------------------------------------------------------------------------
# _maybe_publish_variant_choices
# ---------------------------------------------------------------------------


def _criteria(**overrides):
    base = dict(
        variant_prompted_at=None,
        requested_family_key="ryzen-9",
        requested_variant=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_maybe_publish_variant_choices_skips_when_already_prompted(monkeypatch) -> None:
    session = _mock_async_session()
    round_complete = AsyncMock()
    monkeypatch.setattr(
        "app.collection.orchestration._mission_prelist_round_complete", round_complete
    )
    mission = SimpleNamespace(id=uuid4(), state_version=1)

    asyncio.run(
        _maybe_publish_variant_choices(
            session, mission, _criteria(variant_prompted_at=NOW), NOW
        )
    )

    round_complete.assert_not_awaited()


def test_maybe_publish_variant_choices_skips_when_round_not_complete(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    monkeypatch.setattr(
        "app.collection.orchestration._mission_prelist_round_complete",
        AsyncMock(return_value=False),
    )
    relevance_pending = AsyncMock()
    monkeypatch.setattr(
        "app.collection.orchestration._mission_relevance_pending", relevance_pending
    )
    mission = SimpleNamespace(id=uuid4(), state_version=1)

    asyncio.run(_maybe_publish_variant_choices(session, mission, _criteria(), NOW))

    relevance_pending.assert_not_awaited()


def test_maybe_publish_variant_choices_skips_when_relevance_pending(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    monkeypatch.setattr(
        "app.collection.orchestration._mission_prelist_round_complete",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._mission_relevance_pending",
        AsyncMock(return_value=True),
    )
    variants_mock = AsyncMock()
    monkeypatch.setattr(
        "app.collection.orchestration._available_family_variants", variants_mock
    )
    mission = SimpleNamespace(id=uuid4(), state_version=1)

    asyncio.run(_maybe_publish_variant_choices(session, mission, _criteria(), NOW))

    variants_mock.assert_not_awaited()


def test_maybe_publish_variant_choices_skips_without_requested_family_key(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    monkeypatch.setattr(
        "app.collection.orchestration._mission_prelist_round_complete",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._mission_relevance_pending",
        AsyncMock(return_value=False),
    )
    variants_mock = AsyncMock()
    monkeypatch.setattr(
        "app.collection.orchestration._available_family_variants", variants_mock
    )
    mission = SimpleNamespace(id=uuid4(), state_version=1)

    asyncio.run(
        _maybe_publish_variant_choices(
            session, mission, _criteria(requested_family_key=None), NOW
        )
    )

    variants_mock.assert_not_awaited()


def test_maybe_publish_variant_choices_skips_when_no_variants_found(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    monkeypatch.setattr(
        "app.collection.orchestration._mission_prelist_round_complete",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._mission_relevance_pending",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._available_family_variants",
        AsyncMock(return_value=()),
    )
    publish_mock = AsyncMock()
    monkeypatch.setattr(
        "app.collection.orchestration.publish_event_async", publish_mock
    )
    mission = SimpleNamespace(id=uuid4(), state_version=1)
    criteria = _criteria()

    asyncio.run(_maybe_publish_variant_choices(session, mission, criteria, NOW))

    publish_mock.assert_not_awaited()
    assert criteria.variant_prompted_at is None


def test_maybe_publish_variant_choices_publishes_event_and_marks_prompted(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    monkeypatch.setattr(
        "app.collection.orchestration._mission_prelist_round_complete",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._mission_relevance_pending",
        AsyncMock(return_value=False),
    )
    product = SimpleNamespace(
        id=uuid4(), display_name="Ryzen 9 9950X3D", name="fallback"
    )
    monkeypatch.setattr(
        "app.collection.orchestration._available_family_variants",
        AsyncMock(return_value=(product,)),
    )
    publish_mock = AsyncMock()
    monkeypatch.setattr(
        "app.collection.orchestration.publish_event_async", publish_mock
    )
    mission = SimpleNamespace(id=uuid4(), state_version=3)
    criteria = _criteria()

    asyncio.run(_maybe_publish_variant_choices(session, mission, criteria, NOW))

    publish_mock.assert_awaited_once()
    kwargs = publish_mock.await_args.kwargs
    assert kwargs["event_type"] == EventType.MISSION_VARIANTS_READY_V1
    assert kwargs["aggregate_id"] == mission.id
    assert kwargs["payload"].variants[0].product_id == product.id
    assert kwargs["payload"].variants[0].label == "Ryzen 9 9950X3D"
    assert criteria.variant_prompted_at == NOW


def test_maybe_publish_variant_choices_falls_back_to_name_without_display_name(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    monkeypatch.setattr(
        "app.collection.orchestration._mission_prelist_round_complete",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._mission_relevance_pending",
        AsyncMock(return_value=False),
    )
    product = SimpleNamespace(id=uuid4(), display_name=None, name="Ryzen 9 9950X3D")
    monkeypatch.setattr(
        "app.collection.orchestration._available_family_variants",
        AsyncMock(return_value=(product,)),
    )
    publish_mock = AsyncMock()
    monkeypatch.setattr(
        "app.collection.orchestration.publish_event_async", publish_mock
    )
    mission = SimpleNamespace(id=uuid4(), state_version=3)

    asyncio.run(_maybe_publish_variant_choices(session, mission, _criteria(), NOW))

    kwargs = publish_mock.await_args.kwargs
    assert kwargs["payload"].variants[0].label == "Ryzen 9 9950X3D"


# ---------------------------------------------------------------------------
# _previous_prelist_best_by_store
# ---------------------------------------------------------------------------


def _event(event_type: str, payload, mission_id=None):
    return SimpleNamespace(
        event_type=event_type, payload=payload, mission_id=mission_id or uuid4()
    )


def test_previous_prelist_best_by_store_returns_empty_for_non_dict_payload() -> None:
    session = _mock_async_session()
    result = asyncio.run(
        _previous_prelist_best_by_store(
            session, _event(EventType.MISSION_PRELIST_READY_V2.value, "not-a-dict")
        )
    )
    assert result == {}


def test_previous_prelist_best_by_store_v2_returns_empty_when_offers_not_a_list() -> (
    None
):
    session = _mock_async_session()
    result = asyncio.run(
        _previous_prelist_best_by_store(
            session,
            _event(EventType.MISSION_PRELIST_READY_V2.value, {"offers": "nope"}),
        )
    )
    assert result == {}


def test_previous_prelist_best_by_store_v2_skips_malformed_items() -> None:
    session = _mock_async_session()
    event = _event(
        EventType.MISSION_PRELIST_READY_V2.value,
        {"offers": [{"offer_id": "not-a-uuid"}, "not-a-dict-either"]},
    )
    result = asyncio.run(_previous_prelist_best_by_store(session, event))
    assert result == {}
    session.get.assert_not_awaited()


def test_previous_prelist_best_by_store_v1_reads_first_second_offer_fields() -> None:
    session = _mock_async_session()
    offer_id, observation_id = uuid4(), uuid4()
    event = _event(
        EventType.MISSION_PRELIST_READY_V1.value,
        {
            "first_offer_id": str(offer_id),
            "first_observation_id": str(observation_id),
            "second_offer_id": None,
        },
    )
    offer = _offer(offer_id=offer_id)
    store = _store(store_id=offer.store_id)
    observation = _observation(observation_id=observation_id)
    session.get.side_effect = [offer, observation, _relevance(), store]

    result = asyncio.run(_previous_prelist_best_by_store(session, event))

    assert store.id in result


def test_previous_prelist_best_by_store_skips_when_offer_or_observation_missing() -> (
    None
):
    session = _mock_async_session()
    offer_id, observation_id = uuid4(), uuid4()
    event = _event(
        EventType.MISSION_PRELIST_READY_V1.value,
        {
            "first_offer_id": str(offer_id),
            "first_observation_id": str(observation_id),
            "second_offer_id": None,
        },
    )
    session.get.side_effect = [None, None, None]

    result = asyncio.run(_previous_prelist_best_by_store(session, event))

    assert result == {}


def test_previous_prelist_best_by_store_skips_when_store_missing() -> None:
    session = _mock_async_session()
    offer_id, observation_id = uuid4(), uuid4()
    event = _event(
        EventType.MISSION_PRELIST_READY_V1.value,
        {
            "first_offer_id": str(offer_id),
            "first_observation_id": str(observation_id),
            "second_offer_id": None,
        },
    )
    offer = _offer(offer_id=offer_id)
    observation = _observation(observation_id=observation_id)
    session.get.side_effect = [offer, observation, _relevance(), None]

    result = asyncio.run(_previous_prelist_best_by_store(session, event))

    assert result == {}


def test_previous_prelist_best_by_store_keeps_the_cheapest_key_per_store() -> None:
    session = _mock_async_session()
    store_id = uuid4()
    offer_1, observation_1 = (
        _offer(store_id=store_id),
        _observation(amount=Decimal("500.00")),
    )
    offer_2, observation_2 = (
        _offer(store_id=store_id),
        _observation(amount=Decimal("100.00")),
    )
    event = _event(
        EventType.MISSION_PRELIST_READY_V1.value,
        {
            "first_offer_id": str(offer_1.id),
            "first_observation_id": str(observation_1.id),
            "second_offer_id": str(offer_2.id),
            "second_observation_id": str(observation_2.id),
        },
    )
    store = _store(store_id=store_id)
    session.get.side_effect = [
        offer_1,
        observation_1,
        _relevance(),
        store,
        offer_2,
        observation_2,
        _relevance(),
        store,
    ]

    result = asyncio.run(_previous_prelist_best_by_store(session, event))

    assert len(result) == 1
    cheapest = _PrelistCandidate(OfferRelevance.MATCH, observation_2, offer_2, store)
    from app.collection.orchestration import _prelist_commercial_key

    assert result[store_id] == _prelist_commercial_key(cheapest)
