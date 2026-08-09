"""Testes rápidos da coordenação de coleta sem dependências externas."""

import asyncio
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.collection.adapter import CollectionAdapter
from app.collection.contracts import CollectionResult, RawCollectedOffer
from app.collection.errors import (
    CollectionContractError,
    CollectionNormalizationError,
    ProviderBlockedError,
    ProviderCircuitOpenError,
    ProviderNavigationError,
)
from app.collection.models import CollectionRunStatus
from app.collection.normalization import PriceNormalizer
from app.collection.orchestration import (
    ClaimedCollection,
    CollectionOrchestrator,
    _failure_code,
    _persist_success,
    _raw_evidence,
    _record_failure,
    _resolve_offer,
    _resolve_seller,
    _safe_source,
    claim_due_collections,
    ensure_missing_schedules,
    recover_stale_runs,
)
from app.events import EventType
from app.missions.models import MissionSchedule
from sqlalchemy.exc import IntegrityError

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def _raw(*, source: str = "pichau", external_id: str = "stable"):
    return RawCollectedOffer(
        source_code=source,
        url="https://example.invalid/offer",
        title="Synthetic product",
        collected_at=NOW + timedelta(seconds=1),
        external_id=external_id,
        raw_price="R$ 100,00",
        raw_currency="BRL",
        raw_shipping="Frete grátis",
        raw_availability="Em estoque",
        evidence={"nested": ["safe", object()]},
    )


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ProviderCircuitOpenError("pichau"), "circuit_open"),
        (ProviderBlockedError("pichau", 403), "provider_blocked"),
        (CollectionNormalizationError("bad"), "normalization_failed"),
        (CollectionContractError("bad"), "normalization_failed"),
        (ProviderNavigationError("pichau", 500), "provider_unavailable"),
        (TimeoutError(), "provider_unavailable"),
        (RuntimeError(), "collection_failed"),
    ],
)
def test_failure_codes_are_closed(error: Exception, code: str) -> None:
    assert _failure_code(error) == code


def test_evidence_is_bounded_json_and_source_is_allowlisted() -> None:
    evidence = _raw_evidence(_raw())
    assert evidence["source_code"] == "pichau"
    assert evidence["provider_evidence"]["nested"][0] == "safe"
    assert isinstance(evidence["provider_evidence"]["nested"][1], str)
    assert _safe_source("unknown-dynamic") == "other"


def test_schedule_backfill_uses_conflict_safe_insert() -> None:
    session = MagicMock()
    initial = MagicMock()
    initial.all.return_value = [(uuid4(),), (uuid4(),)]
    inserted = MagicMock(rowcount=1)
    session.execute.side_effect = [initial, inserted, inserted]

    assert ensure_missing_schedules(session, now=NOW, interval_minutes=30) == 2
    assert session.execute.call_count == 3
    session.flush.assert_called_once()
    with pytest.raises(ValueError, match="positive"):
        ensure_missing_schedules(session, interval_minutes=0)


def test_stale_runs_are_terminal_and_publish_failure(monkeypatch) -> None:
    run = SimpleNamespace(
        id=uuid4(),
        mission_id=uuid4(),
        store_id=uuid4(),
        status=CollectionRunStatus.RUNNING,
        started_at=NOW - timedelta(minutes=20),
    )
    session = MagicMock()
    session.scalars.return_value = [run]
    finish = MagicMock()
    publish = MagicMock()
    monkeypatch.setattr("app.collection.orchestration.finish_collection_run", finish)
    monkeypatch.setattr("app.collection.orchestration._publish_failure", publish)

    assert recover_stale_runs(session, now=NOW) == 1
    finish.assert_called_once()
    publish.assert_called_once_with(session, run, "stale_execution", NOW)


