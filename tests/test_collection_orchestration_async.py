"""Testes rápidos (AsyncMock) do caminho `AsyncSession` do collection_worker.

TASK-079: sinal de regressão rápido para as funções que tocam
`AsyncSession` -- não a prova de corretude sob concorrência real, que é
`tests/integration/test_collection_orchestration.py` (PostgreSQL real,
locks/upserts/isolamento de verdade). Aqui só se verifica que cada função
chama suas dependências na ordem esperada e monta o resultado certo a
partir de retornos controlados.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.ai_provider import AIResponse
from app.collection.adapter import CollectionAdapter
from app.collection.contracts import CollectionResult, RawCollectedOffer
from app.collection.errors import ProviderBlockedError
from app.collection.models import CollectionRunStatus
from app.collection.normalization import Availability, PriceNormalizer
from app.collection.orchestration import (
    ClaimedCollection,
    CollectionOrchestrator,
    _AIOutcome,
    _apply_source_backoff,
    _evaluate_mission_prelist,
    _find_offer,
    _maybe_publish_prelist_errata,
    _maybe_publish_prelist_ready,
    _PendingOffer,
    _persist_phase_a,
    _persist_phase_c,
    _PhaseAOutcome,
    _record_failure,
    _reset_source_backoff,
    _resolve_offer,
    _resolve_seller,
    _run_phase_b,
    claim_due_collections,
    ensure_missing_schedules,
    recover_stale_runs,
)
from app.collection.relevance import OfferRelevance
from app.missions.models import MissionStatus
from app.users.models import UserRole

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def _async_cm(value=None):
    """Objeto usável como `async with x():` -- no-op, devolve `value`."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=value if value is not None else cm)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _mock_async_session() -> MagicMock:
    """Sessão `AsyncSession` simulada: `async with`/`begin`/`begin_nested`
    são no-ops; `.scalar`/`.get`/`.execute`/`.scalars`/`.flush` são
    `AsyncMock` configuráveis via `side_effect`/`return_value`."""
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.scalar = AsyncMock()
    session.get = AsyncMock()
    session.execute = AsyncMock()
    session.scalars = AsyncMock()
    session.flush = AsyncMock()
    session.begin = MagicMock(side_effect=lambda: _async_cm())
    session.begin_nested = MagicMock(side_effect=lambda: _async_cm())
    return session


def _session_factory(session: MagicMock) -> MagicMock:
    return MagicMock(return_value=session)


def _raw(
    *,
    source: str = "pichau",
    external_id: str = "stable",
    title: str = "Synthetic product",
    raw_price: str = "R$ 100,00",
    url: str = "https://example.invalid/offer",
    seller_external_id: str | None = None,
):
    return RawCollectedOffer(
        source_code=source,
        url=url,
        title=title,
        collected_at=NOW + timedelta(seconds=1),
        external_id=external_id,
        raw_price=raw_price,
        raw_currency="BRL",
        raw_shipping="Frete grátis",
        raw_availability="Em estoque",
        evidence={"card": "safe"},
        seller_external_id=seller_external_id,
    )


class _StubAIManager:
    def __init__(self, responses: dict[str, str] | None = None) -> None:
        self._responses = responses or {}
        self.calls: list[str] = []

    async def generate(self, request):
        self.calls.append(request.purpose)
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub",
            content=self._responses.get(request.purpose, "{}"),
            finished_at=datetime.now(UTC),
        )


# ---------------------------------------------------------------------------
# ensure_missing_schedules / recover_stale_runs / claim_due_collections
# ---------------------------------------------------------------------------


def test_schedule_backfill_uses_conflict_safe_insert() -> None:
    session = _mock_async_session()
    initial = MagicMock()
    initial.all.return_value = [(uuid4(),), (uuid4(),)]
    inserted = MagicMock(rowcount=1)
    session.execute.side_effect = [initial, inserted, inserted]

    created = asyncio.run(
        ensure_missing_schedules(session, now=NOW, interval_minutes=30)
    )

    assert created == 2
    assert session.execute.await_count == 3
    session.flush.assert_awaited_once()
    with pytest.raises(ValueError, match="positive"):
        asyncio.run(ensure_missing_schedules(session, interval_minutes=0))
    with pytest.raises(ValueError, match="negative"):
        asyncio.run(ensure_missing_schedules(session, stagger_seconds=-1))


