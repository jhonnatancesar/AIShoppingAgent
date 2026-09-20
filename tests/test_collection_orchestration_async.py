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
from app.alerts.evaluator import PriceAlertCandidate
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
from app.collection.models import (
    CollectionRunStatus,
    MissionOfferRelevance,
    PriceObservation,
    StoreThrottleState,
    UserCollectionQueueState,
)
from app.collection.normalization import Availability, PriceNormalizer
from app.collection.orchestration import (
    _OFFER_IDENTITY_INDEXES,
    _PRODUCT_IDENTITY_INDEX,
    _RUNNING_INDEX,
    _SELLER_IDENTITY_INDEX,
    ClaimedCollection,
    CollectionOrchestrator,
    PriceObservationComparison,
    _AIOutcome,
    _apply_rating_snapshot,
    _apply_source_backoff,
    _ClaimedBatch,
    _constraint_name,
    _creation_lock_keys,
    _current_prelist_candidates,
    _deterministic_product_relevance,
    _evaluate_mission_prelist,
    _failure_log_context,
    _filter_deterministic_candidates,
    _find_offer,
    _installment_snapshot,
    _maybe_publish_prelist_errata,
    _maybe_publish_prelist_ready,
    _maybe_set_canonical_image,
    _mission_prelist_round_complete,
    _mission_relevance_pending,
    _PendingOffer,
    _persist_phase_a,
    _persist_phase_c,
    _PhaseAOutcome,
    _prelist_commercial_key,
    _PrelistCandidate,
    _preview_existing_offer_and_product,
    _previous_prelist_best_by_store,
    _product_selected_for_mission,
    _publish_failure,
    _record_failure,
    _refresh_legacy_schedule_aggregate,
    _reset_source_backoff,
    _resolve_global_product,
    _resolve_offer,
    _resolve_seller,
    _run_phase_b,
    _same_commercial_state,
    _sanitize_json,
    _select_due_legacy_sources_for_batch,
    _supersede_old_unattributed_offer,
    _title_looks_like_bundle,
    claim_due_collections,
    ensure_missing_schedules,
    rank_prelist_candidates,
    recover_stale_runs,
)
from app.collection.relevance import OfferRelevance
from app.collection.shared_claim import _SharedClaim
from app.collection.shared_collection import FanOutSweepSummary, SharedCollectionResult
from app.coupons.models import Coupon
from app.coupons.pricing import AppliedCoupon
from app.events import AggregateType, EventType
from app.events.catalog import CollectionFailedPayload, PriceDecreasedPayload
from app.missions.models import (
    MissionProductSelection,
    MissionStatus,
    VariantSelectionMode,
)
from app.offers.models import Offer
from app.products.identity import (
    ProductRequestKind,
    classify_product_request,
    resolve_product_variant,
)
from app.products.models import Product
from app.stores.models import Seller
from app.users.models import UserRole
from sqlalchemy.exc import IntegrityError

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_op_fan_out_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    """TASK-112 fase 3B: `CollectionOrchestrator.run_batch` chama o sweep
    de fan-out ANTES de qualquer outra coisa, sempre -- este arquivo é só
    testes de caixa branca do caminho legado (`AsyncMock`, sem Postgres
    real), então o sweep real (que faz suas próprias queries) nunca teria
    o que responder a uma sessão mockada. `CollectionOrchestrator.
    __init__` resolve `fan_out_sweeper`/`shared_collector` via import
    local de `app.collection.shared_collection` quando não recebe nada
    explicitamente (construção direta, como todo teste deste arquivo já
    faz) -- então patchar a função NA ORIGEM, antes de qualquer
    `CollectionOrchestrator(...)` deste arquivo ser construído, é
    suficiente para todos os 23 pontos de construção sem precisar tocar
    em nenhum deles individualmente."""
    monkeypatch.setattr(
        "app.collection.shared_collection.sweep_shared_collection_fan_out",
        AsyncMock(return_value=FanOutSweepSummary()),
    )


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

    assert (
        _deterministic_product_relevance(criteria, product) is OfferRelevance.NO_MATCH
    )


def test_generic_category_does_not_force_product_relevance() -> None:
    criteria = SimpleNamespace(request_kind="generic_category")
    product = Product(name="Cadeira gamer qualquer")

    assert _deterministic_product_relevance(criteria, product) is None


def test_product_family_rejects_different_family_key() -> None:
    criteria = SimpleNamespace(
        request_kind="product_family",
        requested_family_key="family:iphone",
        requested_variant=None,
    )
    product = Product(name="Galaxy S24", family_key="family:galaxy")

    assert (
        _deterministic_product_relevance(criteria, product) is OfferRelevance.NO_MATCH
    )


def test_product_family_rejects_different_variant() -> None:
    criteria = SimpleNamespace(
        request_kind="product_family",
        requested_family_key="family:iphone",
        requested_variant="pro",
    )
    product = Product(name="iPhone 17", family_key="family:iphone", variant="base")

    assert (
        _deterministic_product_relevance(criteria, product) is OfferRelevance.NO_MATCH
    )


def test_product_family_accepts_matching_family_and_variant() -> None:
    criteria = SimpleNamespace(
        request_kind="product_family",
        requested_family_key="family:iphone",
        requested_variant="pro",
    )
    product = Product(name="iPhone 17 Pro", family_key="family:iphone", variant="pro")

    assert _deterministic_product_relevance(criteria, product) is None


def test_product_family_accepts_when_product_family_key_unknown() -> None:
    """`family_key=None` no `Product` -- ainda não identificado -- nunca
    é rejeitado deterministicamente; segue fail-soft pra IA."""
    criteria = SimpleNamespace(
        request_kind="product_family",
        requested_family_key="family:iphone",
        requested_variant=None,
    )
    product = Product(name="Produto ainda não identificado", family_key=None)

    assert _deterministic_product_relevance(criteria, product) is None


def test_product_selected_for_mission_true_when_not_family_kind() -> None:
    session = _mock_async_session()

    selected = asyncio.run(
        _product_selected_for_mission(
            session,
            mission_id=uuid4(),
            product_id=uuid4(),
            request_kind="generic_category",
            selection_mode=VariantSelectionMode.NOT_REQUIRED,
        )
    )

    assert selected is True
    session.get.assert_not_awaited()


def test_product_selected_for_mission_false_when_product_missing() -> None:
    session = _mock_async_session()
    session.get.return_value = None

    selected = asyncio.run(
        _product_selected_for_mission(
            session,
            mission_id=uuid4(),
            product_id=uuid4(),
            request_kind="product_family",
            selection_mode=VariantSelectionMode.ALL,
        )
    )

    assert selected is False


def test_product_selected_for_mission_false_when_identity_key_unknown() -> None:
    session = _mock_async_session()
    session.get.return_value = Product(name="Ainda não identificado", identity_key=None)

    selected = asyncio.run(
        _product_selected_for_mission(
            session,
            mission_id=uuid4(),
            product_id=uuid4(),
            request_kind="product_family",
            selection_mode=VariantSelectionMode.ALL,
        )
    )

    assert selected is False


def test_product_selected_for_mission_true_when_mode_is_all() -> None:
    session = _mock_async_session()
    session.get.return_value = Product(name="iPhone 17", identity_key="v1:abc")

    selected = asyncio.run(
        _product_selected_for_mission(
            session,
            mission_id=uuid4(),
            product_id=uuid4(),
            request_kind="product_family",
            selection_mode=VariantSelectionMode.ALL,
        )
    )

    assert selected is True


def test_product_selected_for_mission_false_when_mode_not_selected() -> None:
    session = _mock_async_session()
    session.get.return_value = Product(name="iPhone 17", identity_key="v1:abc")

    selected = asyncio.run(
        _product_selected_for_mission(
            session,
            mission_id=uuid4(),
            product_id=uuid4(),
            request_kind="product_family",
            selection_mode=VariantSelectionMode.PENDING,
        )
    )

    assert selected is False


def test_product_selected_for_mission_checks_explicit_selection_row() -> None:
    mission_id, product_id = uuid4(), uuid4()
    session = _mock_async_session()
    product = Product(name="iPhone 17", identity_key="v1:abc")
    selection_row = MissionProductSelection(
        mission_id=mission_id, product_id=product_id
    )
    session.get.side_effect = [product, selection_row]

    selected = asyncio.run(
        _product_selected_for_mission(
            session,
            mission_id=mission_id,
            product_id=product_id,
            request_kind="product_family",
            selection_mode=VariantSelectionMode.SELECTED,
        )
    )

    assert selected is True
    session.get.assert_awaited_with(MissionProductSelection, (mission_id, product_id))


def test_product_selected_for_mission_false_when_no_explicit_selection_row() -> None:
    mission_id, product_id = uuid4(), uuid4()
    session = _mock_async_session()
    product = Product(name="iPhone 17", identity_key="v1:abc")
    session.get.side_effect = [product, None]

    selected = asyncio.run(
        _product_selected_for_mission(
            session,
            mission_id=mission_id,
            product_id=product_id,
            request_kind="product_family",
            selection_mode=VariantSelectionMode.SELECTED,
        )
    )

    assert selected is False


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
    image_url: str | None = None,
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
        image_url=image_url,
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


