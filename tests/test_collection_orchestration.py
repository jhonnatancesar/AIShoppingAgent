"""Testes rápidos da coordenação de coleta sem dependências externas."""

import asyncio
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.ai_provider import AIProviderError, AIResponse
from app.collection.adapter import CollectionAdapter
from app.collection.contracts import CollectionResult, RawCollectedOffer
from app.collection.errors import (
    CollectionContractError,
    CollectionNormalizationError,
    ProviderBlockedError,
    ProviderCircuitOpenError,
    ProviderNavigationError,
)
from app.collection.models import CollectionRunStatus, MissionOfferRelevance
from app.collection.normalization import PriceNormalizer
from app.collection.orchestration import (
    ClaimedCollection,
    CollectionOrchestrator,
    _apply_source_backoff,
    _ensure_display_name,
    _evaluate_mission_prelist,
    _failure_code,
    _filter_deterministic_candidates,
    _is_confirmed_external_block,
    _maybe_publish_prelist_errata,
    _maybe_publish_prelist_ready,
    _persist_success,
    _raw_evidence,
    _record_failure,
    _reset_source_backoff,
    _resolve_offer,
    _resolve_offer_relevance,
    _resolve_seller,
    _safe_source,
    _select_amazon_lowest_price,
    _title_looks_like_bundle,
    _title_matches_model,
    claim_due_collections,
    ensure_missing_schedules,
    recover_stale_runs,
)
from app.collection.relevance import OfferRelevance
from app.events import EventType
from app.missions.models import Mission, MissionSchedule, MissionStatus
from app.products.models import Product
from app.users.models import UserRole
from sqlalchemy.exc import IntegrityError

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


class _StubAIManager:
    """AIProviderManager de teste: respostas fixas por `purpose`, ou falha."""

    def __init__(
        self, responses: dict[str, str] | None = None, *, raises: bool = False
    ) -> None:
        self._responses = responses or {}
        self._raises = raises
        self.calls: list[str] = []

    async def generate(self, request):
        self.calls.append(request.purpose)
        if self._raises:
            raise AIProviderError("stub_failure", retryable=False)
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub",
            content=self._responses.get(request.purpose, "{}"),
            finished_at=datetime.now(UTC),
        )


def _raw(
    *,
    source: str = "pichau",
    external_id: str = "stable",
    title: str = "Synthetic product",
    raw_price: str = "R$ 100,00",
    url: str = "https://example.invalid/offer",
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


@pytest.mark.parametrize(
    ("error", "confirmed"),
    [
        (ProviderBlockedError("pichau", 403), True),
        (ProviderBlockedError("pichau", 429), True),
        # 401 fica de fora de proposito (DEC-047): normalmente representa
        # autenticacao/credencial/configuracao, nao protecao anti-bot, e
        # nao deve crescer exponencialmente como se fosse rate limit. A
        # chamada ainda falha normalmente (provider_blocked, circuito,
        # corte de fallback) -- so o backoff persistente fica de fora.
        (ProviderBlockedError("pichau", 401), False),
        # mesmo erro, mas status ambiguo (selector ausente/oferta vazia com
        # HTTP 200): nao e bloqueio confirmado, DEC-047 nao aciona backoff.
        (ProviderBlockedError("pichau", 200), False),
        (ProviderBlockedError("pichau", None), False),
        (ProviderCircuitOpenError("pichau"), False),
        (ProviderNavigationError("pichau", 500), False),
        (TimeoutError(), False),
        (CollectionNormalizationError("bad"), False),
        (CollectionContractError("bad"), False),
        (RuntimeError("internal"), False),
    ],
)
def test_is_confirmed_external_block_only_matches_403_429(
    error: Exception, confirmed: bool
) -> None:
    assert _is_confirmed_external_block(error) is confirmed


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
    with pytest.raises(ValueError, match="negative"):
        ensure_missing_schedules(session, stagger_seconds=-1)


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
    monkeypatch.setattr(
        "app.collection.orchestration._evaluate_mission_prelist", lambda *_a, **_k: None
    )

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
    criteria = SimpleNamespace(mission_id=mission_id, search_query="GPU", model=None)
    source = SimpleNamespace(consecutive_blocks=2, next_eligible_at=NOW)
    relevance_row = SimpleNamespace(classification=OfferRelevance.MATCH)
    product_row = SimpleNamespace(display_name="Título já normalizado")
    session = MagicMock()
    session.scalar.side_effect = [run, criteria, None]

    def _get(model, _key):
        if model is Mission:
            return mission
        if model is MissionOfferRelevance:
            return relevance_row
        if model is Product:
            return product_row
        return source

    session.get.side_effect = _get
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
        lambda *_args: SimpleNamespace(id=offer_id, product_id=uuid4()),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.evaluate_price_alerts",
        lambda *_args: (candidate,),
    )
    monkeypatch.setattr("app.collection.orchestration.publish_event", publish)
    monkeypatch.setattr("app.collection.orchestration.finish_collection_run", finish)
    monkeypatch.setattr(
        "app.collection.orchestration._evaluate_mission_prelist", lambda *_a, **_k: None
    )
    claim = ClaimedCollection(run_id, mission_id, store_id, "pichau", "GPU", NOW)
    ai_manager = _StubAIManager()

    outcome = asyncio.run(
        _persist_success(session, claim, normalized, ai_manager, UserRole.ADMIN)
    )

    assert outcome is True
    assert session.add.call_count == 1
    assert publish.call_count == 2
    assert source.consecutive_blocks == 0
    assert source.next_eligible_at is None
    finish.assert_called_once()
    # relevância e título já estavam em cache: nenhuma chamada de IA
    assert ai_manager.calls == []