def test_stale_runs_are_terminal_and_publish_failure(monkeypatch) -> None:
    run = SimpleNamespace(
        id=uuid4(),
        mission_id=uuid4(),
        store_id=uuid4(),
        status=CollectionRunStatus.RUNNING,
        started_at=NOW - timedelta(minutes=20),
    )
    session = _mock_async_session()
    session.scalars.return_value = [run]
    finish = AsyncMock()
    publish = AsyncMock()
    evaluate = AsyncMock()
    monkeypatch.setattr("app.collection.orchestration.finish_collection_run", finish)
    monkeypatch.setattr("app.collection.orchestration._publish_failure", publish)
    monkeypatch.setattr(
        "app.collection.orchestration._evaluate_mission_prelist", evaluate
    )

    result = asyncio.run(recover_stale_runs(session, now=NOW))

    assert result == 1
    finish.assert_awaited_once()
    publish.assert_awaited_once_with(session, run, "stale_execution", NOW)
    evaluate.assert_awaited_once_with(session, run.mission_id, NOW)


def test_claim_due_schedule_creates_runs_and_advances(monkeypatch) -> None:
    from app.missions.models import MissionSchedule

    mission_id = uuid4()
    schedule = MissionSchedule(
        id=uuid4(),
        mission_id=mission_id,
        interval_minutes=60,
        next_run_at=NOW,
        is_enabled=True,
    )
    criteria = SimpleNamespace(search_query="GPU")
    store_ids = (uuid4(), uuid4())
    session = _mock_async_session()
    session.scalar.side_effect = [None, criteria]
    rows = MagicMock()
    rows.all.return_value = [(store_ids[0], "kabum"), (store_ids[1], "pichau")]
    session.execute.return_value = rows
    runs = iter((SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4())))
    monkeypatch.setattr(
        "app.collection.orchestration.find_due_schedules_async",
        AsyncMock(return_value=[schedule]),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.start_collection_run",
        AsyncMock(side_effect=lambda *_a, **_k: next(runs)),
    )

    claims = asyncio.run(claim_due_collections(session, now=NOW))

    assert [claim.source_code for claim in claims] == ["kabum", "pichau"]
    assert schedule.last_run_at == NOW
    assert schedule.next_run_at == NOW + timedelta(hours=1)
    session.flush.assert_awaited_once()


def test_claim_due_collections_skips_mission_already_running() -> None:
    from app.missions.models import MissionSchedule

    mission_id = uuid4()
    schedule = MissionSchedule(
        id=uuid4(),
        mission_id=mission_id,
        interval_minutes=60,
        next_run_at=NOW,
        is_enabled=True,
    )
    session = _mock_async_session()
    session.scalar.return_value = uuid4()  # ja existe um run RUNNING

    async def _run():
        import app.collection.orchestration as module

        module.find_due_schedules_async = AsyncMock(return_value=[schedule])
        return await claim_due_collections(session, now=NOW)

    claims = asyncio.run(_run())

    assert claims == ()


# ---------------------------------------------------------------------------
# _resolve_offer / _resolve_seller / _find_offer
# ---------------------------------------------------------------------------


def test_offer_and_seller_resolution_reuse_existing(monkeypatch) -> None:
    session = _mock_async_session()
    item = PriceNormalizer().normalize_offer(_raw())
    existing_offer = SimpleNamespace(id=uuid4())
    monkeypatch.setattr(
        "app.collection.orchestration._resolve_seller", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "app.collection.orchestration._find_offer",
        AsyncMock(return_value=existing_offer),
    )

    assert asyncio.run(_resolve_offer(session, uuid4(), item)) is existing_offer
    assert asyncio.run(_resolve_seller(session, uuid4(), item)) is None


def test_offer_resolution_creates_new_product_and_offer(monkeypatch) -> None:
    session = _mock_async_session()
    item = PriceNormalizer().normalize_offer(_raw())
    monkeypatch.setattr(
        "app.collection.orchestration._resolve_seller", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "app.collection.orchestration._find_offer", AsyncMock(return_value=None)
    )

    offer = asyncio.run(_resolve_offer(session, uuid4(), item))

    assert offer.external_id == "stable"
    assert session.add.call_count == 2
    assert session.flush.await_count == 2


