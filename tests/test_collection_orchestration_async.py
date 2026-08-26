"""Testes rápidos (AsyncMock) do caminho `AsyncSession` do collection_worker.

TASK-079: sinal de regressão rápido para as funções que tocam
`AsyncSession` -- não a prova de corretude sob concorrência real, que é
`tests/integration/test_collection_orchestration.py` (PostgreSQL real,
locks/upserts/isolamento de verdade). Aqui só se verifica que cada função
chama suas dependências na ordem esperada e monta o resultado certo a
partir de retornos controlados.
"""

import asyncio
import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.ai_provider import AIResponse
from app.collection.adapter import CollectionAdapter
from app.collection.contracts import (
    CollectionResult,
    MarketplacePartyKind,
    OfferCondition,
    RawCollectedOffer,
    ResolvedProductIdentity,
)
from app.collection.errors import (
    CollectionNormalizationError,
    ProviderBlockedError,
    ProviderCircuitOpenError,
    ProviderNavigationError,
)
from app.collection.models import CollectionRunStatus, PriceObservation
from app.collection.normalization import Availability, PriceNormalizer
from app.collection.orchestration import (
    ClaimedCollection,
    CollectionOrchestrator,
    _AIOutcome,
    _apply_source_backoff,
    _deterministic_product_relevance,
    _evaluate_mission_prelist,
    _find_offer,
    _installment_snapshot,
    _maybe_publish_prelist_errata,
    _maybe_publish_prelist_ready,
    _mission_relevance_pending,
    _PendingOffer,
    _persist_phase_a,
    _persist_phase_c,
    _PhaseAOutcome,
    _prelist_commercial_key,
    _PrelistCandidate,
    PriceObservationComparison,
    _record_failure,
    _reset_source_backoff,
    _resolve_offer,
    _resolve_seller,
    _run_phase_b,
    _same_commercial_state,
    claim_due_collections,
    ensure_missing_schedules,
    recover_stale_runs,
)
from app.collection.relevance import OfferRelevance
from app.missions.models import MissionStatus, VariantSelectionMode
from app.products.identity import classify_product_request
from app.products.models import Product
from app.users.models import UserRole

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "other_title", ("iPhone 17 Pro Max 256GB", "iPhone 17 Pro 128GB")
)
def test_specific_product_rejects_other_variant_before_ai(other_title: str) -> None:
    request = classify_product_request("iPhone 17 Pro 256GB")
    criteria = SimpleNamespace(
        request_kind=request.kind.value,
        requested_identity_key=request.identity_key,
        requested_family_key=request.family_key,
        requested_variant=request.variant,
    )
    other = classify_product_request(other_title)
    product = Product(
        name=other_title,
        identity_key=other.identity_key,
        family_key=other.family_key,
        variant=other.variant,
    )

    assert _deterministic_product_relevance(criteria, product) is OfferRelevance.NO_MATCH


def test_generic_category_does_not_force_product_relevance() -> None:
    criteria = SimpleNamespace(request_kind="generic_category")
    product = Product(name="Cadeira gamer qualquer")

    assert _deterministic_product_relevance(criteria, product) is None


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
    seller_kind: MarketplacePartyKind | None = None,
    fulfillment_kind: MarketplacePartyKind | None = None,
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
        seller_kind=seller_kind,
        fulfillment_kind=fulfillment_kind,
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
    """TASK-062 original: dado um schedule due com 2 fontes elegíveis, cada
    fonte ganha seu próprio `CollectionRun` e o schedule avança. TASK-108
    trocou a seleção de schedules due por `_select_due_schedules_for_batch`
    (devolve `(schedule, user_id)`, não só `schedule`) -- aqui ela é
    mockada diretamente (não é o objeto deste teste, que é a criação de
    run/avanço de schedule dado um schedule já selecionado). Fairness/
    cooldown/throttle de loja também não são o objeto deste teste --
    neutralizados explicitamente para não mascarar o cenário."""
    from app.missions.models import MissionSchedule

    mission_id = uuid4()
    user_id = uuid4()
    schedule = MissionSchedule(
        id=uuid4(),
        mission_id=mission_id,
        interval_minutes=60,
        next_run_at=NOW,
        is_enabled=True,
    )
    criteria = SimpleNamespace(search_query="GPU", model=None)
    store_ids = (uuid4(), uuid4())
    session = _mock_async_session()
    session.scalar.side_effect = [None, criteria]
    rows = MagicMock()
    rows.all.return_value = [(store_ids[0], "kabum"), (store_ids[1], "pichau")]
    session.execute.return_value = rows
    runs = iter((SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4())))
    monkeypatch.setattr(
        "app.collection.orchestration._select_due_schedules_for_batch",
        AsyncMock(return_value=[(schedule, user_id)]),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.start_collection_run",
        AsyncMock(side_effect=lambda *_a, **_k: next(runs)),
    )

    claims = asyncio.run(
        claim_due_collections(
            session,
            now=NOW,
            user_cooldown_min_seconds=0,
            user_cooldown_max_seconds=0,
            store_min_interval_seconds=0,
        )
    )

    assert [claim.source_code for claim in claims] == ["kabum", "pichau"]
    assert schedule.last_run_at == NOW
    assert schedule.next_run_at == NOW + timedelta(hours=1)
    session.flush.assert_awaited_once()


def test_claim_due_collections_skips_mission_already_running() -> None:
    """TASK-062 original: missão com `CollectionRun` já `RUNNING` não pode
    ser reclamada de novo -- nenhuma claim, nenhum run novo. Mesma troca de
    `find_due_schedules_async` -> `_select_due_schedules_for_batch` do
    teste acima; fairness/cooldown/throttle neutralizados (não são o
    objeto deste teste, e nem chegam a rodar aqui já que nenhuma claim é
    produzida)."""
    from app.missions.models import MissionSchedule

    mission_id = uuid4()
    user_id = uuid4()
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

        module._select_due_schedules_for_batch = AsyncMock(
            return_value=[(schedule, user_id)]
        )
        return await claim_due_collections(
            session,
            now=NOW,
            user_cooldown_min_seconds=0,
            user_cooldown_max_seconds=0,
            store_min_interval_seconds=0,
        )

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
    # scalar: run, criteria, previous(=None), latest(=None) (TASK-093)
    session.scalar.side_effect = [run, criteria, None, None]
    # get: Mission, MissionOfferRelevance cache (None -> needs AI), Product (display_name=None)
    product = SimpleNamespace(display_name=None)
    session.get.side_effect = [mission, None, product]
    offer = SimpleNamespace(id=offer_id, product_id=product_id)
    monkeypatch.setattr(
        "app.collection.orchestration._resolve_offer", AsyncMock(return_value=offer)
    )
    claim = ClaimedCollection(run_id, mission_id, store_id, "pichau", "GPU", NOW)
    result = CollectionResult(
        "pichau",
        NOW,
        NOW + timedelta(seconds=2),
        (
            _raw(
                seller_kind=MarketplacePartyKind.PLATFORM,
                fulfillment_kind=MarketplacePartyKind.MARKETPLACE_PARTNER,
            ),
        ),
    )
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
    observation = next(
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], PriceObservation)
    )
    assert observation.seller_kind is MarketplacePartyKind.PLATFORM
    assert observation.fulfillment_kind is MarketplacePartyKind.MARKETPLACE_PARTNER


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


# --- TASK-089 (DEC-069): persistência de OfferInstallmentOption ---