def test_resolve_offer_relevance_uses_cache_without_calling_ai() -> None:
    mission = SimpleNamespace(id=uuid4())
    criteria = SimpleNamespace(search_query="GPU")
    offer = SimpleNamespace(id=uuid4())
    session = MagicMock()
    session.get.return_value = SimpleNamespace(classification=OfferRelevance.NO_MATCH)
    ai_manager = _StubAIManager()

    classification = asyncio.run(
        _resolve_offer_relevance(
            session, mission, criteria, offer, "raw title", ai_manager, UserRole.ADMIN
        )
    )

    assert classification is OfferRelevance.NO_MATCH
    assert ai_manager.calls == []
    session.add.assert_not_called()


def test_resolve_offer_relevance_classifies_and_persists_when_missing() -> None:
    mission = SimpleNamespace(id=uuid4())
    criteria = SimpleNamespace(search_query="Logitech G Pro X Superlight 2")
    offer = SimpleNamespace(id=uuid4())
    session = MagicMock()
    session.get.return_value = None
    ai_manager = _StubAIManager({"classify_offer_relevance": '{"relevance": "match"}'})

    classification = asyncio.run(
        _resolve_offer_relevance(
            session,
            mission,
            criteria,
            offer,
            "Logitech G PRO X Superlight 2 Preto",
            ai_manager,
            UserRole.ADMIN,
        )
    )

    assert classification is OfferRelevance.MATCH
    assert ai_manager.calls == ["classify_offer_relevance"]
    added = session.add.call_args.args[0]
    assert isinstance(added, MissionOfferRelevance)
    assert added.mission_id == mission.id
    assert added.offer_id == offer.id
    assert added.classification is OfferRelevance.MATCH


@pytest.mark.parametrize("ai_manager", [_StubAIManager(raises=True), _StubAIManager()])
def test_resolve_offer_relevance_is_conservative_and_not_persisted_on_ai_failure(
    ai_manager: _StubAIManager,
) -> None:
    """IA fora do ar ou resposta fora do contrato (`{}`): `None`, sem persistir."""
    mission = SimpleNamespace(id=uuid4())
    criteria = SimpleNamespace(search_query="mouse")
    offer = SimpleNamespace(id=uuid4())
    session = MagicMock()
    session.get.return_value = None

    classification = asyncio.run(
        _resolve_offer_relevance(
            session, mission, criteria, offer, "raw title", ai_manager, UserRole.ADMIN
        )
    )

    assert classification is None
    session.add.assert_not_called()


def test_ensure_display_name_skips_when_already_set() -> None:
    offer = SimpleNamespace(product_id=uuid4())
    product = SimpleNamespace(display_name="Já normalizado")
    session = MagicMock()
    session.get.return_value = product
    ai_manager = _StubAIManager()

    asyncio.run(
        _ensure_display_name(session, offer, "raw title", ai_manager, UserRole.ADMIN)
    )

    assert product.display_name == "Já normalizado"
    assert ai_manager.calls == []


def test_ensure_display_name_normalizes_once_when_missing() -> None:
    offer = SimpleNamespace(product_id=uuid4())
    product = SimpleNamespace(display_name=None)
    session = MagicMock()
    session.get.return_value = product
    ai_manager = _StubAIManager(
        {"normalize_offer_title": '{"display_title": "Logitech G Pro X Superlight 2"}'}
    )

    asyncio.run(
        _ensure_display_name(
            session,
            offer,
            "Mouse Gamer Logitech G PRO X Superlight 2 Lightspeed Wireless...",
            ai_manager,
            UserRole.ADMIN,
        )
    )

    assert product.display_name == "Logitech G Pro X Superlight 2"