def test_seller_resolution_creates_only_stable_identity() -> None:
    session = _mock_async_session()
    session.scalar.return_value = None
    item = PriceNormalizer().normalize_offer(_raw(seller_external_id="seller-1"))

    seller = asyncio.run(_resolve_seller(session, uuid4(), item))

    assert seller is not None
    assert seller.external_id == "seller-1"
    session.add.assert_called_once_with(seller)


def test_find_offer_filters_by_seller_and_external_id() -> None:
    session = _mock_async_session()
    session.scalar.return_value = None
    store_id, seller_id = uuid4(), uuid4()

    result = asyncio.run(
        _find_offer(session, store_id, seller_id, "ext-1", "https://example.invalid")
    )

    assert result is None
    session.scalar.assert_awaited_once()


# ---------------------------------------------------------------------------
# _persist_phase_a / _run_phase_b / _persist_phase_c
# ---------------------------------------------------------------------------


def test_persist_phase_a_returns_none_when_run_not_running() -> None:
    session = _mock_async_session()
    session.scalar.return_value = None
    claim = ClaimedCollection(uuid4(), uuid4(), uuid4(), "pichau", "GPU", NOW)
    result = CollectionResult("pichau", NOW, NOW + timedelta(seconds=2), ())
    normalized = PriceNormalizer().normalize_result(result)

    outcome = asyncio.run(
        _persist_phase_a(_session_factory(session), claim, normalized)
    )

    assert outcome is None


def test_persist_phase_a_marks_offers_needing_ai(monkeypatch) -> None:
    mission_id, run_id, store_id, offer_id, product_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    run = SimpleNamespace(
        id=run_id,
        mission_id=mission_id,
        store_id=store_id,
        status=CollectionRunStatus.RUNNING,
        started_at=NOW,
    )
    mission = SimpleNamespace(id=mission_id)
    criteria = SimpleNamespace(
        mission_id=mission_id,
        search_query="GPU",
        model=None,
        target_amount=None,
        target_currency=None,
    )
    session = _mock_async_session()
    # scalar: run, criteria, previous(=None)
    session.scalar.side_effect = [run, criteria, None]
    # get: Mission, MissionOfferRelevance cache (None -> needs AI), Product (display_name=None)
    product = SimpleNamespace(display_name=None)
    session.get.side_effect = [mission, None, product]
    offer = SimpleNamespace(id=offer_id, product_id=product_id)
    monkeypatch.setattr(
        "app.collection.orchestration._resolve_offer", AsyncMock(return_value=offer)
    )
    claim = ClaimedCollection(run_id, mission_id, store_id, "pichau", "GPU", NOW)
    result = CollectionResult("pichau", NOW, NOW + timedelta(seconds=2), (_raw(),))
    normalized = PriceNormalizer().normalize_result(result)

    outcome = asyncio.run(
        _persist_phase_a(_session_factory(session), claim, normalized)
    )

    assert isinstance(outcome, _PhaseAOutcome)
    assert len(outcome.offers) == 1
    pending = outcome.offers[0]
    assert pending.needs_relevance is True
    assert pending.needs_display_name is True
    assert pending.previous_observation_id is None


def test_persist_phase_a_raises_when_mission_data_missing() -> None:
    run = SimpleNamespace(id=uuid4(), status=CollectionRunStatus.RUNNING)
    session = _mock_async_session()
    session.scalar.side_effect = [run, None]
    session.get.return_value = None
    claim = ClaimedCollection(run.id, uuid4(), uuid4(), "pichau", "GPU", NOW)
    result = CollectionResult("pichau", NOW, NOW + timedelta(seconds=2), ())
    normalized = PriceNormalizer().normalize_result(result)

    with pytest.raises(RuntimeError, match="no longer exists"):
        asyncio.run(_persist_phase_a(_session_factory(session), claim, normalized))


