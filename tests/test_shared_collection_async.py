"""Testes unitários (`AsyncSession` mockada) de `app.collection.shared_
collection` (TASK-112, fase 3A/3B) -- mesmo padrão já estabelecido em
`tests/test_collection_orchestration_async.py`: `MagicMock`/`AsyncMock`
posicional configurado via `side_effect`/`return_value`, verificando
comportamento real (branches, chamadas mockadas, exceções) -- nenhum
teste raso de execução de linha. Sinal de regressão rápido, não prova de
corretude sob concorrência real (essa prova fica para o teste de
integração equivalente contra Postgres real)."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.collection.adapter import CollectionAdapter
from app.collection.contracts import CollectionResult, RawCollectedOffer
from app.collection.models import (
    CollectionRunStatus,
    SharedFanOutStatus,
)
from app.collection.normalization import Availability, PriceNormalizer
from app.collection.orchestration import (
    ClaimedCollection,
    PriceObservationComparison,
    _PhaseAOutcome,
)
from app.collection.shared_claim import _SharedClaim
from app.collection.shared_collection import (
    FanOutSweepSummary,
    SharedCollectionResult,
    SharedFanOutTerminalError,
    _allocate_fan_out_budget,
    _build_mission_phase_a_outcome,
    _claim_fan_out_task,
    _complete_fan_out_task,
    _due_shared_fan_out_targets,
    _eligible_mission_ids_session,
    _execute_claimed_shared_collection,
    _fail_fan_out_task_retryable,
    _fail_fan_out_task_terminal,
    _fan_out_retry_delay_minutes,
    _FanOutBatchOutcome,
    _finish_shared_collection,
    _mission_still_eligible_for_fan_out,
    _persist_shared_offers_and_finish,
    _process_pending_fan_out,
    _reconstruct_shared_results,
    _record_fan_out_failure,
    _release_fan_out_task_for_retry,
    _SharedOfferResult,
    _skip_fan_out_task,
    _start_mission_fan_out_run,
    collect_monitoring_item_store,
    recover_stale_fan_out_tasks,
    resume_shared_collection_fan_out,
    sweep_shared_collection_fan_out,
)
from app.missions.models import MissionSource, MissionStatus
from app.users.models import UserRole
from sqlalchemy.exc import IntegrityError

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
) -> RawCollectedOffer:
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


def _fake_execute_result(rows):
    result = MagicMock()
    result.all.return_value = rows
    return result


class _StubAIManager:
    """Placeholder passado a `_process_pending_fan_out`/afins -- nunca
    usado de fato nestes testes porque `_run_phase_b` é sempre mockado no
    limite da colaboração (o comportamento real de IA já é coberto em
    `tests/test_collection_orchestration_async.py`)."""


# ---------------------------------------------------------------------------
# _finish_shared_collection
# ---------------------------------------------------------------------------


def test_finish_shared_collection_resets_backoff_when_succeeded(monkeypatch) -> None:
    session = _mock_async_session()
    finish = AsyncMock()
    reset = AsyncMock()
    apply_backoff = AsyncMock()
    monkeypatch.setattr(
        "app.collection.shared_collection.finish_collection_run", finish
    )
    monkeypatch.setattr("app.collection.shared_collection._reset_shared_backoff", reset)
    monkeypatch.setattr(
        "app.collection.shared_collection._apply_shared_backoff", apply_backoff
    )
    run_id, item_id, store_id = uuid4(), uuid4(), uuid4()

    asyncio.run(
        _finish_shared_collection(
            _session_factory(session),
            run_id=run_id,
            monitoring_item_id=item_id,
            store_id=store_id,
            status=CollectionRunStatus.SUCCEEDED,
            finished_at=NOW,
            base_interval_minutes=45,
            confirmed_block=False,
        )
    )

    finish.assert_awaited_once_with(
        session, run_id, CollectionRunStatus.SUCCEEDED, finished_at=NOW
    )
    reset.assert_awaited_once_with(
        session, monitoring_item_id=item_id, store_id=store_id, now=NOW
    )
    apply_backoff.assert_not_awaited()


def test_finish_shared_collection_applies_backoff_when_confirmed_block(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    monkeypatch.setattr(
        "app.collection.shared_collection.finish_collection_run", AsyncMock()
    )
    reset = AsyncMock()
    apply_backoff = AsyncMock()
    monkeypatch.setattr("app.collection.shared_collection._reset_shared_backoff", reset)
    monkeypatch.setattr(
        "app.collection.shared_collection._apply_shared_backoff", apply_backoff
    )
    run_id, item_id, store_id = uuid4(), uuid4(), uuid4()

    asyncio.run(
        _finish_shared_collection(
            _session_factory(session),
            run_id=run_id,
            monitoring_item_id=item_id,
            store_id=store_id,
            status=CollectionRunStatus.FAILED,
            finished_at=NOW,
            base_interval_minutes=45,
            confirmed_block=True,
        )
    )

    apply_backoff.assert_awaited_once_with(
        session,
        monitoring_item_id=item_id,
        store_id=store_id,
        base_interval_minutes=45,
        failed_at=NOW,
    )
    reset.assert_not_awaited()


def test_finish_shared_collection_does_nothing_when_failed_without_confirmed_block(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    monkeypatch.setattr(
        "app.collection.shared_collection.finish_collection_run", AsyncMock()
    )
    reset = AsyncMock()
    apply_backoff = AsyncMock()
    monkeypatch.setattr("app.collection.shared_collection._reset_shared_backoff", reset)
    monkeypatch.setattr(
        "app.collection.shared_collection._apply_shared_backoff", apply_backoff
    )

    asyncio.run(
        _finish_shared_collection(
            _session_factory(session),
            run_id=uuid4(),
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            status=CollectionRunStatus.FAILED,
            finished_at=NOW,
            base_interval_minutes=45,
            confirmed_block=False,
        )
    )

    reset.assert_not_awaited()
    apply_backoff.assert_not_awaited()


# ---------------------------------------------------------------------------
# _eligible_mission_ids_session
# ---------------------------------------------------------------------------


def test_eligible_mission_ids_session_returns_scalars_as_tuple() -> None:
    session = _mock_async_session()
    mission_ids = (uuid4(), uuid4())
    session.scalars.return_value = list(mission_ids)

    result = asyncio.run(
        _eligible_mission_ids_session(
            session, monitoring_item_id=uuid4(), store_id=uuid4()
        )
    )

    assert result == mission_ids
    session.scalars.assert_awaited_once()


# ---------------------------------------------------------------------------
# _persist_shared_offers_and_finish
# ---------------------------------------------------------------------------


def test_persist_shared_offers_creates_new_observation_and_fan_out_tasks(
    monkeypatch,
) -> None:
    run_id, item_id, store_id = uuid4(), uuid4(), uuid4()
    mission_ids = (uuid4(), uuid4())
    result = CollectionResult("pichau", NOW, NOW + timedelta(seconds=2), (_raw(),))
    normalized = PriceNormalizer().normalize_result(result)

    session = _mock_async_session()
    # scalar: _find_offer (dentro de _preview_existing_offer_and_product,
    # oferta ainda não existe) -> None; depois latest observation -> None
    # (primeira observação desta Offer).
    session.scalar.side_effect = [None, None]
    session.scalars.side_effect = [list(mission_ids)]
    offer_stub = SimpleNamespace(
        id=uuid4(),
        product_id=uuid4(),
        last_seen_at=None,
        rating_average=None,
        review_count=None,
        rating_observed_at=None,
    )
    monkeypatch.setattr(
        "app.collection.shared_collection._resolve_offer",
        AsyncMock(return_value=offer_stub),
    )
    acquire_locks = AsyncMock()
    monkeypatch.setattr(
        "app.collection.shared_collection._acquire_creation_locks", acquire_locks
    )
    finish = AsyncMock()
    reset = AsyncMock()
    monkeypatch.setattr(
        "app.collection.shared_collection.finish_collection_run", finish
    )
    monkeypatch.setattr("app.collection.shared_collection._reset_shared_backoff", reset)

    results = asyncio.run(
        _persist_shared_offers_and_finish(
            _session_factory(session),
            run_id=run_id,
            monitoring_item_id=item_id,
            store_id=store_id,
            normalized=normalized,
            finished_at=NOW,
        )
    )

    assert len(results) == 1
    assert results[0].observation_created is True
    assert results[0].offer_id == offer_stub.id

    added_types = {type(call.args[0]).__name__ for call in session.add.call_args_list}
    assert added_types == {"PriceObservation", "SharedCollectionOffer"}
    session.flush.assert_awaited_once()
    acquire_locks.assert_awaited_once()
    assert session.execute.await_count == len(mission_ids)
    finish.assert_awaited_once_with(
        session, run_id, CollectionRunStatus.SUCCEEDED, finished_at=NOW
    )
    reset.assert_awaited_once_with(
        session, monitoring_item_id=item_id, store_id=store_id, now=NOW
    )


def test_persist_shared_offers_dedupes_items_sharing_identity_and_locks_existing_rows(
    monkeypatch,
) -> None:
    """Dois `RawCollectedOffer` com a MESMA chave de identidade (mesmo
    `external_id`) -- só o primeiro é processado (linha 411, `continue`);
    e a `Offer`/`Product` já existentes (preview não-`None`) são travadas
    via `SELECT ... FOR UPDATE` (linhas 425-432), nunca criadas de novo."""
    raw_a = _raw(external_id="dup-id", url="https://example.invalid/a")
    raw_b = _raw(external_id="dup-id", url="https://example.invalid/a")
    result = CollectionResult("pichau", NOW, NOW + timedelta(seconds=2), (raw_a, raw_b))
    normalized = PriceNormalizer().normalize_result(result)
    assert len(normalized.offers) == 2

    existing_offer = SimpleNamespace(id=uuid4())
    existing_product = SimpleNamespace(id=uuid4())
    session = _mock_async_session()
    monkeypatch.setattr(
        "app.collection.shared_collection._preview_existing_offer_and_product",
        AsyncMock(return_value=(existing_offer, existing_product)),
    )
    monkeypatch.setattr(
        "app.collection.shared_collection._acquire_creation_locks", AsyncMock()
    )
    # scalar: lock da Offer existente, lock do Product existente, depois
    # a última PriceObservation (None -- primeira observação).
    session.scalar.side_effect = [None, None, None]
    session.scalars.side_effect = [[]]
    offer_stub = SimpleNamespace(
        id=uuid4(),
        product_id=uuid4(),
        last_seen_at=None,
        rating_average=None,
        review_count=None,
        rating_observed_at=None,
    )
    resolve_offer = AsyncMock(return_value=offer_stub)
    monkeypatch.setattr(
        "app.collection.shared_collection._resolve_offer", resolve_offer
    )
    monkeypatch.setattr(
        "app.collection.shared_collection.finish_collection_run", AsyncMock()
    )
    monkeypatch.setattr(
        "app.collection.shared_collection._reset_shared_backoff", AsyncMock()
    )

    results = asyncio.run(
        _persist_shared_offers_and_finish(
            _session_factory(session),
            run_id=uuid4(),
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            normalized=normalized,
            finished_at=NOW,
        )
    )

    # Deduplicado -- só uma resolução comercial, apesar de 2 ofertas brutas.
    assert len(results) == 1
    resolve_offer.assert_awaited_once()


def test_persist_shared_offers_reuses_latest_when_commercial_state_matches(
    monkeypatch,
) -> None:
    result = CollectionResult("pichau", NOW, NOW + timedelta(seconds=2), (_raw(),))
    normalized = PriceNormalizer().normalize_result(result)
    item = normalized.offers[0]
    latest = SimpleNamespace(
        id=uuid4(),
        amount=item.amount,
        currency=item.currency,
        shipping_amount=item.shipping_amount,
        total_amount=item.total_amount,
        fulfillment=item.fulfillment,
        seller_kind=item.seller_kind,
        fulfillment_kind=item.fulfillment_kind,
        condition=item.condition,
        availability=item.availability,
        observed_at=NOW,
    )
    session = _mock_async_session()
    session.scalar.side_effect = [None, latest]
    session.scalars.side_effect = [[], []]
    offer_stub = SimpleNamespace(
        id=uuid4(),
        product_id=uuid4(),
        last_seen_at=None,
        rating_average=None,
        review_count=None,
        rating_observed_at=None,
    )
    monkeypatch.setattr(
        "app.collection.shared_collection._resolve_offer",
        AsyncMock(return_value=offer_stub),
    )
    monkeypatch.setattr(
        "app.collection.shared_collection.finish_collection_run", AsyncMock()
    )
    monkeypatch.setattr(
        "app.collection.shared_collection._reset_shared_backoff", AsyncMock()
    )

    results = asyncio.run(
        _persist_shared_offers_and_finish(
            _session_factory(session),
            run_id=uuid4(),
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            normalized=normalized,
            finished_at=NOW,
        )
    )

    assert results[0].observation_id == latest.id
    assert results[0].observation_created is False
    added_types = {type(call.args[0]).__name__ for call in session.add.call_args_list}
    assert added_types == {"SharedCollectionOffer"}
    session.flush.assert_not_awaited()


def test_persist_shared_offers_creates_installment_options_tied_to_new_observation(
    monkeypatch,
) -> None:
    from app.collection.contracts import InstallmentInterestKind, RawInstallmentOption
    from app.collection.models import OfferInstallmentOption

    raw = RawCollectedOffer(
        source_code="pichau",
        url="https://example.invalid/offer",
        title="Synthetic product",
        collected_at=NOW + timedelta(seconds=1),
        external_id="stable",
        raw_price="R$ 4.299,99",
        raw_currency="BRL",
        raw_shipping="Frete grátis",
        raw_availability="Em estoque",
        evidence={"card": "safe"},
        installment_options=(
            RawInstallmentOption(
                installment_count=12,
                raw_amount="R$ 421,57",
                raw_total_amount="R$ 5.058,81",
                interest_kind=InstallmentInterestKind.INTEREST_FREE,
            ),
        ),
    )
    result = CollectionResult("pichau", NOW, NOW + timedelta(seconds=2), (raw,))
    normalized = PriceNormalizer().normalize_result(result)

    session = _mock_async_session()
    session.scalar.side_effect = [None, None]
    session.scalars.side_effect = [[]]
    offer_stub = SimpleNamespace(
        id=uuid4(),
        product_id=uuid4(),
        last_seen_at=None,
        rating_average=None,
        review_count=None,
        rating_observed_at=None,
    )
    monkeypatch.setattr(
        "app.collection.shared_collection._resolve_offer",
        AsyncMock(return_value=offer_stub),
    )
    monkeypatch.setattr(
        "app.collection.shared_collection._acquire_creation_locks", AsyncMock()
    )
    monkeypatch.setattr(
        "app.collection.shared_collection.finish_collection_run", AsyncMock()
    )
    monkeypatch.setattr(
        "app.collection.shared_collection._reset_shared_backoff", AsyncMock()
    )

    asyncio.run(
        _persist_shared_offers_and_finish(
            _session_factory(session),
            run_id=uuid4(),
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            normalized=normalized,
            finished_at=NOW,
        )
    )

    installment_rows = [
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], OfferInstallmentOption)
    ]
    assert len(installment_rows) == 1
    assert installment_rows[0].installment_count == 12
    assert installment_rows[0].installment_amount == Decimal("421.57")
    assert session.flush.await_count == 2


# ---------------------------------------------------------------------------
# _reconstruct_shared_results
# ---------------------------------------------------------------------------


def test_reconstruct_shared_results_reads_title_from_raw_evidence() -> None:
    session = _mock_async_session()
    offer = SimpleNamespace(id=uuid4(), product_id=uuid4())
    observation = SimpleNamespace(
        id=uuid4(),
        amount=Decimal("100.00"),
        currency="BRL",
        availability=Availability.AVAILABLE,
        observed_at=NOW,
        raw_evidence={"title": "Synthetic product"},
    )
    session.execute.return_value = _fake_execute_result(
        [(SimpleNamespace(), offer, observation)]
    )

    results = asyncio.run(
        _reconstruct_shared_results(_session_factory(session), run_id=uuid4())
    )

    assert len(results) == 1
    assert results[0].raw_title == "Synthetic product"
    assert results[0].observation_created is False


def test_reconstruct_shared_results_falls_back_to_empty_title_without_raw_evidence() -> (
    None
):
    session = _mock_async_session()
    offer = SimpleNamespace(id=uuid4(), product_id=uuid4())
    observation = SimpleNamespace(
        id=uuid4(),
        amount=Decimal("100.00"),
        currency="BRL",
        availability=Availability.AVAILABLE,
        observed_at=NOW,
        raw_evidence=None,
    )
    session.execute.return_value = _fake_execute_result(
        [(SimpleNamespace(), offer, observation)]
    )

    results = asyncio.run(
        _reconstruct_shared_results(_session_factory(session), run_id=uuid4())
    )

    assert results[0].raw_title == ""


# ---------------------------------------------------------------------------
# _build_mission_phase_a_outcome
# ---------------------------------------------------------------------------


def _shared_offer_result(**overrides) -> _SharedOfferResult:
    defaults = dict(
        offer_id=uuid4(),
        product_id=uuid4(),
        observation_id=uuid4(),
        amount=Decimal("100.00"),
        currency="BRL",
        availability=Availability.AVAILABLE,
        observed_at=NOW,
        raw_title="Synthetic product",
        observation_created=True,
    )
    defaults.update(overrides)
    return _SharedOfferResult(**defaults)


def test_build_mission_phase_a_outcome_returns_none_when_run_missing() -> None:
    session = _mock_async_session()
    session.scalar.return_value = None

    outcome = asyncio.run(
        _build_mission_phase_a_outcome(
            _session_factory(session),
            run_id=uuid4(),
            mission_id=uuid4(),
            store_id=uuid4(),
            shared_results=(),
            completed_at=NOW,
        )
    )

    assert outcome is None


def test_build_mission_phase_a_outcome_returns_none_when_run_not_running() -> None:
    session = _mock_async_session()
    session.scalar.return_value = SimpleNamespace(
        id=uuid4(), status=CollectionRunStatus.SUCCEEDED
    )

    outcome = asyncio.run(
        _build_mission_phase_a_outcome(
            _session_factory(session),
            run_id=uuid4(),
            mission_id=uuid4(),
            store_id=uuid4(),
            shared_results=(),
            completed_at=NOW,
        )
    )

    assert outcome is None


def test_build_mission_phase_a_outcome_raises_when_mission_or_criteria_missing() -> (
    None
):
    run_id, mission_id, store_id = uuid4(), uuid4(), uuid4()
    run = SimpleNamespace(
        id=run_id, status=CollectionRunStatus.RUNNING, store_id=store_id
    )
    session = _mock_async_session()
    session.scalar.side_effect = [run, None]
    session.get.return_value = None

    with pytest.raises(
        SharedFanOutTerminalError, match="mission data no longer exists"
    ):
        asyncio.run(
            _build_mission_phase_a_outcome(
                _session_factory(session),
                run_id=run_id,
                mission_id=mission_id,
                store_id=store_id,
                shared_results=(),
                completed_at=NOW,
            )
        )


def test_build_mission_phase_a_outcome_raises_when_product_missing(monkeypatch) -> None:
    run_id, mission_id, store_id = uuid4(), uuid4(), uuid4()
    run = SimpleNamespace(
        id=run_id, status=CollectionRunStatus.RUNNING, store_id=store_id
    )
    mission = SimpleNamespace(id=mission_id)
    criteria = SimpleNamespace(
        search_query="GPU", target_amount=None, target_currency=None
    )
    session = _mock_async_session()
    session.scalar.side_effect = [run, criteria]
    session.get.side_effect = [mission, None, None]
    monkeypatch.setattr(
        "app.collection.shared_collection._deterministic_product_relevance",
        MagicMock(return_value=None),
    )
    shared = _shared_offer_result()

    with pytest.raises(SharedFanOutTerminalError, match="missing product"):
        asyncio.run(
            _build_mission_phase_a_outcome(
                _session_factory(session),
                run_id=run_id,
                mission_id=mission_id,
                store_id=store_id,
                shared_results=(shared,),
                completed_at=NOW,
            )
        )


def test_build_mission_phase_a_outcome_first_observation_no_previous(
    monkeypatch,
) -> None:
    run_id, mission_id, store_id = uuid4(), uuid4(), uuid4()
    run = SimpleNamespace(
        id=run_id, status=CollectionRunStatus.RUNNING, store_id=store_id
    )
    mission = SimpleNamespace(id=mission_id)
    criteria = SimpleNamespace(
        search_query="GPU", target_amount=None, target_currency=None
    )
    product = SimpleNamespace(display_name="RTX", identity_key="existing-key")
    session = _mock_async_session()
    session.scalar.side_effect = [run, criteria]
    # get: existing_relevance (None -- nunca visto antes), Product
    session.get.side_effect = [mission, None, product]
    monkeypatch.setattr(
        "app.collection.shared_collection._deterministic_product_relevance",
        MagicMock(return_value=None),
    )
    publish = AsyncMock()
    monkeypatch.setattr("app.collection.shared_collection.publish_event_async", publish)
    shared = _shared_offer_result()

    outcome = asyncio.run(
        _build_mission_phase_a_outcome(
            _session_factory(session),
            run_id=run_id,
            mission_id=mission_id,
            store_id=store_id,
            shared_results=(shared,),
            completed_at=NOW,
        )
    )

    assert outcome is not None
    pending = outcome.offers[0]
    assert pending.alert_comparison is PriceObservationComparison.FIRST_OBSERVATION
    assert pending.needs_relevance is True
    assert pending.needs_display_name is False
    assert pending.previous_observation_id is None
    publish.assert_not_awaited()


def test_build_mission_phase_a_outcome_unchanged_reused_when_previous_matches(
    monkeypatch,
) -> None:
    run_id, mission_id, store_id = uuid4(), uuid4(), uuid4()
    run = SimpleNamespace(
        id=run_id, status=CollectionRunStatus.RUNNING, store_id=store_id
    )
    mission = SimpleNamespace(id=mission_id)
    criteria = SimpleNamespace(
        search_query="GPU", target_amount=None, target_currency=None
    )
    product = SimpleNamespace(display_name="RTX", identity_key="existing-key")
    shared = _shared_offer_result()
    existing_relevance = SimpleNamespace(last_observation_id=shared.observation_id)
    previous = SimpleNamespace(
        id=shared.observation_id,
        availability=shared.availability,
        amount=shared.amount,
        currency=shared.currency,
        observed_at=shared.observed_at,
    )
    session = _mock_async_session()
    session.scalar.side_effect = [run, criteria]
    session.get.side_effect = [mission, existing_relevance, previous, product]
    monkeypatch.setattr(
        "app.collection.shared_collection._deterministic_product_relevance",
        MagicMock(return_value=None),
    )
    publish = AsyncMock()
    monkeypatch.setattr("app.collection.shared_collection.publish_event_async", publish)

    outcome = asyncio.run(
        _build_mission_phase_a_outcome(
            _session_factory(session),
            run_id=run_id,
            mission_id=mission_id,
            store_id=store_id,
            shared_results=(shared,),
            completed_at=NOW,
        )
    )

    pending = outcome.offers[0]
    assert pending.alert_comparison is PriceObservationComparison.UNCHANGED_REUSED
    assert pending.needs_relevance is False
    publish.assert_not_awaited()


def test_build_mission_phase_a_outcome_changed_publishes_availability_event(
    monkeypatch,
) -> None:
    run_id, mission_id, store_id = uuid4(), uuid4(), uuid4()
    run = SimpleNamespace(
        id=run_id, status=CollectionRunStatus.RUNNING, store_id=store_id
    )
    mission = SimpleNamespace(id=mission_id)
    criteria = SimpleNamespace(
        search_query="GPU", target_amount=None, target_currency=None
    )
    product = SimpleNamespace(display_name="RTX", identity_key="existing-key")
    shared = _shared_offer_result(availability=Availability.UNAVAILABLE)
    previous_id = uuid4()
    existing_relevance = SimpleNamespace(last_observation_id=previous_id)
    previous = SimpleNamespace(
        id=previous_id,
        availability=Availability.AVAILABLE,
        amount=Decimal("90.00"),
        currency="BRL",
        observed_at=NOW - timedelta(days=1),
    )
    session = _mock_async_session()
    session.scalar.side_effect = [run, criteria]
    session.get.side_effect = [mission, existing_relevance, previous, product]
    monkeypatch.setattr(
        "app.collection.shared_collection._deterministic_product_relevance",
        MagicMock(return_value=None),
    )
    publish = AsyncMock()
    monkeypatch.setattr("app.collection.shared_collection.publish_event_async", publish)

    outcome = asyncio.run(
        _build_mission_phase_a_outcome(
            _session_factory(session),
            run_id=run_id,
            mission_id=mission_id,
            store_id=store_id,
            shared_results=(shared,),
            completed_at=NOW,
        )
    )

    pending = outcome.offers[0]
    assert pending.alert_comparison is PriceObservationComparison.CHANGED
    publish.assert_awaited_once()
    _, kwargs = publish.call_args
    assert kwargs["mission_id"] == mission.id
    payload = kwargs["payload"]
    assert payload.previous_availability == Availability.AVAILABLE
    assert payload.current_availability == Availability.UNAVAILABLE


# ---------------------------------------------------------------------------
# _mission_still_eligible_for_fan_out
# ---------------------------------------------------------------------------


def test_mission_still_eligible_returns_false_when_mission_missing() -> None:
    session = _mock_async_session()
    session.get.return_value = None

    eligible = asyncio.run(
        _mission_still_eligible_for_fan_out(
            _session_factory(session),
            mission_id=uuid4(),
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
        )
    )

    assert eligible is False


def test_mission_still_eligible_returns_false_when_mission_not_active() -> None:
    session = _mock_async_session()
    session.get.return_value = SimpleNamespace(status=MissionStatus.PAUSED)

    eligible = asyncio.run(
        _mission_still_eligible_for_fan_out(
            _session_factory(session),
            mission_id=uuid4(),
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
        )
    )

    assert eligible is False


def test_mission_still_eligible_returns_false_when_link_missing() -> None:
    session = _mock_async_session()
    mission = SimpleNamespace(status=MissionStatus.ACTIVE)
    session.get.side_effect = [mission, None]

    eligible = asyncio.run(
        _mission_still_eligible_for_fan_out(
            _session_factory(session),
            mission_id=uuid4(),
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
        )
    )

    assert eligible is False


def test_mission_still_eligible_returns_false_when_link_points_elsewhere() -> None:
    session = _mock_async_session()
    mission = SimpleNamespace(status=MissionStatus.ACTIVE)
    link = SimpleNamespace(monitoring_item_id=uuid4())

    session.get.side_effect = [mission, link]

    eligible = asyncio.run(
        _mission_still_eligible_for_fan_out(
            _session_factory(session),
            mission_id=uuid4(),
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
        )
    )

    assert eligible is False


def test_mission_still_eligible_returns_false_when_source_missing() -> None:
    item_id = uuid4()
    session = _mock_async_session()
    mission = SimpleNamespace(status=MissionStatus.ACTIVE)
    link = SimpleNamespace(monitoring_item_id=item_id)
    session.get.side_effect = [mission, link, None]

    eligible = asyncio.run(
        _mission_still_eligible_for_fan_out(
            _session_factory(session),
            mission_id=uuid4(),
            monitoring_item_id=item_id,
            store_id=uuid4(),
        )
    )

    assert eligible is False


def test_mission_still_eligible_returns_true_when_all_checks_pass() -> None:
    item_id = uuid4()
    session = _mock_async_session()
    mission = SimpleNamespace(status=MissionStatus.ACTIVE)
    link = SimpleNamespace(monitoring_item_id=item_id)
    source = MissionSource(mission_id=uuid4(), store_id=uuid4())
    session.get.side_effect = [mission, link, source]

    eligible = asyncio.run(
        _mission_still_eligible_for_fan_out(
            _session_factory(session),
            mission_id=uuid4(),
            monitoring_item_id=item_id,
            store_id=uuid4(),
        )
    )

    assert eligible is True


# ---------------------------------------------------------------------------
# _skip_fan_out_task / _complete_fan_out_task / _release_fan_out_task_for_retry
# ---------------------------------------------------------------------------


def test_skip_fan_out_task_marks_status_when_task_exists() -> None:
    session = _mock_async_session()
    task = SimpleNamespace(status=SharedFanOutStatus.PROCESSING, updated_at=None)
    session.get.return_value = task

    asyncio.run(
        _skip_fan_out_task(
            _session_factory(session), run_id=uuid4(), mission_id=uuid4(), now=NOW
        )
    )

    assert task.status is SharedFanOutStatus.SKIPPED
    assert task.updated_at == NOW


def test_skip_fan_out_task_noop_when_task_missing() -> None:
    session = _mock_async_session()
    session.get.return_value = None

    asyncio.run(
        _skip_fan_out_task(
            _session_factory(session), run_id=uuid4(), mission_id=uuid4(), now=NOW
        )
    )

    session.get.assert_awaited_once()


def test_complete_fan_out_task_marks_done_when_task_exists() -> None:
    session = _mock_async_session()
    task = SimpleNamespace(status=SharedFanOutStatus.PROCESSING, updated_at=None)
    session.get.return_value = task

    asyncio.run(
        _complete_fan_out_task(
            _session_factory(session), run_id=uuid4(), mission_id=uuid4(), now=NOW
        )
    )

    assert task.status is SharedFanOutStatus.DONE
    assert task.updated_at == NOW


def test_complete_fan_out_task_noop_when_task_missing() -> None:
    session = _mock_async_session()
    session.get.return_value = None

    asyncio.run(
        _complete_fan_out_task(
            _session_factory(session), run_id=uuid4(), mission_id=uuid4(), now=NOW
        )
    )

    session.get.assert_awaited_once()


def test_release_fan_out_task_for_retry_resets_processing_to_pending() -> None:
    session = _mock_async_session()
    task = SimpleNamespace(status=SharedFanOutStatus.PROCESSING, updated_at=None)
    session.get.return_value = task

    asyncio.run(
        _release_fan_out_task_for_retry(
            _session_factory(session), run_id=uuid4(), mission_id=uuid4(), now=NOW
        )
    )

    assert task.status is SharedFanOutStatus.PENDING
    assert task.updated_at == NOW


def test_release_fan_out_task_for_retry_ignores_non_processing_task() -> None:
    session = _mock_async_session()
    task = SimpleNamespace(status=SharedFanOutStatus.DONE, updated_at=None)
    session.get.return_value = task

    asyncio.run(
        _release_fan_out_task_for_retry(
            _session_factory(session), run_id=uuid4(), mission_id=uuid4(), now=NOW
        )
    )

    assert task.status is SharedFanOutStatus.DONE
    assert task.updated_at is None


def test_release_fan_out_task_for_retry_noop_when_task_missing() -> None:
    session = _mock_async_session()
    session.get.return_value = None

    asyncio.run(
        _release_fan_out_task_for_retry(
            _session_factory(session), run_id=uuid4(), mission_id=uuid4(), now=NOW
        )
    )

    session.get.assert_awaited_once()


# ---------------------------------------------------------------------------
# _start_mission_fan_out_run
# ---------------------------------------------------------------------------


def test_start_mission_fan_out_run_returns_new_run_id(monkeypatch) -> None:
    session = _mock_async_session()
    new_run = SimpleNamespace(id=uuid4())
    monkeypatch.setattr(
        "app.collection.shared_collection.start_collection_run",
        AsyncMock(return_value=new_run),
    )

    run_id = asyncio.run(
        _start_mission_fan_out_run(
            _session_factory(session),
            mission_id=uuid4(),
            store_id=uuid4(),
            started_at=NOW,
        )
    )

    assert run_id == new_run.id


def test_start_mission_fan_out_run_returns_none_on_running_index_conflict(
    monkeypatch,
) -> None:
    session = _mock_async_session()

    error = IntegrityError("stmt", {}, Exception("orig"))
    monkeypatch.setattr(
        "app.collection.shared_collection.start_collection_run",
        AsyncMock(side_effect=error),
    )
    monkeypatch.setattr(
        "app.collection.shared_collection._constraint_name",
        lambda exc: "uq_collection_runs_running_mission_store",
    )

    run_id = asyncio.run(
        _start_mission_fan_out_run(
            _session_factory(session),
            mission_id=uuid4(),
            store_id=uuid4(),
            started_at=NOW,
        )
    )

    assert run_id is None


def test_start_mission_fan_out_run_reraises_unrelated_integrity_error(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    error = IntegrityError("stmt", {}, Exception("orig"))
    monkeypatch.setattr(
        "app.collection.shared_collection.start_collection_run",
        AsyncMock(side_effect=error),
    )
    monkeypatch.setattr(
        "app.collection.shared_collection._constraint_name",
        lambda exc: "other_constraint",
    )

    with pytest.raises(IntegrityError):
        asyncio.run(
            _start_mission_fan_out_run(
                _session_factory(session),
                mission_id=uuid4(),
                store_id=uuid4(),
                started_at=NOW,
            )
        )


# ---------------------------------------------------------------------------
# _record_fan_out_failure
# ---------------------------------------------------------------------------


def test_record_fan_out_failure_calls_record_failure(monkeypatch) -> None:
    session = _mock_async_session()
    record = AsyncMock()
    monkeypatch.setattr("app.collection.shared_collection._record_failure", record)
    claim = ClaimedCollection(uuid4(), uuid4(), uuid4(), "pichau", "", NOW, None)

    asyncio.run(
        _record_fan_out_failure(
            _session_factory(session), claim, "shared_fan_out_failed", NOW
        )
    )

    record.assert_awaited_once_with(session, claim, "shared_fan_out_failed", NOW)


def test_record_fan_out_failure_swallows_exception(monkeypatch) -> None:
    session = _mock_async_session()
    monkeypatch.setattr(
        "app.collection.shared_collection._record_failure",
        AsyncMock(side_effect=RuntimeError("db down")),
    )
    claim = ClaimedCollection(uuid4(), uuid4(), uuid4(), "pichau", "", NOW, None)

    # Nunca propaga -- registrar a falha do fan-out não pode, por si só,
    # derrubar o processamento da tarefa que a originou.
    asyncio.run(
        _record_fan_out_failure(
            _session_factory(session), claim, "shared_fan_out_failed", NOW
        )
    )


# ---------------------------------------------------------------------------
# _fan_out_retry_delay_minutes
# ---------------------------------------------------------------------------


def test_fan_out_retry_delay_minutes_grows_exponentially_and_caps() -> None:
    assert _fan_out_retry_delay_minutes(1) == 2
    assert _fan_out_retry_delay_minutes(2) == 4
    assert _fan_out_retry_delay_minutes(10) == 60


# ---------------------------------------------------------------------------
# _claim_fan_out_task
# ---------------------------------------------------------------------------


def test_claim_fan_out_task_returns_true_when_row_updated() -> None:
    session = _mock_async_session()
    execute_result = MagicMock()
    execute_result.rowcount = 1
    session.execute.return_value = execute_result

    claimed = asyncio.run(
        _claim_fan_out_task(
            _session_factory(session), run_id=uuid4(), mission_id=uuid4(), now=NOW
        )
    )

    assert claimed is True


def test_claim_fan_out_task_returns_false_when_no_row_updated() -> None:
    session = _mock_async_session()
    execute_result = MagicMock()
    execute_result.rowcount = 0
    session.execute.return_value = execute_result

    claimed = asyncio.run(
        _claim_fan_out_task(
            _session_factory(session), run_id=uuid4(), mission_id=uuid4(), now=NOW
        )
    )

    assert claimed is False


# ---------------------------------------------------------------------------
# _fail_fan_out_task_retryable / _fail_fan_out_task_terminal
# ---------------------------------------------------------------------------


def test_fail_fan_out_task_retryable_returns_false_when_task_missing() -> None:
    session = _mock_async_session()
    session.get.return_value = None

    became_attention_required = asyncio.run(
        _fail_fan_out_task_retryable(
            _session_factory(session),
            run_id=uuid4(),
            mission_id=uuid4(),
            now=NOW,
            error_message="boom",
        )
    )

    assert became_attention_required is False


def test_fail_fan_out_task_retryable_schedules_backoff_below_limit() -> None:
    session = _mock_async_session()
    task = SimpleNamespace(
        attempt_count=1,
        last_error=None,
        updated_at=None,
        status=SharedFanOutStatus.PROCESSING,
        next_retry_at=None,
    )
    session.get.return_value = task

    became_attention_required = asyncio.run(
        _fail_fan_out_task_retryable(
            _session_factory(session),
            run_id=uuid4(),
            mission_id=uuid4(),
            now=NOW,
            error_message="boom",
        )
    )

    assert became_attention_required is False
    assert task.attempt_count == 2
    assert task.status is SharedFanOutStatus.PENDING
    assert task.next_retry_at == NOW + timedelta(minutes=4)
    assert task.last_error == "boom"


def test_fail_fan_out_task_retryable_becomes_attention_required_at_limit() -> None:
    session = _mock_async_session()
    task = SimpleNamespace(
        attempt_count=4,
        last_error=None,
        updated_at=None,
        status=SharedFanOutStatus.PROCESSING,
        next_retry_at=None,
    )
    session.get.return_value = task

    became_attention_required = asyncio.run(
        _fail_fan_out_task_retryable(
            _session_factory(session),
            run_id=uuid4(),
            mission_id=uuid4(),
            now=NOW,
            error_message="boom",
        )
    )

    assert became_attention_required is True
    assert task.attempt_count == 5
    assert task.status is SharedFanOutStatus.ATTENTION_REQUIRED
    assert task.next_retry_at is None


def test_fail_fan_out_task_retryable_truncates_long_error_message() -> None:
    session = _mock_async_session()
    task = SimpleNamespace(
        attempt_count=0,
        last_error=None,
        updated_at=None,
        status=SharedFanOutStatus.PROCESSING,
        next_retry_at=None,
    )
    session.get.return_value = task

    asyncio.run(
        _fail_fan_out_task_retryable(
            _session_factory(session),
            run_id=uuid4(),
            mission_id=uuid4(),
            now=NOW,
            error_message="x" * 3000,
        )
    )

    assert len(task.last_error) == 2000


def test_fail_fan_out_task_terminal_marks_task_terminal() -> None:
    session = _mock_async_session()
    task = SimpleNamespace(
        attempt_count=1,
        last_error=None,
        status=SharedFanOutStatus.PROCESSING,
        next_retry_at=NOW,
        updated_at=None,
    )
    session.get.return_value = task

    asyncio.run(
        _fail_fan_out_task_terminal(
            _session_factory(session),
            run_id=uuid4(),
            mission_id=uuid4(),
            now=NOW,
            error_message="unrecoverable",
        )
    )

    assert task.status is SharedFanOutStatus.TERMINAL_FAILED
    assert task.attempt_count == 2
    assert task.next_retry_at is None
    assert task.last_error == "unrecoverable"


def test_fail_fan_out_task_terminal_noop_when_task_missing() -> None:
    session = _mock_async_session()
    session.get.return_value = None

    asyncio.run(
        _fail_fan_out_task_terminal(
            _session_factory(session),
            run_id=uuid4(),
            mission_id=uuid4(),
            now=NOW,
            error_message="unrecoverable",
        )
    )

    session.get.assert_awaited_once()


# ---------------------------------------------------------------------------
# recover_stale_fan_out_tasks
# ---------------------------------------------------------------------------


def test_recover_stale_fan_out_tasks_resets_stale_processing_tasks() -> None:
    session = _mock_async_session()
    task = SimpleNamespace(
        status=SharedFanOutStatus.PROCESSING, next_retry_at=NOW, updated_at=None
    )
    session.scalars.return_value = [task]

    count = asyncio.run(recover_stale_fan_out_tasks(session, now=NOW))

    assert count == 1
    assert task.status is SharedFanOutStatus.PENDING
    assert task.next_retry_at is None
    assert task.updated_at == NOW


def test_recover_stale_fan_out_tasks_returns_zero_when_none_found() -> None:
    session = _mock_async_session()
    session.scalars.return_value = []

    count = asyncio.run(recover_stale_fan_out_tasks(session))

    assert count == 0


# ---------------------------------------------------------------------------
# _process_pending_fan_out
# ---------------------------------------------------------------------------


def _patch_fan_out_collaborators(
    monkeypatch,
    *,
    claimed=True,
    still_eligible=True,
    mission_run_id="new",
    phase_a="outcome",
    persist_ok=True,
    build_phase_a_side_effect=None,
    run_phase_b_side_effect=None,
    persist_phase_c_side_effect=None,
):
    claim_mock = AsyncMock(return_value=claimed)
    monkeypatch.setattr(
        "app.collection.shared_collection._claim_fan_out_task", claim_mock
    )
    monkeypatch.setattr(
        "app.collection.shared_collection._mission_still_eligible_for_fan_out",
        AsyncMock(return_value=still_eligible),
    )
    skip_mock = AsyncMock()
    monkeypatch.setattr(
        "app.collection.shared_collection._skip_fan_out_task", skip_mock
    )
    run_id_value = uuid4() if mission_run_id == "new" else mission_run_id
    monkeypatch.setattr(
        "app.collection.shared_collection._start_mission_fan_out_run",
        AsyncMock(return_value=run_id_value),
    )
    phase_a_outcome = (
        _PhaseAOutcome(
            run_id=run_id_value or uuid4(),
            mission_id=uuid4(),
            store_id=uuid4(),
            mission_search_query="GPU",
            target_amount=None,
            target_currency=None,
            completed_at=NOW,
            offers=(),
        )
        if phase_a == "outcome"
        else phase_a
    )
    if build_phase_a_side_effect is not None:
        build_phase_a_mock = AsyncMock(side_effect=build_phase_a_side_effect)
    else:
        build_phase_a_mock = AsyncMock(return_value=phase_a_outcome)
    monkeypatch.setattr(
        "app.collection.shared_collection._build_mission_phase_a_outcome",
        build_phase_a_mock,
    )
    if run_phase_b_side_effect is not None:
        run_phase_b_mock = AsyncMock(side_effect=run_phase_b_side_effect)
    else:
        run_phase_b_mock = AsyncMock(return_value=())
    monkeypatch.setattr(
        "app.collection.shared_collection._run_phase_b", run_phase_b_mock
    )
    if persist_phase_c_side_effect is not None:
        persist_phase_c_mock = AsyncMock(side_effect=persist_phase_c_side_effect)
    else:
        persist_phase_c_mock = AsyncMock(return_value=persist_ok)
    monkeypatch.setattr(
        "app.collection.shared_collection._persist_phase_c", persist_phase_c_mock
    )
    complete_mock = AsyncMock()
    monkeypatch.setattr(
        "app.collection.shared_collection._complete_fan_out_task", complete_mock
    )
    release_mock = AsyncMock()
    monkeypatch.setattr(
        "app.collection.shared_collection._release_fan_out_task_for_retry", release_mock
    )
    record_failure_mock = AsyncMock()
    monkeypatch.setattr(
        "app.collection.shared_collection._record_fan_out_failure", record_failure_mock
    )
    fail_terminal_mock = AsyncMock()
    monkeypatch.setattr(
        "app.collection.shared_collection._fail_fan_out_task_terminal",
        fail_terminal_mock,
    )
    fail_retryable_mock = AsyncMock(return_value=False)
    monkeypatch.setattr(
        "app.collection.shared_collection._fail_fan_out_task_retryable",
        fail_retryable_mock,
    )
    return {
        "claim": claim_mock,
        "skip": skip_mock,
        "complete": complete_mock,
        "release": release_mock,
        "record_failure": record_failure_mock,
        "fail_terminal": fail_terminal_mock,
        "fail_retryable": fail_retryable_mock,
    }


def _process_fan_out_session(mission_id) -> MagicMock:
    session = _mock_async_session()
    session.scalars.return_value = [mission_id]
    return session


def test_process_pending_fan_out_skips_when_claim_lost(monkeypatch) -> None:
    mission_id = uuid4()
    mocks = _patch_fan_out_collaborators(monkeypatch, claimed=False)
    session = _process_fan_out_session(mission_id)

    outcome = asyncio.run(
        _process_pending_fan_out(
            _session_factory(session),
            "pichau",
            _StubAIManager(),
            UserRole.ADMIN,
            run_id=uuid4(),
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            shared_results=(),
            finished_at=NOW,
        )
    )

    assert outcome.attempted_task_count == 0
    assert outcome.done == ()
    mocks["skip"].assert_not_awaited()


def test_process_pending_fan_out_marks_skipped_when_no_longer_eligible(
    monkeypatch,
) -> None:
    mission_id = uuid4()
    mocks = _patch_fan_out_collaborators(monkeypatch, still_eligible=False)
    session = _process_fan_out_session(mission_id)

    outcome = asyncio.run(
        _process_pending_fan_out(
            _session_factory(session),
            "pichau",
            _StubAIManager(),
            UserRole.ADMIN,
            run_id=uuid4(),
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            shared_results=(),
            finished_at=NOW,
        )
    )

    assert outcome.attempted_task_count == 1
    assert outcome.skipped == (mission_id,)
    mocks["skip"].assert_awaited_once()


def test_process_pending_fan_out_releases_when_mission_run_already_exists(
    monkeypatch,
) -> None:
    mission_id = uuid4()
    mocks = _patch_fan_out_collaborators(monkeypatch, mission_run_id=None)
    session = _process_fan_out_session(mission_id)

    outcome = asyncio.run(
        _process_pending_fan_out(
            _session_factory(session),
            "pichau",
            _StubAIManager(),
            UserRole.ADMIN,
            run_id=uuid4(),
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            shared_results=(),
            finished_at=NOW,
        )
    )

    assert outcome.done == ()
    assert outcome.skipped == ()
    mocks["release"].assert_awaited_once()


def test_process_pending_fan_out_releases_when_phase_a_is_none(monkeypatch) -> None:
    mission_id = uuid4()
    mocks = _patch_fan_out_collaborators(monkeypatch, phase_a=None)
    session = _process_fan_out_session(mission_id)

    outcome = asyncio.run(
        _process_pending_fan_out(
            _session_factory(session),
            "pichau",
            _StubAIManager(),
            UserRole.ADMIN,
            run_id=uuid4(),
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            shared_results=(),
            finished_at=NOW,
        )
    )

    assert outcome.done == ()
    mocks["release"].assert_awaited_once()


def test_process_pending_fan_out_completes_task_when_persist_succeeds(
    monkeypatch,
) -> None:
    mission_id = uuid4()
    mocks = _patch_fan_out_collaborators(monkeypatch, persist_ok=True)
    session = _process_fan_out_session(mission_id)

    outcome = asyncio.run(
        _process_pending_fan_out(
            _session_factory(session),
            "pichau",
            _StubAIManager(),
            UserRole.ADMIN,
            run_id=uuid4(),
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            shared_results=(),
            finished_at=NOW,
        )
    )

    assert outcome.done == (mission_id,)
    mocks["complete"].assert_awaited_once()
    mocks["release"].assert_not_awaited()


def test_process_pending_fan_out_releases_when_persist_reports_stale_run(
    monkeypatch,
) -> None:
    mission_id = uuid4()
    mocks = _patch_fan_out_collaborators(monkeypatch, persist_ok=False)
    session = _process_fan_out_session(mission_id)

    outcome = asyncio.run(
        _process_pending_fan_out(
            _session_factory(session),
            "pichau",
            _StubAIManager(),
            UserRole.ADMIN,
            run_id=uuid4(),
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            shared_results=(),
            finished_at=NOW,
        )
    )

    assert outcome.done == ()
    mocks["release"].assert_awaited_once()
    mocks["complete"].assert_not_awaited()


def test_process_pending_fan_out_moves_to_terminal_failed_on_terminal_error(
    monkeypatch,
) -> None:
    mission_id = uuid4()

    async def _raise_terminal(*args, **kwargs):
        raise SharedFanOutTerminalError("boom")

    mocks = _patch_fan_out_collaborators(
        monkeypatch, build_phase_a_side_effect=_raise_terminal
    )
    session = _process_fan_out_session(mission_id)

    outcome = asyncio.run(
        _process_pending_fan_out(
            _session_factory(session),
            "pichau",
            _StubAIManager(),
            UserRole.ADMIN,
            run_id=uuid4(),
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            shared_results=(),
            finished_at=NOW,
        )
    )

    assert outcome.terminally_failed == (mission_id,)
    mocks["record_failure"].assert_awaited_once()
    mocks["fail_terminal"].assert_awaited_once()


def test_process_pending_fan_out_retries_generic_exception_without_attention(
    monkeypatch,
) -> None:
    mission_id = uuid4()

    async def _raise_generic(*args, **kwargs):
        raise RuntimeError("transient db error")

    mocks = _patch_fan_out_collaborators(
        monkeypatch, build_phase_a_side_effect=_raise_generic
    )
    mocks["fail_retryable"].return_value = False
    session = _process_fan_out_session(mission_id)

    outcome = asyncio.run(
        _process_pending_fan_out(
            _session_factory(session),
            "pichau",
            _StubAIManager(),
            UserRole.ADMIN,
            run_id=uuid4(),
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            shared_results=(),
            finished_at=NOW,
        )
    )

    assert outcome.attention_required == ()
    assert outcome.terminally_failed == ()
    mocks["record_failure"].assert_awaited_once()
    mocks["fail_retryable"].assert_awaited_once()


def test_process_pending_fan_out_marks_attention_required_when_retries_exhausted(
    monkeypatch,
) -> None:
    mission_id = uuid4()

    async def _raise_generic(*args, **kwargs):
        raise RuntimeError("transient db error")

    mocks = _patch_fan_out_collaborators(
        monkeypatch, build_phase_a_side_effect=_raise_generic
    )
    mocks["fail_retryable"].return_value = True
    session = _process_fan_out_session(mission_id)

    outcome = asyncio.run(
        _process_pending_fan_out(
            _session_factory(session),
            "pichau",
            _StubAIManager(),
            UserRole.ADMIN,
            run_id=uuid4(),
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            shared_results=(),
            finished_at=NOW,
        )
    )

    assert outcome.attention_required == (mission_id,)


def test_process_pending_fan_out_respects_limit() -> None:
    session = _mock_async_session()
    session.scalars.return_value = []

    outcome = asyncio.run(
        _process_pending_fan_out(
            _session_factory(session),
            "pichau",
            _StubAIManager(),
            UserRole.ADMIN,
            run_id=uuid4(),
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            shared_results=(),
            finished_at=NOW,
            limit=5,
        )
    )

    assert outcome.attempted_task_count == 0


# ---------------------------------------------------------------------------
# resume_shared_collection_fan_out
# ---------------------------------------------------------------------------


def _patch_resume_collaborators(monkeypatch, *, outcomes=None, recover_stale_return=0):
    recover_mock = AsyncMock(return_value=recover_stale_return)
    monkeypatch.setattr(
        "app.collection.shared_collection.recover_stale_fan_out_tasks", recover_mock
    )
    reconstruct_mock = AsyncMock(return_value=())
    monkeypatch.setattr(
        "app.collection.shared_collection._reconstruct_shared_results", reconstruct_mock
    )
    process_mock = AsyncMock(side_effect=list(outcomes or []))
    monkeypatch.setattr(
        "app.collection.shared_collection._process_pending_fan_out", process_mock
    )
    return {
        "recover": recover_mock,
        "reconstruct": reconstruct_mock,
        "process": process_mock,
    }


def _batch_outcome(**overrides) -> _FanOutBatchOutcome:
    defaults = dict(
        done=(),
        skipped=(),
        attention_required=(),
        terminally_failed=(),
        attempted_task_count=0,
    )
    defaults.update(overrides)
    return _FanOutBatchOutcome(**defaults)


def test_resume_shared_collection_fan_out_recovers_stale_by_default(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    session.execute.return_value = _fake_execute_result([])
    mocks = _patch_resume_collaborators(monkeypatch)

    asyncio.run(
        resume_shared_collection_fan_out(
            _session_factory(session),
            _StubAIManager(),
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            now=NOW,
        )
    )

    mocks["recover"].assert_awaited_once_with(session, now=NOW)


def test_resume_shared_collection_fan_out_skips_recovery_when_disabled(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    session.execute.return_value = _fake_execute_result([])
    mocks = _patch_resume_collaborators(monkeypatch)

    asyncio.run(
        resume_shared_collection_fan_out(
            _session_factory(session),
            _StubAIManager(),
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            now=NOW,
            recover_stale=False,
        )
    )

    mocks["recover"].assert_not_awaited()


def test_resume_shared_collection_fan_out_aggregates_across_runs(monkeypatch) -> None:
    session = _mock_async_session()
    run_id_1, run_id_2 = uuid4(), uuid4()
    session.execute.return_value = _fake_execute_result(
        [(run_id_1, "pichau"), (run_id_2, "kabum")]
    )
    mission_1, mission_2 = uuid4(), uuid4()
    outcomes = [
        _batch_outcome(done=(mission_1,), attempted_task_count=1),
        _batch_outcome(skipped=(mission_2,), attempted_task_count=1),
    ]
    mocks = _patch_resume_collaborators(monkeypatch, outcomes=outcomes)

    result = asyncio.run(
        resume_shared_collection_fan_out(
            _session_factory(session),
            _StubAIManager(),
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            now=NOW,
        )
    )

    assert result.fanned_out_mission_ids == (mission_1,)
    assert result.fan_out_skipped_mission_ids == (mission_2,)
    assert result.attempted_task_count == 2
    assert mocks["process"].await_count == 2
    assert mocks["reconstruct"].await_count == 2
    first_call_kwargs = mocks["process"].await_args_list[0].kwargs
    assert first_call_kwargs["run_id"] == run_id_1
    second_call_kwargs = mocks["process"].await_args_list[1].kwargs
    assert second_call_kwargs["run_id"] == run_id_2


def test_resume_shared_collection_fan_out_stops_when_budget_exhausted(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    run_id_1, run_id_2 = uuid4(), uuid4()
    session.execute.return_value = _fake_execute_result(
        [(run_id_1, "pichau"), (run_id_2, "kabum")]
    )
    mission_1 = uuid4()
    outcomes = [_batch_outcome(done=(mission_1,), attempted_task_count=3)]
    mocks = _patch_resume_collaborators(monkeypatch, outcomes=outcomes)

    result = asyncio.run(
        resume_shared_collection_fan_out(
            _session_factory(session),
            _StubAIManager(),
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            now=NOW,
            task_limit=3,
        )
    )

    assert result.fanned_out_mission_ids == (mission_1,)
    mocks["process"].assert_awaited_once()
    first_call_kwargs = mocks["process"].await_args_list[0].kwargs
    assert first_call_kwargs["limit"] == 3


# ---------------------------------------------------------------------------
# _due_shared_fan_out_targets
# ---------------------------------------------------------------------------


def test_due_shared_fan_out_targets_returns_triples_without_earliest_due_at() -> None:
    session = _mock_async_session()
    item_id, store_id = uuid4(), uuid4()
    session.execute.return_value = _fake_execute_result([(item_id, store_id, 5, NOW)])

    result = asyncio.run(_due_shared_fan_out_targets(session, now=NOW, limit=10))

    assert result == ((item_id, store_id, 5),)


def test_due_shared_fan_out_targets_returns_empty_tuple_when_no_rows() -> None:
    session = _mock_async_session()
    session.execute.return_value = _fake_execute_result([])

    result = asyncio.run(_due_shared_fan_out_targets(session, now=NOW, limit=10))

    assert result == ()


# ---------------------------------------------------------------------------
# _allocate_fan_out_budget
# ---------------------------------------------------------------------------


def test_allocate_fan_out_budget_gives_full_allocation_when_budget_sufficient() -> None:
    item_1, store_1 = uuid4(), uuid4()
    item_2, store_2 = uuid4(), uuid4()
    targets = ((item_1, store_1, 3), (item_2, store_2, 2))

    result = _allocate_fan_out_budget(targets, task_budget=10, per_target_task_cap=10)

    assert result == ((item_1, store_1, 3), (item_2, store_2, 2))


def test_allocate_fan_out_budget_caps_per_round_for_fairness_between_targets() -> None:
    item_1, store_1 = uuid4(), uuid4()
    item_2, store_2 = uuid4(), uuid4()
    targets = ((item_1, store_1, 10), (item_2, store_2, 2))

    result = _allocate_fan_out_budget(targets, task_budget=6, per_target_task_cap=3)

    assert result == ((item_1, store_1, 4), (item_2, store_2, 2))


def test_allocate_fan_out_budget_excludes_targets_with_zero_remaining() -> None:
    item_1, store_1 = uuid4(), uuid4()
    item_2, store_2 = uuid4(), uuid4()
    targets = ((item_1, store_1, 0), (item_2, store_2, 3))

    result = _allocate_fan_out_budget(targets, task_budget=5, per_target_task_cap=5)

    assert result == ((item_2, store_2, 3),)


def test_allocate_fan_out_budget_returns_empty_when_no_targets() -> None:
    result = _allocate_fan_out_budget((), task_budget=5, per_target_task_cap=5)

    assert result == ()


def test_allocate_fan_out_budget_returns_empty_when_per_target_cap_is_zero() -> None:
    item_1, store_1 = uuid4(), uuid4()
    targets = ((item_1, store_1, 3),)

    result = _allocate_fan_out_budget(targets, task_budget=5, per_target_task_cap=0)

    assert result == ()


# ---------------------------------------------------------------------------
# FanOutSweepSummary.aggregate
# ---------------------------------------------------------------------------


def _sweep_collection_result(**overrides) -> SharedCollectionResult:
    defaults = dict(claimed=False)
    defaults.update(overrides)
    return SharedCollectionResult(**defaults)


def test_fan_out_sweep_summary_aggregate_sums_counts_across_outcomes() -> None:
    mission_1, mission_2, mission_3, mission_4 = uuid4(), uuid4(), uuid4(), uuid4()
    outcomes = [
        _sweep_collection_result(
            fanned_out_mission_ids=(mission_1,),
            fan_out_skipped_mission_ids=(mission_2,),
            attempted_task_count=2,
        ),
        _sweep_collection_result(
            fan_out_attention_required_mission_ids=(mission_3,),
            fan_out_failed_mission_ids=(mission_4,),
            attempted_task_count=3,
        ),
    ]

    summary = FanOutSweepSummary.aggregate(outcomes)

    assert summary.targets_processed == 2
    assert summary.attempted_task_count == 5
    assert summary.fanned_out_mission_count == 1
    assert summary.skipped_mission_count == 1
    assert summary.attention_required_mission_count == 1
    assert summary.terminally_failed_mission_count == 1


def test_fan_out_sweep_summary_aggregate_returns_zeros_for_empty_outcomes() -> None:
    summary = FanOutSweepSummary.aggregate([])

    assert summary == FanOutSweepSummary()


# ---------------------------------------------------------------------------
# sweep_shared_collection_fan_out
# ---------------------------------------------------------------------------


def _patch_sweep_collaborators(
    monkeypatch, *, targets=(), allocations=None, resume_results=None
):
    recover_mock = AsyncMock(return_value=0)
    monkeypatch.setattr(
        "app.collection.shared_collection.recover_stale_fan_out_tasks", recover_mock
    )
    due_targets_mock = AsyncMock(return_value=targets)
    monkeypatch.setattr(
        "app.collection.shared_collection._due_shared_fan_out_targets", due_targets_mock
    )
    allocations = targets if allocations is None else allocations
    allocate_mock = MagicMock(return_value=allocations)
    monkeypatch.setattr(
        "app.collection.shared_collection._allocate_fan_out_budget", allocate_mock
    )
    resume_mock = AsyncMock(side_effect=list(resume_results or []))
    monkeypatch.setattr(
        "app.collection.shared_collection.resume_shared_collection_fan_out", resume_mock
    )
    return {
        "recover": recover_mock,
        "due_targets": due_targets_mock,
        "allocate": allocate_mock,
        "resume": resume_mock,
    }


def test_sweep_shared_collection_fan_out_returns_default_summary_when_no_allocations(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    mocks = _patch_sweep_collaborators(monkeypatch, targets=(), allocations=())

    summary = asyncio.run(
        sweep_shared_collection_fan_out(
            _session_factory(session), _StubAIManager(), now=NOW
        )
    )

    assert summary == FanOutSweepSummary()
    mocks["resume"].assert_not_awaited()
    mocks["recover"].assert_awaited_once_with(session, now=NOW)


def test_sweep_shared_collection_fan_out_runs_one_target_per_allocation(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    item_1, store_1 = uuid4(), uuid4()
    item_2, store_2 = uuid4(), uuid4()
    allocations = ((item_1, store_1, 4), (item_2, store_2, 2))
    results = [
        _sweep_collection_result(attempted_task_count=4),
        _sweep_collection_result(attempted_task_count=2),
    ]
    mocks = _patch_sweep_collaborators(
        monkeypatch,
        targets=allocations,
        allocations=allocations,
        resume_results=results,
    )

    summary = asyncio.run(
        sweep_shared_collection_fan_out(
            _session_factory(session), _StubAIManager(), now=NOW
        )
    )

    assert summary.targets_processed == 2
    assert summary.attempted_task_count == 6
    assert mocks["resume"].await_count == 2
    called_kwargs = [call.kwargs for call in mocks["resume"].await_args_list]
    called_targets = {
        (kw["monitoring_item_id"], kw["store_id"], kw["task_limit"])
        for kw in called_kwargs
    }
    assert called_targets == {(item_1, store_1, 4), (item_2, store_2, 2)}
    assert all(kw["recover_stale"] is False for kw in called_kwargs)


# ---------------------------------------------------------------------------
# _execute_claimed_shared_collection
# ---------------------------------------------------------------------------


def _shared_claim(**overrides) -> _SharedClaim:
    defaults = dict(
        run_id=uuid4(),
        criteria=SimpleNamespace(search_query="GPU", model=None),
        store_code="pichau",
        monitoring_item_id=uuid4(),
        store_id=uuid4(),
    )
    defaults.update(overrides)
    return _SharedClaim(**defaults)


def _shared_offer_result_full(**overrides) -> _SharedOfferResult:
    defaults = dict(
        offer_id=uuid4(),
        product_id=uuid4(),
        observation_id=uuid4(),
        amount=Decimal("100.00"),
        currency="BRL",
        availability=Availability.AVAILABLE,
        observed_at=NOW,
        raw_title="Synthetic product",
        observation_created=True,
    )
    defaults.update(overrides)
    return _SharedOfferResult(**defaults)


def test_execute_claimed_shared_collection_success_path(monkeypatch) -> None:
    class Provider:
        source_code = "pichau"

        async def collect(self, request):
            return CollectionResult(
                "pichau", request.requested_at, NOW + timedelta(seconds=2), (_raw(),)
            )

    shared_results = (_shared_offer_result_full(),)
    persist_mock = AsyncMock(return_value=shared_results)
    monkeypatch.setattr(
        "app.collection.shared_collection._persist_shared_offers_and_finish",
        persist_mock,
    )
    mission_id = uuid4()
    process_mock = AsyncMock(
        return_value=_batch_outcome(done=(mission_id,), attempted_task_count=1)
    )
    monkeypatch.setattr(
        "app.collection.shared_collection._process_pending_fan_out", process_mock
    )
    session_factory = _session_factory(_mock_async_session())
    claim = _shared_claim()

    result = asyncio.run(
        _execute_claimed_shared_collection(
            session_factory,
            CollectionAdapter((Provider(),)),
            _StubAIManager(),
            claim=claim,
            ai_profile=UserRole.ADMIN,
            normalizer=None,
            effective_now=NOW,
            base_backoff_minutes=15,
        )
    )

    assert result.claimed is True
    assert result.provider_called is True
    assert result.succeeded is True
    assert result.offers_count == 1
    assert result.fanned_out_mission_ids == (mission_id,)
    assert result.attempted_task_count == 1
    persist_mock.assert_awaited_once()
    process_mock.assert_awaited_once()


def test_execute_claimed_shared_collection_enrichment_failure_falls_back_to_selected_raw(
    monkeypatch,
) -> None:
    class Provider:
        source_code = "pichau"

        async def collect(self, request):
            return CollectionResult(
                "pichau",
                request.requested_at,
                NOW + timedelta(seconds=2),
                (_raw(title="Título original"),),
            )

        async def enrich_offer_details(self, offers):
            raise RuntimeError("enriquecimento indisponível")

    persist_mock = AsyncMock(return_value=())
    monkeypatch.setattr(
        "app.collection.shared_collection._persist_shared_offers_and_finish",
        persist_mock,
    )
    monkeypatch.setattr(
        "app.collection.shared_collection._process_pending_fan_out",
        AsyncMock(return_value=_batch_outcome()),
    )
    session_factory = _session_factory(_mock_async_session())
    claim = _shared_claim()

    result = asyncio.run(
        _execute_claimed_shared_collection(
            session_factory,
            CollectionAdapter((Provider(),)),
            _StubAIManager(),
            claim=claim,
            ai_profile=UserRole.ADMIN,
            normalizer=None,
            effective_now=NOW,
            base_backoff_minutes=15,
        )
    )

    assert result.succeeded is True
    normalized_arg = persist_mock.call_args.kwargs["normalized"]
    assert normalized_arg.offers[0].raw_offer.title == "Título original"


def test_execute_claimed_shared_collection_provider_failure_confirmed_block(
    monkeypatch,
) -> None:
    from app.collection.errors import ProviderBlockedError

    class FailingProvider:
        source_code = "pichau"

        async def collect(self, request):
            raise ProviderBlockedError(request.source_code, 403)

    finish_mock = AsyncMock()
    monkeypatch.setattr(
        "app.collection.shared_collection._finish_shared_collection", finish_mock
    )
    session_factory = _session_factory(_mock_async_session())
    claim = _shared_claim()

    result = asyncio.run(
        _execute_claimed_shared_collection(
            session_factory,
            CollectionAdapter((FailingProvider(),)),
            _StubAIManager(),
            claim=claim,
            ai_profile=UserRole.ADMIN,
            normalizer=None,
            effective_now=NOW,
            base_backoff_minutes=15,
        )
    )

    assert result == SharedCollectionResult(
        claimed=True, provider_called=True, succeeded=False
    )
    finish_mock.assert_awaited_once()
    assert finish_mock.call_args.kwargs["status"] == CollectionRunStatus.FAILED
    assert finish_mock.call_args.kwargs["confirmed_block"] is True
    assert finish_mock.call_args.kwargs["finished_at"] == NOW


def test_execute_claimed_shared_collection_provider_failure_not_confirmed_block(
    monkeypatch,
) -> None:
    class FailingProvider:
        source_code = "pichau"

        async def collect(self, request):
            raise RuntimeError("timeout de rede")

    finish_mock = AsyncMock()
    monkeypatch.setattr(
        "app.collection.shared_collection._finish_shared_collection", finish_mock
    )
    session_factory = _session_factory(_mock_async_session())
    claim = _shared_claim()

    result = asyncio.run(
        _execute_claimed_shared_collection(
            session_factory,
            CollectionAdapter((FailingProvider(),)),
            _StubAIManager(),
            claim=claim,
            ai_profile=UserRole.ADMIN,
            normalizer=None,
            effective_now=NOW,
            base_backoff_minutes=15,
        )
    )

    assert result.succeeded is False
    assert finish_mock.call_args.kwargs["confirmed_block"] is False


# ---------------------------------------------------------------------------
# collect_monitoring_item_store
# ---------------------------------------------------------------------------


def test_collect_monitoring_item_store_returns_unclaimed_when_claim_fails(
    monkeypatch,
) -> None:
    claim_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "app.collection.shared_collection._claim_shared_collection", claim_mock
    )
    execute_mock = AsyncMock()
    monkeypatch.setattr(
        "app.collection.shared_collection._execute_claimed_shared_collection",
        execute_mock,
    )
    session_factory = _session_factory(_mock_async_session())

    result = asyncio.run(
        collect_monitoring_item_store(
            session_factory,
            CollectionAdapter(),
            _StubAIManager(),
            monitoring_item_id=uuid4(),
            store_id=uuid4(),
            now=NOW,
        )
    )

    assert result == SharedCollectionResult(claimed=False)
    execute_mock.assert_not_awaited()


def test_collect_monitoring_item_store_delegates_to_execute_when_claimed(
    monkeypatch,
) -> None:
    claim = _shared_claim()
    monkeypatch.setattr(
        "app.collection.shared_collection._claim_shared_collection",
        AsyncMock(return_value=claim),
    )
    expected = SharedCollectionResult(
        claimed=True, provider_called=True, succeeded=True
    )
    execute_mock = AsyncMock(return_value=expected)
    monkeypatch.setattr(
        "app.collection.shared_collection._execute_claimed_shared_collection",
        execute_mock,
    )
    session_factory = _session_factory(_mock_async_session())
    monitoring_item_id, store_id = uuid4(), uuid4()

    result = asyncio.run(
        collect_monitoring_item_store(
            session_factory,
            CollectionAdapter(),
            _StubAIManager(),
            monitoring_item_id=monitoring_item_id,
            store_id=store_id,
            now=NOW,
        )
    )

    assert result is expected
    execute_mock.assert_awaited_once()
    assert execute_mock.call_args.kwargs["claim"] is claim
    assert execute_mock.call_args.kwargs["effective_now"] == NOW