def test_ensure_display_name_falls_back_safely_when_ai_fails() -> None:
    offer = SimpleNamespace(product_id=uuid4())
    product = SimpleNamespace(display_name=None)
    session = MagicMock()
    session.get.return_value = product
    ai_manager = _StubAIManager(raises=True)

    asyncio.run(
        _ensure_display_name(session, offer, "raw title", ai_manager, UserRole.ADMIN)
    )

    # nunca inventa dado; o notifier usa o título bruto (Product.name)
    # como alternativa enquanto display_name continuar None
    assert product.display_name is None


def test_persist_success_blocks_alert_when_relevance_is_no_match(
    monkeypatch,
) -> None:
    mission_id, run_id, store_id, offer_id = uuid4(), uuid4(), uuid4(), uuid4()
    run = SimpleNamespace(
        id=run_id,
        mission_id=mission_id,
        store_id=store_id,
        status=CollectionRunStatus.RUNNING,
        started_at=NOW,
    )
    mission = SimpleNamespace(id=mission_id)
    criteria = SimpleNamespace(mission_id=mission_id, search_query="GPU", model=None)
    source = SimpleNamespace(consecutive_blocks=0, next_eligible_at=None)
    product_row = SimpleNamespace(display_name="Já normalizado")
    session = MagicMock()
    session.scalar.side_effect = [run, criteria, None]

    def _get(model, _key):
        if model is Mission:
            return mission
        if model is MissionOfferRelevance:
            return SimpleNamespace(classification=OfferRelevance.NO_MATCH)
        if model is Product:
            return product_row
        return source

    session.get.side_effect = _get
    result = CollectionResult("pichau", NOW, NOW + timedelta(seconds=2), (_raw(),))
    normalized = PriceNormalizer().normalize_result(result)
    evaluate = MagicMock(return_value=())
    publish = MagicMock()
    monkeypatch.setattr(
        "app.collection.orchestration._resolve_offer",
        lambda *_args: SimpleNamespace(id=offer_id, product_id=uuid4()),
    )
    monkeypatch.setattr("app.collection.orchestration.evaluate_price_alerts", evaluate)
    monkeypatch.setattr("app.collection.orchestration.publish_event", publish)
    monkeypatch.setattr(
        "app.collection.orchestration.finish_collection_run", MagicMock()
    )
    monkeypatch.setattr(
        "app.collection.orchestration._evaluate_mission_prelist", lambda *_a, **_k: None
    )
    claim = ClaimedCollection(run_id, mission_id, store_id, "pichau", "GPU", NOW)

    outcome = asyncio.run(
        _persist_success(session, claim, normalized, _StubAIManager(), UserRole.ADMIN)
    )

    assert outcome is True
    # NO_MATCH: evaluate_price_alerts nunca é chamado, só o evento de
    # coleta concluída é publicado -- nenhum alerta de preço
    evaluate.assert_not_called()
    assert publish.call_count == 1
    assert publish.call_args.kwargs["event_type"] is EventType.COLLECTION_COMPLETED_V1


def test_record_failure_discards_late_result(monkeypatch) -> None:
    claim = ClaimedCollection(uuid4(), uuid4(), uuid4(), "pichau", "GPU", NOW)
    session = MagicMock()
    session.scalar.return_value = SimpleNamespace(
        id=claim.run_id, status=CollectionRunStatus.SUCCEEDED
    )
    assert _record_failure(session, claim, "provider_blocked", NOW) is False


def test_record_failure_applies_backoff_only_when_confirmed(monkeypatch) -> None:
    claim = ClaimedCollection(uuid4(), uuid4(), uuid4(), "pichau", "GPU", NOW)
    session = MagicMock()
    session.scalar.return_value = SimpleNamespace(
        id=claim.run_id,
        status=CollectionRunStatus.RUNNING,
        mission_id=claim.mission_id,
        store_id=claim.store_id,
    )
    apply_backoff = MagicMock()
    monkeypatch.setattr(
        "app.collection.orchestration._apply_source_backoff", apply_backoff
    )
    monkeypatch.setattr(
        "app.collection.orchestration.finish_collection_run", MagicMock()
    )
    monkeypatch.setattr("app.collection.orchestration._publish_failure", MagicMock())
    monkeypatch.setattr(
        "app.collection.orchestration._evaluate_mission_prelist", lambda *_a, **_k: None
    )

    assert (
        _record_failure(session, claim, "provider_blocked", NOW, confirmed_block=True)
        is True
    )
    apply_backoff.assert_called_once_with(
        session, claim.mission_id, claim.store_id, NOW
    )