def test_run_phase_b_skips_offers_without_pending_ai() -> None:
    outcome = _PhaseAOutcome(
        run_id=uuid4(),
        mission_id=uuid4(),
        store_id=uuid4(),
        mission_search_query="GPU",
        target_amount=None,
        target_currency=None,
        completed_at=NOW,
        offers=(),
    )
    result = asyncio.run(_run_phase_b(outcome, _StubAIManager(), UserRole.ADMIN))
    assert result == ()


def test_run_phase_b_calls_ai_only_for_pending_flags() -> None:
    pending = _PendingOffer(
        offer_id=uuid4(),
        product_id=uuid4(),
        observation_id=uuid4(),
        amount=Decimal("100"),
        currency="BRL",
        availability=Availability.AVAILABLE,
        observed_at=NOW,
        raw_title="Título bruto",
        needs_relevance=True,
        needs_display_name=False,
        previous_observation_id=None,
        previous_amount=None,
        previous_currency=None,
        previous_availability=None,
        previous_observed_at=None,
    )
    outcome = _PhaseAOutcome(
        run_id=uuid4(),
        mission_id=uuid4(),
        store_id=uuid4(),
        mission_search_query="GPU",
        target_amount=None,
        target_currency=None,
        completed_at=NOW,
        offers=(pending,),
    )
    ai_manager = _StubAIManager({"classify_offer_relevance": '{"relevance": "match"}'})

    outcomes = asyncio.run(_run_phase_b(outcome, ai_manager, UserRole.ADMIN))

    assert ai_manager.calls == ["classify_offer_relevance"]
    assert outcomes[0].relevance is OfferRelevance.MATCH
    assert outcomes[0].display_title is None


def test_persist_phase_c_returns_false_when_mission_missing() -> None:
    session = _mock_async_session()
    session.scalar.return_value = None
    outcome = _PhaseAOutcome(
        run_id=uuid4(),
        mission_id=uuid4(),
        store_id=uuid4(),
        mission_search_query="GPU",
        target_amount=None,
        target_currency=None,
        completed_at=NOW,
        offers=(),
    )
    result = asyncio.run(_persist_phase_c(_session_factory(session), outcome, ()))
    assert result is False


def test_persist_phase_c_returns_false_when_run_no_longer_running() -> None:
    mission = SimpleNamespace(id=uuid4())
    session = _mock_async_session()
    session.scalar.side_effect = [mission, None]
    outcome = _PhaseAOutcome(
        run_id=uuid4(),
        mission_id=mission.id,
        store_id=uuid4(),
        mission_search_query="GPU",
        target_amount=None,
        target_currency=None,
        completed_at=NOW,
        offers=(),
    )
    result = asyncio.run(_persist_phase_c(_session_factory(session), outcome, ()))
    assert result is False


def test_persist_phase_c_persists_relevance_and_finishes_run(monkeypatch) -> None:
    mission_id, run_id, store_id, offer_id, product_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    mission = SimpleNamespace(id=mission_id, status=MissionStatus.ACTIVE)
    run = SimpleNamespace(
        id=run_id,
        mission_id=mission_id,
        store_id=store_id,
        status=CollectionRunStatus.RUNNING,
    )
    session = _mock_async_session()
    session.scalar.side_effect = [mission, run]
    product = SimpleNamespace(display_name=None)
    session.get.return_value = product
    finish = AsyncMock()
    reset_backoff = AsyncMock()
    evaluate_prelist = AsyncMock()
    publish = AsyncMock()
    monkeypatch.setattr("app.collection.orchestration.finish_collection_run", finish)
    monkeypatch.setattr(
        "app.collection.orchestration._reset_source_backoff", reset_backoff
    )
    monkeypatch.setattr(
        "app.collection.orchestration._evaluate_mission_prelist", evaluate_prelist
    )
    monkeypatch.setattr("app.collection.orchestration.publish_event_async", publish)
    pending = _PendingOffer(
        offer_id=offer_id,
        product_id=product_id,
        observation_id=uuid4(),
        amount=Decimal("100"),
        currency="BRL",
        availability=Availability.AVAILABLE,
        observed_at=NOW,
        raw_title="Título bruto",
        needs_relevance=True,
        needs_display_name=True,
        previous_observation_id=None,
        previous_amount=None,
        previous_currency=None,
        previous_availability=None,
        previous_observed_at=None,
    )
    outcome = _PhaseAOutcome(
        run_id=run_id,
        mission_id=mission_id,
        store_id=store_id,
        mission_search_query="GPU",
        target_amount=None,
        target_currency=None,
        completed_at=NOW,
        offers=(pending,),
    )
    ai_outcomes = (_AIOutcome(offer_id, OfferRelevance.MATCH, "Título normalizado"),)

    result = asyncio.run(
        _persist_phase_c(_session_factory(session), outcome, ai_outcomes)
    )

    assert result is True
    assert product.display_name == "Título normalizado"
    finish.assert_awaited_once()
    reset_backoff.assert_awaited_once()
    evaluate_prelist.assert_awaited_once()
    # 1 alerta (PRICE_TARGET_REACHED nao se aplica sem target) + 1 COLLECTION_COMPLETED_V1
    assert publish.await_count >= 1