def test_claim_due_schedule_creates_runs_and_advances(monkeypatch) -> None:
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
    session = MagicMock()
    session.scalar.side_effect = [None, criteria]
    rows = MagicMock()
    rows.all.return_value = [(store_ids[0], "kabum"), (store_ids[1], "pichau")]
    session.execute.return_value = rows
    session.begin_nested.return_value = nullcontext()
    runs = iter(
        (
            SimpleNamespace(id=uuid4()),
            SimpleNamespace(id=uuid4()),
        )
    )
    monkeypatch.setattr(
        "app.collection.orchestration.find_due_schedules",
        lambda *_args, **_kwargs: [schedule],
    )
    monkeypatch.setattr(
        "app.collection.orchestration.start_collection_run",
        lambda *_args, **_kwargs: next(runs),
    )

    claims = claim_due_collections(session, now=NOW)

    assert [claim.source_code for claim in claims] == ["kabum", "pichau"]
    assert schedule.last_run_at == NOW
    assert schedule.next_run_at == NOW + timedelta(hours=1)


def test_offer_and_seller_resolution_reuse_existing(monkeypatch) -> None:
    session = MagicMock()
    item = PriceNormalizer().normalize_offer(_raw())
    existing_offer = SimpleNamespace(id=uuid4())
    monkeypatch.setattr(
        "app.collection.orchestration._resolve_seller",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "app.collection.orchestration._find_offer",
        lambda *_args: existing_offer,
    )
    assert _resolve_offer(session, uuid4(), item) is existing_offer
    assert _resolve_seller(session, uuid4(), item) is None


def test_offer_resolution_creates_new_product_and_offer(monkeypatch) -> None:
    session = MagicMock()
    session.begin_nested.return_value = nullcontext()
    item = PriceNormalizer().normalize_offer(_raw())
    monkeypatch.setattr(
        "app.collection.orchestration._resolve_seller", lambda *_args: None
    )
    monkeypatch.setattr("app.collection.orchestration._find_offer", lambda *_args: None)

    offer = _resolve_offer(session, uuid4(), item)

    assert offer.external_id == "stable"
    assert session.add.call_count == 2
    assert session.flush.call_count == 2


def test_seller_resolution_creates_only_stable_identity() -> None:
    session = MagicMock()
    session.scalar.return_value = None
    session.begin_nested.return_value = nullcontext()
    item = PriceNormalizer().normalize_offer(
        replace(_raw(), seller_external_id="seller-1", seller_name="Safe seller")
    )

    seller = _resolve_seller(session, uuid4(), item)

    assert seller is not None
    assert seller.external_id == "seller-1"
    session.add.assert_called_once_with(seller)


def test_persist_success_deduplicates_and_publishes_events(monkeypatch) -> None:
    mission_id, run_id, store_id, offer_id = uuid4(), uuid4(), uuid4(), uuid4()
    run = SimpleNamespace(
        id=run_id,
        mission_id=mission_id,
        store_id=store_id,
        status=CollectionRunStatus.RUNNING,
        started_at=NOW,
    )
    mission = SimpleNamespace(id=mission_id)
    criteria = SimpleNamespace(mission_id=mission_id)
    session = MagicMock()
    session.scalar.side_effect = [run, criteria, None]
    session.get.return_value = mission
    result = CollectionResult(
        "pichau", NOW, NOW + timedelta(seconds=2), (_raw(), _raw())
    )
    normalized = PriceNormalizer().normalize_result(result)
    candidate = SimpleNamespace(
        event_type=EventType.PRICE_TARGET_REACHED_V1,
        aggregate_type="mission",
        aggregate_id=mission_id,
        payload=SimpleNamespace(),
    )
    publish = MagicMock()
    finish = MagicMock()
    monkeypatch.setattr(
        "app.collection.orchestration._resolve_offer",
        lambda *_args: SimpleNamespace(id=offer_id),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.evaluate_price_alerts",
        lambda *_args: (candidate,),
    )
    monkeypatch.setattr("app.collection.orchestration.publish_event", publish)
    monkeypatch.setattr("app.collection.orchestration.finish_collection_run", finish)
    claim = ClaimedCollection(run_id, mission_id, store_id, "pichau", "GPU", NOW)

    assert _persist_success(session, claim, normalized) is True
    assert session.add.call_count == 1
    assert publish.call_count == 2
    finish.assert_called_once()