def test_claim_due_sources_creates_runs_and_advances_each_source(monkeypatch) -> None:
    """TASK-112 fase 3B (correção estrutural): due passa a ser por
    `MissionSource` -- (mission, store) --, nunca por `MissionSchedule`
    (missão inteira) -- achado real da auditoria: a versão anterior
    reagendava a Mission inteira pela decisão mais cedo entre as lojas
    claimadas, fazendo uma store NORMAL ser recoletada antes da própria
    cadência só porque outra store da mesma missão estava em
    HIGH_ACTIVITY. Aqui a SELECT ... FOR UPDATE (`_due_legacy_sources_
    statement`) é mockada diretamente via `session.execute` (não é o
    objeto deste teste, que é claim/avanço de CADA source dado um
    conjunto já selecionado); `resolve_collection_cadence`/`start_
    collection_run` mockados no limite da chamada, mesmo espírito de
    `test_stale_runs_are_terminal_and_publish_failure` acima -- fora do
    escopo deste teste (cobertura real de cadência multi-store, com
    verificação de PROVIDER CALL: `tests/integration/test_legacy_source_
    level_cadence.py`). Fairness/cooldown/throttle de loja também não são
    o objeto deste teste -- neutralizados explicitamente."""
    from app.missions.models import MissionSource

    mission_id = uuid4()
    user_id = uuid4()
    store_ids = (uuid4(), uuid4())
    source_a = MissionSource(mission_id=mission_id, store_id=store_ids[0])
    source_b = MissionSource(mission_id=mission_id, store_id=store_ids[1])
    criteria = SimpleNamespace(search_query="GPU", model=None)
    session = _mock_async_session()

    due_rows = MagicMock()
    due_rows.all.return_value = [
        (source_a, "kabum", user_id),
        (source_b, "pichau", user_id),
    ]
    # 1a chamada: SELECT ... FOR UPDATE de sources due; as 3 seguintes são
    # os UPDATEs de throttle (1 por source) e o avanço de fila do usuário
    # (1, já que as duas claims pertencem ao mesmo usuário) -- nenhum
    # deles lê o retorno, só precisam não estourar `StopIteration`.
    session.execute.side_effect = [due_rows, MagicMock(), MagicMock(), MagicMock()]
    session.scalars.return_value = []  # UserCollectionQueueState -- ninguém em cooldown
    session.get.side_effect = [None, None]  # StoreThrottleState -- sem throttle ativo
    # running-check (1x, cache por missão) + MissionCriteria (1x, cache).
    session.scalar.side_effect = [None, criteria]

    runs = iter((SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4())))
    monkeypatch.setattr(
        "app.collection.orchestration.start_collection_run",
        AsyncMock(side_effect=lambda *_a, **_k: next(runs)),
    )
    decisions = iter(
        (
            SimpleNamespace(min_minutes=45, max_minutes=45, mode="normal"),
            SimpleNamespace(min_minutes=30, max_minutes=30, mode="high_activity"),
        )
    )
    monkeypatch.setattr(
        "app.collection.orchestration.resolve_collection_cadence",
        AsyncMock(side_effect=lambda *_a, **_k: next(decisions)),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.sample_next_run_at",
        lambda started_at, decision: (
            started_at + timedelta(minutes=decision.min_minutes)
        ),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._refresh_legacy_schedule_aggregate",
        AsyncMock(),
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
    # Cada source avança pela SUA PRÓPRIA decisão de cadência, nunca uma
    # decisão compartilhada/mais-cedo-vence entre as duas.
    assert source_a.last_run_at == NOW
    assert source_a.next_run_at == NOW + timedelta(minutes=45)
    assert source_b.last_run_at == NOW
    assert source_b.next_run_at == NOW + timedelta(minutes=30)
    session.flush.assert_awaited_once()


def test_select_due_legacy_sources_for_batch_returns_execute_rows() -> None:
    from app.missions.models import MissionSource

    session = _mock_async_session()
    source = MissionSource(mission_id=uuid4(), store_id=uuid4())
    rows = MagicMock()
    rows.all.return_value = [(source, "kabum", uuid4())]
    session.execute.return_value = rows

    result = asyncio.run(
        _select_due_legacy_sources_for_batch(session, due_at=NOW, limit=10)
    )

    assert result == rows.all.return_value
    session.execute.assert_awaited_once()


def test_refresh_legacy_schedule_aggregate_noop_when_schedule_missing() -> None:
    session = _mock_async_session()
    session.scalar.return_value = None

    asyncio.run(
        _refresh_legacy_schedule_aggregate(session, mission_id=uuid4(), now=NOW)
    )

    session.flush.assert_not_awaited()
    session.execute.assert_not_awaited()


def test_refresh_legacy_schedule_aggregate_noop_when_disabled() -> None:
    from app.missions.models import MissionSchedule

    session = _mock_async_session()
    schedule = MissionSchedule(
        mission_id=uuid4(),
        interval_minutes=30,
        next_run_at=NOW,
        is_enabled=False,
    )
    session.scalar.return_value = schedule

    asyncio.run(
        _refresh_legacy_schedule_aggregate(session, mission_id=uuid4(), now=NOW)
    )

    session.flush.assert_not_awaited()
    session.execute.assert_not_awaited()


def test_refresh_legacy_schedule_aggregate_noop_when_no_sources() -> None:
    from app.missions.models import MissionSchedule

    session = _mock_async_session()
    schedule = MissionSchedule(
        mission_id=uuid4(),
        interval_minutes=30,
        next_run_at=NOW - timedelta(minutes=5),
        last_run_at=NOW - timedelta(hours=1),
        is_enabled=True,
    )
    session.scalar.return_value = schedule
    rows = MagicMock()
    rows.all.return_value = []
    session.execute.return_value = rows

    asyncio.run(
        _refresh_legacy_schedule_aggregate(session, mission_id=uuid4(), now=NOW)
    )

    session.flush.assert_awaited_once()
    assert schedule.next_run_at == NOW - timedelta(minutes=5)
    assert schedule.last_run_at == NOW - timedelta(hours=1)


def test_refresh_legacy_schedule_aggregate_recomputes_min_and_max() -> None:
    from app.missions.models import MissionSchedule

    session = _mock_async_session()
    schedule = MissionSchedule(
        mission_id=uuid4(),
        interval_minutes=30,
        next_run_at=NOW,
        is_enabled=True,
    )
    session.scalar.return_value = schedule
    rows = MagicMock()
    rows.all.return_value = [
        (NOW + timedelta(minutes=30), NOW - timedelta(minutes=10)),
        (None, None),  # `next_run_at` NULL tratado como já due (`now`)
        (NOW + timedelta(minutes=10), NOW - timedelta(minutes=5)),
    ]
    session.execute.return_value = rows

    asyncio.run(
        _refresh_legacy_schedule_aggregate(session, mission_id=uuid4(), now=NOW)
    )

    session.flush.assert_awaited_once()
    # MIN entre os `next_run_at` (com `NULL` tratado como `now`, já due).
    assert schedule.next_run_at == NOW
    # MAIS RECENTE entre os `last_run_at` não nulos.
    assert schedule.last_run_at == NOW - timedelta(minutes=5)
    assert schedule.updated_at == NOW


def test_claim_due_collections_skips_mission_already_running() -> None:
    """TASK-062 original: missão com `CollectionRun` já `RUNNING` não pode
    ser reclamada de novo -- nenhuma claim, nenhum run novo, mesmo com
    fonte due. TASK-112 fase 3B: seleção agora é por `MissionSource`."""
    from app.missions.models import MissionSource

    mission_id = uuid4()
    user_id = uuid4()
    source = MissionSource(mission_id=mission_id, store_id=uuid4())
    session = _mock_async_session()

    due_rows = MagicMock()
    due_rows.all.return_value = [(source, "kabum", user_id)]
    session.execute.return_value = due_rows
    session.scalars.return_value = []
    session.scalar.return_value = uuid4()  # já existe um run RUNNING

    claims = asyncio.run(
        claim_due_collections(
            session,
            now=NOW,
            user_cooldown_min_seconds=0,
            user_cooldown_max_seconds=0,
            store_min_interval_seconds=0,
        )
    )

    assert claims == ()


def test_claim_due_collections_returns_empty_when_no_rows_due() -> None:
    session = _mock_async_session()
    due_rows = MagicMock()
    due_rows.all.return_value = []
    session.execute.return_value = due_rows

    claims = asyncio.run(
        claim_due_collections(
            session,
            now=NOW,
            user_cooldown_min_seconds=0,
            user_cooldown_max_seconds=0,
            store_min_interval_seconds=0,
        )
    )

    assert claims == ()


def test_claim_due_collections_skips_source_without_criteria() -> None:
    from app.missions.models import MissionSource

    mission_id = uuid4()
    user_id = uuid4()
    source = MissionSource(mission_id=mission_id, store_id=uuid4())
    session = _mock_async_session()
    due_rows = MagicMock()
    due_rows.all.return_value = [(source, "kabum", user_id)]
    session.execute.return_value = due_rows
    session.scalars.return_value = []
    session.scalar.side_effect = [None, None]  # não RUNNING, sem criteria

    claims = asyncio.run(
        claim_due_collections(
            session,
            now=NOW,
            user_cooldown_min_seconds=0,
            user_cooldown_max_seconds=0,
            store_min_interval_seconds=0,
        )
    )

    assert claims == ()


def test_claim_due_collections_skips_source_with_blank_search_query() -> None:
    from app.missions.models import MissionSource

    mission_id = uuid4()
    user_id = uuid4()
    source = MissionSource(mission_id=mission_id, store_id=uuid4())
    session = _mock_async_session()
    due_rows = MagicMock()
    due_rows.all.return_value = [(source, "kabum", user_id)]
    session.execute.return_value = due_rows
    session.scalars.return_value = []
    criteria = SimpleNamespace(search_query="   ")
    session.scalar.side_effect = [None, criteria]

    claims = asyncio.run(
        claim_due_collections(
            session,
            now=NOW,
            user_cooldown_min_seconds=0,
            user_cooldown_max_seconds=0,
            store_min_interval_seconds=0,
        )
    )

    assert claims == ()


def test_claim_due_collections_skips_when_store_still_throttled() -> None:
    from app.missions.models import MissionSource

    mission_id = uuid4()
    user_id = uuid4()
    store_id = uuid4()
    source = MissionSource(mission_id=mission_id, store_id=store_id)
    session = _mock_async_session()
    due_rows = MagicMock()
    due_rows.all.return_value = [(source, "kabum", user_id)]
    session.execute.return_value = due_rows
    session.scalars.return_value = []
    criteria = SimpleNamespace(search_query="GPU")
    session.scalar.side_effect = [None, criteria]
    session.get.return_value = StoreThrottleState(
        store_id=store_id, next_allowed_at=NOW + timedelta(seconds=5)
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

    assert claims == ()


def test_claim_due_collections_skips_when_next_run_at_still_future() -> None:
    from app.missions.models import MissionSource

    mission_id = uuid4()
    user_id = uuid4()
    source = MissionSource(
        mission_id=mission_id,
        store_id=uuid4(),
        next_run_at=NOW + timedelta(minutes=5),
    )
    session = _mock_async_session()
    due_rows = MagicMock()
    due_rows.all.return_value = [(source, "kabum", user_id)]
    session.execute.return_value = due_rows
    session.scalars.return_value = []
    criteria = SimpleNamespace(search_query="GPU")
    session.scalar.side_effect = [None, criteria]
    session.get.return_value = None

    claims = asyncio.run(
        claim_due_collections(
            session,
            now=NOW,
            user_cooldown_min_seconds=0,
            user_cooldown_max_seconds=0,
            store_min_interval_seconds=0,
        )
    )

    assert claims == ()


def test_claim_due_collections_skips_when_next_eligible_at_still_future() -> None:
    from app.missions.models import MissionSource

    mission_id = uuid4()
    user_id = uuid4()
    source = MissionSource(
        mission_id=mission_id,
        store_id=uuid4(),
        next_run_at=None,
        next_eligible_at=NOW + timedelta(minutes=5),
    )
    session = _mock_async_session()
    due_rows = MagicMock()
    due_rows.all.return_value = [(source, "kabum", user_id)]
    session.execute.return_value = due_rows
    session.scalars.return_value = []
    criteria = SimpleNamespace(search_query="GPU")
    session.scalar.side_effect = [None, criteria]
    session.get.return_value = None

    claims = asyncio.run(
        claim_due_collections(
            session,
            now=NOW,
            user_cooldown_min_seconds=0,
            user_cooldown_max_seconds=0,
            store_min_interval_seconds=0,
        )
    )

    assert claims == ()


def test_claim_due_collections_skips_on_running_index_conflict(monkeypatch) -> None:
    from app.missions.models import MissionSource

    mission_id = uuid4()
    user_id = uuid4()
    source = MissionSource(
        mission_id=mission_id, store_id=uuid4(), next_run_at=None, next_eligible_at=None
    )
    session = _mock_async_session()
    due_rows = MagicMock()
    due_rows.all.return_value = [(source, "kabum", user_id)]
    session.execute.return_value = due_rows
    session.scalars.return_value = []
    criteria = SimpleNamespace(search_query="GPU")
    session.scalar.side_effect = [None, criteria]
    session.get.return_value = None
    monkeypatch.setattr(
        "app.collection.orchestration.start_collection_run",
        AsyncMock(side_effect=IntegrityError("stmt", {}, Exception())),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._constraint_name",
        lambda error: _RUNNING_INDEX,
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

    assert claims == ()


def test_claim_due_collections_reraises_unrelated_integrity_error(monkeypatch) -> None:
    from app.missions.models import MissionSource

    mission_id = uuid4()
    user_id = uuid4()
    source = MissionSource(
        mission_id=mission_id, store_id=uuid4(), next_run_at=None, next_eligible_at=None
    )
    session = _mock_async_session()
    due_rows = MagicMock()
    due_rows.all.return_value = [(source, "kabum", user_id)]
    session.execute.return_value = due_rows
    session.scalars.return_value = []
    criteria = SimpleNamespace(search_query="GPU")
    session.scalar.side_effect = [None, criteria]
    session.get.return_value = None
    error = IntegrityError("stmt", {}, Exception())
    monkeypatch.setattr(
        "app.collection.orchestration.start_collection_run",
        AsyncMock(side_effect=error),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._constraint_name",
        lambda err: "some_other_constraint",
    )

    try:
        asyncio.run(
            claim_due_collections(
                session,
                now=NOW,
                user_cooldown_min_seconds=0,
                user_cooldown_max_seconds=0,
                store_min_interval_seconds=0,
            )
        )
    except IntegrityError as caught:
        assert caught is error
    else:
        raise AssertionError("deveria propagar IntegrityError não relacionada")


def test_claim_due_collections_sorts_processed_users_by_last_processed_at(
    monkeypatch,
) -> None:
    from app.missions.models import MissionSource

    mission_id = uuid4()
    user_id = uuid4()
    store_id = uuid4()
    source = MissionSource(
        mission_id=mission_id,
        store_id=store_id,
        next_run_at=None,
        next_eligible_at=None,
    )
    session = _mock_async_session()
    due_rows = MagicMock()
    due_rows.all.return_value = [(source, "kabum", user_id)]
    session.execute.return_value = due_rows
    session.scalars.return_value = [
        UserCollectionQueueState(
            user_id=user_id,
            last_processed_at=NOW - timedelta(hours=1),
            next_eligible_at=None,
        )
    ]
    criteria = SimpleNamespace(search_query="GPU", model=None)
    session.scalar.side_effect = [None, criteria]
    session.get.return_value = None
    monkeypatch.setattr(
        "app.collection.orchestration.start_collection_run",
        AsyncMock(return_value=SimpleNamespace(id=uuid4())),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.resolve_collection_cadence",
        AsyncMock(
            return_value=SimpleNamespace(min_minutes=45, max_minutes=45, mode="normal")
        ),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.sample_next_run_at",
        lambda started_at, decision: (
            started_at + timedelta(minutes=decision.min_minutes)
        ),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._refresh_legacy_schedule_aggregate",
        AsyncMock(),
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

    assert len(claims) == 1


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


def test_find_offer_falls_back_to_url_when_external_id_missing() -> None:
    session = _mock_async_session()
    session.scalar.return_value = None
    store_id = uuid4()

    result = asyncio.run(
        _find_offer(session, store_id, None, None, "https://example.invalid/x")
    )

    assert result is None
    compiled = str(
        session.scalar.await_args.args[0].compile(
            compile_kwargs={"literal_binds": False}
        )
    )
    assert "offers.url = " in compiled
    assert "offers.external_id = " not in compiled


def test_resolve_seller_returns_existing_without_creating() -> None:
    session = _mock_async_session()
    existing = Seller(
        id=uuid4(), store_id=uuid4(), external_id="seller-1", name="Loja X"
    )
    session.scalar.return_value = existing
    item = PriceNormalizer().normalize_offer(_raw(seller_external_id="seller-1"))

    seller = asyncio.run(_resolve_seller(session, uuid4(), item))

    assert seller is existing
    session.add.assert_not_called()


def test_resolve_seller_reraises_unrelated_integrity_error(monkeypatch) -> None:
    session = _mock_async_session()
    session.scalar.return_value = None
    session.flush.side_effect = IntegrityError("stmt", {}, Exception())
    monkeypatch.setattr(
        "app.collection.orchestration._constraint_name",
        lambda error: "some_other_constraint",
    )
    item = PriceNormalizer().normalize_offer(_raw(seller_external_id="seller-1"))

    try:
        asyncio.run(_resolve_seller(session, uuid4(), item))
    except IntegrityError:
        pass
    else:
        raise AssertionError("deveria propagar IntegrityError não relacionada")


def test_resolve_seller_concurrent_insert_returns_winner(monkeypatch) -> None:
    session = _mock_async_session()
    winner = Seller(id=uuid4(), store_id=uuid4(), external_id="seller-1", name="Loja X")
    session.scalar.side_effect = [None, winner]
    session.flush.side_effect = IntegrityError("stmt", {}, Exception())
    monkeypatch.setattr(
        "app.collection.orchestration._constraint_name",
        lambda error: _SELLER_IDENTITY_INDEX,
    )
    item = PriceNormalizer().normalize_offer(_raw(seller_external_id="seller-1"))

    seller = asyncio.run(_resolve_seller(session, uuid4(), item))

    assert seller is winner


def test_resolve_seller_concurrent_insert_without_winner_reraises(monkeypatch) -> None:
    session = _mock_async_session()
    session.scalar.side_effect = [None, None]
    error = IntegrityError("stmt", {}, Exception())
    session.flush.side_effect = error
    monkeypatch.setattr(
        "app.collection.orchestration._constraint_name",
        lambda err: _SELLER_IDENTITY_INDEX,
    )
    item = PriceNormalizer().normalize_offer(_raw(seller_external_id="seller-1"))

    try:
        asyncio.run(_resolve_seller(session, uuid4(), item))
    except IntegrityError as caught:
        assert caught is error
    else:
        raise AssertionError("deveria propagar quando não há vencedor concorrente")


def test_maybe_set_canonical_image_only_sets_when_absent() -> None:
    product = Product(name="x")
    _maybe_set_canonical_image(product, "https://example.invalid/first.jpg")
    assert product.canonical_image_url == "https://example.invalid/first.jpg"

    _maybe_set_canonical_image(product, "https://example.invalid/second.jpg")
    assert product.canonical_image_url == "https://example.invalid/first.jpg"


def test_supersede_noop_without_seller_or_external_id() -> None:
    session = _mock_async_session()
    new_offer = Offer(id=uuid4(), external_id=None)

    asyncio.run(
        _supersede_old_unattributed_offer(
            session, store_id=uuid4(), seller_id=None, new_offer=new_offer
        )
    )
    session.scalar.assert_not_awaited()

    new_offer_without_external_id = Offer(id=uuid4(), external_id=None)
    asyncio.run(
        _supersede_old_unattributed_offer(
            session,
            store_id=uuid4(),
            seller_id=uuid4(),
            new_offer=new_offer_without_external_id,
        )
    )
    session.scalar.assert_not_awaited()


def test_supersede_marks_old_unattributed_offer_when_found() -> None:
    session = _mock_async_session()
    old_offer = Offer(id=uuid4(), external_id="ext-1", superseded_by_id=None)
    session.scalar.return_value = old_offer
    new_offer = Offer(id=uuid4(), external_id="ext-1")

    asyncio.run(
        _supersede_old_unattributed_offer(
            session, store_id=uuid4(), seller_id=uuid4(), new_offer=new_offer
        )
    )

    assert old_offer.superseded_by_id == new_offer.id
    assert old_offer.superseded_at is not None


def test_supersede_is_noop_when_no_old_offer_found() -> None:
    session = _mock_async_session()
    session.scalar.return_value = None
    new_offer = Offer(id=uuid4(), external_id="ext-1")

    asyncio.run(
        _supersede_old_unattributed_offer(
            session, store_id=uuid4(), seller_id=uuid4(), new_offer=new_offer
        )
    )
    session.scalar.assert_awaited_once()


def test_resolve_global_product_returns_existing_match() -> None:
    session = _mock_async_session()
    existing = Product(id=uuid4(), name="Apple iPhone 17 Pro 256 GB")
    session.scalar.return_value = existing

    product = asyncio.run(
        _resolve_global_product(session, "Apple iPhone 17 Pro 256 GB")
    )

    assert product is existing
    session.add.assert_not_called()


def test_resolve_global_product_creates_new_when_absent() -> None:
    session = _mock_async_session()
    session.scalar.return_value = None

    product = asyncio.run(
        _resolve_global_product(session, "Apple iPhone 17 Pro 256 GB")
    )

    identity = resolve_product_variant("Apple iPhone 17 Pro 256 GB")
    assert product.identity_key == identity.identity_key
    assert product.family_key == identity.family_key
    session.add.assert_called_once_with(product)
    session.flush.assert_awaited_once()


def test_resolve_global_product_reraises_unrelated_integrity_error(monkeypatch) -> None:
    session = _mock_async_session()
    session.scalar.return_value = None
    session.flush.side_effect = IntegrityError("stmt", {}, Exception())
    monkeypatch.setattr(
        "app.collection.orchestration._constraint_name",
        lambda error: "some_other_constraint",
    )

    try:
        asyncio.run(_resolve_global_product(session, "Apple iPhone 17 Pro 256 GB"))
    except IntegrityError:
        pass
    else:
        raise AssertionError("deveria propagar IntegrityError não relacionada")


def test_resolve_global_product_concurrent_insert_returns_winner(monkeypatch) -> None:
    session = _mock_async_session()
    winner = Product(id=uuid4(), name="Apple iPhone 17 Pro 256 GB")
    session.scalar.side_effect = [None, winner]
    session.flush.side_effect = IntegrityError("stmt", {}, Exception())
    monkeypatch.setattr(
        "app.collection.orchestration._constraint_name",
        lambda error: _PRODUCT_IDENTITY_INDEX,
    )

    product = asyncio.run(
        _resolve_global_product(session, "Apple iPhone 17 Pro 256 GB")
    )

    assert product is winner


def test_resolve_global_product_concurrent_insert_without_winner_reraises(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    session.scalar.side_effect = [None, None]
    error = IntegrityError("stmt", {}, Exception())
    session.flush.side_effect = error
    monkeypatch.setattr(
        "app.collection.orchestration._constraint_name",
        lambda err: _PRODUCT_IDENTITY_INDEX,
    )

    try:
        asyncio.run(_resolve_global_product(session, "Apple iPhone 17 Pro 256 GB"))
    except IntegrityError as caught:
        assert caught is error
    else:
        raise AssertionError("deveria propagar quando não há vencedor concorrente")


def test_resolve_offer_reused_offer_upgrades_unidentified_product(monkeypatch) -> None:
    session = _mock_async_session()
    resolved_product = Product(id=uuid4(), name="Apple iPhone 17 Pro 256 GB")
    current_product = Product(id=uuid4(), name="antigo", identity_key=None)
    existing_offer = Offer(id=uuid4(), product_id=current_product.id, image_url=None)
    session.get.return_value = current_product
    monkeypatch.setattr(
        "app.collection.orchestration._resolve_seller", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "app.collection.orchestration._find_offer",
        AsyncMock(return_value=existing_offer),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._resolve_global_product",
        AsyncMock(return_value=resolved_product),
    )
    item = PriceNormalizer().normalize_offer(
        _raw(image_url="https://example.invalid/new.jpg")
    )

    offer = asyncio.run(_resolve_offer(session, uuid4(), item))

    assert offer is existing_offer
    assert offer.product_id == resolved_product.id
    assert offer.image_url == "https://example.invalid/new.jpg"
    assert resolved_product.canonical_image_url == "https://example.invalid/new.jpg"


def test_resolve_offer_reused_offer_keeps_already_identified_product(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    resolved_product = Product(id=uuid4(), name="Apple iPhone 17 Pro 256 GB")
    current_product = Product(
        id=uuid4(), name="já identificado", identity_key="v1:already"
    )
    existing_offer = Offer(id=uuid4(), product_id=current_product.id, image_url=None)
    session.get.return_value = current_product
    monkeypatch.setattr(
        "app.collection.orchestration._resolve_seller", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "app.collection.orchestration._find_offer",
        AsyncMock(return_value=existing_offer),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._resolve_global_product",
        AsyncMock(return_value=resolved_product),
    )
    item = PriceNormalizer().normalize_offer(
        _raw(image_url="https://example.invalid/new.jpg")
    )

    offer = asyncio.run(_resolve_offer(session, uuid4(), item))

    assert offer.product_id == current_product.id
    assert current_product.canonical_image_url == "https://example.invalid/new.jpg"


def test_resolve_offer_new_product_sets_canonical_image_when_identity_resolved(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    session.scalar.return_value = None
    monkeypatch.setattr(
        "app.collection.orchestration._resolve_seller", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "app.collection.orchestration._find_offer", AsyncMock(return_value=None)
    )
    item = PriceNormalizer().normalize_offer(
        _raw(
            title="Apple iPhone 17 Pro 256 GB",
            image_url="https://example.invalid/new.jpg",
        )
    )

    offer = asyncio.run(_resolve_offer(session, uuid4(), item))

    assert offer.image_url == "https://example.invalid/new.jpg"
    added_products = [
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], Product)
    ]
    assert len(added_products) == 1
    assert added_products[0].canonical_image_url == "https://example.invalid/new.jpg"


def test_resolve_offer_concurrent_insert_reraises_unrelated_integrity_error(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    monkeypatch.setattr(
        "app.collection.orchestration._resolve_seller", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "app.collection.orchestration._find_offer", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "app.collection.orchestration._resolve_global_product",
        AsyncMock(return_value=None),
    )
    session.flush.side_effect = IntegrityError("stmt", {}, Exception())
    monkeypatch.setattr(
        "app.collection.orchestration._constraint_name",
        lambda error: "some_other_constraint",
    )
    item = PriceNormalizer().normalize_offer(_raw())

    try:
        asyncio.run(_resolve_offer(session, uuid4(), item))
    except IntegrityError:
        pass
    else:
        raise AssertionError("deveria propagar IntegrityError não relacionada")


def test_resolve_offer_concurrent_insert_returns_winner_and_updates_image(
    monkeypatch,
) -> None:
    session = _mock_async_session()
    winner_product = Product(id=uuid4(), name="ganhador", identity_key="v1:winner")
    winner_offer = Offer(id=uuid4(), product_id=winner_product.id, image_url=None)
    monkeypatch.setattr(
        "app.collection.orchestration._resolve_seller", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "app.collection.orchestration._find_offer",
        AsyncMock(side_effect=[None, winner_offer]),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._resolve_global_product",
        AsyncMock(return_value=None),
    )
    session.get.return_value = winner_product
    session.flush.side_effect = IntegrityError("stmt", {}, Exception())
    monkeypatch.setattr(
        "app.collection.orchestration._constraint_name",
        lambda error: next(iter(_OFFER_IDENTITY_INDEXES)),
    )
    item = PriceNormalizer().normalize_offer(
        _raw(image_url="https://example.invalid/winner.jpg")
    )

    offer = asyncio.run(_resolve_offer(session, uuid4(), item))

    assert offer is winner_offer
    assert winner_offer.image_url == "https://example.invalid/winner.jpg"
    assert winner_product.canonical_image_url == "https://example.invalid/winner.jpg"


def test_resolve_offer_concurrent_insert_without_winner_reraises(monkeypatch) -> None:
    session = _mock_async_session()
    monkeypatch.setattr(
        "app.collection.orchestration._resolve_seller", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "app.collection.orchestration._find_offer",
        AsyncMock(side_effect=[None, None]),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._resolve_global_product",
        AsyncMock(return_value=None),
    )
    error = IntegrityError("stmt", {}, Exception())
    session.flush.side_effect = error
    monkeypatch.setattr(
        "app.collection.orchestration._constraint_name",
        lambda err: next(iter(_OFFER_IDENTITY_INDEXES)),
    )
    item = PriceNormalizer().normalize_offer(_raw())

    try:
        asyncio.run(_resolve_offer(session, uuid4(), item))
    except IntegrityError as caught:
        assert caught is error
    else:
        raise AssertionError("deveria propagar quando não há vencedor concorrente")


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
    # scalar: run, criteria, offer lock, product lock (Subtask 6),
    # previous(=None), latest(=None) (TASK-093)
    session.scalar.side_effect = [run, criteria, None, None, None, None]
    # get: Mission, MissionOfferRelevance cache (None -> needs AI), Product (display_name=None)
    product = SimpleNamespace(display_name=None, identity_key="existing-key")
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
    session.scalar.side_effect = [
        run,
        criteria,
        None,
        None,
        None,
        None,
    ]  # +offer lock +product lock (Subtask 6) +latest (TASK-093)
    product = SimpleNamespace(display_name=None, identity_key="existing-key")
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
    session.scalar.side_effect = [
        run,
        criteria,
        None,
        None,
        None,
        None,
    ]  # +offer lock +product lock (Subtask 6) +latest (TASK-093)
    product = SimpleNamespace(display_name=None, identity_key="existing-key")
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
    # scalar: run, criteria, depois lock (Subtask 6) + previous+latest
    # (TASK-093) por sobrevivente (5, dentro do pool intermediário de 8
    # da TASK-094)
    session.scalar.side_effect = [run, criteria] + [None] * 16
    product = SimpleNamespace(display_name="Cadeira", identity_key="existing-key")
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
    # lock (Subtask 6) + previous+latest (TASK-093) por sobrevivente (5, sem corte)
    session.scalar.side_effect = [run, criteria] + [None] * 16
    product = SimpleNamespace(display_name="RTX 5070 Ti", identity_key="existing-key")
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


def test_persist_phase_a_dedupes_offers_sharing_the_same_identity_key(
    monkeypatch,
) -> None:
    """Dois candidatos com a mesma `(seller_external_id, external_id, url)`
    (mesmo `store_code`) -- só o primeiro é processado; o segundo é pulado
    pelo dedupe do próprio loop (`items_by_key`)."""
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
        search_query="GPU",
        model=None,
        target_amount=None,
        target_currency=None,
    )
    session = _mock_async_session()
    # scalar: run, criteria, preview (find_offer -> None), previous(None), latest(None)
    session.scalar.side_effect = [run, criteria, None, None, None]
    product = SimpleNamespace(display_name="RTX", identity_key="existing-key")
    session.get.side_effect = [mission, None, product]
    offer_stub = SimpleNamespace(id=uuid4(), product_id=uuid4())
    resolve_offer = AsyncMock(return_value=offer_stub)
    monkeypatch.setattr("app.collection.orchestration._resolve_offer", resolve_offer)
    claim = ClaimedCollection(run_id, mission_id, store_id, "pichau", "GPU", NOW)
    result = CollectionResult(
        "pichau",
        NOW,
        NOW + timedelta(seconds=2),
        (
            _raw(external_id="dup-1", url="https://example.invalid/same-offer"),
            _raw(external_id="dup-1", url="https://example.invalid/same-offer"),
        ),
    )
    normalized = PriceNormalizer().normalize_result(result)

    outcome = asyncio.run(
        _persist_phase_a(_session_factory(session), claim, normalized, preselected=True)
    )

    assert len(outcome.offers) == 1
    resolve_offer.assert_awaited_once()


def test_persist_phase_a_locks_previously_existing_offer_and_product_rows(
    monkeypatch,
) -> None:
    """Subtask 6: Offer/Product já existentes descobertos na pré-visualização
    são travados (`SELECT ... FOR UPDATE`) antes de `_resolve_offer`."""
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
        search_query="GPU",
        model=None,
        target_amount=None,
        target_currency=None,
    )
    session = _mock_async_session()
    existing_offer = SimpleNamespace(id=uuid4(), product_id=uuid4())
    existing_product = SimpleNamespace(id=uuid4())
    # scalar: run, criteria, preview (find_offer -> existing_offer),
    # offer lock, product lock, previous(None), latest(None)
    session.scalar.side_effect = [
        run,
        criteria,
        existing_offer,
        None,
        None,
        None,
        None,
    ]
    resolved_product = SimpleNamespace(display_name=None, identity_key="existing-key")
    # get: Mission, Product (preview, Subtask 6), relevance cache (None), Product (final)
    session.get.side_effect = [mission, existing_product, None, resolved_product]
    offer_stub = SimpleNamespace(id=uuid4(), product_id=uuid4())
    monkeypatch.setattr(
        "app.collection.orchestration._resolve_offer",
        AsyncMock(return_value=offer_stub),
    )
    claim = ClaimedCollection(run_id, mission_id, store_id, "pichau", "GPU", NOW)
    result = CollectionResult("pichau", NOW, NOW + timedelta(seconds=2), (_raw(),))
    normalized = PriceNormalizer().normalize_result(result)

    outcome = asyncio.run(
        _persist_phase_a(_session_factory(session), claim, normalized, preselected=True)
    )

    assert len(outcome.offers) == 1
    # Todos os 7 `scalar` configurados foram de fato consumidos -- prova
    # que os dois loops de trava (offer_ids/product_ids) rodaram.
    assert session.scalar.await_count == 7


def test_persist_phase_a_reuses_latest_observation_when_commercial_state_and_installments_match(  # noqa: E501
    monkeypatch,
) -> None:
    """TASK-093: estado comercial (preço, moeda, disponibilidade,
    fulfillment, parcelamento) idêntico à última `PriceObservation` da
    mesma Offer -- nenhuma nova `PriceObservation`/`OfferInstallmentOption`
    é criada; `latest` é reaproveitada como `observation`, e o resultado
    é marcado como `UNCHANGED_REUSED` (mesma `previous`)."""
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
        search_query="GPU",
        model=None,
        target_amount=None,
        target_currency=None,
    )
    claim = ClaimedCollection(run_id, mission_id, store_id, "pichau", "GPU", NOW)
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
    # scalar: run, criteria, preview (find_offer -> None -- oferta nova),
    # previous (mesma missão, = latest), latest (última observação global)
    session.scalar.side_effect = [run, criteria, None, latest, latest]
    # session.scalars: OfferInstallmentOption ligadas a `latest` -- vazia,
    # igual a `item.installment_options` (também vazia por padrão em `_raw()`).
    session.scalars.return_value = []
    resolved_product = SimpleNamespace(display_name="RTX", identity_key="existing-key")
    session.get.side_effect = [mission, None, resolved_product]
    offer_stub = SimpleNamespace(id=uuid4(), product_id=uuid4())
    resolve_offer = AsyncMock(return_value=offer_stub)
    monkeypatch.setattr("app.collection.orchestration._resolve_offer", resolve_offer)

    outcome = asyncio.run(
        _persist_phase_a(_session_factory(session), claim, normalized, preselected=True)
    )

    assert len(outcome.offers) == 1
    pending = outcome.offers[0]
    # Nenhuma nova PriceObservation/OfferInstallmentOption -- `session.add`
    # só foi usado para o `SharedCollectionOffer` de confirmação.
    added_types = {type(call.args[0]).__name__ for call in session.add.call_args_list}
    assert added_types == {"SharedCollectionOffer"}
    session.flush.assert_not_awaited()
    assert pending.observation_id == latest.id
    assert pending.observation_created is False
    assert pending.alert_comparison is PriceObservationComparison.UNCHANGED_REUSED
    assert pending.previous_observation_id == latest.id


def test_persist_phase_a_raises_when_offer_references_missing_product(
    monkeypatch,
) -> None:
    """Linha defensiva: `offer.product_id` aponta para um `Product` que já
    não existe -- erro de integridade que nunca deveria acontecer em
    produção, mas a função levanta `RuntimeError` em vez de seguir com um
    `product=None`."""
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
        search_query="GPU",
        model=None,
        target_amount=None,
        target_currency=None,
    )
    session = _mock_async_session()
    # scalar: run, criteria, preview (find_offer -> None), previous(None), latest(None)
    session.scalar.side_effect = [run, criteria, None, None, None]
    # get: Mission, relevance cache (None), Product (final) -- ausente
    session.get.side_effect = [mission, None, None]
    offer_stub = SimpleNamespace(id=uuid4(), product_id=uuid4())
    monkeypatch.setattr(
        "app.collection.orchestration._resolve_offer",
        AsyncMock(return_value=offer_stub),
    )
    claim = ClaimedCollection(run_id, mission_id, store_id, "pichau", "GPU", NOW)
    result = CollectionResult("pichau", NOW, NOW + timedelta(seconds=2), (_raw(),))
    normalized = PriceNormalizer().normalize_result(result)

    with pytest.raises(RuntimeError, match="missing product"):
        asyncio.run(
            _persist_phase_a(
                _session_factory(session), claim, normalized, preselected=True
            )
        )


def test_persist_phase_a_publishes_availability_changed_when_previous_differs(
    monkeypatch,
) -> None:
    """`previous` (última observação da MESMA missão) existe, é diferente
    da nova `observation` recém-criada (`CHANGED`, não `UNCHANGED_REUSED`)
    e tem disponibilidade diferente -- publica `AVAILABILITY_CHANGED_V1`
    já na Fase A, sem esperar a Fase C."""
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
        search_query="GPU",
        model=None,
        target_amount=None,
        target_currency=None,
    )
    previous = SimpleNamespace(
        id=uuid4(),
        amount=Decimal("120.00"),
        currency="BRL",
        availability=Availability.UNAVAILABLE,
        observed_at=NOW - timedelta(hours=1),
    )
    session = _mock_async_session()
    # scalar: run, criteria, preview (find_offer -> None), previous, latest (None)
    session.scalar.side_effect = [run, criteria, None, previous, None]
    resolved_product = SimpleNamespace(display_name="RTX", identity_key="existing-key")
    session.get.side_effect = [mission, None, resolved_product]
    offer_stub = SimpleNamespace(id=uuid4(), product_id=uuid4())
    monkeypatch.setattr(
        "app.collection.orchestration._resolve_offer",
        AsyncMock(return_value=offer_stub),
    )
    publish = AsyncMock()
    monkeypatch.setattr("app.collection.orchestration.publish_event_async", publish)
    claim = ClaimedCollection(run_id, mission_id, store_id, "pichau", "GPU", NOW)
    result = CollectionResult("pichau", NOW, NOW + timedelta(seconds=2), (_raw(),))
    normalized = PriceNormalizer().normalize_result(result)

    outcome = asyncio.run(
        _persist_phase_a(_session_factory(session), claim, normalized, preselected=True)
    )

    pending = outcome.offers[0]
    assert pending.observation_created is True
    assert pending.alert_comparison is PriceObservationComparison.CHANGED
    publish.assert_awaited_once()
    _, kwargs = publish.call_args
    assert kwargs["event_type"] is EventType.AVAILABILITY_CHANGED_V1
    payload = kwargs["payload"]
    assert payload.previous_availability is Availability.UNAVAILABLE
    assert payload.current_availability is Availability.AVAILABLE


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


def test_run_phase_b_normalizes_title_when_needs_display_name() -> None:
    """`needs_display_name=True` -- `_classify` chama `normalize_offer_title`
    e o resultado (título curto da IA) é propagado no `_AIOutcome`."""
    pending = _PendingOffer(
        offer_id=uuid4(),
        product_id=uuid4(),
        observation_id=uuid4(),
        amount=Decimal("100"),
        currency="BRL",
        availability=Availability.AVAILABLE,
        observed_at=NOW,
        raw_title="Título bruto muito longo da loja",
        needs_relevance=False,
        needs_display_name=True,
        observation_created=True,
        alert_comparison=PriceObservationComparison.FIRST_OBSERVATION,
        forced_relevance=OfferRelevance.MATCH,
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
    ai_manager = _StubAIManager(
        {"normalize_offer_title": '{"display_title": "Título curto"}'}
    )

    outcomes = asyncio.run(_run_phase_b(outcome, ai_manager, UserRole.ADMIN))

    assert ai_manager.calls == ["normalize_offer_title"]
    assert outcomes[0].relevance is OfferRelevance.MATCH
    assert outcomes[0].display_title == "Título curto"


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


def test_persist_phase_c_returns_false_when_criteria_no_longer_exists() -> None:
    """`MissionCriteria` foi apagada entre a Fase A e a Fase C (mission
    reconfigurada/cancelada nesse meio-tempo) -- sem critério, não há como
    validar/gravar relevância; a função aborta sem persistir nada."""
    mission = SimpleNamespace(id=uuid4())
    run = SimpleNamespace(status=CollectionRunStatus.RUNNING)
    session = _mock_async_session()
    session.scalar.side_effect = [mission, run, None]
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
    product = SimpleNamespace(display_name=None, identity_key="existing-key")
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


def test_persist_phase_c_applies_learned_identity_and_uses_cached_relevance(
    monkeypatch,
) -> None:
    """Rodada de aprendizado de identidade (2026-09-12): `ai_outcome.
    learned_identity` presente e `Product.identity_key` ainda `None` --
    aplica a identidade aprendida DENTRO da seção crítica. Também cobre o
    `else` da decisão de relevância (nem `forced_relevance`, nem
    `needs_relevance`): usa `MissionOfferRelevance` já cacheada -- aqui,
    nenhuma (`cached=None`), então a oferta segue sem relevância nesta
    rodada."""
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
    current_criteria = SimpleNamespace(
        request_kind="generic_category",
        variant_selection_mode=VariantSelectionMode.NOT_REQUIRED,
    )
    session = _mock_async_session()
    session.scalar.side_effect = [mission, run, current_criteria]
    current_product = SimpleNamespace(identity_key=None)
    cached_relevance = SimpleNamespace(
        classification=OfferRelevance.NO_MATCH, last_observation_id=uuid4()
    )
    # Product, cached relevance (NO_MATCH -- evita o bloco de alerta, que
    # só roda para MATCH; ainda cobre `cached.last_observation_id = ...`)
    session.get.side_effect = [current_product, cached_relevance]
    apply_identity = AsyncMock()
    monkeypatch.setattr(
        "app.collection.orchestration.apply_learned_identity", apply_identity
    )
    monkeypatch.setattr(
        "app.collection.orchestration.finish_collection_run", AsyncMock()
    )
    monkeypatch.setattr(
        "app.collection.orchestration._reset_source_backoff", AsyncMock()
    )
    monkeypatch.setattr(
        "app.collection.orchestration._evaluate_mission_prelist", AsyncMock()
    )
    monkeypatch.setattr("app.collection.orchestration.publish_event_async", AsyncMock())
    pending = _PendingOffer(
        offer_id=offer_id,
        product_id=product_id,
        observation_id=uuid4(),
        amount=Decimal("100"),
        currency="BRL",
        availability=Availability.AVAILABLE,
        observed_at=NOW,
        raw_title="Título bruto",
        needs_relevance=False,
        needs_display_name=False,
        observation_created=True,
        alert_comparison=PriceObservationComparison.UNCHANGED_REUSED,
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
    learned = SimpleNamespace(identity_key="v1:learned")
    ai_outcomes = (_AIOutcome(offer_id, None, None, learned_identity=learned),)

    result = asyncio.run(
        _persist_phase_c(_session_factory(session), outcome, ai_outcomes)
    )

    assert result is True
    apply_identity.assert_awaited_once_with(
        session, product=current_product, resolved=learned
    )
    assert cached_relevance.last_observation_id == pending.observation_id


def test_persist_phase_c_uses_coupon_final_amount_for_alert_but_never_the_persisted_row(
    monkeypatch,
) -> None:
    """Consumo de cupons (2026-09-06): quando a Fase B calculou um cupom
    aplicável, a decisão de alerta usa `applied_coupon.final_amount` como
    `current.amount` -- o `PriceObservation` JÁ persistido na Fase A com
    `pending.amount` (preço original coletado) nunca é tocado por isso;
    aqui não existe nenhuma escrita usando `evaluation_amount`, só o valor
    efêmero passado ao evaluator."""
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
    product = SimpleNamespace(display_name=None, identity_key="existing-key")
    session.get.return_value = product
    monkeypatch.setattr(
        "app.collection.orchestration.finish_collection_run", AsyncMock()
    )
    monkeypatch.setattr(
        "app.collection.orchestration._reset_source_backoff", AsyncMock()
    )
    monkeypatch.setattr(
        "app.collection.orchestration._evaluate_mission_prelist", AsyncMock()
    )
    monkeypatch.setattr("app.collection.orchestration.publish_event_async", AsyncMock())
    evaluator = MagicMock(return_value=())
    monkeypatch.setattr("app.collection.orchestration.evaluate_price_alerts", evaluator)
    pending = _PendingOffer(
        offer_id=offer_id,
        product_id=product_id,
        observation_id=uuid4(),
        amount=Decimal("100"),  # preço original persistido pela Fase A
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
    applied_coupon = AppliedCoupon(
        coupon_id=uuid4(),
        code="PROMO20",
        discount_kind="fixed_amount",
        original_amount=Decimal("100"),
        discount_amount=Decimal("20"),
        final_amount=Decimal("80"),
        currency="BRL",
    )
    ai_outcomes = (
        _AIOutcome(
            offer_id,
            OfferRelevance.MATCH,
            "Título normalizado",
            applied_coupon=applied_coupon,
        ),
    )

    result = asyncio.run(
        _persist_phase_c(_session_factory(session), outcome, ai_outcomes)
    )

    assert result is True
    evaluator.assert_called_once()
    current = evaluator.call_args.args[2]
    assert current.amount == Decimal("80")  # preço final com cupom, não o original
    # A Fase A já persistiu `pending.amount` original -- nunca sobrescrito aqui.
    assert pending.amount == Decimal("100")


def test_persist_phase_c_with_settings_persists_new_checkpoint_on_first_alert(
    monkeypatch,
) -> None:
    """`settings` fornecido (produção, via `CollectionOrchestrator`) --
    monta `checkpoint`/`realert_window`/`material_policy`, chama o
    evaluator de verdade e, quando ele gera candidatos, faz upsert de
    `MissionProductAlertState` (sem checkpoint anterior: `best_notified_
    amount` nasce do preço atual)."""
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
    current_criteria = SimpleNamespace(
        request_kind="generic_category",
        variant_selection_mode=VariantSelectionMode.NOT_REQUIRED,
        target_amount=None,
        target_currency=None,
    )
    session = _mock_async_session()
    session.scalar.side_effect = [mission, run, current_criteria]
    session.get.return_value = None  # checkpoint_row: nenhum ainda
    session.execute = AsyncMock()
    monkeypatch.setattr(
        "app.collection.orchestration.finish_collection_run", AsyncMock()
    )
    monkeypatch.setattr(
        "app.collection.orchestration._reset_source_backoff", AsyncMock()
    )
    monkeypatch.setattr(
        "app.collection.orchestration._evaluate_mission_prelist", AsyncMock()
    )
    monkeypatch.setattr("app.collection.orchestration.publish_event_async", AsyncMock())
    monkeypatch.setattr(
        "app.collection.orchestration.resolve_realert_window",
        AsyncMock(return_value=timedelta(hours=168)),
    )
    candidate = PriceAlertCandidate(
        event_type=EventType.PRICE_DECREASED_V1,
        aggregate_type=AggregateType.OFFER,
        aggregate_id=offer_id,
        payload=PriceDecreasedPayload(
            offer_id=offer_id,
            observation_id=uuid4(),
            previous_observation_id=uuid4(),
            previous_total=Decimal("150.00"),
            current_total=Decimal("100.00"),
            currency="BRL",
        ),
    )
    evaluator = MagicMock(return_value=(candidate,))
    monkeypatch.setattr("app.collection.orchestration.evaluate_price_alerts", evaluator)
    settings = SimpleNamespace(
        material_improvement_percent=0.01,
        material_improvement_min_amount=2.00,
        material_improvement_max_amount=50.00,
        rearm_rise_percent=0.05,
    )
    pending = _PendingOffer(
        offer_id=offer_id,
        product_id=product_id,
        observation_id=uuid4(),
        amount=Decimal("100.00"),
        currency="BRL",
        availability=Availability.AVAILABLE,
        observed_at=NOW,
        raw_title="Título bruto",
        needs_relevance=False,
        needs_display_name=False,
        observation_created=True,
        alert_comparison=PriceObservationComparison.CHANGED,
        forced_relevance=OfferRelevance.MATCH,
        previous_observation_id=uuid4(),
        previous_amount=Decimal("150.00"),
        previous_currency="BRL",
        previous_availability=Availability.AVAILABLE,
        previous_observed_at=NOW - timedelta(days=1),
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

    result = asyncio.run(
        _persist_phase_c(_session_factory(session), outcome, (), settings=settings)
    )

    assert result is True
    evaluator.assert_called_once()
    upsert_params = next(
        call.args[0].compile().params
        for call in session.execute.call_args_list
        if "best_notified_amount" in call.args[0].compile().params
    )
    assert upsert_params["best_notified_amount"] == Decimal("100.00")
    assert upsert_params["last_notified_amount"] == Decimal("100.00")


def test_persist_phase_c_with_settings_rearms_checkpoint_when_no_alert_but_should_rearm(
    monkeypatch,
) -> None:
    """Checkpoint já existente, evaluator não gera candidatos desta vez,
    mas `should_rearm` diz que o preço subiu o suficiente para permitir um
    futuro re-alert (§33.8) -- só `rearmed_at`/`updated_at` avançam, sem
    publicar nada nem tocar `best_notified_amount`/`last_notified_amount`."""
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
    current_criteria = SimpleNamespace(
        request_kind="generic_category",
        variant_selection_mode=VariantSelectionMode.NOT_REQUIRED,
        target_amount=None,
        target_currency=None,
    )
    checkpoint_row = SimpleNamespace(
        best_notified_amount=Decimal("90.00"),
        last_notified_amount=Decimal("90.00"),
        last_notified_at=NOW - timedelta(days=10),
        rearmed_at=None,
        updated_at=NOW - timedelta(days=10),
    )
    session = _mock_async_session()
    session.scalar.side_effect = [mission, run, current_criteria]
    session.get.return_value = checkpoint_row
    session.execute = AsyncMock()
    monkeypatch.setattr(
        "app.collection.orchestration.finish_collection_run", AsyncMock()
    )
    monkeypatch.setattr(
        "app.collection.orchestration._reset_source_backoff", AsyncMock()
    )
    monkeypatch.setattr(
        "app.collection.orchestration._evaluate_mission_prelist", AsyncMock()
    )
    monkeypatch.setattr("app.collection.orchestration.publish_event_async", AsyncMock())
    monkeypatch.setattr(
        "app.collection.orchestration.resolve_realert_window",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.evaluate_price_alerts",
        MagicMock(return_value=()),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.should_rearm", MagicMock(return_value=True)
    )
    settings = SimpleNamespace(
        material_improvement_percent=0.01,
        material_improvement_min_amount=2.00,
        material_improvement_max_amount=50.00,
        rearm_rise_percent=0.05,
    )
    pending = _PendingOffer(
        offer_id=offer_id,
        product_id=product_id,
        observation_id=uuid4(),
        amount=Decimal("110.00"),
        currency="BRL",
        availability=Availability.AVAILABLE,
        observed_at=NOW,
        raw_title="Título bruto",
        needs_relevance=False,
        needs_display_name=False,
        observation_created=True,
        alert_comparison=PriceObservationComparison.CHANGED,
        forced_relevance=OfferRelevance.MATCH,
        previous_observation_id=uuid4(),
        previous_amount=Decimal("90.00"),
        previous_currency="BRL",
        previous_availability=Availability.AVAILABLE,
        previous_observed_at=NOW - timedelta(days=1),
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

    result = asyncio.run(
        _persist_phase_c(_session_factory(session), outcome, (), settings=settings)
    )

    assert result is True
    assert checkpoint_row.rearmed_at == NOW
    assert checkpoint_row.updated_at == NOW
    # Nenhum upsert de MissionProductAlertState -- checkpoint mutado
    # in-place, sem candidatos, sem novo `best_notified_amount`.
    assert not any(
        "best_notified_amount" in call.args[0].compile().params
        for call in session.execute.call_args_list
        if hasattr(call.args[0], "compile")
    )


def test_persist_phase_c_without_applicable_coupon_uses_original_amount_for_alert(
    monkeypatch,
) -> None:
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
    product = SimpleNamespace(display_name=None, identity_key="existing-key")
    session.get.return_value = product
    monkeypatch.setattr(
        "app.collection.orchestration.finish_collection_run", AsyncMock()
    )
    monkeypatch.setattr(
        "app.collection.orchestration._reset_source_backoff", AsyncMock()
    )
    monkeypatch.setattr(
        "app.collection.orchestration._evaluate_mission_prelist", AsyncMock()
    )
    monkeypatch.setattr("app.collection.orchestration.publish_event_async", AsyncMock())
    evaluator = MagicMock(return_value=())
    monkeypatch.setattr("app.collection.orchestration.evaluate_price_alerts", evaluator)
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
    # applied_coupon=None -- nenhum cupom aplicável (comportamento padrão,
    # sem regressão para o fluxo já existente sem cupons).
    ai_outcomes = (_AIOutcome(offer_id, OfferRelevance.MATCH, "Título normalizado"),)

    result = asyncio.run(
        _persist_phase_c(_session_factory(session), outcome, ai_outcomes)
    )

    assert result is True
    evaluator.assert_called_once()
    current = evaluator.call_args.args[2]
    assert current.amount == Decimal("100")


def test_persist_phase_c_passes_coupon_snapshot_matching_current_total_to_evaluator(
    monkeypatch,
) -> None:
    """Correção 2026-09-06: o evaluator precisa RECEBER o snapshot do
    cupom que produziu `current.amount` -- é isso que depois vira
    `PriceDecreasedPayload.coupon`/`PriceTargetReachedPayload.coupon`, o
    que o Telegram vai usar para não precisar buscar de novo (ver
    `tests/test_telegram_notifications.py`)."""
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
    product = SimpleNamespace(display_name=None, identity_key="existing-key")
    session.get.return_value = product
    monkeypatch.setattr(
        "app.collection.orchestration.finish_collection_run", AsyncMock()
    )
    monkeypatch.setattr(
        "app.collection.orchestration._reset_source_backoff", AsyncMock()
    )
    monkeypatch.setattr(
        "app.collection.orchestration._evaluate_mission_prelist", AsyncMock()
    )
    monkeypatch.setattr("app.collection.orchestration.publish_event_async", AsyncMock())
    evaluator = MagicMock(return_value=())
    monkeypatch.setattr("app.collection.orchestration.evaluate_price_alerts", evaluator)
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
    applied_coupon = AppliedCoupon(
        coupon_id=uuid4(),
        code="PROMO20",
        discount_kind="fixed_amount",
        original_amount=Decimal("100"),
        discount_amount=Decimal("20"),
        final_amount=Decimal("80"),
        currency="BRL",
        raw_rule_text="Válido só para compras acima de R$50",
    )
    ai_outcomes = (
        _AIOutcome(
            offer_id,
            OfferRelevance.MATCH,
            "Título normalizado",
            applied_coupon=applied_coupon,
        ),
    )

    result = asyncio.run(
        _persist_phase_c(_session_factory(session), outcome, ai_outcomes)
    )

    assert result is True
    evaluator.assert_called_once()
    current = evaluator.call_args.args[2]
    snapshot = evaluator.call_args.kwargs["coupon"]
    assert snapshot is not None
    assert snapshot.coupon_id == applied_coupon.coupon_id
    assert snapshot.code == "PROMO20"
    assert snapshot.final_amount == current.amount == Decimal("80")
    assert snapshot.raw_rule_text == "Válido só para compras acima de R$50"


def test_persist_phase_c_without_coupon_passes_no_snapshot_to_evaluator(
    monkeypatch,
) -> None:
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
    product = SimpleNamespace(display_name=None, identity_key="existing-key")
    session.get.return_value = product
    monkeypatch.setattr(
        "app.collection.orchestration.finish_collection_run", AsyncMock()
    )
    monkeypatch.setattr(
        "app.collection.orchestration._reset_source_backoff", AsyncMock()
    )
    monkeypatch.setattr(
        "app.collection.orchestration._evaluate_mission_prelist", AsyncMock()
    )
    monkeypatch.setattr("app.collection.orchestration.publish_event_async", AsyncMock())
    evaluator = MagicMock(return_value=())
    monkeypatch.setattr("app.collection.orchestration.evaluate_price_alerts", evaluator)
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

    asyncio.run(_persist_phase_c(_session_factory(session), outcome, ai_outcomes))

    evaluator.assert_called_once()
    assert evaluator.call_args.kwargs["coupon"] is None


def test_run_phase_b_feeds_f2_gate_with_coupon_final_amount(monkeypatch) -> None:
    """Cenário 10 (auditoria 2026-09-06): F2 (`evaluate_trigger_and_
    maybe_research`) precisa receber EXATAMENTE o preço final do cupom
    vencedor -- reaproveita o `best_applicable_coupon` real (não
    mockado), já coberto por `tests/test_coupons_pricing.py`; este teste
    só verifica a fiação até o gatilho."""
    offer_id, product_id, store_id = uuid4(), uuid4(), uuid4()
    pending = _PendingOffer(
        offer_id=offer_id,
        product_id=product_id,
        observation_id=uuid4(),
        amount=Decimal("300.00"),
        currency="BRL",
        availability=Availability.AVAILABLE,
        observed_at=NOW,
        raw_title="Título bruto",
        needs_relevance=False,
        needs_display_name=False,
        observation_created=True,
        alert_comparison=PriceObservationComparison.FIRST_OBSERVATION,
        previous_observation_id=None,
        previous_amount=None,
        previous_currency=None,
        previous_availability=None,
        previous_observed_at=None,
        forced_relevance=OfferRelevance.MATCH,
    )
    outcome = _PhaseAOutcome(
        run_id=uuid4(),
        mission_id=uuid4(),
        store_id=store_id,
        mission_search_query="GPU",
        target_amount=None,
        target_currency=None,
        completed_at=NOW,
        offers=(pending,),
    )
    offer_row = Offer(
        id=offer_id,
        product_id=product_id,
        store_id=store_id,
        url="https://loja.example/produto",
    )
    session = _mock_async_session()
    session.get.return_value = offer_row
    coupon = Coupon(
        id=uuid4(),
        store_id=store_id,
        code="F2CUP",
        discount_kind="fixed_amount",
        discount_value=Decimal("30.00"),
        scope_kind="store_wide",
        evidence="ev",
        status="active",
        last_seen_at=NOW,
    )
    monkeypatch.setattr(
        "app.collection.orchestration.get_candidate_coupons_for_offer",
        AsyncMock(return_value=(coupon,)),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.run_historical_bootstrap", AsyncMock()
    )
    trigger = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "app.collection.orchestration.evaluate_trigger_and_maybe_research", trigger
    )
    settings = SimpleNamespace(
        historical_bootstrap_revalidation_days=90,
        market_assessment_lease_seconds=300,
        market_assessment_failure_backoff_minutes=15,
        market_assessment_failure_backoff_max_minutes=360,
        # FASE G: este teste é sobre a fiação cupom -> F2, não sobre F1 --
        # F1 fica desligada (comportamento default) e mockada de qualquer
        # forma; cupons precisa estar ligada para exercitar o caminho.
        historical_bootstrap_enabled=False,
        coupons_enabled=True,
    )

    outcomes = asyncio.run(
        _run_phase_b(
            outcome,
            _StubAIManager(),
            UserRole.ADMIN,
            session_factory=_session_factory(session),
            firecrawl=None,
            settings=settings,
        )
    )

    trigger.assert_awaited_once()
    assert trigger.call_args.kwargs["current_amount"] == Decimal("270.00")
    assert outcomes[0].applied_coupon is not None
    assert outcomes[0].applied_coupon.final_amount == Decimal("270.00")


def test_run_phase_b_flags_off_never_touches_coupons_or_historical_bootstrap(
    monkeypatch,
) -> None:
    """FASE G: prova de paridade com o fluxo anterior -- `coupons_enabled`
    e `historical_bootstrap_enabled` desligadas (default de produção) não
    consultam cupom nem rodam o bootstrap histórico; F2 recebe o preço
    original, exatamente como antes de F1/cupons existirem."""
    offer_id, product_id, store_id = uuid4(), uuid4(), uuid4()
    pending = _PendingOffer(
        offer_id=offer_id,
        product_id=product_id,
        observation_id=uuid4(),
        amount=Decimal("300.00"),
        currency="BRL",
        availability=Availability.AVAILABLE,
        observed_at=NOW,
        raw_title="Título bruto",
        needs_relevance=False,
        needs_display_name=False,
        observation_created=True,
        alert_comparison=PriceObservationComparison.FIRST_OBSERVATION,
        previous_observation_id=None,
        previous_amount=None,
        previous_currency=None,
        previous_availability=None,
        previous_observed_at=None,
        forced_relevance=OfferRelevance.MATCH,
    )
    outcome = _PhaseAOutcome(
        run_id=uuid4(),
        mission_id=uuid4(),
        store_id=store_id,
        mission_search_query="GPU",
        target_amount=None,
        target_currency=None,
        completed_at=NOW,
        offers=(pending,),
    )
    session = _mock_async_session()
    coupon_query = AsyncMock(side_effect=AssertionError("não deveria consultar cupons"))
    monkeypatch.setattr(
        "app.collection.orchestration.get_candidate_coupons_for_offer", coupon_query
    )
    bootstrap = AsyncMock(side_effect=AssertionError("não deveria rodar o bootstrap"))
    monkeypatch.setattr(
        "app.collection.orchestration.run_historical_bootstrap", bootstrap
    )
    trigger = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "app.collection.orchestration.evaluate_trigger_and_maybe_research", trigger
    )
    settings = SimpleNamespace(
        historical_bootstrap_revalidation_days=90,
        market_assessment_lease_seconds=300,
        market_assessment_failure_backoff_minutes=15,
        market_assessment_failure_backoff_max_minutes=360,
        historical_bootstrap_enabled=False,
        coupons_enabled=False,
    )

    outcomes = asyncio.run(
        _run_phase_b(
            outcome,
            _StubAIManager(),
            UserRole.ADMIN,
            session_factory=_session_factory(session),
            firecrawl=None,
            settings=settings,
        )
    )

    coupon_query.assert_not_called()
    bootstrap.assert_not_called()
    trigger.assert_awaited_once()
    assert trigger.call_args.kwargs["current_amount"] == Decimal("300.00")
    assert outcomes[0].applied_coupon is None


def _phase_b_pending_and_outcome(
    *, offer_id, product_id, store_id, **overrides
) -> tuple[_PendingOffer, _PhaseAOutcome]:
    defaults = dict(
        offer_id=offer_id,
        product_id=product_id,
        observation_id=uuid4(),
        amount=Decimal("300.00"),
        currency="BRL",
        availability=Availability.AVAILABLE,
        observed_at=NOW,
        raw_title="Título bruto",
        needs_relevance=False,
        needs_display_name=False,
        observation_created=True,
        alert_comparison=PriceObservationComparison.FIRST_OBSERVATION,
        previous_observation_id=None,
        previous_amount=None,
        previous_currency=None,
        previous_availability=None,
        previous_observed_at=None,
        forced_relevance=OfferRelevance.MATCH,
    )
    defaults.update(overrides)
    pending = _PendingOffer(**defaults)
    outcome = _PhaseAOutcome(
        run_id=uuid4(),
        mission_id=uuid4(),
        store_id=store_id,
        mission_search_query="GPU",
        target_amount=None,
        target_currency=None,
        completed_at=NOW,
        offers=(pending,),
    )
    return pending, outcome


def test_run_phase_b_coupon_lookup_failure_keeps_original_amount(monkeypatch) -> None:
    """Coroa a disciplina de `coupon_evaluation_failed`: uma falha de
    infraestrutura na consulta de cupons nunca derruba o processamento
    normal da oferta -- F2 segue com o preço original, como se nenhum
    cupom tivesse sido encontrado."""
    offer_id, product_id, store_id = uuid4(), uuid4(), uuid4()
    pending, outcome = _phase_b_pending_and_outcome(
        offer_id=offer_id, product_id=product_id, store_id=store_id
    )
    offer_row = Offer(
        id=offer_id,
        product_id=product_id,
        store_id=store_id,
        url="https://loja.example/produto",
    )
    session = _mock_async_session()
    session.get.return_value = offer_row
    monkeypatch.setattr(
        "app.collection.orchestration.get_candidate_coupons_for_offer",
        AsyncMock(side_effect=RuntimeError("timeout no coupon service")),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.run_historical_bootstrap", AsyncMock()
    )
    trigger = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "app.collection.orchestration.evaluate_trigger_and_maybe_research", trigger
    )
    settings = SimpleNamespace(
        historical_bootstrap_revalidation_days=90,
        market_assessment_lease_seconds=300,
        market_assessment_failure_backoff_minutes=15,
        market_assessment_failure_backoff_max_minutes=360,
        historical_bootstrap_enabled=False,
        coupons_enabled=True,
    )

    outcomes = asyncio.run(
        _run_phase_b(
            outcome,
            _StubAIManager(),
            UserRole.ADMIN,
            session_factory=_session_factory(session),
            firecrawl=None,
            settings=settings,
        )
    )

    trigger.assert_awaited_once()
    assert trigger.call_args.kwargs["current_amount"] == Decimal("300.00")
    assert outcomes[0].applied_coupon is None


def test_run_phase_b_runs_historical_bootstrap_when_enabled(monkeypatch) -> None:
    offer_id, product_id, store_id = uuid4(), uuid4(), uuid4()
    pending, outcome = _phase_b_pending_and_outcome(
        offer_id=offer_id, product_id=product_id, store_id=store_id
    )
    session = _mock_async_session()
    bootstrap = AsyncMock()
    monkeypatch.setattr(
        "app.collection.orchestration.run_historical_bootstrap", bootstrap
    )
    monkeypatch.setattr(
        "app.collection.orchestration.evaluate_trigger_and_maybe_research",
        AsyncMock(return_value=None),
    )
    settings = SimpleNamespace(
        historical_bootstrap_revalidation_days=90,
        market_assessment_lease_seconds=300,
        market_assessment_failure_backoff_minutes=15,
        market_assessment_failure_backoff_max_minutes=360,
        historical_bootstrap_enabled=True,
        coupons_enabled=False,
    )

    asyncio.run(
        _run_phase_b(
            outcome,
            _StubAIManager(),
            UserRole.ADMIN,
            session_factory=_session_factory(session),
            firecrawl="firecrawl-stub",
            settings=settings,
        )
    )

    bootstrap.assert_awaited_once()
    assert bootstrap.call_args.kwargs["product_id"] == product_id
    assert bootstrap.call_args.kwargs["fetch"] == "firecrawl-stub"
    assert bootstrap.call_args.kwargs["revalidation_days"] == 90


def test_run_phase_b_uses_cached_relevance_when_not_forced_nor_reclassified(
    monkeypatch,
) -> None:
    """Oferta já classificada em ciclo ANTERIOR (nem forçada nesta
    rodada, nem precisando de reclassificação agora) -- só pode ter
    virado MATCH via `MissionOfferRelevance` de um ciclo passado; sem
    reler aqui, pesquisa de mercado nunca dispararia para o caso mais
    comum (missão rodando há semanas)."""
    offer_id, product_id, store_id = uuid4(), uuid4(), uuid4()
    pending, outcome = _phase_b_pending_and_outcome(
        offer_id=offer_id,
        product_id=product_id,
        store_id=store_id,
        needs_relevance=False,
        forced_relevance=None,
    )
    session = _mock_async_session()
    session.get.return_value = SimpleNamespace(classification=OfferRelevance.MATCH)
    monkeypatch.setattr(
        "app.collection.orchestration.run_historical_bootstrap", AsyncMock()
    )
    trigger = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "app.collection.orchestration.evaluate_trigger_and_maybe_research", trigger
    )
    settings = SimpleNamespace(
        historical_bootstrap_revalidation_days=90,
        market_assessment_lease_seconds=300,
        market_assessment_failure_backoff_minutes=15,
        market_assessment_failure_backoff_max_minutes=360,
        historical_bootstrap_enabled=False,
        coupons_enabled=False,
    )

    outcomes = asyncio.run(
        _run_phase_b(
            outcome,
            _StubAIManager(),
            UserRole.ADMIN,
            session_factory=_session_factory(session),
            firecrawl=None,
            settings=settings,
        )
    )

    session.get.assert_awaited_once_with(
        MissionOfferRelevance, (outcome.mission_id, pending.offer_id)
    )
    trigger.assert_awaited_once()
    # `relevance` (persistida em `_AIOutcome`) permanece intocada -- só a
    # decisão INTERNA de disparar pesquisa de mercado usa o cache.
    assert outcomes[0].relevance is None


def test_run_phase_b_identity_learning_success_updates_outcome(monkeypatch) -> None:
    offer_id, product_id, store_id = uuid4(), uuid4(), uuid4()
    pending, outcome = _phase_b_pending_and_outcome(
        offer_id=offer_id,
        product_id=product_id,
        store_id=store_id,
        identity_unresolved=True,
    )
    session = _mock_async_session()
    session.commit = AsyncMock()
    learned = SimpleNamespace(identity_key="v1:learned")
    monkeypatch.setattr(
        "app.collection.orchestration.resolve_or_learn_product_variant",
        AsyncMock(return_value=learned),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.evaluate_trigger_and_maybe_research",
        AsyncMock(return_value=None),
    )
    settings = SimpleNamespace(
        historical_bootstrap_revalidation_days=90,
        market_assessment_lease_seconds=300,
        market_assessment_failure_backoff_minutes=15,
        market_assessment_failure_backoff_max_minutes=360,
        historical_bootstrap_enabled=False,
        coupons_enabled=False,
        product_identity_learning_enabled=True,
    )

    outcomes = asyncio.run(
        _run_phase_b(
            outcome,
            _StubAIManager(),
            UserRole.ADMIN,
            session_factory=_session_factory(session),
            firecrawl=None,
            settings=settings,
            arbiter_ai_manager=_StubAIManager(),
        )
    )

    assert outcomes[0].learned_identity is learned
    session.commit.assert_awaited_once()


def test_run_phase_b_identity_learning_failure_is_isolated(monkeypatch) -> None:
    offer_id, product_id, store_id = uuid4(), uuid4(), uuid4()
    pending, outcome = _phase_b_pending_and_outcome(
        offer_id=offer_id,
        product_id=product_id,
        store_id=store_id,
        identity_unresolved=True,
    )
    session = _mock_async_session()
    monkeypatch.setattr(
        "app.collection.orchestration.resolve_or_learn_product_variant",
        AsyncMock(side_effect=RuntimeError("provider indisponível")),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.evaluate_trigger_and_maybe_research",
        AsyncMock(return_value=None),
    )
    settings = SimpleNamespace(
        historical_bootstrap_revalidation_days=90,
        market_assessment_lease_seconds=300,
        market_assessment_failure_backoff_minutes=15,
        market_assessment_failure_backoff_max_minutes=360,
        historical_bootstrap_enabled=False,
        coupons_enabled=False,
        product_identity_learning_enabled=True,
    )

    outcomes = asyncio.run(
        _run_phase_b(
            outcome,
            _StubAIManager(),
            UserRole.ADMIN,
            session_factory=_session_factory(session),
            firecrawl=None,
            settings=settings,
            arbiter_ai_manager=_StubAIManager(),
        )
    )

    assert outcomes[0].learned_identity is None
    session.commit.assert_not_called()


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
    evaluator = MagicMock(
        side_effect=AssertionError("evaluator não deveria ser chamado")
    )
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
    session.execute.return_value = _fake_execute_result([(offer_id, store_id, NOW)])
    session.scalars.return_value = [offer_id]  # já tem MissionOfferRelevance

    pending = asyncio.run(_mission_relevance_pending(session, uuid4()))

    assert pending is False


def test_mission_relevance_pending_true_when_current_offer_lacks_relevance() -> None:
    store_id = uuid4()
    offer_id = uuid4()
    session = _mock_async_session()
    session.execute.return_value = _fake_execute_result([(offer_id, store_id, NOW)])
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
    previous = _prelist_candidate("100.00", condition=OfferCondition.USED, store=store)
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
        AsyncMock(return_value={store.id: _prelist_commercial_key(previous)}),
    )
    publish = AsyncMock()
    monkeypatch.setattr("app.collection.orchestration.publish_event_async", publish)

    asyncio.run(_maybe_publish_prelist_errata(session, mission, NOW))

    assert mission.prelist_errata_sent is False
    publish.assert_not_awaited()


# ---------------------------------------------------------------------------
# Cauda de branches de orchestration.py (checkpoint 8, fechamento final)
# ---------------------------------------------------------------------------


def test_apply_rating_snapshot_updates_pair_when_both_fields_present() -> None:
    offer = SimpleNamespace(
        rating_average=None, review_count=None, rating_observed_at=None
    )
    item = SimpleNamespace(
        rating_average=Decimal("4.7"),
        review_count=812,
        raw_offer=SimpleNamespace(collected_at=NOW),
    )
    _apply_rating_snapshot(offer, item)
    assert offer.rating_average == Decimal("4.7")
    assert offer.review_count == 812
    assert offer.rating_observed_at == NOW


def test_publish_failure_publishes_collection_failed_event(monkeypatch) -> None:
    session = _mock_async_session()
    run = SimpleNamespace(id=uuid4(), store_id=uuid4(), mission_id=uuid4())
    publish = AsyncMock()
    monkeypatch.setattr("app.collection.orchestration.publish_event_async", publish)

    asyncio.run(_publish_failure(session, run, "provider_blocked", NOW))

    publish.assert_awaited_once()
    kwargs = publish.call_args.kwargs
    assert kwargs["event_type"] is EventType.COLLECTION_FAILED_V1
    assert kwargs["mission_id"] == run.mission_id
    payload = kwargs["payload"]
    assert isinstance(payload, CollectionFailedPayload)
    assert payload.collection_run_id == run.id
    assert payload.store_id == run.store_id
    assert payload.failure_code == "provider_blocked"


def test_apply_source_backoff_noop_when_source_missing() -> None:
    session = _mock_async_session()
    session.get.return_value = None

    asyncio.run(_apply_source_backoff(session, uuid4(), uuid4(), NOW))

    session.scalar.assert_not_awaited()


def test_apply_source_backoff_noop_when_schedule_interval_missing() -> None:
    session = _mock_async_session()
    source = SimpleNamespace(consecutive_blocks=0, next_eligible_at=None)
    session.get.return_value = source
    session.scalar.return_value = None

    asyncio.run(_apply_source_backoff(session, uuid4(), uuid4(), NOW))

    assert source.consecutive_blocks == 0
    assert source.next_eligible_at is None


def test_reset_source_backoff_noop_when_source_missing() -> None:
    session = _mock_async_session()
    session.get.return_value = None

    asyncio.run(_reset_source_backoff(session, uuid4(), uuid4()))

    session.flush.assert_not_awaited()


def test_reset_source_backoff_noop_when_already_reset() -> None:
    session = _mock_async_session()
    source = SimpleNamespace(consecutive_blocks=0, next_eligible_at=None)
    session.get.return_value = source

    asyncio.run(_reset_source_backoff(session, uuid4(), uuid4()))

    assert source.consecutive_blocks == 0
    assert source.next_eligible_at is None


def test_evaluate_mission_prelist_noop_when_criteria_missing(monkeypatch) -> None:
    mission = SimpleNamespace(id=uuid4(), status=MissionStatus.ACTIVE)
    session = _mock_async_session()
    session.scalar.side_effect = [mission, None]
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


def test_evaluate_mission_prelist_dispatches_variant_choices_when_pending(
    monkeypatch,
) -> None:
    mission = SimpleNamespace(id=uuid4(), status=MissionStatus.ACTIVE)
    criteria = SimpleNamespace(
        request_kind=ProductRequestKind.PRODUCT_FAMILY.value,
        variant_selection_mode=VariantSelectionMode.PENDING,
    )
    session = _mock_async_session()
    session.scalar.side_effect = [mission, criteria]
    variant_choices = AsyncMock()
    monkeypatch.setattr(
        "app.collection.orchestration._maybe_publish_variant_choices", variant_choices
    )
    ready = AsyncMock()
    monkeypatch.setattr(
        "app.collection.orchestration._maybe_publish_prelist_ready", ready
    )

    asyncio.run(_evaluate_mission_prelist(session, mission.id, NOW))

    variant_choices.assert_awaited_once_with(session, mission, criteria, NOW)
    ready.assert_not_awaited()


def test_mission_prelist_round_complete_false_without_any_source() -> None:
    session = _mock_async_session()
    session.scalar.return_value = 0

    complete = asyncio.run(_mission_prelist_round_complete(session, uuid4()))

    assert complete is False


def test_mission_prelist_round_complete_true_when_every_source_finished() -> None:
    session = _mock_async_session()
    session.scalar.side_effect = [2, 2]

    complete = asyncio.run(_mission_prelist_round_complete(session, uuid4()))

    assert complete is True


def test_mission_prelist_round_complete_false_when_some_source_still_running() -> None:
    session = _mock_async_session()
    session.scalar.side_effect = [2, 1]

    complete = asyncio.run(_mission_prelist_round_complete(session, uuid4()))

    assert complete is False


def test_mission_relevance_pending_false_when_no_offer_rows() -> None:
    session = _mock_async_session()
    session.execute.return_value = _fake_execute_result([])

    pending = asyncio.run(_mission_relevance_pending(session, uuid4()))

    assert pending is False
    session.scalars.assert_not_awaited()


def test_rank_prelist_candidates_caps_per_store_limit() -> None:
    store = SimpleNamespace(id=uuid4(), code="amazon")
    candidates = tuple(
        _prelist_candidate(f"{100 + index}.00", store=store) for index in range(7)
    )

    ranked = rank_prelist_candidates(candidates)

    assert len(ranked) == 5


def test_current_prelist_candidates_filters_by_family_variant_and_selection() -> None:
    mission_id = uuid4()
    store = SimpleNamespace(id=uuid4(), code="amazon")
    kept_offer = SimpleNamespace(
        id=uuid4(), store_id=store.id, product_id=uuid4(), last_seen_at=NOW
    )
    dropped_offer = SimpleNamespace(
        id=uuid4(), store_id=store.id, product_id=uuid4(), last_seen_at=NOW
    )
    relevance = SimpleNamespace(classification=OfferRelevance.MATCH)

    def _observation(amount: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=uuid4(),
            amount=Decimal(amount),
            total_amount=Decimal(amount),
            currency="BRL",
            condition=OfferCondition.NEW,
            seller_kind=MarketplacePartyKind.PLATFORM,
            availability=Availability.AVAILABLE,
        )

    session = _mock_async_session()
    session.execute.return_value = _fake_execute_result(
        [
            (relevance, _observation("100.00"), kept_offer, store),
            (relevance, _observation("90.00"), dropped_offer, store),
        ]
    )
    criteria = SimpleNamespace(
        request_kind=ProductRequestKind.PRODUCT_FAMILY.value,
        variant_selection_mode=VariantSelectionMode.SELECTED,
        requested_family_key="family-x",
        requested_variant="256GB",
    )
    session.scalar.return_value = criteria
    session.scalars.side_effect = [
        [kept_offer.product_id, dropped_offer.product_id],
        [kept_offer.product_id],
    ]

    candidates = asyncio.run(_current_prelist_candidates(session, mission_id))

    assert [candidate.offer.id for candidate in candidates] == [kept_offer.id]


def test_maybe_publish_prelist_ready_marks_sent_without_publishing_when_no_candidates(
    monkeypatch,
) -> None:
    mission = SimpleNamespace(
        id=uuid4(), prelist_sent=False, prelist_lowest_amount=None
    )
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
        AsyncMock(return_value=()),
    )
    publish = AsyncMock()
    monkeypatch.setattr("app.collection.orchestration.publish_event_async", publish)

    asyncio.run(_maybe_publish_prelist_ready(_mock_async_session(), mission, NOW))

    assert mission.prelist_sent is True
    publish.assert_not_awaited()