# ---------------------------------------------------------------------------
# _record_failure / _apply_source_backoff / _reset_source_backoff
# ---------------------------------------------------------------------------


def test_record_failure_discards_late_result() -> None:
    claim = ClaimedCollection(uuid4(), uuid4(), uuid4(), "pichau", "GPU", NOW)
    session = _mock_async_session()
    session.scalar.return_value = SimpleNamespace(
        id=claim.run_id, status=CollectionRunStatus.SUCCEEDED
    )
    assert (
        asyncio.run(_record_failure(session, claim, "provider_blocked", NOW)) is False
    )


def test_record_failure_applies_backoff_only_when_confirmed(monkeypatch) -> None:
    claim = ClaimedCollection(uuid4(), uuid4(), uuid4(), "pichau", "GPU", NOW)
    session = _mock_async_session()
    session.scalar.return_value = SimpleNamespace(
        id=claim.run_id,
        status=CollectionRunStatus.RUNNING,
        mission_id=claim.mission_id,
        store_id=claim.store_id,
    )
    apply_backoff = AsyncMock()
    monkeypatch.setattr(
        "app.collection.orchestration._apply_source_backoff", apply_backoff
    )
    monkeypatch.setattr(
        "app.collection.orchestration.finish_collection_run", AsyncMock()
    )
    monkeypatch.setattr("app.collection.orchestration._publish_failure", AsyncMock())
    monkeypatch.setattr(
        "app.collection.orchestration._evaluate_mission_prelist", AsyncMock()
    )

    result = asyncio.run(
        _record_failure(session, claim, "provider_blocked", NOW, confirmed_block=True)
    )

    assert result is True
    apply_backoff.assert_awaited_once_with(
        session, claim.mission_id, claim.store_id, NOW
    )


def test_apply_source_backoff_updates_only_the_matching_source() -> None:
    mission_id, store_id = uuid4(), uuid4()
    source = SimpleNamespace(consecutive_blocks=0, next_eligible_at=None)
    session = _mock_async_session()
    session.get.return_value = source
    session.scalar.return_value = 30

    asyncio.run(_apply_source_backoff(session, mission_id, store_id, NOW))

    assert source.consecutive_blocks == 1
    assert source.next_eligible_at == NOW + timedelta(minutes=60)


def test_reset_source_backoff_clears_only_that_source() -> None:
    source = SimpleNamespace(consecutive_blocks=3, next_eligible_at=NOW)
    session = _mock_async_session()
    session.get.return_value = source

    asyncio.run(_reset_source_backoff(session, uuid4(), uuid4()))

    assert source.consecutive_blocks == 0
    assert source.next_eligible_at is None


# ---------------------------------------------------------------------------
# _evaluate_mission_prelist / _maybe_publish_prelist_ready / _errata
# ---------------------------------------------------------------------------


def test_evaluate_mission_prelist_is_noop_for_inactive_mission(monkeypatch) -> None:
    mission = SimpleNamespace(id=uuid4(), status=MissionStatus.PAUSED)
    session = _mock_async_session()
    session.scalar.return_value = mission
    ready = AsyncMock()
    errata = AsyncMock()
    monkeypatch.setattr(
        "app.collection.orchestration._maybe_publish_prelist_ready", ready
    )
    monkeypatch.setattr(
        "app.collection.orchestration._maybe_publish_prelist_errata", errata
    )

    asyncio.run(_evaluate_mission_prelist(session, mission.id, NOW))

    ready.assert_not_awaited()
    errata.assert_not_awaited()