def test_record_failure_discards_late_result(monkeypatch) -> None:
    claim = ClaimedCollection(uuid4(), uuid4(), uuid4(), "pichau", "GPU", NOW)
    session = MagicMock()
    session.scalar.return_value = SimpleNamespace(
        id=claim.run_id, status=CollectionRunStatus.SUCCEEDED
    )
    assert _record_failure(session, claim, "provider_blocked", NOW) is False


def test_orchestrator_batch_processes_success_and_failure(monkeypatch) -> None:
    session_factory = MagicMock()
    session_factory.begin.return_value = nullcontext(MagicMock())
    claims = (
        ClaimedCollection(uuid4(), uuid4(), uuid4(), "pichau", "GPU", NOW),
        ClaimedCollection(uuid4(), uuid4(), uuid4(), "kabum", "GPU", NOW),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.ensure_missing_schedules", lambda *_a, **_k: 0
    )
    monkeypatch.setattr(
        "app.collection.orchestration.recover_stale_runs", lambda *_a, **_k: 0
    )
    monkeypatch.setattr(
        "app.collection.orchestration.claim_due_collections",
        lambda *_a, **_k: claims,
    )
    orchestrator = CollectionOrchestrator(session_factory, CollectionAdapter())

    async def process(claim):
        return claim.source_code == "pichau"

    monkeypatch.setattr(orchestrator, "_process", process)
    result = asyncio.run(orchestrator.run_batch(now=NOW))
    assert (result.claimed, result.succeeded, result.failed) == (2, 1, 1)


@pytest.mark.parametrize(
    "options",
    [
        {"schedule_interval_minutes": 0},
        {"stale_run_minutes": 0},
        {"max_concurrency": 0},
        {"max_concurrency": 5},
    ],
)
def test_orchestrator_rejects_unsafe_limits(options: dict) -> None:
    with pytest.raises(ValueError):
        CollectionOrchestrator(MagicMock(), CollectionAdapter(), **options)


def test_orchestrator_processes_one_source_successfully(monkeypatch) -> None:
    class Provider:
        source_code = "pichau"

        async def collect(self, request):
            return CollectionResult(
                "pichau", request.requested_at, NOW + timedelta(seconds=2), (_raw(),)
            )

    session_factory = MagicMock()
    session_factory.begin.return_value = nullcontext(MagicMock())
    persist = MagicMock(return_value=True)
    monkeypatch.setattr("app.collection.orchestration._persist_success", persist)
    orchestrator = CollectionOrchestrator(
        session_factory, CollectionAdapter((Provider(),))
    )
    claim = ClaimedCollection(uuid4(), uuid4(), uuid4(), "pichau", "GPU", NOW)

    assert asyncio.run(orchestrator._process(claim)) is True
    persist.assert_called_once()


def test_orchestrator_isolates_provider_failure(monkeypatch) -> None:
    class Provider:
        source_code = "kabum"

        async def collect(self, request):
            raise ProviderBlockedError(request.source_code, 403)

    session_factory = MagicMock()
    session_factory.begin.return_value = nullcontext(MagicMock())
    record = MagicMock(return_value=True)
    monkeypatch.setattr("app.collection.orchestration._record_failure", record)
    orchestrator = CollectionOrchestrator(
        session_factory, CollectionAdapter((Provider(),))
    )
    claim = ClaimedCollection(uuid4(), uuid4(), uuid4(), "kabum", "GPU", NOW)

    assert asyncio.run(orchestrator._process(claim)) is False
    assert record.call_args.args[2] == "provider_blocked"


def test_unexpected_integrity_error_propagates(monkeypatch) -> None:
    class Provider:
        source_code = "pichau"

        async def collect(self, request):
            return CollectionResult(
                "pichau", request.requested_at, request.requested_at
            )

    session_factory = MagicMock()
    session_factory.begin.return_value = nullcontext(MagicMock())
    monkeypatch.setattr(
        "app.collection.orchestration._persist_success",
        MagicMock(side_effect=IntegrityError("statement", {}, RuntimeError())),
    )
    orchestrator = CollectionOrchestrator(
        session_factory, CollectionAdapter((Provider(),))
    )
    claim = ClaimedCollection(uuid4(), uuid4(), uuid4(), "pichau", "GPU", NOW)

    with pytest.raises(IntegrityError):
        asyncio.run(orchestrator._process(claim))