def test_record_failure_skips_backoff_when_not_confirmed(monkeypatch) -> None:
    claim = ClaimedCollection(uuid4(), uuid4(), uuid4(), "pichau", "GPU", NOW)
    session = MagicMock()
    session.scalar.return_value = SimpleNamespace(
        id=claim.run_id,
        status=CollectionRunStatus.RUNNING,
        mission_id=claim.mission_id,
        store_id=claim.store_id,
    )
    apply_backoff = MagicMock()
    monkeypatch.setattr(
        "app.collection.orchestration._apply_source_backoff", apply_backoff
    )
    monkeypatch.setattr(
        "app.collection.orchestration.finish_collection_run", MagicMock()
    )
    monkeypatch.setattr("app.collection.orchestration._publish_failure", MagicMock())
    monkeypatch.setattr(
        "app.collection.orchestration._evaluate_mission_prelist", lambda *_a, **_k: None
    )

    # timeout/erro interno/parsing: confirmed_block=False (o padrao)
    assert _record_failure(session, claim, "provider_unavailable", NOW) is True
    apply_backoff.assert_not_called()


def test_apply_source_backoff_updates_only_the_matching_source() -> None:
    mission_id, store_id = uuid4(), uuid4()
    source = SimpleNamespace(consecutive_blocks=0, next_eligible_at=None)
    session = MagicMock()
    session.get.return_value = source
    session.scalar.return_value = 30  # interval_minutes da MissionSchedule

    _apply_source_backoff(session, mission_id, store_id, NOW)

    assert source.consecutive_blocks == 1
    assert source.next_eligible_at == NOW + timedelta(minutes=60)


def test_apply_source_backoff_is_noop_without_source_or_schedule() -> None:
    session = MagicMock()
    session.get.return_value = None
    _apply_source_backoff(session, uuid4(), uuid4(), NOW)  # nao levanta

    source = SimpleNamespace(consecutive_blocks=0, next_eligible_at=None)
    session2 = MagicMock()
    session2.get.return_value = source
    session2.scalar.return_value = None  # missao sem agenda (nao deveria ocorrer)
    _apply_source_backoff(session2, uuid4(), uuid4(), NOW)
    assert source.consecutive_blocks == 0  # nao mutado sem intervalo base


def test_reset_source_backoff_clears_only_that_source() -> None:
    source = SimpleNamespace(consecutive_blocks=3, next_eligible_at=NOW)
    session = MagicMock()
    session.get.return_value = source

    _reset_source_backoff(session, uuid4(), uuid4())

    assert source.consecutive_blocks == 0
    assert source.next_eligible_at is None


def test_reset_source_backoff_is_noop_when_already_reset_or_missing() -> None:
    session = MagicMock()
    session.get.return_value = None
    _reset_source_backoff(session, uuid4(), uuid4())  # nao levanta

    already_reset = SimpleNamespace(consecutive_blocks=0, next_eligible_at=None)
    session2 = MagicMock()
    session2.get.return_value = already_reset
    _reset_source_backoff(session2, uuid4(), uuid4())
    assert already_reset.consecutive_blocks == 0
    assert already_reset.next_eligible_at is None


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
    orchestrator = CollectionOrchestrator(
        session_factory, CollectionAdapter(), ai_manager=_StubAIManager()
    )

    async def process(claim):
        return claim.source_code == "pichau"

    monkeypatch.setattr(orchestrator, "_process", process)
    result = asyncio.run(orchestrator.run_batch(now=NOW))
    assert (result.claimed, result.succeeded, result.failed) == (2, 1, 1)


@pytest.mark.parametrize(
    "options",
    [
        {"schedule_interval_minutes": 0},
        {"schedule_stagger_seconds": -1},
        {"stale_run_minutes": 0},
        {"max_concurrency": 0},
        {"max_concurrency": 5},
    ],
)
def test_orchestrator_rejects_unsafe_limits(options: dict) -> None:
    with pytest.raises(ValueError):
        CollectionOrchestrator(
            MagicMock(), CollectionAdapter(), ai_manager=_StubAIManager(), **options
        )