def test_evaluate_mission_prelist_dispatches_ready_then_errata(monkeypatch) -> None:
    ready = AsyncMock()
    errata = AsyncMock()
    monkeypatch.setattr(
        "app.collection.orchestration._maybe_publish_prelist_ready", ready
    )
    monkeypatch.setattr(
        "app.collection.orchestration._maybe_publish_prelist_errata", errata
    )
    session = _mock_async_session()

    pending = SimpleNamespace(
        id=uuid4(), status=MissionStatus.ACTIVE, prelist_sent=False
    )
    session.scalar.return_value = pending
    asyncio.run(_evaluate_mission_prelist(session, pending.id, NOW))
    ready.assert_awaited_once_with(session, pending, NOW)
    errata.assert_not_awaited()

    ready.reset_mock()
    sent = SimpleNamespace(
        id=uuid4(),
        status=MissionStatus.ACTIVE,
        prelist_sent=True,
        prelist_errata_sent=False,
    )
    session.scalar.return_value = sent
    asyncio.run(_evaluate_mission_prelist(session, sent.id, NOW))
    ready.assert_not_awaited()
    errata.assert_awaited_once_with(session, sent, NOW)


def test_maybe_publish_prelist_ready_picks_two_cheapest_of_three_stores(
    monkeypatch,
) -> None:
    mission = SimpleNamespace(
        id=uuid4(), prelist_sent=False, prelist_lowest_amount=None
    )
    cheap = SimpleNamespace(
        offer_id=uuid4(), id=uuid4(), amount=Decimal("100.00"), currency="BRL"
    )
    mid = SimpleNamespace(
        offer_id=uuid4(), id=uuid4(), amount=Decimal("150.00"), currency="BRL"
    )
    expensive = SimpleNamespace(
        offer_id=uuid4(), id=uuid4(), amount=Decimal("999.00"), currency="BRL"
    )
    monkeypatch.setattr(
        "app.collection.orchestration._mission_prelist_round_complete",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._latest_match_observations_by_store",
        AsyncMock(return_value=[expensive, cheap, mid]),
    )
    publish = AsyncMock()
    monkeypatch.setattr("app.collection.orchestration.publish_event_async", publish)

    asyncio.run(_maybe_publish_prelist_ready(_mock_async_session(), mission, NOW))

    assert mission.prelist_sent is True
    assert mission.prelist_lowest_amount == Decimal("100.00")
    payload = publish.call_args.kwargs["payload"]
    assert payload.first_offer_id == cheap.offer_id
    assert payload.second_offer_id == mid.offer_id


def test_maybe_publish_prelist_ready_waits_for_the_full_round(monkeypatch) -> None:
    mission = SimpleNamespace(
        id=uuid4(), prelist_sent=False, prelist_lowest_amount=None
    )
    monkeypatch.setattr(
        "app.collection.orchestration._mission_prelist_round_complete",
        AsyncMock(return_value=False),
    )
    publish = AsyncMock()
    monkeypatch.setattr("app.collection.orchestration.publish_event_async", publish)

    asyncio.run(_maybe_publish_prelist_ready(_mock_async_session(), mission, NOW))

    assert mission.prelist_sent is False
    publish.assert_not_awaited()


def test_maybe_publish_prelist_errata_publishes_once_when_cheaper_found() -> None:
    mission = SimpleNamespace(
        id=uuid4(),
        prelist_errata_sent=False,
        prelist_lowest_amount=Decimal("100.00"),
        prelist_lowest_currency="BRL",
    )
    cheaper = SimpleNamespace(
        offer_id=uuid4(), id=uuid4(), amount=Decimal("80.00"), currency="BRL"
    )
    session = _mock_async_session()
    session.scalar.return_value = cheaper

    async def _run():
        import app.collection.orchestration as module

        publish = AsyncMock()
        module.publish_event_async = publish
        await _maybe_publish_prelist_errata(session, mission, NOW)
        return publish

    publish = asyncio.run(_run())

    assert mission.prelist_errata_sent is True
    payload = publish.call_args.kwargs["payload"]
    assert payload.offer_id == cheaper.offer_id