def test_maybe_publish_prelist_errata_noop_when_no_previous_event() -> None:
    mission = SimpleNamespace(id=uuid4(), prelist_errata_sent=False)
    session = _mock_async_session()
    session.scalar.return_value = None

    asyncio.run(_maybe_publish_prelist_errata(session, mission, NOW))

    assert mission.prelist_errata_sent is False
    session.get.assert_not_awaited()


def test_previous_prelist_best_by_store_skips_malformed_legacy_v1_references() -> None:
    event = SimpleNamespace(
        mission_id=uuid4(),
        event_type=EventType.MISSION_PRELIST_READY_V1.value,
        payload={
            "first_offer_id": "not-a-uuid",
            "first_observation_id": str(uuid4()),
            "second_offer_id": None,
        },
    )
    session = _mock_async_session()

    result = asyncio.run(_previous_prelist_best_by_store(session, event))

    assert result == {}
    session.get.assert_not_awaited()


def test_failure_log_context_marks_integrity_error_as_persistence_stage() -> None:
    error = IntegrityError("insert", {}, Exception("duplicate key"))

    context = _failure_log_context(error)

    assert context["failure_stage"] == "persistence"


def test_constraint_name_reads_diagnostic_from_orig() -> None:
    orig = SimpleNamespace(
        diag=SimpleNamespace(constraint_name="ux_offers_store_seller_external")
    )
    error = IntegrityError("insert", {}, orig)

    assert _constraint_name(error) == "ux_offers_store_seller_external"