def test_orchestrator_forwards_stagger_to_schedule_backfill(monkeypatch) -> None:
    session_factory = MagicMock()
    session_factory.begin.return_value = nullcontext(MagicMock())
    captured: list[dict] = []

    def _capture(*_args, **kwargs):
        captured.append(kwargs)
        return 0

    monkeypatch.setattr(
        "app.collection.orchestration.ensure_missing_schedules", _capture
    )
    monkeypatch.setattr(
        "app.collection.orchestration.recover_stale_runs", lambda *_a, **_k: 0
    )
    monkeypatch.setattr(
        "app.collection.orchestration.claim_due_collections", lambda *_a, **_k: ()
    )
    orchestrator = CollectionOrchestrator(
        session_factory,
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
        schedule_interval_minutes=30,
        schedule_stagger_seconds=300,
    )

    asyncio.run(orchestrator.run_batch(now=NOW))

    assert captured == [{"now": NOW, "interval_minutes": 30, "stagger_seconds": 300}]


def test_orchestrator_processes_one_source_successfully(monkeypatch) -> None:
    class Provider:
        source_code = "pichau"

        async def collect(self, request):
            return CollectionResult(
                "pichau", request.requested_at, NOW + timedelta(seconds=2), (_raw(),)
            )

    session_factory = MagicMock()
    session_factory.begin.return_value = nullcontext(MagicMock())
    persist = AsyncMock(return_value=True)
    monkeypatch.setattr("app.collection.orchestration._persist_success", persist)
    orchestrator = CollectionOrchestrator(
        session_factory, CollectionAdapter((Provider(),)), ai_manager=_StubAIManager()
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
        session_factory, CollectionAdapter((Provider(),)), ai_manager=_StubAIManager()
    )
    claim = ClaimedCollection(uuid4(), uuid4(), uuid4(), "kabum", "GPU", NOW)

    assert asyncio.run(orchestrator._process(claim)) is False
    assert record.call_args.args[2] == "provider_blocked"


def test_evaluate_mission_prelist_is_noop_for_inactive_mission(monkeypatch) -> None:
    mission = SimpleNamespace(id=uuid4(), status=MissionStatus.PAUSED)
    session = MagicMock()
    session.scalar.return_value = mission
    ready = MagicMock()
    errata = MagicMock()
    monkeypatch.setattr(
        "app.collection.orchestration._maybe_publish_prelist_ready", ready
    )
    monkeypatch.setattr(
        "app.collection.orchestration._maybe_publish_prelist_errata", errata
    )

    _evaluate_mission_prelist(session, mission.id, NOW)

    ready.assert_not_called()
    errata.assert_not_called()


def test_evaluate_mission_prelist_dispatches_ready_then_errata(monkeypatch) -> None:
    ready = MagicMock()
    errata = MagicMock()
    monkeypatch.setattr(
        "app.collection.orchestration._maybe_publish_prelist_ready", ready
    )
    monkeypatch.setattr(
        "app.collection.orchestration._maybe_publish_prelist_errata", errata
    )
    session = MagicMock()

    # ainda não enviada: só avalia a pré-lista original, nunca a errata na
    # mesma chamada (evita publicar os dois eventos de uma vez).
    pending = SimpleNamespace(
        id=uuid4(), status=MissionStatus.ACTIVE, prelist_sent=False
    )
    session.scalar.return_value = pending
    _evaluate_mission_prelist(session, pending.id, NOW)
    ready.assert_called_once_with(session, pending, NOW)
    errata.assert_not_called()

    # já enviada, correção ainda não: avalia só a errata.
    ready.reset_mock()
    sent = SimpleNamespace(
        id=uuid4(),
        status=MissionStatus.ACTIVE,
        prelist_sent=True,
        prelist_errata_sent=False,
    )
    session.scalar.return_value = sent
    _evaluate_mission_prelist(session, sent.id, NOW)
    ready.assert_not_called()
    errata.assert_called_once_with(session, sent, NOW)

    # os dois já resolvidos: nenhuma consulta adicional é feita.
    errata.reset_mock()
    done = SimpleNamespace(
        id=uuid4(),
        status=MissionStatus.ACTIVE,
        prelist_sent=True,
        prelist_errata_sent=True,
    )
    session.scalar.return_value = done
    _evaluate_mission_prelist(session, done.id, NOW)
    errata.assert_not_called()


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
        lambda *_a: True,
    )
    monkeypatch.setattr(
        "app.collection.orchestration._latest_match_observations_by_store",
        lambda *_a: [expensive, cheap, mid],
    )
    publish = MagicMock()
    monkeypatch.setattr("app.collection.orchestration.publish_event", publish)

    _maybe_publish_prelist_ready(MagicMock(), mission, NOW)

    assert mission.prelist_sent is True
    assert mission.prelist_lowest_amount == Decimal("100.00")
    assert mission.prelist_lowest_currency == "BRL"
    payload = publish.call_args.kwargs["payload"]
    assert payload.first_offer_id == cheap.offer_id
    assert payload.first_amount == Decimal("100.00")
    assert payload.second_offer_id == mid.offer_id  # a 3a mais barata fica de fora
    assert payload.second_amount == Decimal("150.00")