# ---------------------------------------------------------------------------
# CollectionOrchestrator.run_batch / _process / _process_claim
# ---------------------------------------------------------------------------


def test_orchestrator_batch_processes_success_and_failure(monkeypatch) -> None:
    session = _mock_async_session()
    session_factory = _session_factory(session)
    claims = (
        ClaimedCollection(uuid4(), uuid4(), uuid4(), "pichau", "GPU", NOW),
        ClaimedCollection(uuid4(), uuid4(), uuid4(), "kabum", "GPU", NOW),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.ensure_missing_schedules",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.recover_stale_runs", AsyncMock(return_value=0)
    )
    monkeypatch.setattr(
        "app.collection.orchestration.claim_due_collections",
        AsyncMock(return_value=claims),
    )
    orchestrator = CollectionOrchestrator(
        session_factory, CollectionAdapter(), ai_manager=_StubAIManager()
    )

    async def process(claim):
        return claim.source_code == "pichau"

    monkeypatch.setattr(orchestrator, "_process", process)
    result = asyncio.run(orchestrator.run_batch(now=NOW))
    assert (result.claimed, result.succeeded, result.failed) == (2, 1, 1)


def test_orchestrator_processes_one_source_successfully(monkeypatch) -> None:
    class Provider:
        source_code = "pichau"

        async def collect(self, request):
            return CollectionResult(
                "pichau", request.requested_at, NOW + timedelta(seconds=2), (_raw(),)
            )

    session_factory = _session_factory(_mock_async_session())
    persist_a = AsyncMock(return_value="phase-a")
    run_b = AsyncMock(return_value=())
    persist_c = AsyncMock(return_value=True)
    monkeypatch.setattr("app.collection.orchestration._persist_phase_a", persist_a)
    monkeypatch.setattr("app.collection.orchestration._run_phase_b", run_b)
    monkeypatch.setattr("app.collection.orchestration._persist_phase_c", persist_c)
    orchestrator = CollectionOrchestrator(
        session_factory, CollectionAdapter((Provider(),)), ai_manager=_StubAIManager()
    )
    claim = ClaimedCollection(uuid4(), uuid4(), uuid4(), "pichau", "GPU", NOW)

    assert asyncio.run(orchestrator._process(claim)) is True
    persist_a.assert_awaited_once()
    persist_c.assert_awaited_once()


def test_orchestrator_isolates_provider_failure(monkeypatch) -> None:
    class Provider:
        source_code = "kabum"

        async def collect(self, request):
            raise ProviderBlockedError(request.source_code, 403)

    session_factory = _session_factory(_mock_async_session())
    record = AsyncMock(return_value=True)
    monkeypatch.setattr("app.collection.orchestration._record_failure", record)
    orchestrator = CollectionOrchestrator(
        session_factory, CollectionAdapter((Provider(),)), ai_manager=_StubAIManager()
    )
    claim = ClaimedCollection(uuid4(), uuid4(), uuid4(), "kabum", "GPU", NOW)

    assert asyncio.run(orchestrator._process(claim)) is False
    assert record.call_args.args[2] == "provider_blocked"


def test_orchestrator_claim_deadline_exceeded_records_failure_and_returns_false(
    monkeypatch,
) -> None:
    class SlowProvider:
        source_code = "pichau"

        async def collect(self, request):
            await asyncio.sleep(10)
            raise AssertionError("nao deveria completar")

    session_factory = _session_factory(_mock_async_session())
    record = AsyncMock(return_value=True)
    monkeypatch.setattr("app.collection.orchestration._record_failure", record)
    orchestrator = CollectionOrchestrator(
        session_factory,
        CollectionAdapter((SlowProvider(),)),
        ai_manager=_StubAIManager(),
        claim_deadline_seconds=0.05,
    )
    claim = ClaimedCollection(uuid4(), uuid4(), uuid4(), "pichau", "GPU", NOW)

    assert asyncio.run(orchestrator._process(claim)) is False
    record.assert_awaited_once()
    assert record.call_args.args[2] == "claim_deadline_exceeded"