def test_constraint_name_returns_none_without_diagnostic() -> None:
    error = IntegrityError("insert", {}, Exception("no diag attribute"))

    assert _constraint_name(error) is None


def test_sanitize_json_truncates_beyond_max_depth() -> None:
    nested = {"a": {"b": {"c": {"d": {"e": 1}}}}}

    assert _sanitize_json(nested) == {"a": {"b": {"c": {"d": "truncated"}}}}


def test_sanitize_json_passes_through_scalars_and_none() -> None:
    assert _sanitize_json(None) is None
    assert _sanitize_json(True) is True
    assert _sanitize_json(42) == 42
    assert _sanitize_json(3.14) == 3.14


def test_sanitize_json_bounds_sequences_and_falls_back_to_str_repr() -> None:
    class _Custom:
        def __str__(self) -> str:
            return "custom-object-repr"

    assert _sanitize_json([1, 2, 3]) == [1, 2, 3]
    assert _sanitize_json((1, 2)) == [1, 2]
    assert _sanitize_json(_Custom()) == "custom-object-repr"


# ---------------------------------------------------------------------------
# CollectionOrchestrator.__init__ -- validação de parâmetros
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"schedule_interval_minutes": 0},
        {"schedule_stagger_seconds": -1},
        {"stale_run_minutes": 0},
        {"max_concurrency": 0},
        {"max_concurrency": 5},
        {"claim_deadline_seconds": 0},
        {"max_concurrent_user_batches": 0},
        {"user_cooldown_min_seconds": -1},
        {"user_cooldown_max_seconds": -1},
        {"user_cooldown_min_seconds": 100, "user_cooldown_max_seconds": 10},
        {"store_min_interval_seconds": -1},
        {"candidate_scan_limit": 0},
        {"fan_out_target_scan_limit": 0},
        {"fan_out_task_budget": 0},
        {"fan_out_per_target_task_cap": 0},
        {"fan_out_concurrency": 0},
    ],
)
def test_orchestrator_init_rejects_invalid_parameters(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        CollectionOrchestrator(
            _session_factory(_mock_async_session()),
            CollectionAdapter(),
            ai_manager=_StubAIManager(),
            **kwargs,
        )


def test_orchestrator_init_accepts_zero_user_cooldown() -> None:
    """TASK-108: `0` é aceito no cooldown de usuário/throttle de loja só
    quando o `CollectionOrchestrator` é construído direto (scripts/testes) --
    `Settings`/override ADMIN exigem `> 0` em outra camada."""
    orchestrator = CollectionOrchestrator(
        _session_factory(_mock_async_session()),
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
        user_cooldown_min_seconds=0,
        user_cooldown_max_seconds=0,
        store_min_interval_seconds=0,
    )
    assert orchestrator is not None


# ---------------------------------------------------------------------------
# _preview_existing_offer_and_product / _creation_lock_keys (Subtask 6)
# ---------------------------------------------------------------------------


def test_preview_existing_offer_and_product_resolves_seller_by_external_id() -> None:
    """`item.seller_external_id` presente -- busca o `Seller` por
    `(store_id, external_id)` primeiro, e usa o `seller_id` resolvido (não
    `None`) na busca subsequente da `Offer`."""
    store_id = uuid4()
    seller = SimpleNamespace(id=uuid4())
    offer = SimpleNamespace(id=uuid4(), product_id=uuid4())
    product = SimpleNamespace(id=offer.product_id)
    session = _mock_async_session()
    session.scalar.side_effect = [seller, offer]
    session.get.return_value = product
    item = SimpleNamespace(
        seller_external_id="seller-123",
        raw_offer=SimpleNamespace(
            external_id="ext-1", url="https://example.invalid/offer"
        ),
    )

    found_offer, found_product = asyncio.run(
        _preview_existing_offer_and_product(session, store_id, item)
    )

    assert found_offer is offer
    assert found_product is product
    assert session.scalar.await_count == 2


def test_creation_lock_keys_includes_product_key_when_title_resolves_identity() -> None:
    """Título que `resolve_product_variant` reconhece -- a chave lógica de
    criação do `Product` (`uq_products_identity_key`) também entra no
    conjunto, além das chaves de Offer/Seller."""
    store_id = uuid4()
    item = SimpleNamespace(
        seller_external_id="seller-123",
        raw_offer=SimpleNamespace(
            external_id="ext-1",
            url="https://example.invalid/offer",
            title="Apple iPhone 17 Pro 256 GB",
        ),
    )

    keys = _creation_lock_keys(store_id, item)

    identity = resolve_product_variant("Apple iPhone 17 Pro 256 GB")
    assert identity is not None
    assert f"product:{identity.identity_key}" in keys
    assert f"seller:{store_id}:seller-123" in keys


# ---------------------------------------------------------------------------
# _title_looks_like_bundle / _filter_deterministic_candidates (TASK-075)
# ---------------------------------------------------------------------------


def test_title_looks_like_bundle_true_when_signal_word_absent_from_query() -> None:
    assert (
        _title_looks_like_bundle("RTX 4070", "PC Gamer completo com RTX 4070") is True
    )


def test_title_looks_like_bundle_false_when_signal_word_also_in_query() -> None:
    # "kit" também está na própria busca -- não é sinal de pacote maior.
    assert (
        _title_looks_like_bundle("kit teclado e mouse", "Kit teclado e mouse RGB")
        is False
    )


def test_title_looks_like_bundle_false_without_any_signal_word() -> None:
    assert _title_looks_like_bundle("RTX 4070", "Placa de vídeo RTX 4070 Ti") is False


def test_filter_deterministic_candidates_rejects_model_mismatch() -> None:
    criteria = SimpleNamespace(model="9800X3D", search_query="9800X3D")
    matching = SimpleNamespace(raw_offer=SimpleNamespace(title="AMD Ryzen 9800X3D"))
    mismatching = SimpleNamespace(raw_offer=SimpleNamespace(title="AMD Ryzen 7600"))

    survivors = _filter_deterministic_candidates(criteria, (matching, mismatching))

    assert survivors == (matching,)


def test_filter_deterministic_candidates_rejects_bundle_signal() -> None:
    criteria = SimpleNamespace(model=None, search_query="RTX 4070")
    standalone = SimpleNamespace(raw_offer=SimpleNamespace(title="Placa RTX 4070"))
    bundled = SimpleNamespace(
        raw_offer=SimpleNamespace(title="PC Gamer completo com RTX 4070")
    )

    survivors = _filter_deterministic_candidates(criteria, (standalone, bundled))

    assert survivors == (standalone,)


def test_filter_deterministic_candidates_keeps_ambiguous_offers() -> None:
    """Sem `criteria.model` e sem sinal de bundle -- candidato ambíguo
    sempre sobrevive, seguindo pro fluxo de relevância existente."""
    criteria = SimpleNamespace(model=None, search_query="RTX 4070")
    item = SimpleNamespace(raw_offer=SimpleNamespace(title="Placa de vídeo genérica"))

    survivors = _filter_deterministic_candidates(criteria, (item,))

    assert survivors == (item,)


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
    # TASK-112 fase 3B: `run_batch` passou a chamar `claim_due_work`
    # (scheduler unificado), nunca mais `claim_due_collections` direto --
    # este teste só exercita o caminho legado, então o batch retornado
    # tem `shared=()`.
    monkeypatch.setattr(
        "app.collection.orchestration.claim_due_work",
        AsyncMock(return_value=_ClaimedBatch(old_path=claims, shared=())),
    )
    orchestrator = CollectionOrchestrator(
        session_factory, CollectionAdapter(), ai_manager=_StubAIManager()
    )

    async def process(claim):
        return claim.source_code == "pichau"

    monkeypatch.setattr(orchestrator, "_process", process)
    result = asyncio.run(orchestrator.run_batch(now=NOW))
    assert (result.claimed, result.succeeded, result.failed) == (2, 1, 1)


def test_orchestrator_batch_logs_when_schedules_were_created(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    session_factory = _session_factory(_mock_async_session())
    monkeypatch.setattr(
        "app.collection.orchestration.ensure_missing_schedules",
        AsyncMock(return_value=3),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.recover_stale_runs", AsyncMock(return_value=0)
    )
    monkeypatch.setattr(
        "app.collection.orchestration.claim_due_work",
        AsyncMock(return_value=_ClaimedBatch(old_path=(), shared=())),
    )
    orchestrator = CollectionOrchestrator(
        session_factory, CollectionAdapter(), ai_manager=_StubAIManager()
    )

    with caplog.at_level("INFO", logger="app.collection.orchestration"):
        result = asyncio.run(orchestrator.run_batch(now=NOW))

    assert (result.claimed, result.succeeded, result.failed) == (0, 0, 0)
    assert any(
        record.message == "collection_schedules_created" for record in caplog.records
    )


def test_orchestrator_batch_notifies_coupon_worker_on_high_activity(
    monkeypatch,
) -> None:
    session_factory = _session_factory(_mock_async_session())
    monkeypatch.setattr(
        "app.collection.orchestration.ensure_missing_schedules",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.recover_stale_runs", AsyncMock(return_value=0)
    )
    monkeypatch.setattr(
        "app.collection.orchestration.claim_due_work",
        AsyncMock(
            return_value=_ClaimedBatch(
                old_path=(), shared=(), high_activity_detected=True
            )
        ),
    )
    notify = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "app.collection.orchestration.notify_coupon_worker_high_activity", notify
    )
    settings = SimpleNamespace()
    orchestrator = CollectionOrchestrator(
        session_factory,
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
        settings=settings,
    )

    result = asyncio.run(orchestrator.run_batch(now=NOW))

    assert (result.claimed, result.succeeded, result.failed) == (0, 0, 0)
    notify.assert_awaited_once_with(settings, now=NOW)


def test_orchestrator_batch_isolates_coupon_worker_notify_failure(
    monkeypatch,
) -> None:
    session_factory = _session_factory(_mock_async_session())
    monkeypatch.setattr(
        "app.collection.orchestration.ensure_missing_schedules",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.recover_stale_runs", AsyncMock(return_value=0)
    )
    monkeypatch.setattr(
        "app.collection.orchestration.claim_due_work",
        AsyncMock(
            return_value=_ClaimedBatch(
                old_path=(), shared=(), high_activity_detected=True
            )
        ),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.notify_coupon_worker_high_activity",
        AsyncMock(side_effect=RuntimeError("coupon worker indisponível")),
    )
    orchestrator = CollectionOrchestrator(
        session_factory,
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
        settings=SimpleNamespace(),
    )

    # Nunca deve vazar -- erro na notificação, best-effort, é isolado.
    result = asyncio.run(orchestrator.run_batch(now=NOW))
    assert (result.claimed, result.succeeded, result.failed) == (0, 0, 0)


def test_orchestrator_batch_skips_coupon_worker_notify_without_settings(
    monkeypatch,
) -> None:
    session_factory = _session_factory(_mock_async_session())
    monkeypatch.setattr(
        "app.collection.orchestration.ensure_missing_schedules",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.recover_stale_runs", AsyncMock(return_value=0)
    )
    monkeypatch.setattr(
        "app.collection.orchestration.claim_due_work",
        AsyncMock(
            return_value=_ClaimedBatch(
                old_path=(), shared=(), high_activity_detected=True
            )
        ),
    )
    notify = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "app.collection.orchestration.notify_coupon_worker_high_activity", notify
    )
    orchestrator = CollectionOrchestrator(
        session_factory, CollectionAdapter(), ai_manager=_StubAIManager()
    )

    asyncio.run(orchestrator.run_batch(now=NOW))

    notify.assert_not_awaited()


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