def test_maybe_publish_prelist_ready_sends_nothing_without_match_offers(
    monkeypatch,
) -> None:
    mission = SimpleNamespace(
        id=uuid4(), prelist_sent=False, prelist_lowest_amount=None
    )
    monkeypatch.setattr(
        "app.collection.orchestration._mission_prelist_round_complete",
        lambda *_a: True,
    )
    monkeypatch.setattr(
        "app.collection.orchestration._latest_match_observations_by_store",
        lambda *_a: [],
    )
    publish = MagicMock()
    monkeypatch.setattr("app.collection.orchestration.publish_event", publish)

    _maybe_publish_prelist_ready(MagicMock(), mission, NOW)

    # sem nenhuma oferta MATCH ainda, marca como enviada mesmo assim -- não
    # fica reavaliando a mesma rodada já completa a cada nova coleta.
    assert mission.prelist_sent is True
    publish.assert_not_called()


def test_maybe_publish_prelist_ready_waits_for_the_full_round(monkeypatch) -> None:
    mission = SimpleNamespace(
        id=uuid4(), prelist_sent=False, prelist_lowest_amount=None
    )
    monkeypatch.setattr(
        "app.collection.orchestration._mission_prelist_round_complete",
        lambda *_a: False,
    )
    publish = MagicMock()
    monkeypatch.setattr("app.collection.orchestration.publish_event", publish)

    _maybe_publish_prelist_ready(MagicMock(), mission, NOW)

    assert mission.prelist_sent is False
    publish.assert_not_called()


def test_maybe_publish_prelist_errata_requires_strictly_cheaper_offer(
    monkeypatch,
) -> None:
    mission = SimpleNamespace(
        id=uuid4(),
        prelist_errata_sent=False,
        prelist_lowest_amount=Decimal("100.00"),
        prelist_lowest_currency="BRL",
    )
    session = MagicMock()
    session.scalar.return_value = None  # nada mais barato encontrado
    publish = MagicMock()
    monkeypatch.setattr("app.collection.orchestration.publish_event", publish)

    _maybe_publish_prelist_errata(session, mission, NOW)

    assert mission.prelist_errata_sent is False
    publish.assert_not_called()


def test_maybe_publish_prelist_errata_publishes_once_when_cheaper_found(
    monkeypatch,
) -> None:
    mission = SimpleNamespace(
        id=uuid4(),
        prelist_errata_sent=False,
        prelist_lowest_amount=Decimal("100.00"),
        prelist_lowest_currency="BRL",
    )
    cheaper = SimpleNamespace(
        offer_id=uuid4(), id=uuid4(), amount=Decimal("80.00"), currency="BRL"
    )
    session = MagicMock()
    session.scalar.return_value = cheaper
    publish = MagicMock()
    monkeypatch.setattr("app.collection.orchestration.publish_event", publish)

    _maybe_publish_prelist_errata(session, mission, NOW)

    assert mission.prelist_errata_sent is True
    payload = publish.call_args.kwargs["payload"]
    assert payload.offer_id == cheaper.offer_id
    assert payload.current_amount == Decimal("80.00")
    assert payload.previous_lowest_amount == Decimal("100.00")


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
        AsyncMock(side_effect=IntegrityError("statement", {}, RuntimeError())),
    )
    orchestrator = CollectionOrchestrator(
        session_factory, CollectionAdapter((Provider(),)), ai_manager=_StubAIManager()
    )
    claim = ClaimedCollection(uuid4(), uuid4(), uuid4(), "pichau", "GPU", NOW)

    with pytest.raises(IntegrityError):
        asyncio.run(orchestrator._process(claim))