def test_persist_phase_a_creates_installment_options_tied_to_new_observation(
    monkeypatch,
) -> None:
    from app.collection.contracts import InstallmentInterestKind, RawInstallmentOption
    from app.collection.models import OfferInstallmentOption

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
    session.scalar.side_effect = [run, criteria, None, None]  # +latest (TASK-093)
    product = SimpleNamespace(display_name=None)
    session.get.side_effect = [mission, None, product]
    offer = SimpleNamespace(id=offer_id, product_id=product_id)
    monkeypatch.setattr(
        "app.collection.orchestration._resolve_offer", AsyncMock(return_value=offer)
    )
    claim = ClaimedCollection(run_id, mission_id, store_id, "pichau", "GPU", NOW)
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

    asyncio.run(_persist_phase_a(_session_factory(session), claim, normalized))

    observation = next(
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], PriceObservation)
    )
    installment_row = next(
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], OfferInstallmentOption)
    )
    assert installment_row.price_observation_id == observation.id
    assert installment_row.installment_count == 12
    assert installment_row.installment_amount == Decimal("421.57")
    assert installment_row.installment_total_amount == Decimal("5058.81")
    assert installment_row.interest_kind is InstallmentInterestKind.INTEREST_FREE


def test_persist_phase_a_adds_no_installment_row_when_offer_has_none(
    monkeypatch,
) -> None:
    from app.collection.models import OfferInstallmentOption

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
    session.scalar.side_effect = [run, criteria, None, None]  # +latest (TASK-093)
    product = SimpleNamespace(display_name=None)
    session.get.side_effect = [mission, None, product]
    offer = SimpleNamespace(id=offer_id, product_id=product_id)
    monkeypatch.setattr(
        "app.collection.orchestration._resolve_offer", AsyncMock(return_value=offer)
    )
    claim = ClaimedCollection(run_id, mission_id, store_id, "kabum", "GPU", NOW)
    result = CollectionResult(
        "kabum", NOW, NOW + timedelta(seconds=2), (_raw(source="kabum"),)
    )
    normalized = PriceNormalizer().normalize_result(result)

    asyncio.run(_persist_phase_a(_session_factory(session), claim, normalized))

    assert not any(
        isinstance(call.args[0], OfferInstallmentOption)
        for call in session.add.call_args_list
    )


# --- TASK-082: limitação de candidatos em busca genérica, integrada na Fase A ---


def test_persist_phase_a_limits_generic_search_candidates_per_source(
    monkeypatch,
) -> None:
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
        search_query="cadeira gamer",
        model=None,
        target_amount=None,
        target_currency=None,
    )
    session = _mock_async_session()
    # scalar: run, criteria, depois previous+latest (TASK-093) por
    # sobrevivente (5, dentro do pool intermediário de 8 da TASK-094)
    session.scalar.side_effect = [run, criteria] + [None] * 10
    product = SimpleNamespace(display_name="Cadeira")
    # get: Mission, depois (relevance_cache, product) por sobrevivente.
    session.get.side_effect = [mission] + [None, product] * 5
    offer_stub = SimpleNamespace(id=uuid4(), product_id=uuid4())
    monkeypatch.setattr(
        "app.collection.orchestration._resolve_offer",
        AsyncMock(return_value=offer_stub),
    )
    claim = ClaimedCollection(
        run_id, mission_id, store_id, "kabum", "cadeira gamer", NOW
    )
    result = CollectionResult(
        "kabum",
        NOW,
        NOW + timedelta(seconds=2),
        (
            _raw(
                source="kabum",
                external_id="1",
                title="Cadeira A",
                raw_price="R$ 900,00",
            ),
            _raw(
                source="kabum",
                external_id="2",
                title="Cadeira B",
                raw_price="R$ 500,00",
            ),
            _raw(
                source="kabum",
                external_id="3",
                title="Cadeira C",
                raw_price="R$ 700,00",
            ),
            _raw(
                source="kabum",
                external_id="4",
                title="Cadeira D",
                raw_price="R$ 300,00",
            ),
            _raw(
                source="kabum",
                external_id="5",
                title="Cadeira E",
                raw_price="R$ 1.000,00",
            ),
        ),
    )
    normalized = PriceNormalizer().normalize_result(result)

    outcome = asyncio.run(
        _persist_phase_a(_session_factory(session), claim, normalized)
    )

    # TASK-094 substituiu o corte antigo de 3 por pool comum de até 8.
    assert len(outcome.offers) == 5
    assert [pending.raw_title for pending in outcome.offers] == [
        "Cadeira D",
        "Cadeira B",
        "Cadeira C",
        "Cadeira A",
        "Cadeira E",
    ]


def test_persist_phase_a_specific_search_not_limited(monkeypatch) -> None:
    """Busca específica (`criteria.model` preenchido) preserva o
    comportamento da TASK-075 -- o corte novo da TASK-082 nunca se
    aplica, mesmo com muitos candidatos sobrevivendo ao filtro de modelo."""
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
        search_query="Placa de Video RTX 5070 Ti",
        model="RTX 5070 Ti",
        target_amount=None,
        target_currency=None,
    )
    session = _mock_async_session()
    # previous+latest (TASK-093) por sobrevivente (5, sem corte)
    session.scalar.side_effect = [run, criteria] + [None] * 10
    product = SimpleNamespace(display_name="RTX 5070 Ti")
    session.get.side_effect = [mission] + [None, product] * 5
    offer_stub = SimpleNamespace(id=uuid4(), product_id=uuid4())
    monkeypatch.setattr(
        "app.collection.orchestration._resolve_offer",
        AsyncMock(return_value=offer_stub),
    )
    claim = ClaimedCollection(
        run_id, mission_id, store_id, "kabum", "Placa de Video RTX 5070 Ti", NOW
    )
    result = CollectionResult(
        "kabum",
        NOW,
        NOW + timedelta(seconds=2),
        tuple(
            _raw(
                source="kabum",
                external_id=str(index),
                title="Placa de Video RTX 5070 Ti",
                raw_price=f"R$ {900 + index},00",
            )
            for index in range(5)
        ),
    )
    normalized = PriceNormalizer().normalize_result(result)

    outcome = asyncio.run(
        _persist_phase_a(_session_factory(session), claim, normalized)
    )

    assert len(outcome.offers) == 5


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
        observation_created=True,
        alert_comparison=PriceObservationComparison.FIRST_OBSERVATION,
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
    current_criteria = SimpleNamespace(
        request_kind="generic_category",
        variant_selection_mode=VariantSelectionMode.NOT_REQUIRED,
    )
    session.scalar.side_effect = [mission, run, current_criteria]
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
        observation_created=True,
        alert_comparison=PriceObservationComparison.FIRST_OBSERVATION,
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