def test_process_claim_enrichment_failure_falls_back_to_selected_raw(
    monkeypatch,
) -> None:
    class EnrichmentFailingProvider:
        source_code = "pichau"

        async def collect(self, request):
            return CollectionResult(
                "pichau",
                request.requested_at,
                NOW + timedelta(seconds=2),
                (_raw(title="Título original"),),
            )

        async def enrich_offer_details(self, offers):
            raise RuntimeError("enrichment indisponível")

    persist_a = AsyncMock(return_value="phase-a")
    monkeypatch.setattr("app.collection.orchestration._persist_phase_a", persist_a)
    monkeypatch.setattr(
        "app.collection.orchestration._run_phase_b", AsyncMock(return_value=())
    )
    monkeypatch.setattr(
        "app.collection.orchestration._persist_phase_c", AsyncMock(return_value=True)
    )
    orchestrator = CollectionOrchestrator(
        _session_factory(_mock_async_session()),
        CollectionAdapter((EnrichmentFailingProvider(),)),
        ai_manager=_StubAIManager(),
    )
    claim = ClaimedCollection(uuid4(), uuid4(), uuid4(), "pichau", "GPU", NOW)

    assert asyncio.run(orchestrator._process(claim)) is True
    normalized = persist_a.call_args.args[2]
    assert normalized.offers[0].raw_offer.title == "Título original"