# TASK-075: filtro determinístico de modelo -- casamento tolerante a
# separador, limite alfanumérico e sufixo de variante forte.
@pytest.mark.parametrize(
    ("model", "title", "expected"),
    [
        ("9950X3D", "Processador AMD Ryzen 9 9950X3D", True),
        ("9950X3D", "Processador AMD Ryzen 9 9950X3D2", False),
        ("9950X3D", "Processador AMD A9950X3D Edition", False),
        ("9950x3d", "processador amd ryzen 9 9950X3D", True),  # case-insensitive
        ("9950X3D", "Processador AMD Ryzen 9 9900X", False),  # modelo ausente
        ("RTX 4070", "Placa de Vídeo NVIDIA RTX 4070", True),
        ("RTX 4070", "Placa de Vídeo NVIDIA RTX 4070 Ti", False),
        ("RTX 4070", "Placa de Vídeo NVIDIA RTX 4070 SUPER", False),
        ("RTX 4070 Ti", "Placa de Vídeo NVIDIA RTX 4070 Ti", True),
        ("RTX 4070 Ti", "Placa de Vídeo NVIDIA RTX 4070 Ti SUPER", False),
        ("RTX 4070 Ti", "Placa de Vídeo NVIDIA RTX 4070 Ti OC 12GB", True),
        ("RTX 4070 Ti", "Placa de Vídeo NVIDIA RTX4070Ti Gaming", True),
        ("RTX 4070 Ti", "Placa de Vídeo NVIDIA RTX-4070-Ti Gaming", True),
    ],
)
def test_title_matches_model(model: str, title: str, expected: bool) -> None:
    assert _title_matches_model(model, title) is expected


def test_title_looks_like_bundle_rejects_full_system_not_asked() -> None:
    assert (
        _title_looks_like_bundle(
            "Processador AMD Ryzen 9 9950X3D",
            "Computador Gamer Completo Ryzen 9 9950X3D RTX 4090",
        )
        is True
    )


def test_title_looks_like_bundle_allows_when_mission_asks_for_bundle() -> None:
    assert (
        _title_looks_like_bundle(
            "Kit Upgrade Ryzen 9 9950X3D",
            "Kit Upgrade Placa-mãe + Ryzen 9 9950X3D",
        )
        is False
    )


def test_title_looks_like_bundle_false_for_plain_component() -> None:
    assert (
        _title_looks_like_bundle(
            "Processador AMD Ryzen 9 9950X3D",
            "Processador AMD Ryzen 9 9950X3D, 4.4 GHz, AM5",
        )
        is False
    )


def _normalized_offers(*raws: RawCollectedOffer):
    result = CollectionResult(
        raws[0].source_code, NOW, NOW + timedelta(seconds=2), raws
    )
    return PriceNormalizer().normalize_result(result).offers


def test_filter_deterministic_candidates_rejects_wrong_model_and_bundle() -> None:
    criteria = SimpleNamespace(
        search_query="Processador AMD Ryzen 9 9950X3D", model="9950X3D"
    )
    offers = _normalized_offers(
        _raw(external_id="1", title="Processador AMD Ryzen 9 9950X3D"),
        _raw(external_id="2", title="Processador AMD Ryzen 9 9950X3D2"),
        _raw(
            external_id="3",
            title="Computador Gamer Completo Ryzen 9 9950X3D RTX 4090",
        ),
    )

    survivors = _filter_deterministic_candidates(criteria, offers)

    assert [item.raw_offer.external_id for item in survivors] == ["1"]


def test_filter_deterministic_candidates_ambiguous_without_model_survives() -> None:
    """Sem `criteria.model`, o filtro de modelo não roda -- só o de bundle."""
    criteria = SimpleNamespace(search_query="processador AMD", model=None)
    offers = _normalized_offers(
        _raw(external_id="1", title="Processador AMD Ryzen 9 9900X"),
        _raw(external_id="2", title="Computador Gamer AMD Completo"),
    )

    survivors = _filter_deterministic_candidates(criteria, offers)

    assert [item.raw_offer.external_id for item in survivors] == ["1"]


def test_select_amazon_lowest_price_keeps_only_cheapest() -> None:
    offers = _normalized_offers(
        _raw(external_id="B01", title="Processador X", raw_price="R$ 4.500,00"),
        _raw(external_id="B02", title="Processador X", raw_price="R$ 4.000,00"),
        _raw(external_id="B03", title="Processador X", raw_price="R$ 4.500,00"),
    )

    survivors = _select_amazon_lowest_price(offers)

    assert len(survivors) == 1
    assert survivors[0].raw_offer.external_id == "B02"
    assert survivors[0].amount == Decimal("4000.00")


def test_select_amazon_lowest_price_tie_break_by_external_id() -> None:
    offers = _normalized_offers(
        _raw(external_id="B999", title="Processador X", raw_price="R$ 4.000,00"),
        _raw(external_id="B001", title="Processador X", raw_price="R$ 4.000,00"),
        _raw(external_id="B500", title="Processador X", raw_price="R$ 4.000,00"),
    )

    survivors = _select_amazon_lowest_price(offers)

    assert len(survivors) == 1
    assert survivors[0].raw_offer.external_id == "B001"