def test_persist_phase_c_reused_observation_skips_evaluator_and_run_succeeds(
    monkeypatch,
) -> None:
    """Correção arquitetural (bugfix pós-TASK-093): `UNCHANGED_REUSED` é a
    fonte de verdade vinda da Fase A -- Fase C nunca chama o evaluator para
    reconfirmar o mesmo estado comercial já avaliado antes por esta missão.
    Não é uma segunda comparação, então não pode gerar `PriceAlertEvaluationError`
    (guard de `app/alerts/evaluator.py` continua intocado) nem alerta nenhum,
    e o CollectionRun segue SUCCEEDED normalmente."""
    mission_id, run_id, store_id, offer_id, product_id, observation_id = (
        uuid4(),
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
    current_criteria = SimpleNamespace(
        request_kind="generic_category",
        variant_selection_mode=VariantSelectionMode.NOT_REQUIRED,
    )
    session.scalar.side_effect = [mission, run, current_criteria]
    finish = AsyncMock()
    monkeypatch.setattr("app.collection.orchestration.finish_collection_run", finish)
    monkeypatch.setattr(
        "app.collection.orchestration._reset_source_backoff", AsyncMock()
    )
    monkeypatch.setattr(
        "app.collection.orchestration._evaluate_mission_prelist", AsyncMock()
    )
    monkeypatch.setattr("app.collection.orchestration.publish_event_async", AsyncMock())
    evaluator = MagicMock(side_effect=AssertionError("evaluator não deveria ser chamado"))
    monkeypatch.setattr("app.collection.orchestration.evaluate_price_alerts", evaluator)
    pending = _PendingOffer(
        offer_id=offer_id,
        product_id=product_id,
        # mesmo id em observation_id e previous_observation_id: exatamente o
        # cenário que antes derrubava o CollectionRun (guard "observations
        # must be distinct" do evaluator), agora impedido na fonte.
        observation_id=observation_id,
        amount=Decimal("1900"),
        currency="BRL",
        availability=Availability.AVAILABLE,
        observed_at=NOW,
        raw_title="Título bruto",
        needs_relevance=False,
        needs_display_name=False,
        forced_relevance=OfferRelevance.MATCH,
        observation_created=False,
        alert_comparison=PriceObservationComparison.UNCHANGED_REUSED,
        previous_observation_id=observation_id,
        previous_amount=Decimal("1900"),
        previous_currency="BRL",
        previous_availability=Availability.AVAILABLE,
        previous_observed_at=NOW - timedelta(hours=1),
    )
    outcome = _PhaseAOutcome(
        run_id=run_id,
        mission_id=mission_id,
        store_id=store_id,
        mission_search_query="GPU",
        target_amount=Decimal("2000"),
        target_currency="BRL",
        completed_at=NOW,
        offers=(pending,),
    )

    result = asyncio.run(_persist_phase_c(_session_factory(session), outcome, ()))

    assert result is True
    evaluator.assert_not_called()
    finish.assert_awaited_once_with(
        session, run_id, CollectionRunStatus.SUCCEEDED, finished_at=NOW
    )


def test_persist_phase_c_isolates_alert_evaluator_error_and_run_still_succeeds(
    monkeypatch, caplog
) -> None:
    """Item 3 da correção arquitetural: avaliação de alerta é uma etapa
    derivada da coleta, não parte atômica dela. Um erro real e inesperado
    do evaluator (dado inconsistente, bug -- não o caso estrutural de
    `UNCHANGED_REUSED`, coberto à parte) não pode falsificar o resultado de
    uma coleta já persistida corretamente: fica isolado, logado, e o
    CollectionRun ainda finaliza SUCCEEDED."""
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
    current_criteria = SimpleNamespace(
        request_kind="generic_category",
        variant_selection_mode=VariantSelectionMode.NOT_REQUIRED,
    )
    session.scalar.side_effect = [mission, run, current_criteria]
    finish = AsyncMock()
    monkeypatch.setattr("app.collection.orchestration.finish_collection_run", finish)
    monkeypatch.setattr(
        "app.collection.orchestration._reset_source_backoff", AsyncMock()
    )
    monkeypatch.setattr(
        "app.collection.orchestration._evaluate_mission_prelist", AsyncMock()
    )
    monkeypatch.setattr("app.collection.orchestration.publish_event_async", AsyncMock())
    monkeypatch.setattr(
        "app.collection.orchestration.evaluate_price_alerts",
        MagicMock(side_effect=RuntimeError("dado inconsistente inesperado")),
    )
    pending = _PendingOffer(
        offer_id=offer_id,
        product_id=product_id,
        observation_id=uuid4(),
        amount=Decimal("1500"),
        currency="BRL",
        availability=Availability.AVAILABLE,
        observed_at=NOW,
        raw_title="Título bruto",
        needs_relevance=False,
        needs_display_name=False,
        forced_relevance=OfferRelevance.MATCH,
        observation_created=True,
        alert_comparison=PriceObservationComparison.CHANGED,
        previous_observation_id=uuid4(),
        previous_amount=Decimal("1900"),
        previous_currency="BRL",
        previous_availability=Availability.AVAILABLE,
        previous_observed_at=NOW - timedelta(hours=1),
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

    with caplog.at_level("WARNING", logger="app.collection.orchestration"):
        result = asyncio.run(_persist_phase_c(_session_factory(session), outcome, ()))

    assert result is True
    finish.assert_awaited_once_with(
        session, run_id, CollectionRunStatus.SUCCEEDED, finished_at=NOW
    )
    assert "price_alert_evaluation_failed" in caplog.text


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
    generic = SimpleNamespace(
        request_kind="generic_category",
        variant_selection_mode=VariantSelectionMode.NOT_REQUIRED,
    )
    session.scalar.side_effect = [pending, generic]
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
    session.scalar.side_effect = [sent, generic]
    asyncio.run(_evaluate_mission_prelist(session, sent.id, NOW))
    ready.assert_not_awaited()
    errata.assert_awaited_once_with(session, sent, NOW)


def _prelist_candidate(
    amount: str = "100.00",
    *,
    condition: OfferCondition = OfferCondition.NEW,
    store=None,
) -> _PrelistCandidate:
    store = store or SimpleNamespace(id=uuid4(), code="amazon")
    offer = SimpleNamespace(id=uuid4(), store_id=store.id)
    observation = SimpleNamespace(
        id=uuid4(),
        offer_id=offer.id,
        amount=Decimal(amount),
        total_amount=Decimal(amount),
        currency="BRL",
        condition=condition,
        seller_kind=MarketplacePartyKind.PLATFORM,
        availability=Availability.AVAILABLE,
    )
    return _PrelistCandidate(OfferRelevance.MATCH, observation, offer, store)


def test_maybe_publish_prelist_ready_publishes_v2_collection(
    monkeypatch,
) -> None:
    mission = SimpleNamespace(
        id=uuid4(), prelist_sent=False, prelist_lowest_amount=None
    )
    cheap = _prelist_candidate("100.00")
    mid = _prelist_candidate("150.00")
    monkeypatch.setattr(
        "app.collection.orchestration._mission_prelist_round_complete",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._mission_relevance_pending",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._current_prelist_candidates",
        AsyncMock(return_value=(cheap, mid)),
    )
    publish = AsyncMock()
    monkeypatch.setattr("app.collection.orchestration.publish_event_async", publish)

    asyncio.run(_maybe_publish_prelist_ready(_mock_async_session(), mission, NOW))

    assert mission.prelist_sent is True
    assert mission.prelist_lowest_amount == Decimal("100.00")
    payload = publish.call_args.kwargs["payload"]
    assert [item.offer_id for item in payload.offers] == [cheap.offer.id, mid.offer.id]
    assert publish.call_args.kwargs["event_type"].value == "mission.prelist_ready.v2"


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


def test_maybe_publish_prelist_ready_defers_when_relevance_pending(
    monkeypatch,
) -> None:
    """Causa real do bug de produção -- classificação de
    relevância falhou (IA indisponível) para uma oferta atual, então a
    rodada está "completa" por lojas mas ainda tem classificação
    pendente. `prelist_sent` NUNCA pode virar `True` nesse caso -- a
    pré-lista precisa continuar elegível para a próxima coleta, nunca
    "queimada" com uma lista vazia."""
    mission = SimpleNamespace(
        id=uuid4(), prelist_sent=False, prelist_lowest_amount=None
    )
    monkeypatch.setattr(
        "app.collection.orchestration._mission_prelist_round_complete",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._mission_relevance_pending",
        AsyncMock(return_value=True),
    )
    candidates = AsyncMock()
    monkeypatch.setattr(
        "app.collection.orchestration._current_prelist_candidates",
        candidates,
    )
    publish = AsyncMock()
    monkeypatch.setattr("app.collection.orchestration.publish_event_async", publish)

    asyncio.run(_maybe_publish_prelist_ready(_mock_async_session(), mission, NOW))

    assert mission.prelist_sent is False
    candidates.assert_not_awaited()  # nem chega a montar a lista de MATCH
    publish.assert_not_awaited()


def test_maybe_publish_prelist_ready_sends_after_pending_relevance_resolves(
    monkeypatch,
) -> None:
    """Continuação do cenário acima: na coleta seguinte, a classificação
    que tinha falhado agora resolve como MATCH (fallback pro Groq
    funcionando, ou nova tentativa bem-sucedida do Gemini) -- a mesma
    missão, agora sem nada pendente, publica a pré-lista normal (não uma
    errata) na primeira vez que isso acontece."""
    mission = SimpleNamespace(
        id=uuid4(), prelist_sent=False, prelist_lowest_amount=None
    )
    match = _prelist_candidate("4255.05")
    monkeypatch.setattr(
        "app.collection.orchestration._mission_prelist_round_complete",
        AsyncMock(return_value=True),
    )
    publish = AsyncMock()
    monkeypatch.setattr("app.collection.orchestration.publish_event_async", publish)

    # Primeira coleta: classificação ainda pendente -- nada é enviado.
    monkeypatch.setattr(
        "app.collection.orchestration._mission_relevance_pending",
        AsyncMock(return_value=True),
    )
    asyncio.run(_maybe_publish_prelist_ready(_mock_async_session(), mission, NOW))
    assert mission.prelist_sent is False
    publish.assert_not_awaited()

    # Coleta seguinte: classificação resolvida como MATCH -- pré-lista
    # normal (mission.prelist_ready.v2), não errata.
    monkeypatch.setattr(
        "app.collection.orchestration._mission_relevance_pending",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._current_prelist_candidates",
        AsyncMock(return_value=(match,)),
    )
    asyncio.run(_maybe_publish_prelist_ready(_mock_async_session(), mission, NOW))

    assert mission.prelist_sent is True
    assert mission.prelist_lowest_amount == Decimal("4255.05")
    publish.assert_awaited_once()
    assert publish.call_args.kwargs["event_type"].value == "mission.prelist_ready.v2"


def _fake_execute_result(rows: list[tuple]) -> MagicMock:
    result = MagicMock()
    result.all.return_value = rows
    return result


def test_mission_relevance_pending_false_when_every_current_offer_is_resolved() -> None:
    """Unidade direta de `_mission_relevance_pending` após o rebase sobre a
    TASK-093: "oferta atual" de cada loja é a de maior `Offer.last_seen_at`
    -- qualquer linha já persistida em `mission_offer_relevance` (`MATCH`,
    `POSSIBLE_MATCH` ou `NO_MATCH`) resolve essa oferta, então uma
    classificação resolvida como não-match não trava a pré-lista para
    sempre; só a ausência completa da linha conta como pendente."""
    store_id = uuid4()
    offer_id = uuid4()
    session = _mock_async_session()
    session.execute.return_value = _fake_execute_result(
        [(offer_id, store_id, NOW)]
    )
    session.scalars.return_value = [offer_id]  # já tem MissionOfferRelevance

    pending = asyncio.run(_mission_relevance_pending(session, uuid4()))

    assert pending is False


def test_mission_relevance_pending_true_when_current_offer_lacks_relevance() -> None:
    store_id = uuid4()
    offer_id = uuid4()
    session = _mock_async_session()
    session.execute.return_value = _fake_execute_result(
        [(offer_id, store_id, NOW)]
    )
    session.scalars.return_value = []  # nenhuma MissionOfferRelevance ainda

    pending = asyncio.run(_mission_relevance_pending(session, uuid4()))

    assert pending is True


def test_mission_relevance_pending_ignores_offer_superseded_by_newer_same_store() -> (
    None
):
    """Prova direta do motivo do rebase: uma oferta antiga da mesma loja,
    nunca classificada, mas já substituída por uma mais nova (maior
    `Offer.last_seen_at`, TASK-093) nunca deve travar a pré-lista -- só a
    oferta mais recente da loja é considerada "atual". Sem isso, uma
    oferta reaproveitada (preço idêntico, `PriceObservation` não
    duplicada) que nunca mais aparece ficaria presa para sempre."""
    store_id = uuid4()
    old_offer_id = uuid4()
    new_offer_id = uuid4()
    session = _mock_async_session()
    session.execute.return_value = _fake_execute_result(
        [
            (old_offer_id, store_id, NOW - timedelta(hours=1)),
            (new_offer_id, store_id, NOW),
        ]
    )
    session.scalars.return_value = [new_offer_id]  # só a nova foi classificada

    pending = asyncio.run(_mission_relevance_pending(session, uuid4()))

    assert pending is False


def test_maybe_publish_prelist_errata_uses_commercial_ranking(monkeypatch) -> None:
    mission = SimpleNamespace(
        id=uuid4(),
        prelist_errata_sent=False,
        prelist_lowest_amount=Decimal("100.00"),
        prelist_lowest_currency="BRL",
    )
    store = SimpleNamespace(id=uuid4(), code="amazon")
    improved = _prelist_candidate("180.00", store=store)
    previous = _prelist_candidate(
        "100.00", condition=OfferCondition.USED, store=store
    )
    previous_event = SimpleNamespace(id=uuid4())
    session = _mock_async_session()
    session.scalar.return_value = previous_event
    monkeypatch.setattr(
        "app.collection.orchestration._current_prelist_candidates",
        AsyncMock(return_value=(improved,)),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._previous_prelist_best_by_store",
        AsyncMock(
            return_value={
                improved.store.id: _prelist_commercial_key(previous),
            }
        ),
    )
    publish = AsyncMock()
    monkeypatch.setattr("app.collection.orchestration.publish_event_async", publish)

    asyncio.run(_maybe_publish_prelist_errata(session, mission, NOW))

    assert mission.prelist_errata_sent is True
    payload = publish.call_args.kwargs["payload"]
    assert payload.offers[0].offer_id == improved.offer.id
    assert publish.call_args.kwargs["event_type"].value == "mission.prelist_errata.v2"


def test_prelist_errata_does_not_replace_new_with_cheaper_used(monkeypatch) -> None:
    mission = SimpleNamespace(
        id=uuid4(),
        prelist_errata_sent=False,
        prelist_lowest_amount=Decimal("200.00"),
        prelist_lowest_currency="BRL",
    )
    store = SimpleNamespace(id=uuid4(), code="amazon")
    previous = _prelist_candidate("200.00", store=store)
    cheaper_used = _prelist_candidate(
        "100.00", condition=OfferCondition.USED, store=store
    )
    session = _mock_async_session()
    session.scalar.return_value = SimpleNamespace(id=uuid4())
    monkeypatch.setattr(
        "app.collection.orchestration._current_prelist_candidates",
        AsyncMock(return_value=(cheaper_used,)),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._previous_prelist_best_by_store",
        AsyncMock(
            return_value={store.id: _prelist_commercial_key(previous)}
        ),
    )
    publish = AsyncMock()
    monkeypatch.setattr("app.collection.orchestration.publish_event_async", publish)

    asyncio.run(_maybe_publish_prelist_errata(session, mission, NOW))

    assert mission.prelist_errata_sent is False
    publish.assert_not_awaited()


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
        source_code = "magalu"

        async def collect(self, request):
            raise ProviderBlockedError(request.source_code, 403)

    session_factory = _session_factory(_mock_async_session())
    record = AsyncMock(return_value=True)
    monkeypatch.setattr("app.collection.orchestration._record_failure", record)
    orchestrator = CollectionOrchestrator(
        session_factory, CollectionAdapter((Provider(),)), ai_manager=_StubAIManager()
    )
    claim = ClaimedCollection(uuid4(), uuid4(), uuid4(), "magalu", "GPU", NOW)

    assert asyncio.run(orchestrator._process(claim)) is False
    assert record.call_args.args[2] == "provider_blocked"


@pytest.mark.parametrize(
    ("error", "expected_stage", "expected_status"),
    [
        (ProviderNavigationError("pichau", 504), "navigation", 504),
        (ProviderBlockedError("pichau", 429), "extraction", 429),
        (ProviderCircuitOpenError("pichau"), "provider_availability", None),
        (CollectionNormalizationError("currency is missing"), "normalization", None),
    ],
)
def test_provider_failure_log_preserves_sanitized_diagnostics(
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    expected_stage: str,
    expected_status: int | None,
) -> None:
    class FailingProvider:
        source_code = "pichau"

        async def collect(self, request):
            raise error

    record_failure = AsyncMock(return_value=True)
    monkeypatch.setattr("app.collection.orchestration._record_failure", record_failure)
    orchestrator = CollectionOrchestrator(
        _session_factory(_mock_async_session()),
        CollectionAdapter((FailingProvider(),)),
        ai_manager=_StubAIManager(),
    )
    claim = ClaimedCollection(uuid4(), uuid4(), uuid4(), "pichau", "GPU", NOW)

    with caplog.at_level("WARNING", logger="app.collection.orchestration"):
        assert asyncio.run(orchestrator._process(claim)) is False

    log_record = next(
        item for item in caplog.records if item.message == "collection_source_failed"
    )
    assert log_record.source_code == "pichau"
    assert log_record.failure_code
    assert log_record.error_class == type(error).__name__
    assert log_record.error_detail == str(error)
    assert log_record.failure_stage == expected_stage
    assert type(error).__name__ in log_record.failure_traceback
    if expected_status is None:
        assert not hasattr(log_record, "provider_status")
    else:
        assert log_record.provider_status == expected_status


def test_unexpected_provider_failure_log_omits_raw_error_text(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    secret_canary = "https://example.invalid/?token=must-not-leak"

    class FailingProvider:
        source_code = "pichau"

        async def collect(self, request):
            raise RuntimeError(secret_canary)

    monkeypatch.setattr(
        "app.collection.orchestration._record_failure", AsyncMock(return_value=True)
    )
    orchestrator = CollectionOrchestrator(
        _session_factory(_mock_async_session()),
        CollectionAdapter((FailingProvider(),)),
        ai_manager=_StubAIManager(),
    )
    claim = ClaimedCollection(uuid4(), uuid4(), uuid4(), "pichau", "GPU", NOW)

    with caplog.at_level("WARNING", logger="app.collection.orchestration"):
        assert asyncio.run(orchestrator._process(claim)) is False

    log_record = next(
        item for item in caplog.records if item.message == "collection_source_failed"
    )
    assert log_record.error_class == "RuntimeError"
    assert log_record.failure_stage == "collection"
    assert not hasattr(log_record, "error_detail")
    assert secret_canary not in log_record.failure_traceback


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


# ---------------------------------------------------------------------------
# Fase B (TASK-083 SUBETAPA 4): resolução de identidade antes do fan-out
# ---------------------------------------------------------------------------


class _FakeIdentityResolver:
    """Fake de `ProductIdentityResolver` -- resultado fixo por `model`,
    ou exceção fixa."""

    def __init__(
        self,
        results: dict[str, ResolvedProductIdentity] | None = None,
        *,
        error: BaseException | None = None,
    ) -> None:
        self._results = results or {}
        self._error = error
        self.calls: list[str] = []

    async def resolve(self, model: str) -> ResolvedProductIdentity | None:
        self.calls.append(model)
        if self._error is not None:
            raise self._error
        return self._results.get(model)


def _patch_phase_a(monkeypatch, claims: tuple[ClaimedCollection, ...]) -> None:
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


def _mission_claims(
    mission_id, *, search_query: str, model: str | None, sources=("kabum", "amazon")
) -> tuple[ClaimedCollection, ...]:
    return tuple(
        ClaimedCollection(
            uuid4(), mission_id, uuid4(), source, search_query, NOW, model
        )
        for source in sources
    )


async def _run_batch_recording_processed(
    orchestrator: CollectionOrchestrator, monkeypatch
) -> list[ClaimedCollection]:
    processed: list[ClaimedCollection] = []

    async def process(claim: ClaimedCollection) -> bool:
        processed.append(claim)
        return True

    monkeypatch.setattr(orchestrator, "_process", process)
    await orchestrator.run_batch(now=NOW)
    return processed


# --- A: resolve uma vez, enriquece os 4 claims da missão ---


def test_scenario_a_resolves_once_and_enriches_all_claims(monkeypatch) -> None:
    mission_id = uuid4()
    claims = _mission_claims(
        mission_id,
        search_query="9800X3D",
        model="9800X3D",
        sources=("kabum", "amazon", "pichau", "terabyte"),
    )
    _patch_phase_a(monkeypatch, claims)
    resolver = _FakeIdentityResolver(
        {
            "9800X3D": ResolvedProductIdentity(
                model="9800X3D", search_query="AMD Ryzen 7 9800X3D", source="kabum"
            )
        }
    )
    orchestrator = CollectionOrchestrator(
        _session_factory(_mock_async_session()),
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
        identity_resolver=resolver,
    )

    processed = asyncio.run(_run_batch_recording_processed(orchestrator, monkeypatch))

    assert resolver.calls == ["9800X3D"]
    assert len(processed) == 4
    assert all(c.search_query == "AMD Ryzen 7 9800X3D" for c in processed)
    assert all(c.model == "9800X3D" for c in processed)


# --- B: resolver devolve None -> coleta continua com a query original ---


def test_scenario_b_resolver_returns_none_keeps_original_query(monkeypatch) -> None:
    mission_id = uuid4()
    claims = _mission_claims(mission_id, search_query="9800X3D", model="9800X3D")
    _patch_phase_a(monkeypatch, claims)
    resolver = _FakeIdentityResolver()  # nenhum resultado configurado -> None
    orchestrator = CollectionOrchestrator(
        _session_factory(_mock_async_session()),
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
        identity_resolver=resolver,
    )

    processed = asyncio.run(_run_batch_recording_processed(orchestrator, monkeypatch))

    assert resolver.calls == ["9800X3D"]
    assert all(c.search_query == "9800X3D" for c in processed)


# --- C: resolver falha operacionalmente -> batch não cai, query original ---


def test_scenario_c_resolver_operational_failure_does_not_break_batch(
    monkeypatch,
) -> None:
    mission_id = uuid4()
    claims = _mission_claims(mission_id, search_query="9800X3D", model="9800X3D")
    _patch_phase_a(monkeypatch, claims)
    resolver = _FakeIdentityResolver(error=RuntimeError("falha operacional"))
    orchestrator = CollectionOrchestrator(
        _session_factory(_mock_async_session()),
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
        identity_resolver=resolver,
    )

    processed = asyncio.run(_run_batch_recording_processed(orchestrator, monkeypatch))

    assert len(processed) == 2  # batch completou -- não caiu
    assert all(c.search_query == "9800X3D" for c in processed)


# --- D: model=None -> resolver nunca chamado ---


def test_scenario_d_generic_search_never_calls_resolver(monkeypatch) -> None:
    mission_id = uuid4()
    claims = _mission_claims(
        mission_id, search_query="cadeira gamer", model=None, sources=("kabum",)
    )
    _patch_phase_a(monkeypatch, claims)
    resolver = _FakeIdentityResolver()
    orchestrator = CollectionOrchestrator(
        _session_factory(_mock_async_session()),
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
        identity_resolver=resolver,
    )

    asyncio.run(_run_batch_recording_processed(orchestrator, monkeypatch))

    assert resolver.calls == []


# --- E: search_query já canonicalizada -> resolver nunca chamado ---


def test_scenario_e_already_canonical_query_never_calls_resolver(monkeypatch) -> None:
    mission_id = uuid4()
    claims = _mission_claims(
        mission_id,
        search_query="AMD Ryzen 7 9800X3D",
        model="9800X3D",
        sources=("kabum",),
    )
    _patch_phase_a(monkeypatch, claims)
    resolver = _FakeIdentityResolver()
    orchestrator = CollectionOrchestrator(
        _session_factory(_mock_async_session()),
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
        identity_resolver=resolver,
    )

    asyncio.run(_run_batch_recording_processed(orchestrator, monkeypatch))

    assert resolver.calls == []


# --- F: separador tolerado -> reconhecido como identidade crua ---


def test_scenario_f_separator_variant_is_recognized_as_raw_identity(
    monkeypatch,
) -> None:
    mission_id = uuid4()
    claims = _mission_claims(
        mission_id, search_query="9800-X3D", model="9800X3D", sources=("kabum",)
    )
    _patch_phase_a(monkeypatch, claims)
    resolver = _FakeIdentityResolver()
    orchestrator = CollectionOrchestrator(
        _session_factory(_mock_async_session()),
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
        identity_resolver=resolver,
    )

    asyncio.run(_run_batch_recording_processed(orchestrator, monkeypatch))

    assert resolver.calls == ["9800X3D"]


# --- G: duas missões específicas -> resolve 1x cada, sem misturar ---


def test_scenario_g_two_missions_resolved_independently(monkeypatch) -> None:
    mission_a, mission_b = uuid4(), uuid4()
    claims = _mission_claims(
        mission_a, search_query="9800X3D", model="9800X3D", sources=("kabum",)
    ) + _mission_claims(
        mission_b, search_query="RTX5070TI", model="RTX5070TI", sources=("kabum",)
    )
    _patch_phase_a(monkeypatch, claims)
    resolver = _FakeIdentityResolver(
        {
            "9800X3D": ResolvedProductIdentity(
                model="9800X3D", search_query="AMD Ryzen 7 9800X3D", source="kabum"
            ),
            "RTX5070TI": ResolvedProductIdentity(
                model="RTX5070TI",
                search_query="NVIDIA RTX 5070 Ti",
                source="kabum",
            ),
        }
    )
    orchestrator = CollectionOrchestrator(
        _session_factory(_mock_async_session()),
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
        identity_resolver=resolver,
    )

    processed = asyncio.run(_run_batch_recording_processed(orchestrator, monkeypatch))

    assert sorted(resolver.calls) == ["9800X3D", "RTX5070TI"]
    by_mission = {c.mission_id: c.search_query for c in processed}
    assert by_mission[mission_a] == "AMD Ryzen 7 9800X3D"
    assert by_mission[mission_b] == "NVIDIA RTX 5070 Ti"


# --- H: uma missão específica + uma genérica -> só a específica resolve ---


def test_scenario_h_only_specific_mission_calls_resolver(monkeypatch) -> None:
    specific, generic = uuid4(), uuid4()
    claims = _mission_claims(
        specific, search_query="9800X3D", model="9800X3D", sources=("kabum",)
    ) + _mission_claims(
        generic, search_query="cadeira gamer", model=None, sources=("kabum",)
    )
    _patch_phase_a(monkeypatch, claims)
    resolver = _FakeIdentityResolver()
    orchestrator = CollectionOrchestrator(
        _session_factory(_mock_async_session()),
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
        identity_resolver=resolver,
    )

    asyncio.run(_run_batch_recording_processed(orchestrator, monkeypatch))

    assert resolver.calls == ["9800X3D"]


# --- I: identity_resolver=None -> comportamento anterior preservado ---


def test_scenario_i_no_resolver_configured_preserves_previous_behavior(
    monkeypatch,
) -> None:
    mission_id = uuid4()
    claims = _mission_claims(
        mission_id, search_query="9800X3D", model="9800X3D", sources=("kabum",)
    )
    _patch_phase_a(monkeypatch, claims)
    orchestrator = CollectionOrchestrator(
        _session_factory(_mock_async_session()),
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
        # identity_resolver não informado -- default None
    )

    processed = asyncio.run(_run_batch_recording_processed(orchestrator, monkeypatch))

    assert processed[0].search_query == "9800X3D"


# --- J: resolve() só acontece depois que a transação da Fase A fechou ---


class _TransactionTracker:
    def __init__(self) -> None:
        self.in_transaction = False


class _TrackingBeginCM:
    def __init__(self, tracker: _TransactionTracker) -> None:
        self._tracker = tracker

    async def __aenter__(self) -> _TrackingBeginCM:
        self._tracker.in_transaction = True
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        self._tracker.in_transaction = False
        return False


def test_scenario_j_resolve_happens_after_phase_a_transaction_closes(
    monkeypatch,
) -> None:
    tracker = _TransactionTracker()
    session = _mock_async_session()
    session.begin = MagicMock(side_effect=lambda: _TrackingBeginCM(tracker))

    mission_id = uuid4()
    claims = _mission_claims(
        mission_id, search_query="9800X3D", model="9800X3D", sources=("kabum",)
    )
    _patch_phase_a(monkeypatch, claims)

    observed_in_transaction: list[bool] = []

    class _ObservingResolver:
        async def resolve(self, model: str) -> ResolvedProductIdentity | None:
            observed_in_transaction.append(tracker.in_transaction)
            return None

    orchestrator = CollectionOrchestrator(
        _session_factory(session),
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
        identity_resolver=_ObservingResolver(),
    )

    asyncio.run(_run_batch_recording_processed(orchestrator, monkeypatch))

    assert observed_in_transaction == [False]


# --- K: fan-out continua processando todos os claims após a resolução ---


def test_scenario_k_fan_out_still_processes_every_claim_after_resolution(
    monkeypatch,
) -> None:
    mission_id = uuid4()
    claims = _mission_claims(
        mission_id,
        search_query="9800X3D",
        model="9800X3D",
        sources=("kabum", "amazon", "pichau"),
    )
    _patch_phase_a(monkeypatch, claims)
    resolver = _FakeIdentityResolver(
        {
            "9800X3D": ResolvedProductIdentity(
                model="9800X3D", search_query="AMD Ryzen 7 9800X3D", source="kabum"
            )
        }
    )
    orchestrator = CollectionOrchestrator(
        _session_factory(_mock_async_session()),
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
        identity_resolver=resolver,
    )

    processed = asyncio.run(_run_batch_recording_processed(orchestrator, monkeypatch))

    assert len(processed) == 3
    assert {c.source_code for c in processed} == {"kabum", "amazon", "pichau"}
    assert all(c.search_query == "AMD Ryzen 7 9800X3D" for c in processed)


# --- L: _resolve_identities em si continua sem `session` (Fase B "pura") ---


def test_scenario_l_resolve_identities_method_has_no_session_param(monkeypatch) -> None:
    """`_resolve_identities` continua sem receber `session` -- a Fase B
    (Playwright) em si continua estruturalmente incapaz de abrir
    transação. A persistência da identidade confirmada (TASK-083,
    correção de regressão) acontece à parte, depois, via
    `_promote_resolved_identities` -- ver a seção "Fase B.1" abaixo."""
    signature = inspect.signature(CollectionOrchestrator._resolve_identities)
    assert "session" not in signature.parameters


# ---------------------------------------------------------------------------
# TASK-083 (correção de regressão): Fase B.1 -- promoção da identidade
# confirmada à missão, para o próximo batch não resolver de novo.
# ---------------------------------------------------------------------------


def _mission_and_criteria_session(mission, criteria) -> MagicMock:
    """Sessão mockada para a transação curta de `_promote_resolved_identities`
    -- `session.scalar` devolve a `Mission` na 1ª chamada e a
    `MissionCriteria` na 2ª, reproduzindo `promote_confirmed_product_identity_async`."""
    session = _mock_async_session()
    session.scalar = AsyncMock(side_effect=[mission, criteria])
    return session


def test_scenario_m_confirmed_identity_is_promoted_to_the_mission(monkeypatch) -> None:
    from app.missions.models import Mission, MissionCriteria, MissionStatus

    mission_id = uuid4()
    claims = _mission_claims(mission_id, search_query="9950X3D", model="9950X3D")
    _patch_phase_a(monkeypatch, claims)
    # TASK-112 (fase 2): `promote_confirmed_product_identity_async` passou
    # a chamar `reconcile_mission_monitoring_item_async` no fim -- essa
    # chamada tem cobertura própria (`tests/integration/
    # test_shared_monitoring.py`, contra PostgreSQL real); aqui a sessão
    # mockada (`_mission_and_criteria_session`) só serve `Mission`/
    # `MissionCriteria` para as duas leituras que este teste É sobre
    # (promoção de search_query/title), então essa chamada extra é
    # neutralizada (mesmo padrão de `tests/test_mission_service_async.py`).
    monkeypatch.setattr(
        "app.missions.service.reconcile_mission_monitoring_item_async",
        AsyncMock(return_value=None),
    )
    resolver = _FakeIdentityResolver(
        {
            "9950X3D": ResolvedProductIdentity(
                model="9950X3D",
                search_query="Processador AMD Ryzen 9 9950X3D",
                source="kabum",
            )
        }
    )
    mission = Mission(
        id=mission_id,
        user_id=uuid4(),
        title="9950X3D",
        status=MissionStatus.ACTIVE,
        state_version=0,
        created_at=NOW,
        updated_at=NOW,
    )
    criteria = MissionCriteria(
        id=uuid4(),
        mission_id=mission_id,
        search_query="9950X3D",
        model="9950X3D",
        created_at=NOW,
        updated_at=NOW,
    )
    promote_session = _mission_and_criteria_session(mission, criteria)
    phase_a_session = _mock_async_session()

    call_count = {"n": 0}

    def _factory():
        call_count["n"] += 1
        return phase_a_session if call_count["n"] == 1 else promote_session

    orchestrator = CollectionOrchestrator(
        _factory,
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
        identity_resolver=resolver,
    )

    asyncio.run(_run_batch_recording_processed(orchestrator, monkeypatch))

    assert criteria.search_query == "Processador AMD Ryzen 9 9950X3D"
    assert mission.title == "Processador AMD Ryzen 9 9950X3D"
    assert criteria.model == "9950X3D"  # nunca alterado
    promote_session.flush.assert_awaited_once_with()


def test_scenario_n_promotion_happens_once_per_mission_even_with_many_claims(
    monkeypatch,
) -> None:
    """Vários claims da mesma mission_id -- resolve 1x, persiste 1x."""
    from app.missions.models import Mission, MissionCriteria, MissionStatus

    mission_id = uuid4()
    claims = _mission_claims(
        mission_id,
        search_query="9950X3D",
        model="9950X3D",
        sources=("kabum", "amazon", "pichau", "terabyte"),
    )
    _patch_phase_a(monkeypatch, claims)
    # TASK-112 (fase 2): ver comentário equivalente em
    # test_scenario_m_confirmed_identity_is_promoted_to_the_mission.
    monkeypatch.setattr(
        "app.missions.service.reconcile_mission_monitoring_item_async",
        AsyncMock(return_value=None),
    )
    resolver = _FakeIdentityResolver(
        {
            "9950X3D": ResolvedProductIdentity(
                model="9950X3D",
                search_query="Processador AMD Ryzen 9 9950X3D",
                source="kabum",
            )
        }
    )
    mission = Mission(
        id=mission_id,
        user_id=uuid4(),
        title="9950X3D",
        status=MissionStatus.ACTIVE,
        state_version=0,
        created_at=NOW,
        updated_at=NOW,
    )
    criteria = MissionCriteria(
        id=uuid4(),
        mission_id=mission_id,
        search_query="9950X3D",
        model="9950X3D",
        created_at=NOW,
        updated_at=NOW,
    )
    promote_calls: list[MagicMock] = []
    phase_a_session = _mock_async_session()

    def _factory():
        if not promote_calls:
            promote_calls.append(_mission_and_criteria_session(mission, criteria))
            return phase_a_session
        return promote_calls[0]

    orchestrator = CollectionOrchestrator(
        _factory,
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
        identity_resolver=resolver,
    )

    asyncio.run(_run_batch_recording_processed(orchestrator, monkeypatch))

    assert resolver.calls == ["9950X3D"]  # resolveu 1x
    promote_calls[0].flush.assert_awaited_once_with()  # persistiu 1x


def test_scenario_o_resolver_fails_both_stores_never_persists_invented_identity(
    monkeypatch,
) -> None:
    """Kabum + Amazon falham (resolver devolve `None`) -- nenhuma
    identidade inventada é persistida; missão continua funcional com o
    fallback seguro."""
    mission_id = uuid4()
    claims = _mission_claims(mission_id, search_query="9950X3D", model="9950X3D")
    _patch_phase_a(monkeypatch, claims)
    resolver = _FakeIdentityResolver()  # nenhum resultado -> None
    promote_session = _mock_async_session()
    phase_a_session = _mock_async_session()
    call_count = {"n": 0}

    def _factory():
        call_count["n"] += 1
        return phase_a_session if call_count["n"] == 1 else promote_session

    orchestrator = CollectionOrchestrator(
        _factory,
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
        identity_resolver=resolver,
    )

    processed = asyncio.run(_run_batch_recording_processed(orchestrator, monkeypatch))

    assert all(c.search_query == "9950X3D" for c in processed)  # segue operacional
    promote_session.scalar.assert_not_awaited()  # nunca tentou persistir nada
    promote_session.flush.assert_not_awaited()


def test_scenario_p_promotion_failure_never_breaks_the_batch(monkeypatch) -> None:
    """Persistência falha (ex.: erro de banco na Fase B.1) -- batch
    continua, coleta deste ciclo já usou a identidade em memória."""
    mission_id = uuid4()
    claims = _mission_claims(mission_id, search_query="9950X3D", model="9950X3D")
    _patch_phase_a(monkeypatch, claims)
    resolver = _FakeIdentityResolver(
        {
            "9950X3D": ResolvedProductIdentity(
                model="9950X3D",
                search_query="Processador AMD Ryzen 9 9950X3D",
                source="kabum",
            )
        }
    )

    async def _broken_promote(*args: object, **kwargs: object) -> bool:
        raise RuntimeError("falha de banco simulada")

    monkeypatch.setattr(
        "app.collection.orchestration.promote_confirmed_product_identity_async",
        _broken_promote,
    )
    orchestrator = CollectionOrchestrator(
        _session_factory(_mock_async_session()),
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
        identity_resolver=resolver,
    )

    processed = asyncio.run(_run_batch_recording_processed(orchestrator, monkeypatch))

    assert len(processed) == 2  # batch completou -- não caiu
    assert all(c.search_query == "Processador AMD Ryzen 9 9950X3D" for c in processed)


def test_scenario_q_no_transaction_open_during_playwright_resolution(
    monkeypatch,
) -> None:
    """Extensão do cenário J: a transação de promoção (Fase B.1) só abre
    depois que a resolução via Playwright (Fase B) já terminou -- nenhuma
    transação fica aberta durante `identity_resolver.resolve`."""
    tracker = _TransactionTracker()
    session = _mock_async_session()
    session.begin = MagicMock(side_effect=lambda: _TrackingBeginCM(tracker))

    mission_id = uuid4()
    claims = _mission_claims(mission_id, search_query="9950X3D", model="9950X3D")
    _patch_phase_a(monkeypatch, claims)

    observed_in_transaction: list[bool] = []

    class _ObservingResolver:
        async def resolve(self, model: str) -> ResolvedProductIdentity | None:
            observed_in_transaction.append(tracker.in_transaction)
            return ResolvedProductIdentity(
                model=model,
                search_query="Processador AMD Ryzen 9 9950X3D",
                source="kabum",
            )

    orchestrator = CollectionOrchestrator(
        _session_factory(session),
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
        identity_resolver=_ObservingResolver(),
    )

    asyncio.run(_run_batch_recording_processed(orchestrator, monkeypatch))

    assert observed_in_transaction == [False]


def test_scenario_r_next_batch_does_not_resolve_promoted_identity(monkeypatch) -> None:
    """Critério obrigatório da correção: o primeiro batch resolve e
    promove; a Fase A do segundo batch já lê a `search_query` confirmada,
    portanto `_needs_identity_resolution` não chama o resolver novamente."""
    mission_id = uuid4()
    persisted = SimpleNamespace(
        search_query="9950X3D",
        model="9950X3D",
    )
    resolver = _FakeIdentityResolver(
        {
            "9950X3D": ResolvedProductIdentity(
                model="9950X3D",
                search_query="Processador AMD Ryzen 9 9950X3D",
                source="kabum",
            )
        }
    )

    monkeypatch.setattr(
        "app.collection.orchestration.ensure_missing_schedules",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.recover_stale_runs",
        AsyncMock(return_value=0),
    )

    async def _dynamic_claims(*args: object, **kwargs: object):
        return _mission_claims(
            mission_id,
            search_query=persisted.search_query,
            model=persisted.model,
            sources=("kabum", "amazon"),
        )

    monkeypatch.setattr(
        "app.collection.orchestration.claim_due_collections",
        _dynamic_claims,
    )
    promotion_calls: list[tuple[object, str]] = []

    async def _promote(
        session: object,
        *,
        mission_id: object,
        confirmed_search_query: str,
        promoted_at: datetime | None = None,
    ) -> bool:
        promotion_calls.append((mission_id, confirmed_search_query))
        persisted.search_query = confirmed_search_query
        return True

    monkeypatch.setattr(
        "app.collection.orchestration.promote_confirmed_product_identity_async",
        _promote,
    )
    orchestrator = CollectionOrchestrator(
        _session_factory(_mock_async_session()),
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
        identity_resolver=resolver,
    )

    async def _run_twice() -> None:
        processed: list[ClaimedCollection] = []

        async def _process(claim: ClaimedCollection) -> bool:
            processed.append(claim)
            return True

        monkeypatch.setattr(orchestrator, "_process", _process)
        await orchestrator.run_batch(now=NOW)
        await orchestrator.run_batch(now=NOW + timedelta(hours=1))

        assert len(processed) == 4
        assert all(
            claim.search_query == "Processador AMD Ryzen 9 9950X3D"
            for claim in processed
        )

    asyncio.run(_run_twice())

    assert resolver.calls == ["9950X3D"]
    assert promotion_calls == [(mission_id, "Processador AMD Ryzen 9 9950X3D")]


# ---------------------------------------------------------------------------
# TASK-093 (redução de PriceObservation redundante): _same_commercial_state /
# _installment_snapshot -- comparação pura, sem sessão.
# ---------------------------------------------------------------------------


def _observation(**overrides):
    base = dict(
        amount=Decimal("100.00"),
        currency="BRL",
        shipping_amount=None,
        total_amount=Decimal("100.00"),
        fulfillment="loja",
        seller_kind=None,
        fulfillment_kind=None,
        condition=OfferCondition.NEW,
        availability=Availability.AVAILABLE,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_same_commercial_state_true_when_all_relevant_fields_match() -> None:
    latest = _observation()
    item = _observation()

    assert _same_commercial_state(latest, item) is True


def test_same_commercial_state_false_when_price_differs() -> None:
    latest = _observation()
    item = _observation(amount=Decimal("99.00"), total_amount=Decimal("99.00"))

    assert _same_commercial_state(latest, item) is False


def test_same_commercial_state_false_when_availability_differs() -> None:
    latest = _observation()
    item = _observation(availability=Availability.UNAVAILABLE)

    assert _same_commercial_state(latest, item) is False


def test_same_commercial_state_false_when_condition_differs() -> None:
    latest = _observation(condition=OfferCondition.NEW)
    item = _observation(condition=OfferCondition.USED)

    assert _same_commercial_state(latest, item) is False


def _installment(**overrides):
    base = dict(
        installment_count=12,
        installment_amount=Decimal("10.00"),
        installment_total_amount=Decimal("120.00"),
        discount_percent=None,
        interest_kind="interest_free",
        is_highlighted=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_installment_snapshot_equal_ignores_order() -> None:
    left = [_installment(installment_count=12), _installment(installment_count=6)]
    right = [_installment(installment_count=6), _installment(installment_count=12)]

    assert _installment_snapshot(left) == _installment_snapshot(right)


def test_installment_snapshot_differs_when_a_condition_is_added() -> None:
    """TASK-093: preço à vista igual não basta -- uma nova condição de
    parcelamento (ex.: desconto passou a valer para 6x) é mudança
    comercial relevante mesmo sem o preço à vista mudar."""
    left = [_installment(installment_count=12)]
    right = [_installment(installment_count=12), _installment(installment_count=6)]

    assert _installment_snapshot(left) != _installment_snapshot(right)