def test_process_claim_enrichment_cancelled_error_propagates() -> None:
    class CancellingProvider:
        source_code = "pichau"

        async def collect(self, request):
            return CollectionResult(
                "pichau", request.requested_at, NOW + timedelta(seconds=2), (_raw(),)
            )

        async def enrich_offer_details(self, offers):
            raise asyncio.CancelledError()

    orchestrator = CollectionOrchestrator(
        _session_factory(_mock_async_session()),
        CollectionAdapter((CancellingProvider(),)),
        ai_manager=_StubAIManager(),
    )
    claim = ClaimedCollection(uuid4(), uuid4(), uuid4(), "pichau", "GPU", NOW)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(orchestrator._process_claim(claim))


def test_process_claim_returns_false_when_phase_a_is_none(monkeypatch) -> None:
    class Provider:
        source_code = "pichau"

        async def collect(self, request):
            return CollectionResult(
                "pichau", request.requested_at, NOW + timedelta(seconds=2), (_raw(),)
            )

    run_b = AsyncMock(return_value=())
    persist_c = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "app.collection.orchestration._persist_phase_a", AsyncMock(return_value=None)
    )
    monkeypatch.setattr("app.collection.orchestration._run_phase_b", run_b)
    monkeypatch.setattr("app.collection.orchestration._persist_phase_c", persist_c)
    orchestrator = CollectionOrchestrator(
        _session_factory(_mock_async_session()),
        CollectionAdapter((Provider(),)),
        ai_manager=_StubAIManager(),
    )
    claim = ClaimedCollection(uuid4(), uuid4(), uuid4(), "pichau", "GPU", NOW)

    assert asyncio.run(orchestrator._process(claim)) is False
    run_b.assert_not_awaited()
    persist_c.assert_not_awaited()