def test_select_amazon_lowest_price_empty_input() -> None:
    assert _select_amazon_lowest_price(()) == ()


def test_persist_success_rejects_wrong_model_before_any_persistence(
    monkeypatch,
) -> None:
    """TASK-075: candidato rejeitado pelo filtro de modelo nunca cria
    Product/Offer/PriceObservation nem chama classify_offer_relevance."""
    mission_id, run_id, store_id = uuid4(), uuid4(), uuid4()
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
        search_query="Processador AMD Ryzen 9 9950X3D",
        model="9950X3D",
    )
    session = MagicMock()
    session.scalar.side_effect = [run, criteria]
    session.get.side_effect = lambda model, _key: mission if model is Mission else None
    result = CollectionResult(
        "pichau",
        NOW,
        NOW + timedelta(seconds=2),
        (_raw(title="Processador AMD Ryzen 9 9950X3D2"),),  # modelo errado
    )
    normalized = PriceNormalizer().normalize_result(result)
    resolve_offer = MagicMock()
    monkeypatch.setattr("app.collection.orchestration._resolve_offer", resolve_offer)
    monkeypatch.setattr(
        "app.collection.orchestration.finish_collection_run", MagicMock()
    )
    monkeypatch.setattr("app.collection.orchestration.publish_event", MagicMock())
    monkeypatch.setattr(
        "app.collection.orchestration._evaluate_mission_prelist", lambda *_a, **_k: None
    )
    claim = ClaimedCollection(
        run_id, mission_id, store_id, "pichau", "Processador AMD Ryzen 9 9950X3D", NOW
    )
    ai_manager = _StubAIManager()

    outcome = asyncio.run(
        _persist_success(session, claim, normalized, ai_manager, UserRole.ADMIN)
    )

    assert outcome is True
    resolve_offer.assert_not_called()
    assert ai_manager.calls == []


def test_persist_success_amazon_without_model_never_selects_cheapest(
    monkeypatch,
) -> None:
    """TASK-075: gate obrigatório -- sem criteria.model, a Amazon nunca
    escolhe "o mais barato"; todos os sobreviventes do bundle seguem
    independentes, igual a qualquer outra loja."""
    mission_id, run_id, store_id = uuid4(), uuid4(), uuid4()
    run = SimpleNamespace(
        id=run_id,
        mission_id=mission_id,
        store_id=store_id,
        status=CollectionRunStatus.RUNNING,
        started_at=NOW,
    )
    mission = SimpleNamespace(id=mission_id)
    criteria = SimpleNamespace(
        mission_id=mission_id, search_query="notebook gamer", model=None
    )
    session = MagicMock()
    session.scalar.side_effect = [run, criteria, None, None]
    session.get.side_effect = lambda model, _key: mission if model is Mission else None
    result = CollectionResult(
        "amazon",
        NOW,
        NOW + timedelta(seconds=2),
        (
            _raw(
                source="amazon",
                external_id="B01",
                title="Notebook Gamer Acer",
                raw_price="R$ 4.500,00",
            ),
            _raw(
                source="amazon",
                external_id="B02",
                title="Notebook Gamer Dell",
                raw_price="R$ 4.000,00",
            ),
        ),
    )
    normalized = PriceNormalizer().normalize_result(result)
    resolved_offers = {
        "B01": SimpleNamespace(id=uuid4(), product_id=uuid4()),
        "B02": SimpleNamespace(id=uuid4(), product_id=uuid4()),
    }
    monkeypatch.setattr(
        "app.collection.orchestration._resolve_offer",
        lambda _session, _store_id, item: resolved_offers[item.raw_offer.external_id],
    )
    monkeypatch.setattr(
        "app.collection.orchestration.finish_collection_run", MagicMock()
    )
    monkeypatch.setattr("app.collection.orchestration.publish_event", MagicMock())
    monkeypatch.setattr(
        "app.collection.orchestration._evaluate_mission_prelist", lambda *_a, **_k: None
    )
    claim = ClaimedCollection(
        run_id, mission_id, store_id, "amazon", "notebook gamer", NOW
    )
    ai_manager = _StubAIManager()

    outcome = asyncio.run(
        _persist_success(session, claim, normalized, ai_manager, UserRole.ADMIN)
    )

    assert outcome is True
    # os dois notebooks (produtos genuinamente diferentes) sobrevivem --
    # nenhuma seleção de "mais barato" acontece sem identidade confirmada
    assert session.add.call_count == 2