def test_process_claim_integrity_error_logs_and_reraises(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    class Provider:
        source_code = "pichau"

        async def collect(self, request):
            return CollectionResult(
                "pichau", request.requested_at, NOW + timedelta(seconds=2), (_raw(),)
            )

    error = IntegrityError("stmt", {}, Exception())
    monkeypatch.setattr(
        "app.collection.orchestration._persist_phase_a",
        AsyncMock(return_value="phase-a"),
    )
    monkeypatch.setattr(
        "app.collection.orchestration._run_phase_b", AsyncMock(return_value=())
    )
    monkeypatch.setattr(
        "app.collection.orchestration._persist_phase_c", AsyncMock(side_effect=error)
    )
    orchestrator = CollectionOrchestrator(
        _session_factory(_mock_async_session()),
        CollectionAdapter((Provider(),)),
        ai_manager=_StubAIManager(),
    )
    claim = ClaimedCollection(uuid4(), uuid4(), uuid4(), "pichau", "GPU", NOW)

    with caplog.at_level("ERROR", logger="app.collection.orchestration"):
        with pytest.raises(IntegrityError):
            asyncio.run(orchestrator._process_claim(claim))

    assert any(
        record.message == "collection_integrity_failure" for record in caplog.records
    )


def test_record_failure_safely_isolates_recording_failure(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        "app.collection.orchestration._record_failure",
        AsyncMock(side_effect=RuntimeError("banco indisponível")),
    )
    orchestrator = CollectionOrchestrator(
        _session_factory(_mock_async_session()),
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
    )
    claim = ClaimedCollection(uuid4(), uuid4(), uuid4(), "pichau", "GPU", NOW)

    with caplog.at_level("ERROR", logger="app.collection.orchestration"):
        asyncio.run(orchestrator._record_failure_safely(claim, "some_code"))

    assert any(
        record.message == "collection_failure_recording_failed"
        for record in caplog.records
    )


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


def test_process_reraises_cancelled_error(monkeypatch) -> None:
    orchestrator = CollectionOrchestrator(
        _session_factory(_mock_async_session()),
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
    )
    claim = ClaimedCollection(uuid4(), uuid4(), uuid4(), "pichau", "GPU", NOW)

    async def _cancelled(_claim):
        raise asyncio.CancelledError()

    monkeypatch.setattr(orchestrator, "_process_claim", _cancelled)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(orchestrator._process(claim))


def _shared_claim() -> _SharedClaim:
    return _SharedClaim(
        run_id=uuid4(),
        criteria=SimpleNamespace(search_query="GPU", model=None),
        store_code="kabum",
        monitoring_item_id=uuid4(),
        store_id=uuid4(),
    )


def test_process_shared_claim_calls_shared_collector_within_semaphore() -> None:
    expected = SharedCollectionResult(
        claimed=True, provider_called=True, succeeded=True
    )
    shared_collector = AsyncMock(return_value=expected)
    orchestrator = CollectionOrchestrator(
        _session_factory(_mock_async_session()),
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
        shared_collector=shared_collector,
    )
    claim = _shared_claim()

    result = asyncio.run(orchestrator._process_shared_claim(claim, NOW))

    assert result is expected
    shared_collector.assert_awaited_once()
    assert shared_collector.call_args.kwargs["claim"] is claim


def test_process_shared_returns_shared_collector_result_on_success() -> None:
    expected = SharedCollectionResult(
        claimed=True, provider_called=True, succeeded=True
    )
    orchestrator = CollectionOrchestrator(
        _session_factory(_mock_async_session()),
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
        shared_collector=AsyncMock(return_value=expected),
    )
    claim = _shared_claim()

    result = asyncio.run(orchestrator._process_shared(claim, NOW))

    assert result is expected


def test_process_shared_timeout_returns_fallback_result() -> None:
    async def _slow_collector(*args, **kwargs):
        await asyncio.sleep(10)
        raise AssertionError("nao deveria completar")

    orchestrator = CollectionOrchestrator(
        _session_factory(_mock_async_session()),
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
        shared_collector=_slow_collector,
        claim_deadline_seconds=0.05,
    )
    claim = _shared_claim()

    result = asyncio.run(orchestrator._process_shared(claim, NOW))

    assert result == SharedCollectionResult(
        claimed=True, provider_called=False, succeeded=False
    )


def test_process_shared_reraises_cancelled_error(monkeypatch) -> None:
    orchestrator = CollectionOrchestrator(
        _session_factory(_mock_async_session()),
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
    )
    claim = _shared_claim()

    async def _cancelled(_claim, _now):
        raise asyncio.CancelledError()

    monkeypatch.setattr(orchestrator, "_process_shared_claim", _cancelled)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(orchestrator._process_shared(claim, NOW))


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
    # TASK-112 fase 3B: `run_batch` chama `claim_due_work`, nunca mais
    # `claim_due_collections` direto -- só caminho legado aqui (`shared=()`).
    monkeypatch.setattr(
        "app.collection.orchestration.claim_due_work",
        AsyncMock(return_value=_ClaimedBatch(old_path=claims, shared=())),
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


def test_resolve_identity_safely_reraises_cancelled_error() -> None:
    resolver = _FakeIdentityResolver(error=asyncio.CancelledError())
    orchestrator = CollectionOrchestrator(
        _session_factory(_mock_async_session()),
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
        identity_resolver=resolver,
    )
    claim = ClaimedCollection(uuid4(), uuid4(), uuid4(), "kabum", "9800X3D", NOW)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(orchestrator._resolve_identity_safely(claim))


def test_promote_resolved_identities_reraises_cancelled_error(monkeypatch) -> None:
    orchestrator = CollectionOrchestrator(
        _session_factory(_mock_async_session()),
        CollectionAdapter(),
        ai_manager=_StubAIManager(),
    )
    monkeypatch.setattr(
        "app.collection.orchestration.promote_confirmed_product_identity_async",
        AsyncMock(side_effect=asyncio.CancelledError()),
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            orchestrator._promote_resolved_identities({uuid4(): "search query"})
        )


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
        return _ClaimedBatch(
            old_path=_mission_claims(
                mission_id,
                search_query=persisted.search_query,
                model=persisted.model,
                sources=("kabum", "amazon"),
            ),
            shared=(),
        )

    # TASK-112 fase 3B: `run_batch` chama `claim_due_work`, nunca mais
    # `claim_due_collections` direto.
    monkeypatch.setattr(
        "app.collection.orchestration.claim_due_work",
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
        payment_method=None,
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
