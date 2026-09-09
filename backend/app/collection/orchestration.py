"""Orquestra coletas agendadas sem manter transações durante acesso externo.

TASK-079: todo o caminho de persistência usa `AsyncSession` (nunca uma
`Session` síncrona bloqueante direto na thread do event loop -- causa raiz
comprovada de um autodeadlock em produção). A fronteira transacional segue
sempre Fase A (local, curta, commit) -> Fase B (IA, sem transação aberta)
-> Fase C (seção crítica por `mission_id`, curta, commit). Nenhuma dessas
transações jamais mantém um `await` externo (IA, HTTP, Playwright) aberto.
"""

import asyncio
import enum
import logging
import random
import traceback
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import exists, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai_provider import AIProviderManager
from app.alerts import evaluate_price_alerts
from app.alerts.evaluator import (
    AlertCheckpoint,
    MaterialImprovementPolicy,
    should_rearm,
)
from app.alerts.models import MissionProductAlertState
from app.collection.adapter import CollectionAdapter
from app.collection.cadence import (
    _MODE_HIGH_ACTIVITY,
    CadenceConfig,
    resolve_collection_cadence,
    sample_next_run_at,
)
from app.collection.contracts import (
    CollectionRequest,
    MarketplacePartyKind,
    OfferCondition,
    ProductIdentityResolver,
    ResolvedProductIdentity,
)
from app.collection.errors import (
    CollectionContractError,
    CollectionNormalizationError,
    ProviderBlockedError,
    ProviderCircuitOpenError,
    ProviderNavigationError,
)
from app.collection.fairness import (
    _commit_fairness_turn_for_owner,
    _reserve_fairness_owners,
    is_user_eligible,
    queue_sort_key,
)
from app.collection.model_matching import (
    normalize_for_matching as _normalize_for_matching,
)
from app.collection.model_matching import same_code as _same_code
from app.collection.model_matching import title_matches_model as _title_matches_model
from app.collection.models import (
    CollectionQueueConfig,
    CollectionRun,
    CollectionRunStatus,
    MissionOfferRelevance,
    OfferInstallmentOption,
    PriceObservation,
    StoreThrottleState,
    UserCollectionQueueState,
)
from app.collection.normalization import (
    Availability,
    NormalizedCollectedOffer,
    NormalizedCollectionResult,
    NormalizedInstallmentOption,
    PriceNormalizer,
)
from app.collection.persistence import finish_collection_run, start_collection_run
from app.collection.queue_config import (
    COLLECTION_QUEUE_CONFIG_ID,
    resolve_queue_config,
)
from app.collection.relevance import (
    OfferRelevance,
    classify_offer_relevance,
    normalize_offer_title,
)
from app.collection.shared_claim import (
    _claim_shared_collection_in_session,
    _SharedClaim,
)
from app.core.config import Settings
from app.coupons.pricing import AppliedCoupon, best_applicable_coupon
from app.coupons.service import get_candidate_coupons_for_offer
from app.coupons.worker_control import notify_coupon_worker_high_activity
from app.database.time import utc_now
from app.events import (
    AggregateType,
    AppliedCouponPayload,
    AvailabilityChangedPayload,
    CollectionCompletedPayload,
    CollectionFailedPayload,
    Event,
    EventType,
    MissionPrelistErrataV2Payload,
    MissionPrelistReadyV2Payload,
    MissionVariantsReadyPayload,
    PrelistOfferPayload,
    ProductVariantOptionPayload,
)
from app.events.service import publish_event_async
from app.historical_bootstrap.service import run_historical_bootstrap
from app.market_research.service import (
    AssessmentSnapshot,
    evaluate_trigger_and_maybe_research,
    resolve_realert_window,
)
from app.missions.models import (
    Mission,
    MissionCriteria,
    MissionMonitoringItem,
    MissionProductSelection,
    MissionSchedule,
    MissionSource,
    MissionStatus,
    MonitoringItemStore,
    VariantSelectionMode,
)
from app.missions.schedule import (
    next_source_backoff,
    staggered_next_run_at,
)
from app.missions.service import promote_confirmed_product_identity_async
from app.offers.models import Offer
from app.products.identity import (
    IDENTITY_VERSION,
    ProductRequestKind,
    resolve_product_variant,
)
from app.products.models import Product
from app.search.cesar_core_fetch import CesarCoreFetchProvider
from app.search.manager import build_web_search_manager
from app.stores.models import Seller, Store
from app.users.models import UserRole

if TYPE_CHECKING:
    # TASK-112 fase 3B: `shared_collection.py` já depende deste módulo
    # (17+ símbolos privados reaproveitados) -- um import de módulo aqui
    # criaria um ciclo real. `TYPE_CHECKING` nunca executa em runtime
    # (só para checagem estática), então não custa nada; as referências
    # reais chegam via injeção de dependência no construtor de
    # `CollectionOrchestrator` (mesmo padrão de `identity_resolver`,
    # TASK-083), resolvidas de verdade em `worker.py`, que já importa os
    # dois módulos livremente.
    from app.collection.shared_collection import (
        FanOutSweepSummary,
        SharedCollectionResult,
    )

logger = logging.getLogger("app.collection.orchestration")

V1_SOURCE_CODES = frozenset(
    {"pichau", "terabyte", "amazon", "kabum", "magalu", "mercadolivre"}
)
_RUNNING_INDEX = "uq_collection_runs_running_mission_store"
_MISSION_OFFER_RELEVANCE_PK = (
    MissionOfferRelevance.mission_id,
    MissionOfferRelevance.offer_id,
)
# DEC-047: só 403/429 confirmados acionam backoff persistente por fonte
# (bloqueio/rate-limit/challenge externo). 401 fica de fora de propósito:
# normalmente representa autenticação/credencial/configuração, não
# proteção anti-bot, e não deve crescer exponencialmente como se fosse
# rate limit. ProviderBlockedError também cobre seletor ausente/oferta
# vazia com outro status (possível markup change, não bloqueio
# confirmado) — esses casos, e 401, permanecem sem backoff persistente,
# só o corte por execução que já existe em app.collection.providers.base
# (que continua tratando 401/403/429 como bloqueio da chamada em si).
_CONFIRMED_BLOCK_STATUSES = frozenset({403, 429})
_SOURCE_BACKOFF_CAP_MINUTES = 360
_FAILURE_TRACEBACK_MAX_CHARS = 4000
_OFFER_IDENTITY_INDEXES = frozenset(
    {
        "uq_offers_retailer_external_id",
        "uq_offers_marketplace_external_id",
        "uq_offers_retailer_url",
        "uq_offers_marketplace_url",
    }
)
_SELLER_IDENTITY_INDEX = "uq_sellers_store_external_id"
_PRODUCT_IDENTITY_INDEX = "uq_products_identity_key"

# Palavras-sinal de sistema completo/kit -- ausentes no search_query da
# missão mas presentes no título do candidato indicam um resultado que não
# é o componente avulso pedido.
_BUNDLE_SIGNAL_WORDS = (
    "computador",
    "pc gamer",
    "workstation",
    "kit",
    "combo",
    "notebook",
)


@dataclass(frozen=True, slots=True)
class ClaimedCollection:
    run_id: UUID
    mission_id: UUID
    store_id: UUID
    source_code: str
    search_query: str
    requested_at: datetime
    model: str | None = None
    """TASK-083: espelha `MissionCriteria.model` no momento do claim -- só
    para decidir, na Fase B (fora de transação), se a identidade ainda está
    crua e vale a pena resolver. Default `None` preserva toda construção
    posicional existente (`ClaimedCollection(run_id, ..., requested_at)`)
    sem quebrar nenhum teste/chamador anterior à TASK-083."""


@dataclass(frozen=True, slots=True)
class CollectionBatchResult:
    """TASK-112 fase 3B: `claimed`/`succeeded`/`failed` são TOTAIS (legado
    + compartilhado somados) -- `backend/app/collection/worker.py` já loga
    esses 3 campos como métrica operacional (`collection_claimed` etc.);
    mantê-los "só legado" faria qualquer painel/alerta existente
    subcontar depois do cutover, já que o caminho compartilhado passaria
    a produzir coleta real que nunca apareceria nessas métricas. Toda
    fixture de teste hoje tem zero trabalho compartilhado, então
    `claimed == legacy_claimed` em todo teste existente -- a mudança de
    contrato não quebra nada hoje, só passa a somar corretamente quando o
    caminho novo entrar em produção. `legacy_*`/`shared_*` detalham a
    composição; `fan_out_*` são as métricas do sweep (independentes de
    claim novo neste ciclo -- backlog de ciclos anteriores)."""

    claimed: int
    succeeded: int
    failed: int
    recovered_stale: int
    legacy_claimed: int = 0
    legacy_succeeded: int = 0
    legacy_failed: int = 0
    shared_claimed: int = 0
    shared_succeeded: int = 0
    shared_failed: int = 0
    fan_out_attempted_task_count: int = 0
    fan_out_done_mission_count: int = 0
    fan_out_attention_required_mission_count: int = 0
    fan_out_terminally_failed_mission_count: int = 0


async def ensure_missing_schedules(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    interval_minutes: int = 60,
    stagger_seconds: int = 0,
) -> int:
    """Cria apenas agendas ausentes de missões antigas ainda elegíveis."""
    effective_now = now or utc_now()
    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be positive")
    if stagger_seconds < 0:
        raise ValueError("stagger_seconds must not be negative")
    eligible_missions = select(Mission.id).where(
        Mission.status == MissionStatus.ACTIVE,
        (Mission.expires_at.is_(None) | (Mission.expires_at > effective_now)),
        exists().where(MissionCriteria.mission_id == Mission.id),
        exists()
        .where(MissionSource.mission_id == Mission.id)
        .where(MissionSource.store_id == Store.id)
        .where(Store.is_active.is_(True)),
        ~exists().where(MissionSchedule.mission_id == Mission.id),
    )
    rows = (await session.execute(eligible_missions)).all()
    created = 0
    for (mission_id,) in rows:
        statement = (
            postgresql_insert(MissionSchedule)
            .values(
                id=uuid4(),
                mission_id=mission_id,
                interval_minutes=interval_minutes,
                next_run_at=staggered_next_run_at(
                    effective_now, max_stagger_seconds=stagger_seconds
                ),
                is_enabled=True,
                created_at=effective_now,
                updated_at=effective_now,
            )
            .on_conflict_do_nothing(index_elements=[MissionSchedule.mission_id])
        )
        result = await session.execute(statement)
        created += int((result.rowcount or 0) > 0)
    await session.flush()
    return created


async def recover_stale_runs(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    stale_after: timedelta = timedelta(minutes=10),
    limit: int = 100,
) -> int:
    """Finaliza claims abandonados; resultado tardio não poderá ser persistido."""
    effective_now = now or utc_now()
    cutoff = effective_now - stale_after
    result = await session.scalars(
        select(CollectionRun)
        .where(
            CollectionRun.status == CollectionRunStatus.RUNNING,
            CollectionRun.started_at <= cutoff,
        )
        .order_by(CollectionRun.started_at, CollectionRun.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    runs = list(result)
    for run in runs:
        await finish_collection_run(
            session, run.id, CollectionRunStatus.FAILED, finished_at=effective_now
        )
        await _publish_failure(session, run, "stale_execution", effective_now)
        await _evaluate_mission_prelist(session, run.mission_id, effective_now)
    return len(runs)


def _due_legacy_sources_statement(*, due_at: datetime, limit: int, lock: bool):
    """TASK-112 fase 3B (correção estrutural, achado da auditoria pós-
    implementação): a unidade REAL de execução do caminho legado é
    `MissionSource` -- `(Mission, Store)` --, nunca `MissionSchedule`
    (agenda por MISSÃO INTEIRA). A versão anterior desta fase selecionava
    por `MissionSchedule` due e expandia para TODAS as `MissionSource`
    habilitadas da missão, sem checar a cadência de cada uma -- uma
    Mission com KaBuM em NORMAL (45-75min) e Amazon em HIGH_ACTIVITY
    (30-45min) tinha `MissionSchedule.next_run_at` reagendada para o mais
    cedo entre as duas; quando esse horário vencia, KaBuM era recoletada
    de novo mesmo sem ter passado sua própria cadência, só porque a
    missão "acordou" para atender Amazon. Corrigido selecionando
    diretamente por `MissionSource` due -- cada `(mission, store)` só
    aparece aqui quando SUA PRÓPRIA `next_run_at` (cadência,
    `app.collection.cadence`) e `next_eligible_at` (backoff DEC-046, gate
    SEPARADO que sempre vence) permitem.

    `MissionSchedule.is_enabled` continua um gate real (join, nunca
    dropado) -- é o kill-switch independente que ADMIN
    (`_stop_user_missions`) e privacidade (`deidentify_account`) já usam
    para desligar TODA a missão de uma vez sem precisar tocar em cada
    `MissionSource`; `MissionSchedule.next_run_at`/`last_run_at` deixam de
    ser lidos aqui (viram agregado DERIVADO só de exibição, ver
    `_refresh_legacy_schedule_aggregate`).

    Anti-join contra `MissionMonitoringItem` preservado (TASK-112 fase
    3B original): uma Mission vinculada a um `MonitoringItem` nunca é
    candidata aqui, mesmo com `MissionSource`/`MissionSchedule`
    fisicamente presentes (dado morto, nunca apagado) -- sem isto, uma
    Mission vinculada seria coletada duas vezes.

    `lock=True` (usado só por `claim_due_collections`, compatibility/
    legacy-only, transação única sem a reserva de fairness em duas fases)
    aplica `FOR UPDATE SKIP LOCKED` na própria `MissionSource`. `lock=False`
    (usado pela Fase 1 somente-leitura de `claim_due_work`) nunca trava --
    travar um candidato que o corte de `max_users` vai descartar bloquearia
    outro worker à toa; a garantia real de "nunca claima duas vezes" fica
    inteiramente no lock da Fase 2 (`_claim_legacy_source_attempt`)."""
    statement = (
        select(MissionSource, Store.code, Mission.user_id)
        .join(Mission, Mission.id == MissionSource.mission_id)
        .join(Store, Store.id == MissionSource.store_id)
        .join(MissionSchedule, MissionSchedule.mission_id == Mission.id)
        .where(
            Mission.status == MissionStatus.ACTIVE,
            or_(Mission.expires_at.is_(None), Mission.expires_at > due_at),
            MissionSchedule.is_enabled.is_(True),
            Store.is_active.is_(True),
            Store.code.in_(V1_SOURCE_CODES),
            or_(
                MissionSource.next_run_at.is_(None),
                MissionSource.next_run_at <= due_at,
            ),
            or_(
                MissionSource.next_eligible_at.is_(None),
                MissionSource.next_eligible_at <= due_at,
            ),
            ~exists().where(MissionMonitoringItem.mission_id == Mission.id),
        )
        .order_by(MissionSource.next_run_at.asc().nulls_first(), Mission.id, Store.id)
        .limit(limit)
    )
    if lock:
        statement = statement.with_for_update(skip_locked=True, of=MissionSource)
    return statement


async def _select_due_legacy_sources_for_batch(
    session: AsyncSession, *, due_at: datetime, limit: int
) -> list[tuple[MissionSource, str, UUID]]:
    """Somente leitura -- ver `_due_legacy_sources_statement` (`lock=False`)."""
    return (
        await session.execute(
            _due_legacy_sources_statement(due_at=due_at, limit=limit, lock=False)
        )
    ).all()


async def _refresh_legacy_schedule_aggregate(
    session: AsyncSession, *, mission_id: UUID, now: datetime
) -> None:
    """`MissionSchedule` deixou de ser autoritativa para cadência
    (`MissionSource` é, TASK-112 fase 3B) -- este helper só mantém o
    AGREGADO DE EXIBIÇÃO coerente: `next_run_at` = MIN entre as
    `MissionSource` da missão (`NULL` tratado como `now`, já due);
    `last_run_at` = MAIS RECENTE. Consumido por `MissionScheduleOut`
    (API de detalhe da missão) e pelo dashboard ADMIN -- nunca lido por
    nenhuma decisão real de claim. `is_enabled`/`interval_minutes` nunca
    são tocados aqui (permanecem o kill-switch e a base de DEC-046,
    respectivamente -- papéis que não mudaram)."""
    schedule = await session.scalar(
        select(MissionSchedule)
        .where(MissionSchedule.mission_id == mission_id)
        .with_for_update()
    )
    if schedule is None or not schedule.is_enabled:
        return
    # `autoflush=False` (convenção do projeto, `app.database.session`) --
    # o claim por source, logo acima na mesma transação, só mutou atributos
    # Python de objetos `MissionSource` já carregados; esta é uma SELECT
    # "core" nova (só colunas, nunca passa pelo identity map), nunca veria
    # essas mudanças sem um flush explícito antes.
    await session.flush()
    rows = (
        await session.execute(
            select(MissionSource.next_run_at, MissionSource.last_run_at).where(
                MissionSource.mission_id == mission_id
            )
        )
    ).all()
    if not rows:
        return
    next_candidates = [next_run_at or now for next_run_at, _ in rows]
    last_candidates = [
        last_run_at for _, last_run_at in rows if last_run_at is not None
    ]
    schedule.next_run_at = min(next_candidates)
    if last_candidates:
        schedule.last_run_at = max(last_candidates)
    schedule.updated_at = now


async def _advance_user_queue_state(
    session: AsyncSession,
    *,
    user_id: UUID,
    processed_at: datetime,
    cooldown_min_seconds: float,
    cooldown_max_seconds: float,
) -> None:
    """TASK-108: registra que este usuário acabou de ter um lote real
    reivindicado -- fica inelegível por um jitter curto e, por
    consequência de `last_processed_at` avançar, vai para o fim da fila
    round-robin. Nunca afeta outros usuários."""
    next_eligible_at = processed_at + timedelta(
        seconds=random.uniform(cooldown_min_seconds, cooldown_max_seconds)
    )
    statement = (
        postgresql_insert(UserCollectionQueueState)
        .values(
            user_id=user_id,
            last_processed_at=processed_at,
            next_eligible_at=next_eligible_at,
            updated_at=processed_at,
        )
        .on_conflict_do_update(
            index_elements=[UserCollectionQueueState.user_id],
            set_={
                "last_processed_at": processed_at,
                "next_eligible_at": next_eligible_at,
                "updated_at": processed_at,
            },
        )
    )
    await session.execute(statement)


async def _advance_store_throttle(
    session: AsyncSession,
    *,
    store_id: UUID,
    claimed_at: datetime,
    min_interval_seconds: float,
) -> None:
    """TASK-108: pacing GLOBAL por loja -- toda claim reivindicada para
    esta loja, de qualquer usuário/missão, empurra `next_allowed_at`
    para a frente. Executado imediatamente (não em lote no fim do
    batch, ao contrário de `_advance_user_queue_state`) para que a
    PRÓXIMA fonte da mesma loja, ainda dentro do mesmo batch/transação,
    já veja o novo valor -- nenhuma troca de usuário ou missão dentro do
    mesmo ciclo fura o intervalo."""
    next_allowed_at = claimed_at + timedelta(seconds=min_interval_seconds)
    statement = (
        postgresql_insert(StoreThrottleState)
        .values(
            store_id=store_id,
            next_allowed_at=next_allowed_at,
            updated_at=claimed_at,
        )
        .on_conflict_do_update(
            index_elements=[StoreThrottleState.store_id],
            set_={
                "next_allowed_at": next_allowed_at,
                "updated_at": claimed_at,
            },
        )
    )
    await session.execute(statement)


async def claim_due_collections(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 25,
    max_users: int = 1,
    user_cooldown_min_seconds: float = 60.0,
    user_cooldown_max_seconds: float = 180.0,
    store_min_interval_seconds: float = 2.0,
    cadence_config: CadenceConfig | None = None,
) -> tuple[ClaimedCollection, ...]:
    """Reserva fonte por fonte e avança a cadência individual numa
    transação curta -- compatibility/legacy-only (scripts/testes que
    querem só o comportamento legado isolado; produção usa
    `claim_due_work`, que compartilha `_due_legacy_sources_statement` com
    esta função para nunca divergir sobre o que conta como "due").

    TASK-112 fase 3B (correção estrutural): due é decidido por
    `MissionSource` -- `(mission, store)` --, nunca por `MissionSchedule`
    (missão inteira). Contrato externo (assinatura/retorno) preservado
    para quem já chama isto direto."""
    effective_now = now or utc_now()
    effective_cadence_config = cadence_config or CadenceConfig()
    rows = (
        await session.execute(
            _due_legacy_sources_statement(due_at=effective_now, limit=limit, lock=True)
        )
    ).all()
    if not rows:
        return ()

    sources_by_user: dict[UUID, list[tuple[MissionSource, str]]] = {}
    user_order: list[UUID] = []
    for source, store_code, user_id in rows:
        if user_id not in sources_by_user:
            sources_by_user[user_id] = []
            user_order.append(user_id)
        sources_by_user[user_id].append((source, store_code))

    states = {
        state.user_id: state
        for state in await session.scalars(
            select(UserCollectionQueueState).where(
                UserCollectionQueueState.user_id.in_(user_order)
            )
        )
    }

    def sort_key(user_id: UUID) -> tuple[int, object]:
        last_processed_at = (
            states[user_id].last_processed_at if user_id in states else None
        )
        if last_processed_at is None:
            return (0, str(user_id))
        return (1, last_processed_at, str(user_id))

    eligible_users = [
        user_id
        for user_id in user_order
        if user_id not in states
        or states[user_id].next_eligible_at is None
        or states[user_id].next_eligible_at <= effective_now
    ]
    eligible_users.sort(key=sort_key)
    selected_users = eligible_users[:max_users]

    claims: list[ClaimedCollection] = []
    processed_users: set[UUID] = set()
    claimed_mission_ids: set[UUID] = set()
    criteria_cache: dict[UUID, MissionCriteria | None] = {}
    # Mesma granularidade de sempre: se a MISSÃO já tem QUALQUER store em
    # CollectionRun RUNNING, o ciclo inteiro dela fica de fora (não só a
    # store específica -- a unicidade por `(mission_id, store_id)` já é
    # garantida à parte pelo índice `_RUNNING_INDEX`/`start_collection_
    # run`). Cache evita repetir a mesma query para cada source da mesma
    # missão.
    running_cache: dict[UUID, bool] = {}
    for user_id in selected_users:
        for source, store_code in sources_by_user[user_id]:
            mission_id = source.mission_id
            if mission_id not in running_cache:
                running_cache[mission_id] = (
                    await session.scalar(
                        select(CollectionRun.id)
                        .where(
                            CollectionRun.mission_id == mission_id,
                            CollectionRun.status == CollectionRunStatus.RUNNING,
                        )
                        .limit(1)
                    )
                ) is not None
            if running_cache[mission_id]:
                continue
            if mission_id not in criteria_cache:
                criteria_cache[mission_id] = await session.scalar(
                    select(MissionCriteria).where(
                        MissionCriteria.mission_id == mission_id
                    )
                )
            criteria = criteria_cache[mission_id]
            if criteria is None or not criteria.search_query.strip():
                continue
            # TASK-108: pacing GLOBAL por loja -- independe de qual
            # usuário/missão pediu a última claim desta loja.
            throttle = await session.get(StoreThrottleState, source.store_id)
            if (
                throttle is not None
                and throttle.next_allowed_at is not None
                and throttle.next_allowed_at > effective_now
            ):
                continue
            # Recheck sob o lock já adquirido pela SELECT ... FOR UPDATE
            # acima -- a leitura desta linha pode ter precedido o lock
            # efetivo sobre ela; mesma disciplina do resto do sistema
            # (nunca confiar só na leitura otimista da seleção).
            if source.next_run_at is not None and source.next_run_at > effective_now:
                continue
            if (
                source.next_eligible_at is not None
                and source.next_eligible_at > effective_now
            ):
                continue
            try:
                async with session.begin_nested():
                    run = await start_collection_run(
                        session,
                        source.store_id,
                        mission_id,
                        started_at=effective_now,
                    )
            except IntegrityError as error:
                if _constraint_name(error) != _RUNNING_INDEX:
                    raise
                continue
            decision = await resolve_collection_cadence(
                session,
                store_id=source.store_id,
                scope_id=mission_id,
                mission_ids=(mission_id,),
                now=effective_now,
                config=effective_cadence_config,
            )
            source.last_run_at = effective_now
            source.next_run_at = sample_next_run_at(effective_now, decision)
            # TASK-108: aplicado já, dentro do mesmo batch/transação -- a
            # próxima source desta loja (mesma missão ou outra, mais
            # adiante no loop) já vê o novo `next_allowed_at`.
            await _advance_store_throttle(
                session,
                store_id=source.store_id,
                claimed_at=effective_now,
                min_interval_seconds=store_min_interval_seconds,
            )
            claims.append(
                ClaimedCollection(
                    run.id,
                    mission_id,
                    source.store_id,
                    store_code,
                    criteria.search_query,
                    effective_now,
                    criteria.model,
                )
            )
            processed_users.add(user_id)
            claimed_mission_ids.add(mission_id)

    # Agregado de exibição (`MissionScheduleOut`, dashboard ADMIN) -- nunca
    # lido por nenhuma decisão de claim (ver `_refresh_legacy_schedule_
    # aggregate`).
    for mission_id in claimed_mission_ids:
        await _refresh_legacy_schedule_aggregate(
            session, mission_id=mission_id, now=effective_now
        )
    # TASK-108: só usuários que realmente contribuíram com uma claim real
    # nesta rodada entram em cooldown -- um usuário selecionado cujas
    # sources due não geraram nenhuma claim (já rodando, fontes em
    # backoff) não é penalizado por um lote vazio.
    for user_id in processed_users:
        await _advance_user_queue_state(
            session,
            user_id=user_id,
            processed_at=effective_now,
            cooldown_min_seconds=user_cooldown_min_seconds,
            cooldown_max_seconds=user_cooldown_max_seconds,
        )
    await session.flush()
    return tuple(claims)


# ---------------------------------------------------------------------------
# TASK-112 fase 3B: scheduler unificado -- fila de fairness compartilhada
# entre o caminho antigo (por MissionSource) e o caminho novo (por
# MonitoringItemStore). `claim_due_collections` acima é compatibility/
# legacy-only -- usada por qualquer caller que ainda queira só o
# comportamento legado isolado, mas compartilha `_due_legacy_sources_
# statement` com a Fase 1 abaixo para nunca divergir sobre due-ness.
# `claim_due_work` abaixo é o único caminho usado pelo
# `CollectionOrchestrator` de produção.
#
# Desenho (documentado em detalhe em `docs/tasks/TASK-112.md`, revisado
# em 6 rodadas antes desta implementação):
#   1. Seleção somente-leitura (`_select_due_work_for_batch`) -- nunca
#      trava um candidato que o corte de `max_users` vai descartar.
#      Candidatos compartilhados contam por `MonitoringItemStore` ÚNICO
#      (nunca explodidos por usuário vinculado).
#   2. Reserva de fairness (`app.collection.fairness._reserve_fairness_
#      owners`) -- lock real de `UserCollectionQueueState`, em ordem de
#      `user_id`, SEMPRE antes de qualquer lock de loja. É este lock
#      contínuo (não mais um token comparado por igualdade) que impede
#      duas execuções concorrentes de `claim_due_work` (mesmo com `now`
#      diferentes) de creditarem o mesmo usuário duas vezes.
#   3. Só para donos reservados: lista ÚNICA de tentativas (legado +
#      compartilhado juntos), ordenada por `(store_id, due_at, kind,
#      resource_id)` -- ordem global de locja evita deadlock cruzado
#      entre transações concorrentes; nenhum caminho tem prioridade
#      estrutural sobre o outro na mesma loja.
#   4. Cooldown só é gravado para donos que tiveram >= 1 claim real
#      (`_commit_fairness_turn_for_owner`) -- usa o lock já em mãos desde
#      o passo 2, `UPDATE` simples, sem CAS.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _SelectedWork:
    owner_ids: frozenset[UUID]
    old_path_sources_by_owner: dict[UUID, tuple[tuple[MissionSource, str], ...]]
    shared_targets_by_owner: dict[UUID, tuple[tuple[UUID, UUID], ...]]


async def _select_due_work_for_batch(
    session: AsyncSession,
    *,
    due_at: datetime,
    limit: int,
    max_users: int,
    candidate_scan_limit: int,
) -> _SelectedWork:
    """Somente leitura -- NENHUM `FOR UPDATE` aqui (achado real, rodada 5:
    travar um candidato que o corte de `max_users` vai descartar bloqueia
    outro worker à toa). A garantia real de "nunca claima duas vezes"
    fica inteiramente na Fase 2 (`claim_due_work`, lock real no momento
    do claim)."""
    # --- Caminho antigo: MissionSource due -- (mission, store) --, nunca
    #     MissionSchedule (missão inteira). Compartilha `_due_legacy_
    #     sources_statement` com `claim_due_collections` (`lock=False`
    #     aqui -- usada só pela Fase 2 deste scheduler) para as duas
    #     nunca divergirem sobre o que conta como "due". ---
    legacy_rows = await _select_due_legacy_sources_for_batch(
        session, due_at=due_at, limit=limit
    )
    old_path_sources_by_owner: dict[UUID, list[tuple[MissionSource, str]]] = {}
    for source, store_code, user_id in legacy_rows:
        old_path_sources_by_owner.setdefault(user_id, []).append((source, store_code))

    # --- Caminho novo: MonitoringItemStore due ÚNICOS -- 1 linha = 1
    #     candidato, nunca explodido por usuário vinculado (achado real,
    #     rodada 6: um item com 100 vinculados não pode consumir 100
    #     posições da janela de scan). `candidate_scan_limit` (default
    #     1000, folgado para a escala real da V1.2) existe só para
    #     descobrir trabalho/donos -- depois que os donos forem
    #     reservados, TODO o trabalho due deles entra (§ da rodada 6,
    #     "scan de fairness != limite de execução"), sem cap adicional
    #     por dono. ---
    shared_rows = (
        await session.execute(
            select(MonitoringItemStore.monitoring_item_id, MonitoringItemStore.store_id)
            .where(
                MonitoringItemStore.is_enabled.is_(True),
                or_(
                    MonitoringItemStore.next_run_at.is_(None),
                    MonitoringItemStore.next_run_at <= due_at,
                ),
                or_(
                    MonitoringItemStore.next_eligible_at.is_(None),
                    MonitoringItemStore.next_eligible_at <= due_at,
                ),
            )
            .order_by(
                MonitoringItemStore.next_run_at,
                MonitoringItemStore.monitoring_item_id,
                MonitoringItemStore.store_id,
            )
            .limit(candidate_scan_limit)
        )
    ).all()
    pairs = [(item_id, store_id) for item_id, store_id in shared_rows]

    linked_by_pair: dict[tuple[UUID, UUID], set[UUID]] = {}
    all_linked_users: set[UUID] = set()
    if pairs:
        link_rows = (
            await session.execute(
                select(
                    MissionMonitoringItem.monitoring_item_id,
                    MissionSource.store_id,
                    Mission.user_id,
                )
                .select_from(MissionMonitoringItem)
                .join(Mission, Mission.id == MissionMonitoringItem.mission_id)
                .join(MissionSource, MissionSource.mission_id == Mission.id)
                .where(
                    Mission.status == MissionStatus.ACTIVE,
                    tuple_(
                        MissionMonitoringItem.monitoring_item_id, MissionSource.store_id
                    ).in_(pairs),
                )
            )
        ).all()
        for item_id, store_id, user_id in link_rows:
            linked_by_pair.setdefault((item_id, store_id), set()).add(user_id)
            all_linked_users.add(user_id)

    all_candidate_user_ids = set(old_path_sources_by_owner) | all_linked_users
    states: dict[UUID, UserCollectionQueueState] = {}
    if all_candidate_user_ids:
        states = {
            state.user_id: state
            for state in await session.scalars(
                select(UserCollectionQueueState).where(
                    UserCollectionQueueState.user_id.in_(all_candidate_user_ids)
                )
            )
        }

    # §12: dono único por target -- argmin entre os vinculados elegíveis.
    shared_targets_by_owner: dict[UUID, list[tuple[UUID, UUID]]] = {}
    for pair, linked_users in linked_by_pair.items():
        eligible = [u for u in linked_users if is_user_eligible(states.get(u), due_at)]
        if not eligible:
            continue
        owner = min(eligible, key=lambda u: queue_sort_key(states.get(u), u))
        shared_targets_by_owner.setdefault(owner, []).append(pair)

    owner_candidates = set(old_path_sources_by_owner) | set(shared_targets_by_owner)
    eligible_owners = sorted(
        (u for u in owner_candidates if is_user_eligible(states.get(u), due_at)),
        key=lambda u: queue_sort_key(states.get(u), u),
    )
    selected_owner_ids = frozenset(eligible_owners[:max_users])

    return _SelectedWork(
        owner_ids=selected_owner_ids,
        old_path_sources_by_owner={
            user_id: tuple(sources)
            for user_id, sources in old_path_sources_by_owner.items()
            if user_id in selected_owner_ids
        },
        shared_targets_by_owner={
            user_id: tuple(pairs_)
            for user_id, pairs_ in shared_targets_by_owner.items()
            if user_id in selected_owner_ids
        },
    )


@dataclass(frozen=True, slots=True)
class _ClaimAttempt:
    """Uma tentativa de claim de recurso -- legado (missão, loja) ou
    compartilhado (item, loja) -- já normalizada para a ordenação global
    única (`(store_id, due_at, kind, resource_id)`, seção 5 do desenho)."""

    kind: str  # "legacy_store" | "shared"
    owner_user_id: UUID
    store_id: UUID
    due_at: datetime
    resource_id: str
    mission_id: UUID | None = None
    source_code: str | None = None
    search_query: str | None = None
    model: str | None = None
    monitoring_item_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class _ClaimedBatch:
    old_path: tuple[ClaimedCollection, ...]
    shared: tuple[_SharedClaim, ...]
    # DEC-132: só o FATO "algum claim deste batch decidiu HIGH_ACTIVITY"
    # -- nunca se o Coupon Worker foi avisado (isso é decisão do
    # chamador, depois do commit, e depende de `settings`).
    high_activity_detected: bool = False


async def _build_claim_attempts(
    session: AsyncSession,
    selected: _SelectedWork,
    reserved_owners: set[UUID],
    *,
    due_at: datetime,
) -> list[_ClaimAttempt]:
    """Monta a lista de tentativas só para donos reservados -- uma
    tentativa por `MissionSource` já devida (Fase 1 já decidiu isso, por
    `(mission, store)`, TASK-112 fase 3B correção estrutural -- nunca mais
    "expande a missão inteira em todas as lojas"). `MissionCriteria`
    buscada uma vez por missão distinta (cache local), não por source --
    Mission sem `MissionCriteria` válida é pulada, mesmo comportamento de
    sempre."""
    attempts: list[_ClaimAttempt] = []
    for owner in reserved_owners:
        criteria_cache: dict[UUID, MissionCriteria | None] = {}
        for source, source_code in selected.old_path_sources_by_owner.get(owner, ()):
            mission_id = source.mission_id
            if mission_id not in criteria_cache:
                criteria_cache[mission_id] = await session.scalar(
                    select(MissionCriteria).where(
                        MissionCriteria.mission_id == mission_id
                    )
                )
            criteria = criteria_cache[mission_id]
            if criteria is None or not criteria.search_query.strip():
                continue
            attempts.append(
                _ClaimAttempt(
                    kind="legacy_store",
                    owner_user_id=owner,
                    store_id=source.store_id,
                    due_at=source.next_run_at or due_at,
                    resource_id=f"{mission_id}:{source.store_id}",
                    mission_id=mission_id,
                    source_code=source_code,
                    search_query=criteria.search_query,
                    model=criteria.model,
                )
            )
        for item_id, store_id in selected.shared_targets_by_owner.get(owner, ()):
            attempts.append(
                _ClaimAttempt(
                    kind="shared",
                    owner_user_id=owner,
                    store_id=store_id,
                    due_at=due_at,
                    resource_id=f"{item_id}:{store_id}",
                    monitoring_item_id=item_id,
                )
            )
    attempts.sort(key=lambda a: (str(a.store_id), a.due_at, a.kind, a.resource_id))
    return attempts


async def _claim_legacy_source_attempt(
    session: AsyncSession,
    attempt: _ClaimAttempt,
    *,
    effective_now: datetime,
    store_min_interval_seconds: float,
    cadence_config: CadenceConfig,
    high_activity_notify: list[bool] | None = None,
) -> ClaimedCollection | None:
    """Mesmo template de `_claim_shared_collection_in_session`
    (`shared_claim.py`): 1. LOCK real da linha específica já escolhida
    (nunca um scan -- `SKIP LOCKED` já filtrou isso na seleção); 2.
    RECHECK sob o lock, nunca a leitura otimista da Fase 1 (`next_run_at`/
    `next_eligible_at` podem ter mudado entre a seleção e este momento);
    3. THROTTLE global de loja; 4. registrar `CollectionRun`; 5. avançar
    cadência (SÓ desta `MissionSource`, nunca de outra da mesma missão) +
    throttle."""
    source = await session.get(
        MissionSource, (attempt.mission_id, attempt.store_id), with_for_update=True
    )
    if source is None:
        return None
    if source.next_run_at is not None and source.next_run_at > effective_now:
        return None
    if source.next_eligible_at is not None and source.next_eligible_at > effective_now:
        return None
    throttle = await session.get(StoreThrottleState, attempt.store_id)
    if (
        throttle is not None
        and throttle.next_allowed_at is not None
        and throttle.next_allowed_at > effective_now
    ):
        return None
    try:
        async with session.begin_nested():
            run = await start_collection_run(
                session, attempt.store_id, attempt.mission_id, started_at=effective_now
            )
    except IntegrityError as error:
        if _constraint_name(error) != _RUNNING_INDEX:
            raise
        return None
    decision = await resolve_collection_cadence(
        session,
        store_id=attempt.store_id,
        scope_id=attempt.mission_id,
        mission_ids=(attempt.mission_id,),
        now=effective_now,
        config=cadence_config,
    )
    if decision.mode == _MODE_HIGH_ACTIVITY and high_activity_notify is not None:
        # DEC-132: só sinaliza que este claim decidiu HIGH_ACTIVITY -- a
        # chamada de rede real (`notify_coupon_worker_high_activity`)
        # acontece em `run_batch`, DEPOIS que a transação desta função
        # (que ainda segura o lock `FOR UPDATE` de `source`) já commitou.
        # Nunca I/O externo dentro da região transacional do claim.
        high_activity_notify.append(True)
    source.last_run_at = effective_now
    source.next_run_at = sample_next_run_at(effective_now, decision)
    await _advance_store_throttle(
        session,
        store_id=attempt.store_id,
        claimed_at=effective_now,
        min_interval_seconds=store_min_interval_seconds,
    )
    return ClaimedCollection(
        run.id,
        attempt.mission_id,
        attempt.store_id,
        attempt.source_code,
        attempt.search_query,
        effective_now,
        attempt.model,
    )


async def claim_due_work(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 25,
    max_users: int = 1,
    user_cooldown_min_seconds: float = 60.0,
    user_cooldown_max_seconds: float = 180.0,
    store_min_interval_seconds: float = 2.0,
    cadence_config: CadenceConfig | None = None,
    candidate_scan_limit: int = 1000,
    settings: Settings | None = None,
) -> _ClaimedBatch:
    """Scheduler unificado (TASK-112 fase 3B) -- único caminho usado pelo
    `CollectionOrchestrator` de produção. Ver o bloco de comentário acima
    para o desenho completo (seleção somente-leitura -> reserva de
    fairness -> claim em ordem global de loja -> cooldown só para quem
    claimou de verdade)."""
    effective_now = now or utc_now()
    effective_cadence_config = cadence_config or CadenceConfig()
    turn_id = uuid4()
    # DEC-132: mesmo gate de sempre (`settings is not None`), só decidido
    # ANTES do loop em vez de em cada attempt -- se ninguém quer aviso,
    # nem cria o coletor; se quer, os dois caminhos (legado/compartilhado)
    # só acumulam o FATO aqui dentro (nunca chamam rede). `run_batch` (o
    # único chamador de produção) decide, DEPOIS do commit, se de fato
    # avisa o Coupon Worker.
    high_activity_notify: list[bool] | None = [] if settings is not None else None

    selected = await _select_due_work_for_batch(
        session,
        due_at=effective_now,
        limit=limit,
        max_users=max_users,
        candidate_scan_limit=candidate_scan_limit,
    )
    reserved_owners = await _reserve_fairness_owners(
        session, candidate_owners=set(selected.owner_ids), due_at=effective_now
    )
    if not reserved_owners:
        return _ClaimedBatch(old_path=(), shared=())

    attempts = await _build_claim_attempts(
        session, selected, reserved_owners, due_at=effective_now
    )

    old_claims: list[ClaimedCollection] = []
    shared_claims: list[_SharedClaim] = []
    claimed_owners: set[UUID] = set()
    claimed_mission_ids: set[UUID] = set()

    for attempt in attempts:
        if attempt.kind == "legacy_store":
            resource_claim = await _claim_legacy_source_attempt(
                session,
                attempt,
                effective_now=effective_now,
                store_min_interval_seconds=store_min_interval_seconds,
                cadence_config=effective_cadence_config,
                high_activity_notify=high_activity_notify,
            )
            if resource_claim is not None:
                old_claims.append(resource_claim)
                claimed_owners.add(attempt.owner_user_id)
                claimed_mission_ids.add(attempt.mission_id)
        else:
            resource_claim = await _claim_shared_collection_in_session(
                session,
                monitoring_item_id=attempt.monitoring_item_id,
                store_id=attempt.store_id,
                now=effective_now,
                cadence_config=effective_cadence_config,
                store_min_interval_seconds=store_min_interval_seconds,
                fairness_owner_user_id=attempt.owner_user_id,
                high_activity_notify=high_activity_notify,
            )
            if resource_claim is not None:
                shared_claims.append(resource_claim)
                claimed_owners.add(attempt.owner_user_id)

    # Cadência já avançada POR SOURCE dentro de `_claim_legacy_source_
    # attempt` (TASK-112 fase 3B, correção estrutural -- cada
    # `MissionSource` tem sua própria agenda, nunca a Mission inteira).
    # Só o agregado de EXIBIÇÃO (`MissionScheduleOut`, dashboard ADMIN)
    # precisa ser recalculado aqui, uma vez por missão que teve >= 1
    # source claimada de verdade -- nunca lido por nenhuma decisão real.
    for mission_id in claimed_mission_ids:
        await _refresh_legacy_schedule_aggregate(
            session, mission_id=mission_id, now=effective_now
        )

    for user_id in claimed_owners:
        await _commit_fairness_turn_for_owner(
            session,
            user_id=user_id,
            processed_at=effective_now,
            turn_id=turn_id,
            cooldown_min_seconds=user_cooldown_min_seconds,
            cooldown_max_seconds=user_cooldown_max_seconds,
        )

    await session.flush()
    return _ClaimedBatch(
        old_path=tuple(old_claims),
        shared=tuple(shared_claims),
        high_activity_detected=bool(high_activity_notify),
    )


class CollectionOrchestrator:
    """Executa claims em paralelo e persiste cada fonte atomicamente.

    TASK-079: a coleta (Playwright) de fontes diferentes continua paralela
    via `asyncio.gather`/`Semaphore`; apenas a seção crítica de uma mesma
    missão (Fase C) é serializada, via `SELECT ... FOR UPDATE` no
    PostgreSQL -- não por `asyncio.Lock`, para continuar correto mesmo com
    múltiplos processos de worker.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        adapter: CollectionAdapter,
        *,
        ai_manager: AIProviderManager,
        ai_profile: UserRole = UserRole.ADMIN,
        firecrawl: CesarCoreFetchProvider | None = None,
        settings: Settings | None = None,
        normalizer: PriceNormalizer | None = None,
        identity_resolver: ProductIdentityResolver | None = None,
        schedule_interval_minutes: int = 60,
        schedule_stagger_seconds: int = 0,
        stale_run_minutes: int = 10,
        max_concurrency: int = 4,
        claim_deadline_seconds: float = 300.0,
        max_concurrent_user_batches: int = 1,
        user_cooldown_min_seconds: float = 60.0,
        user_cooldown_max_seconds: float = 180.0,
        store_min_interval_seconds: float = 2.0,
        # TASK-112 fase 3B -- caminho compartilhado.
        cadence_config: CadenceConfig | None = None,
        candidate_scan_limit: int = 1000,
        fan_out_target_scan_limit: int = 25,
        fan_out_task_budget: int = 100,
        fan_out_per_target_task_cap: int = 25,
        fan_out_concurrency: int = 4,
        shared_collector: "Callable[..., Awaitable[SharedCollectionResult]] | None" = None,
        fan_out_sweeper: "Callable[..., Awaitable[FanOutSweepSummary]] | None" = None,
    ) -> None:
        if schedule_interval_minutes <= 0:
            raise ValueError("schedule_interval_minutes must be positive")
        if schedule_stagger_seconds < 0:
            raise ValueError("schedule_stagger_seconds must not be negative")
        if stale_run_minutes <= 0:
            raise ValueError("stale_run_minutes must be positive")
        if not 1 <= max_concurrency <= 4:
            raise ValueError("max_concurrency must be between 1 and 4")
        if claim_deadline_seconds <= 0:
            raise ValueError("claim_deadline_seconds must be positive")
        if max_concurrent_user_batches <= 0:
            raise ValueError("max_concurrent_user_batches must be positive")
        # TASK-108: `0` é aceito aqui (nunca em `Settings`/no override do
        # ADMIN, que exigem `> 0`) -- mesmo motivo do throttle de loja
        # abaixo: só a construção direta do orchestrator (scripts/testes)
        # pode desligar o cooldown de propósito.
        if user_cooldown_min_seconds < 0 or user_cooldown_max_seconds < 0:
            raise ValueError("user cooldown seconds must not be negative")
        if user_cooldown_max_seconds < user_cooldown_min_seconds:
            raise ValueError("user cooldown max must not be smaller than min")
        if store_min_interval_seconds < 0:
            raise ValueError("store_min_interval_seconds must not be negative")
        if candidate_scan_limit <= 0:
            raise ValueError("candidate_scan_limit must be positive")
        if fan_out_target_scan_limit <= 0:
            raise ValueError("fan_out_target_scan_limit must be positive")
        if fan_out_task_budget <= 0:
            raise ValueError("fan_out_task_budget must be positive")
        if fan_out_per_target_task_cap <= 0:
            raise ValueError("fan_out_per_target_task_cap must be positive")
        if fan_out_concurrency <= 0:
            raise ValueError("fan_out_concurrency must be positive")
        self._session_factory = session_factory
        self._adapter = adapter
        self._ai_manager = ai_manager
        self._ai_profile = ai_profile
        # TASK-113: pesquisa de mercado -- `None` (default) desliga por
        # completo o recurso, mesmo comportamento de sempre (nenhum
        # `MarketPriceAssessment` é disparado); produção injeta os dois
        # via `worker.py`, mesmo padrão de `identity_resolver`.
        self._firecrawl = firecrawl
        self._settings = settings
        self._normalizer = normalizer or PriceNormalizer()
        self._identity_resolver = identity_resolver
        self._schedule_interval_minutes = schedule_interval_minutes
        self._schedule_stagger_seconds = schedule_stagger_seconds
        self._stale_after = timedelta(minutes=stale_run_minutes)
        self._claim_deadline_seconds = claim_deadline_seconds
        self._semaphore = asyncio.Semaphore(max_concurrency)
        # TASK-108: fila justa por usuário -- camada ortogonal ao backoff
        # por provider (DEC-046), que continua intocado.
        self._max_concurrent_user_batches = max_concurrent_user_batches
        self._user_cooldown_min_seconds = user_cooldown_min_seconds
        self._user_cooldown_max_seconds = user_cooldown_max_seconds
        # TASK-108: `0` é aceito aqui (nunca em `Settings`/no override do
        # ADMIN, que exigem `> 0`) -- só a construção direta do
        # orchestrator (scripts/testes) pode desligar o throttle global
        # de propósito.
        self._store_min_interval_seconds = store_min_interval_seconds
        # TASK-112 fase 3B.
        self._cadence_config = cadence_config or CadenceConfig()
        self._candidate_scan_limit = candidate_scan_limit
        self._fan_out_target_scan_limit = fan_out_target_scan_limit
        self._fan_out_task_budget = fan_out_task_budget
        self._fan_out_per_target_task_cap = fan_out_per_target_task_cap
        self._fan_out_concurrency = fan_out_concurrency
        # Injeção de dependência (mesmo padrão de `identity_resolver`,
        # TASK-083) -- evita `orchestration.py` importar `shared_
        # collection.py` no nível de módulo, que já depende deste módulo
        # (17+ símbolos privados), criando um ciclo real. `worker.py` (que
        # já importa os dois módulos livremente) passa as referências
        # reais em produção; aqui, só quando `None`, um import local
        # (executado uma vez, na construção, nunca por chamada) resolve
        # os defaults para quem constrói o orchestrator direto sem se
        # importar com isto (scripts/testes).
        if shared_collector is None or fan_out_sweeper is None:
            from app.collection.shared_collection import (
                _execute_claimed_shared_collection,
                sweep_shared_collection_fan_out,
            )

            shared_collector = shared_collector or _execute_claimed_shared_collection
            fan_out_sweeper = fan_out_sweeper or sweep_shared_collection_fan_out
        self._shared_collector = shared_collector
        self._fan_out_sweeper = fan_out_sweeper

    async def run_batch(
        self, *, now: datetime | None = None, limit: int = 25
    ) -> CollectionBatchResult:
        effective_now = now or utc_now()

        # TASK-112 fase 3B: sweep de fan-out pendente SEMPRE primeiro,
        # antes de gastar capacidade com coletas novas -- backlog de
        # ciclos anteriores (crash/retry) tem prioridade sobre trabalho
        # novo. Orçamento real (alvos E tarefas, nunca "todos os
        # pendentes") -- nunca monopoliza o worker. Roda mesmo quando não
        # há nenhum claim novo neste ciclo.
        fan_out_summary = await self._fan_out_sweeper(
            self._session_factory,
            self._ai_manager,
            now=effective_now,
            ai_profile=self._ai_profile,
            target_scan_limit=self._fan_out_target_scan_limit,
            task_budget=self._fan_out_task_budget,
            per_target_task_cap=self._fan_out_per_target_task_cap,
            concurrency=self._fan_out_concurrency,
            firecrawl=self._firecrawl,
            settings=self._settings,
        )

        # Fase A: transação curta, só dados locais -- nenhum Playwright,
        # HTTP ou IA acontece dentro deste bloco. `claim_due_work`
        # (TASK-112 fase 3B) já claima os dois caminhos (legado +
        # compartilhado) dentro desta mesma transação -- ver o bloco de
        # comentário antes de `claim_due_work` para o desenho completo.
        async with self._session_factory() as session, session.begin():
            # TASK-108: lida a cada batch -- override do ADMIN
            # (`CollectionQueueConfig`, persistido) tem prioridade sobre os
            # valores default deste orchestrator; sem override, usa
            # exatamente os defaults de sempre. Nunca precisa de restart.
            queue_config_row = await session.get(
                CollectionQueueConfig, COLLECTION_QUEUE_CONFIG_ID
            )
            queue_config = resolve_queue_config(
                queue_config_row,
                default_max_concurrent_user_batches=self._max_concurrent_user_batches,
                default_user_cooldown_min_seconds=self._user_cooldown_min_seconds,
                default_user_cooldown_max_seconds=self._user_cooldown_max_seconds,
                default_store_min_interval_seconds=self._store_min_interval_seconds,
            )
            schedules = await ensure_missing_schedules(
                session,
                now=effective_now,
                interval_minutes=self._schedule_interval_minutes,
                stagger_seconds=self._schedule_stagger_seconds,
            )
            stale = await recover_stale_runs(
                session, now=effective_now, stale_after=self._stale_after
            )
            batch = await claim_due_work(
                session,
                now=effective_now,
                limit=limit,
                max_users=queue_config.max_concurrent_user_batches,
                user_cooldown_min_seconds=queue_config.user_cooldown_min_seconds,
                user_cooldown_max_seconds=queue_config.user_cooldown_max_seconds,
                store_min_interval_seconds=queue_config.store_min_interval_seconds,
                cadence_config=self._cadence_config,
                candidate_scan_limit=self._candidate_scan_limit,
                settings=self._settings,
            )
        # Transação da Fase A já fechada neste ponto (fim do `async with`
        # acima) -- a Fase B (TASK-083) roda inteiramente fora dela.
        if schedules:
            logger.info(
                "collection_schedules_created", extra={"schedule_count": schedules}
            )
        if batch.high_activity_detected and self._settings is not None:
            # DEC-132: correção do DEC-130 -- a notificação ao Coupon
            # Worker é I/O de rede e NUNCA pode rodar dentro da transação
            # de claim (que segura `FOR UPDATE` em `MissionSource`/
            # `MonitoringItemStore`). `claim_due_work` só sinalizou o
            # FATO (`batch.high_activity_detected`); o claim já está
            # commitado neste ponto -- o aviso é estritamente posterior,
            # nunca pode desfazer nem alterar o que já foi persistido.
            # `try/except` aqui é redundância deliberada sobre o
            # best-effort já embutido em `notify_coupon_worker_high_
            # activity` (nunca levanta na prática) -- garante que nenhum
            # erro inesperado nesta chamada jamais vaze para fora de
            # `run_batch` e derrube o worker por causa de uma notificação
            # de cadência, que é sempre opcional.
            try:
                await notify_coupon_worker_high_activity(
                    self._settings, now=effective_now
                )
            except Exception:
                logger.warning(
                    "coupon_worker_promo_notify_failed", exc_info=True
                )
        old_claims = await self._resolve_identities(batch.old_path)
        old_outcomes, shared_outcomes = await asyncio.gather(
            asyncio.gather(*(self._process(claim) for claim in old_claims)),
            asyncio.gather(
                *(self._process_shared(claim, effective_now) for claim in batch.shared)
            ),
        )
        legacy_succeeded = sum(old_outcomes)
        legacy_failed = len(old_claims) - legacy_succeeded
        shared_succeeded = sum(1 for outcome in shared_outcomes if outcome.succeeded)
        shared_failed = len(batch.shared) - shared_succeeded

        return CollectionBatchResult(
            claimed=len(old_claims) + len(batch.shared),
            succeeded=legacy_succeeded + shared_succeeded,
            failed=legacy_failed + shared_failed,
            recovered_stale=stale,
            legacy_claimed=len(old_claims),
            legacy_succeeded=legacy_succeeded,
            legacy_failed=legacy_failed,
            shared_claimed=len(batch.shared),
            shared_succeeded=shared_succeeded,
            shared_failed=shared_failed,
            fan_out_attempted_task_count=(
                fan_out_summary.attempted_task_count
                + sum(outcome.attempted_task_count for outcome in shared_outcomes)
            ),
            fan_out_done_mission_count=(
                fan_out_summary.fanned_out_mission_count
                + sum(
                    len(outcome.fanned_out_mission_ids) for outcome in shared_outcomes
                )
            ),
            fan_out_attention_required_mission_count=(
                fan_out_summary.attention_required_mission_count
                + sum(
                    len(outcome.fan_out_attention_required_mission_ids)
                    for outcome in shared_outcomes
                )
            ),
            fan_out_terminally_failed_mission_count=(
                fan_out_summary.terminally_failed_mission_count
                + sum(
                    len(outcome.fan_out_failed_mission_ids)
                    for outcome in shared_outcomes
                )
            ),
        )

    async def _process_shared(
        self, claim: "_SharedClaim", effective_now: datetime
    ) -> "SharedCollectionResult":
        """Análogo a `self._process` (teto de tempo, TASK-079 item 7) para
        o caminho compartilhado -- o claim em si JÁ aconteceu dentro da
        Fase A (`claim_due_work`); aqui só roda o "rabo" (rede +
        persistência + fan-out do trabalho novo, `_execute_claimed_
        shared_collection`), sob o MESMO `self._semaphore` do caminho
        antigo (Playwright/CDP é um recurso único, compartilhado entre os
        dois caminhos)."""
        try:
            return await asyncio.wait_for(
                self._process_shared_claim(claim, effective_now),
                timeout=self._claim_deadline_seconds,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            logger.warning(
                "shared_collection_claim_deadline_exceeded",
                extra={"source_code": _safe_source(claim.store_code)},
            )
            from app.collection.shared_collection import SharedCollectionResult

            return SharedCollectionResult(
                claimed=True, provider_called=False, succeeded=False
            )

    async def _process_shared_claim(
        self, claim: "_SharedClaim", effective_now: datetime
    ) -> "SharedCollectionResult":
        async with self._semaphore:
            return await self._shared_collector(
                self._session_factory,
                self._adapter,
                self._ai_manager,
                claim=claim,
                ai_profile=self._ai_profile,
                normalizer=self._normalizer,
                effective_now=effective_now,
                base_backoff_minutes=self._cadence_config.normal_min_minutes,
                firecrawl=self._firecrawl,
                settings=self._settings,
            )

    async def _resolve_identities(
        self, claims: tuple[ClaimedCollection, ...]
    ) -> tuple[ClaimedCollection, ...]:
        """Fase B (TASK-083): sempre roda fora de qualquer transação do
        orchestrator -- só é alcançada depois que o `async with` da Fase A
        em `run_batch` já terminou. Resolve cada `mission_id` com
        identidade ainda crua no máximo uma vez neste batch (dedupe
        determinístico), aplica o resultado em memória a todos os claims
        daquela missão para a coleta deste batch, e promove a identidade
        confirmada à missão (Fase B.1, correção de regressão --
        `_promote_resolved_identities`) para que o próximo batch não
        precise resolver de novo. Resolver ausente, retorno `None`,
        timeout ou falha operacional nunca impedem a coleta: o pior caso
        é manter `search_query` original em todos os claims."""
        if self._identity_resolver is None:
            return claims

        resolved: dict[UUID, str] = {}
        attempted: set[UUID] = set()
        for claim in claims:
            if claim.mission_id in attempted or not _needs_identity_resolution(claim):
                continue
            attempted.add(claim.mission_id)
            identity = await self._resolve_identity_safely(claim)
            if identity is not None:
                resolved[claim.mission_id] = identity.search_query

        if not resolved:
            return claims
        await self._promote_resolved_identities(resolved)
        return tuple(
            replace(claim, search_query=resolved[claim.mission_id])
            if claim.mission_id in resolved
            else claim
            for claim in claims
        )

    async def _resolve_identity_safely(
        self, claim: ClaimedCollection
    ) -> ResolvedProductIdentity | None:
        try:
            return await self._identity_resolver.resolve(claim.model)
        except asyncio.CancelledError:
            raise
        except Exception:
            # TASK-083: mesmo boundary de `_process_claim` -- resolução de
            # identidade é enriquecimento, nunca requisito para a coleta
            # existir. Logado para não esconder bug de programação real,
            # mas nunca derruba o batch.
            logger.warning(
                "identity_resolution_failed",
                extra={"mission_id": str(claim.mission_id)},
                exc_info=True,
            )
            return None

    async def _promote_resolved_identities(self, resolved: dict[UUID, str]) -> None:
        """Fase B.1 (TASK-083, correção de regressão): persiste, uma vez
        por `mission_id`, a identidade que acabou de ser confirmada por
        `ProductIdentityResolver` -- sempre depois que a Fase B (Playwright)
        já terminou por completo, cada missão em sua própria transação
        curta (nunca uma transação por claim, nunca uma transação
        compartilhada entre missões diferentes). Falha ao persistir nunca
        derruba o batch nem desfaz o enriquecimento em memória já aplicado
        aos claims -- o pior caso é resolver de novo no próximo batch."""
        for mission_id, confirmed_search_query in resolved.items():
            try:
                async with self._session_factory() as session, session.begin():
                    await promote_confirmed_product_identity_async(
                        session,
                        mission_id=mission_id,
                        confirmed_search_query=confirmed_search_query,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "identity_promotion_failed",
                    extra={"mission_id": str(mission_id)},
                    exc_info=True,
                )

    async def _process(self, claim: ClaimedCollection) -> bool:
        """Teto de tempo para a claim inteira (TASK-079, item 7): nenhuma
        claim trava o worker indefinidamente, mesmo diante de um bug futuro
        não coberto pelos timeouts específicos (navegação, HTTP, IA,
        banco) já existentes em cada camada."""
        try:
            return await asyncio.wait_for(
                self._process_claim(claim), timeout=self._claim_deadline_seconds
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            logger.warning(
                "collection_claim_deadline_exceeded",
                extra={"source_code": _safe_source(claim.source_code)},
            )
            await self._record_failure_safely(claim, "claim_deadline_exceeded")
            return False

    async def _process_claim(self, claim: ClaimedCollection) -> bool:
        try:
            async with self._semaphore:
                result = await self._adapter.collect(
                    CollectionRequest(
                        mission_id=claim.mission_id,
                        source_code=claim.source_code,
                        search_query=claim.search_query,
                        requested_at=claim.requested_at,
                    )
                )
                normalized = self._normalizer.normalize_result(result)
                selected = _select_final_candidates(
                    search_query=claim.search_query,
                    model=claim.model,
                    source_code=claim.source_code,
                    offers=normalized.offers,
                )
                selected_raw = tuple(item.raw_offer for item in selected)
                try:
                    enriched_raw = await self._adapter.enrich_offer_details(
                        claim.source_code, selected_raw
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning(
                        "offer_detail_enrichment_failed",
                        extra={"source_code": _safe_source(claim.source_code)},
                        exc_info=True,
                    )
                    enriched_raw = selected_raw
            enriched = tuple(
                replace(item, raw_offer=raw_offer)
                for item, raw_offer in zip(selected, enriched_raw, strict=True)
            )
            normalized = NormalizedCollectionResult(
                raw_result=replace(result, offers=enriched_raw),
                offers=enriched,
            )
            phase_a = await _persist_phase_a(
                self._session_factory, claim, normalized, preselected=True
            )
            if phase_a is None:
                return False
            ai_outcomes = await _run_phase_b(
                phase_a,
                self._ai_manager,
                self._ai_profile,
                session_factory=self._session_factory,
                firecrawl=self._firecrawl,
                settings=self._settings,
            )
            return await _persist_phase_c(
                self._session_factory,
                phase_a,
                ai_outcomes,
                settings=self._settings,
            )
        except asyncio.CancelledError:
            raise
        except IntegrityError:
            logger.exception(
                "collection_integrity_failure",
                extra={"source_code": _safe_source(claim.source_code)},
            )
            raise
        except Exception as error:
            failure_code = _failure_code(error)
            confirmed_block = _is_confirmed_external_block(error)
            await self._record_failure_safely(
                claim, failure_code, confirmed_block=confirmed_block
            )
            logger.warning(
                "collection_source_failed",
                extra={
                    "source_code": _safe_source(claim.source_code),
                    "failure_code": failure_code,
                    **_failure_log_context(error),
                },
            )
            return False

    async def _record_failure_safely(
        self,
        claim: ClaimedCollection,
        failure_code: str,
        *,
        confirmed_block: bool = False,
    ) -> None:
        try:
            async with self._session_factory() as session, session.begin():
                await _record_failure(
                    session,
                    claim,
                    failure_code,
                    utc_now(),
                    confirmed_block=confirmed_block,
                )
        except Exception:
            logger.exception(
                "collection_failure_recording_failed",
                extra={"source_code": _safe_source(claim.source_code)},
            )


def _needs_identity_resolution(claim: ClaimedCollection) -> bool:
    """TASK-083: identidade ainda crua -- `model` existe e `search_query`
    é essencialmente só o próprio código (tolerando separador/maiúscula
    via `same_code`), sem nenhuma canonicalização incorporada. Nunca usa
    comparação textual literal (`==`): "9800-X3D"/"9800 X3D"/"9800x3d" são
    todos reconhecidos como o mesmo `model` "9800X3D". Uma `search_query`
    com qualquer palavra a mais (ex.: "AMD Ryzen 7 9800X3D") já não bate
    mais em `same_code` -- não é tratada como crua. Busca genérica
    (`model is None`) nunca aciona resolução."""
    return claim.model is not None and _same_code(claim.search_query, claim.model)


def _title_looks_like_bundle(search_query: str, title: str) -> bool:
    """TASK-075: True quando o título tem palavra-sinal de sistema
    completo/kit que o `search_query` da missão não tem -- a missão pede o
    componente avulso e o candidato é um pacote maior. Roda sempre,
    independente de `criteria.model`."""
    normalized_query = _normalize_for_matching(search_query)
    normalized_title = _normalize_for_matching(title)
    for word in _BUNDLE_SIGNAL_WORDS:
        signal = _normalize_for_matching(word)
        if signal in normalized_title and signal not in normalized_query:
            return True
    return False


def _filter_deterministic_candidates(
    criteria: MissionCriteria,
    offers: tuple,
) -> tuple:
    """TASK-075: passo único, roda antes de qualquer persistência ou
    chamada de IA (uma vez por execução -- nunca reavaliado depois).
    Rejeita só incompatibilidade determinística segura: modelo
    comprovadamente diferente (só quando `criteria.model` existe) ou sinal
    de bundle/PC completo. Candidato ambíguo sempre sobrevive, seguindo
    pro fluxo de relevância existente."""
    survivors = []
    for item in offers:
        title = item.raw_offer.title
        if criteria.model is not None and not _title_matches_model(
            criteria.model, title
        ):
            continue
        if _title_looks_like_bundle(criteria.search_query, title):
            continue
        survivors.append(item)
    return tuple(survivors)


def _select_final_candidates(
    *, search_query: str, model: str | None, source_code: str, offers: tuple
) -> tuple:
    """Seleciona um pool intermediário comum antes da IA e do detalhe.

    `source_code` permanece no contrato do helper para compatibilidade dos
    chamadores, mas nenhuma loja recebe regra comercial exclusiva aqui.
    """
    survivors = tuple(
        item
        for item in offers
        if (model is None or _title_matches_model(model, item.raw_offer.title))
        and not _title_looks_like_bundle(search_query, item.raw_offer.title)
    )
    return _limit_intermediate_candidates(
        survivors, limit=_PRELIST_INTERMEDIATE_CANDIDATE_LIMIT
    )


_PRELIST_INTERMEDIATE_CANDIDATE_LIMIT = 8
"""TASK-094: cinco saídas por loja mais três posições de diversidade.

Evita voltar aos até 20 cards brutos/classificações por loja, mas não corta
exatamente no top 5 antes de relevância e enriquecimento comercial.
"""

_CONDITION_RANK = {
    OfferCondition.NEW: 0,
    OfferCondition.REFURBISHED: 1,
    OfferCondition.USED: 2,
    OfferCondition.UNKNOWN: 3,
}
_SELLER_RANK = {
    MarketplacePartyKind.PLATFORM: 0,
    MarketplacePartyKind.MARKETPLACE_PARTNER: 1,
    MarketplacePartyKind.UNKNOWN: 2,
    None: 2,
}
_AVAILABILITY_RANK = {
    Availability.AVAILABLE: 0,
    Availability.UNKNOWN: 1,
    Availability.UNAVAILABLE: 2,
}


def _limit_intermediate_candidates(offers: tuple, *, limit: int) -> tuple:
    """Limita cedo sem reduzir o conjunto a preço absoluto."""
    ordered = sorted(
        offers,
        key=lambda item: (
            _CONDITION_RANK[item.condition],
            _SELLER_RANK[item.seller_kind],
            _AVAILABILITY_RANK[item.availability],
            item.amount,
            item.total_amount,
            item.raw_offer.external_id or item.raw_offer.url,
        ),
    )
    return tuple(ordered[:limit])


class PriceObservationComparison(enum.Enum):
    """Resultado explícito do dedupe da TASK-093, na fonte (Fase A), para o
    contrato de avaliação de alerta de preço da Fase C -- descreve a relação
    entre a observação atual da oferta e a observação anterior DESTA missão
    (não entre a atual e a última observação global da oferta, que é um
    dedupe ortogonal). Fase C nunca mais deve inferir isso comparando UUIDs
    reconstruídos por acidente; ela só lê este campo.

    FIRST_OBSERVATION: não existe observação anterior desta missão para esta
    oferta -- semântica de primeira observação (evaluator recebe
    ``previous=None``).

    CHANGED: existe observação anterior desta missão e ela é uma linha
    diferente da observação atual -- comparação real, evaluator roda normal.

    UNCHANGED_REUSED: existe observação anterior desta missão e ela É a
    mesma linha que a observação atual (a Fase A reaproveitou a última
    observação global por estado comercial idêntico, e essa reaproveitada
    também é a última observação já vista por esta missão). Não é uma nova
    comparação -- é reconfirmação do mesmo estado já avaliado antes. Não
    chamar o evaluator; resultado normal, zero alertas, não é erro.
    """

    FIRST_OBSERVATION = "first_observation"
    CHANGED = "changed"
    UNCHANGED_REUSED = "unchanged_reused"


@dataclass(frozen=True, slots=True)
class _PendingOffer:
    """Uma oferta já persistida na Fase A; o que falta decidir na Fase B."""

    offer_id: UUID
    product_id: UUID
    observation_id: UUID
    amount: Decimal
    currency: str
    availability: Availability
    observed_at: datetime
    raw_title: str
    needs_relevance: bool
    needs_display_name: bool
    observation_created: bool
    alert_comparison: PriceObservationComparison
    previous_observation_id: UUID | None
    previous_amount: Decimal | None
    previous_currency: str | None
    previous_availability: Availability | None
    previous_observed_at: datetime | None
    forced_relevance: OfferRelevance | None = None


@dataclass(frozen=True, slots=True)
class _PhaseAOutcome:
    """Só valores simples -- nunca objetos ORM presos a uma sessão já
    fechada (TASK-079: evita qualquer risco de instância desanexada
    atravessando a fronteira entre fases)."""

    run_id: UUID
    mission_id: UUID
    store_id: UUID
    mission_search_query: str
    target_amount: Decimal | None
    target_currency: str | None
    completed_at: datetime
    offers: tuple[_PendingOffer, ...]


@dataclass(frozen=True, slots=True)
class _AIOutcome:
    offer_id: UUID
    relevance: OfferRelevance | None
    display_title: str | None
    market_snapshot: AssessmentSnapshot | None = None
    """TASK-113: `MarketPriceAssessment` corrente (cache reaproveitado ou
    recém-pesquisado) para o `product_id` desta oferta, quando a Fase B
    conseguiu resolver um -- `None` quando pesquisa foi pulada
    (identidade não resolvida, gatilho não disparou, ou outro worker
    está com o claim). Valor simples (TASK-079), nunca ORM."""
    applied_coupon: AppliedCoupon | None = None
    """Consumo de cupons (2026-09-06): melhor cupom aplicável calculado
    na Fase B, reaproveitado pela Fase C para usar o preço FINAL com
    cupom na decisão de alerta -- nunca o preço original coletado, que
    continua intacto em `PriceObservation.amount`. `None` quando não há
    cupom aplicável ou a etapa de cupom falhou (nunca derruba a coleta,
    ver `_classify`)."""


def _deterministic_product_relevance(
    criteria: MissionCriteria, product: Product
) -> OfferRelevance | None:
    """Rejeita diferenças comprovadas antes da IA; desconhecido segue fail-soft."""
    request_kind = getattr(
        criteria, "request_kind", ProductRequestKind.GENERIC_CATEGORY.value
    )
    if request_kind == ProductRequestKind.SPECIFIC_PRODUCT.value:
        if (
            getattr(product, "identity_key", None) is None
            or product.identity_key != criteria.requested_identity_key
        ):
            return OfferRelevance.NO_MATCH
    elif request_kind == ProductRequestKind.PRODUCT_FAMILY.value:
        if (
            getattr(product, "family_key", None) is not None
            and product.family_key != criteria.requested_family_key
        ):
            return OfferRelevance.NO_MATCH
        if (
            getattr(criteria, "requested_variant", None) is not None
            and getattr(product, "variant", None) is not None
            and product.variant != criteria.requested_variant
        ):
            return OfferRelevance.NO_MATCH
    return None


async def _product_selected_for_mission(
    session: AsyncSession,
    *,
    mission_id: UUID,
    product_id: UUID,
    request_kind: str,
    selection_mode: VariantSelectionMode,
) -> bool:
    if request_kind != ProductRequestKind.PRODUCT_FAMILY.value:
        return True
    product = await session.get(Product, product_id)
    if product is None or product.identity_key is None:
        return False
    if selection_mode is VariantSelectionMode.ALL:
        return True
    if selection_mode is not VariantSelectionMode.SELECTED:
        return False
    return (
        await session.get(MissionProductSelection, (mission_id, product_id)) is not None
    )


async def _persist_phase_a(
    session_factory: async_sessionmaker[AsyncSession],
    claim: ClaimedCollection,
    normalized: NormalizedCollectionResult,
    *,
    preselected: bool = False,
) -> _PhaseAOutcome | None:
    """Fase A (TASK-079): transação curta e só trabalho local -- nenhum
    `await` externo (IA/HTTP/Playwright) acontece com esta transação
    aberta. Resolve/gera Product/Offer/Seller, grava PriceObservation,
    decide o que precisa de IA na Fase B, e libera o lock da missão no
    COMMIT."""
    async with session_factory() as session, session.begin():
        run = await session.scalar(
            select(CollectionRun)
            .where(CollectionRun.id == claim.run_id)
            .with_for_update()
        )
        if run is None or run.status is not CollectionRunStatus.RUNNING:
            return None
        mission = await session.get(Mission, claim.mission_id)
        criteria = await session.scalar(
            select(MissionCriteria).where(
                MissionCriteria.mission_id == claim.mission_id
            )
        )
        if mission is None or criteria is None:
            raise RuntimeError("claimed mission data no longer exists")

        # TASK-075: passo único de filtragem determinística, antes de
        # qualquer persistência ou chamada de IA. A seleção por menor
        # preço da Amazon só roda quando criteria.model já confirmou
        # identidade forte (gate obrigatório) -- sem modelo, cada
        # sobrevivente segue independente, nunca "o mais barato" é
        # escolhido sem identidade confirmada.
        final_offers = (
            normalized.offers
            if preselected
            else _select_final_candidates(
                search_query=criteria.search_query,
                model=criteria.model,
                source_code=claim.source_code,
                offers=normalized.offers,
            )
        )

        pending: list[_PendingOffer] = []

        # Subtask 6 (correção estrutural de deadlock -- race entre
        # missões concorrentes na MESMA Offer/Product): a Fase A de
        # missões diferentes roda em paralelo de verdade (`asyncio.
        # gather`, comentário da classe acima) -- só a Fase C é
        # serializada por `mission_id`.
        #
        # Três passos, nesta ordem exata, nunca misturados:
        #
        # 1) IDENTIFICAÇÃO SOMENTE LEITURA (`_preview_existing_offer_and_
        #    product`) -- zero mutação, zero `session.add`, zero
        #    `begin_nested()`. Precisa ser assim: `Session.begin_nested()`
        #    força `flush()` incondicional de TODO o estado pendente da
        #    sessão antes do SAVEPOINT, mesmo com `autoflush=False` (só
        #    afeta flush automático antes de query). Se `_resolve_offer`
        #    (que cria Seller/Offer/Product novos via `begin_nested()`)
        #    rodasse aqui, misturado com o loop, o `begin_nested()` de um
        #    item flusharia -- e travaria -- uma mutação pendente de um
        #    item ANTERIOR (ex. `canonical_image_url`) na ordem de
        #    iteração comercial, não na ordem global determinística.
        #
        # 2) TRAVA GLOBAL, sobre uma sessão comprovadamente limpa (nenhum
        #    flush ainda ocorreu): todas as Offers já existentes,
        #    ordenadas por `offer.id`, depois todos os Products já
        #    existentes, ordenados por `product_id` -- sempre nessa ordem
        #    relativa entre as duas fases, em toda transação, nunca na
        #    ordem de seleção comercial (que pode variar entre duas
        #    coletas quase simultâneas da mesma Offer -- preço é critério
        #    de `_limit_intermediate_candidates` e pode oscilar de
        #    verdade, ex. flash sale). `SELECT ... FOR UPDATE`, não
        #    `asyncio.Lock` -- mesmo motivo do lock de `CollectionRun`:
        #    continua correto com múltiplos processos de worker.
        #
        # 3) RESOLUÇÃO DE VERDADE (`_resolve_offer`, com mutação/criação
        #    e `begin_nested()` quando preciso) -- agora seguro: qualquer
        #    linha que já existia está travada antes de qualquer flush
        #    poder tocá-la; linhas genuinamente novas não têm concorrência
        #    de ordem entre transações (o retry por `IntegrityError` já
        #    cobre a corrida de criação).
        #
        # A ordem de seleção comercial (`final_offers`, decide qual
        # sobrevivente "vence" na Amazon etc.) continua completamente
        # intocada nos três passos.
        items_by_key: dict[tuple[str | None, str | None, str], Any] = {}
        preview: dict[
            tuple[str | None, str | None, str], tuple[Offer | None, Product | None]
        ] = {}
        creation_lock_keys: set[str] = set()
        for item in final_offers:
            identity_key = (
                item.seller_external_id,
                item.raw_offer.external_id,
                item.raw_offer.external_id or item.raw_offer.url,
            )
            if identity_key in items_by_key:
                continue
            items_by_key[identity_key] = item
            preview[identity_key] = await _preview_existing_offer_and_product(
                session, claim.store_id, item
            )
            creation_lock_keys |= _creation_lock_keys(claim.store_id, item)

        # Fase 0 (correção estrutural de deadlock, segunda rodada): trava
        # transacional por CHAVE LÓGICA (`pg_advisory_xact_lock`, não
        # `SELECT ... FOR UPDATE`, porque as linhas podem ainda nem
        # existir) para todo Seller/Product/Offer que este lote possa vir
        # a CRIAR -- sempre antes das fases de linha física abaixo e de
        # `_resolve_offer`. Ver docstring de `_creation_lock_keys`.
        await _acquire_creation_locks(session, frozenset(creation_lock_keys))

        offer_ids = sorted({o.id for o, _ in preview.values() if o is not None})
        for offer_id in offer_ids:
            await session.scalar(
                select(Offer.id).where(Offer.id == offer_id).with_for_update()
            )
        product_ids = sorted({p.id for _, p in preview.values() if p is not None})
        for product_id in product_ids:
            await session.scalar(
                select(Product.id).where(Product.id == product_id).with_for_update()
            )

        resolved_offers: dict[tuple[str | None, str | None, str], Offer] = {}
        resolved_items: list[tuple[tuple[str | None, str | None, str], Any]] = []
        for identity_key, item in items_by_key.items():
            offer = await _resolve_offer(session, claim.store_id, item)
            resolved_offers[identity_key] = offer
            resolved_items.append((identity_key, item))

        for identity_key, item in resolved_items:
            offer = resolved_offers[identity_key]
            # DEC-048/TASK-063: escopado por missão -- duas missões
            # diferentes que coletem a mesma Offer nao compartilham mais o
            # "ultimo preco visto" para fins de cruzamento de alvo.
            previous = await session.scalar(
                select(PriceObservation)
                .join(
                    CollectionRun,
                    CollectionRun.id == PriceObservation.collection_run_id,
                )
                .where(
                    PriceObservation.offer_id == offer.id,
                    CollectionRun.mission_id == claim.mission_id,
                )
                .order_by(
                    PriceObservation.observed_at.desc(), PriceObservation.id.desc()
                )
                .limit(1)
            )
            latest = await session.scalar(
                select(PriceObservation)
                .where(PriceObservation.offer_id == offer.id)
                .order_by(
                    PriceObservation.observed_at.desc(), PriceObservation.id.desc()
                )
                .limit(1)
            )
            current_state = _CommercialState.from_normalized_offer(item)
            redundant = False
            if latest is not None and _same_commercial_state(latest, current_state):
                latest_installments = list(
                    await session.scalars(
                        select(OfferInstallmentOption).where(
                            OfferInstallmentOption.price_observation_id == latest.id
                        )
                    )
                )
                redundant = _installment_snapshot(
                    latest_installments
                ) == _installment_snapshot(current_state.installments)

            if redundant:
                # TASK-093: estado comercial (preço, moeda, disponibilidade,
                # vendedor/fulfillment, parcelamento) idêntico ao já
                # registrado -- não grava PriceObservation redundante.
                # Histórico append-only intocado; `offer.last_seen_at`
                # (abaixo) é o único registro de que esta coleta confirmou
                # a oferta.
                observation = latest
            else:
                # Subtask 6 (correção de causa raiz): construído a partir
                # de `current_state` -- a MESMA instância comparada acima
                # -- nunca relendo `item` de novo (ver docstring de
                # `_CommercialState`).
                observation = PriceObservation(
                    offer_id=offer.id,
                    collection_run_id=run.id,
                    amount=current_state.amount,
                    currency=current_state.currency,
                    shipping_amount=current_state.shipping_amount,
                    total_amount=current_state.total_amount,
                    fulfillment=current_state.fulfillment,
                    seller_kind=current_state.seller_kind,
                    fulfillment_kind=current_state.fulfillment_kind,
                    condition=current_state.condition,
                    availability=current_state.availability,
                    observed_at=item.raw_offer.collected_at,
                    raw_evidence=_raw_evidence(item.raw_offer),
                )
                session.add(observation)
                await session.flush()
                for option in current_state.installments:
                    session.add(
                        OfferInstallmentOption(
                            price_observation_id=observation.id,
                            installment_count=option.installment_count,
                            installment_amount=option.installment_amount,
                            installment_total_amount=option.installment_total_amount,
                            discount_percent=option.discount_percent,
                            interest_kind=option.interest_kind,
                            is_highlighted=option.is_highlighted,
                        )
                    )
                if current_state.installments:
                    await session.flush()
            offer.last_seen_at = item.raw_offer.collected_at

            # Correção arquitetural (dedupe da TASK-093 x contrato de
            # alertas, DEC-097): fonte única de verdade do dedupe, calculada
            # aqui -- junto da lógica que efetivamente decide `observation`
            # -- para a Fase C nunca mais deduzir isso comparando IDs
            # reconstruídos por acidente.
            observation_created = not redundant
            if previous is None:
                alert_comparison = PriceObservationComparison.FIRST_OBSERVATION
            elif previous.id == observation.id:
                alert_comparison = PriceObservationComparison.UNCHANGED_REUSED
            else:
                alert_comparison = PriceObservationComparison.CHANGED

            if (
                previous is not None
                and previous.availability != observation.availability
            ):
                # Não depende de IA -- publicado já na Fase A, sem motivo
                # para adiar para a Fase C.
                await publish_event_async(
                    session,
                    event_type=EventType.AVAILABILITY_CHANGED_V1,
                    aggregate_type=AggregateType.OFFER,
                    aggregate_id=offer.id,
                    payload=AvailabilityChangedPayload(
                        offer.id,
                        observation.id,
                        previous.id,
                        previous.availability,
                        observation.availability,
                    ),
                    occurred_at=normalized.raw_result.completed_at,
                    mission_id=mission.id,
                )

            existing_relevance = await session.get(
                MissionOfferRelevance, (mission.id, offer.id)
            )
            product = await session.get(Product, offer.product_id)
            if product is None:
                raise RuntimeError("offer references a missing product")
            forced_relevance = _deterministic_product_relevance(criteria, product)

            pending.append(
                _PendingOffer(
                    offer_id=offer.id,
                    product_id=offer.product_id,
                    observation_id=observation.id,
                    amount=observation.amount,
                    currency=observation.currency,
                    availability=observation.availability,
                    observed_at=observation.observed_at,
                    raw_title=item.raw_offer.title,
                    needs_relevance=(
                        existing_relevance is None and forced_relevance is None
                    ),
                    needs_display_name=product.display_name is None,
                    observation_created=observation_created,
                    alert_comparison=alert_comparison,
                    forced_relevance=forced_relevance,
                    previous_observation_id=previous.id
                    if previous is not None
                    else None,
                    previous_amount=previous.amount if previous is not None else None,
                    previous_currency=previous.currency
                    if previous is not None
                    else None,
                    previous_availability=(
                        previous.availability if previous is not None else None
                    ),
                    previous_observed_at=(
                        previous.observed_at if previous is not None else None
                    ),
                )
            )

        return _PhaseAOutcome(
            run_id=run.id,
            mission_id=mission.id,
            store_id=run.store_id,
            mission_search_query=criteria.search_query,
            target_amount=criteria.target_amount,
            target_currency=criteria.target_currency,
            completed_at=normalized.raw_result.completed_at,
            offers=tuple(pending),
        )


async def _run_phase_b(
    outcome: _PhaseAOutcome,
    ai_manager: AIProviderManager,
    ai_profile: UserRole,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    firecrawl: CesarCoreFetchProvider | None = None,
    settings: Settings | None = None,
) -> tuple[_AIOutcome, ...]:
    """Fase B (TASK-079): nenhuma transação aberta -- só chamadas de IA
    (e, TASK-113, Firecrawl -- mesma disciplina: nenhum lock retido).

    As chamadas de ofertas diferentes podem rodar em paralelo entre si,
    já que não há lock nem sessão de banco compartilhada retida aqui
    (leituras/upserts de pesquisa de mercado usam suas próprias
    transações curtas, `app.market_research.service`).

    Pesquisa de mercado só é considerada quando `session_factory`/
    `firecrawl`/`settings` são fornecidos (produção, via
    `CollectionOrchestrator`) -- `None` em qualquer um deles desliga o
    recurso por completo, comportamento idêntico ao anterior a esta
    TASK (nenhum chamador de teste/script existente precisa mudar)."""
    market_research_enabled = session_factory is not None and settings is not None

    async def _classify(pending: _PendingOffer) -> _AIOutcome:
        relevance = pending.forced_relevance
        if pending.needs_relevance:
            relevance = await classify_offer_relevance(
                ai_manager,
                mission_search_query=outcome.mission_search_query,
                raw_title=pending.raw_title,
                profile=ai_profile,
            )
        display_title = None
        if pending.needs_display_name:
            display_title = await normalize_offer_title(
                ai_manager, raw_title=pending.raw_title, profile=ai_profile
            )
        effective_relevance = relevance
        if (
            market_research_enabled
            and effective_relevance is None
            and pending.forced_relevance is None
            and not pending.needs_relevance
        ):
            # TASK-113: nem forçada nesta rodada, nem precisando de
            # reclassificação agora -- só pode ser um MATCH decidido em
            # um ciclo ANTERIOR (`MissionOfferRelevance`, lido de novo
            # normalmente na Fase C). Sem reler aqui, toda oferta já
            # classificada há mais de um ciclo nunca disparava pesquisa
            # de mercado -- exatamente o caso mais comum (Mission rodando
            # há semanas), nunca só a exceção. Leitura pura, sem lock,
            # sem transação -- mesma disciplina de Fase B.
            assert session_factory is not None
            async with session_factory() as lookup_session:
                cached = await lookup_session.get(
                    MissionOfferRelevance, (outcome.mission_id, pending.offer_id)
                )
            effective_relevance = cached.classification if cached is not None else None
        market_snapshot = None
        if (
            market_research_enabled
            and effective_relevance is OfferRelevance.MATCH
            and settings.historical_bootstrap_enabled
        ):
            assert session_factory is not None
            assert settings is not None
            # `search` é uma fábrica, nunca um `WebSearchManager` já
            # construído -- `run_historical_bootstrap` só a invoca depois
            # de confirmar que precisa mesmo pesquisar (produto/identity_key
            # válidos, histórico interno insuficiente, bootstrap ainda não
            # feito). Search nunca é uma dependência antecipada de toda
            # coleta, só da funcionalidade que realmente a usa.
            await run_historical_bootstrap(
                session_factory,
                product_id=pending.product_id,
                search=lambda: build_web_search_manager(settings),
                fetch=firecrawl,
                ai=ai_manager,
                profile=ai_profile,
                now=outcome.completed_at,
                revalidation_days=settings.historical_bootstrap_revalidation_days,
                # Reaproveita o MESMO padrão já aprovado de
                # `MarketPriceAssessment` (TASK-113) -- nenhum config
                # novo criado para o bootstrap histórico.
                lease_seconds=settings.market_assessment_lease_seconds,
                failure_backoff_minutes=settings.market_assessment_failure_backoff_minutes,
                failure_backoff_max_minutes=settings.market_assessment_failure_backoff_max_minutes,
            )
        applied_coupon: AppliedCoupon | None = None
        if (
            market_research_enabled
            and effective_relevance is OfferRelevance.MATCH
            and pending.alert_comparison
            is not PriceObservationComparison.UNCHANGED_REUSED
        ):
            assert session_factory is not None
            assert settings is not None
            if settings.coupons_enabled:
                try:
                    async with session_factory() as coupon_session:
                        offer_row = await coupon_session.get(Offer, pending.offer_id)
                        if offer_row is not None:
                            candidates = await get_candidate_coupons_for_offer(
                                coupon_session,
                                offer_id=pending.offer_id,
                                store_id=outcome.store_id,
                            )
                            applied_coupon = best_applicable_coupon(
                                offer_row, candidates, pending.amount, pending.currency
                            )
                except Exception:
                    # Falha na etapa de cupom (infraestrutura/integração) NUNCA
                    # derruba o processamento normal da oferta -- segue com o
                    # preço original, como se nenhum cupom tivesse sido
                    # encontrado. Distinto de "consulta válida com zero
                    # candidatos" (que não cai aqui, só devolve `None` de
                    # `best_applicable_coupon` normalmente).
                    logger.warning(
                        "coupon_evaluation_failed",
                        extra={"offer_id": str(pending.offer_id)},
                        exc_info=True,
                    )
                    applied_coupon = None
            evaluation_amount = (
                applied_coupon.final_amount if applied_coupon is not None else pending.amount
            )
            market_snapshot = await evaluate_trigger_and_maybe_research(
                session_factory,
                ai_manager,
                firecrawl,
                mission_id=outcome.mission_id,
                product_id=pending.product_id,
                store_id=outcome.store_id,
                current_amount=evaluation_amount,
                current_currency=pending.currency,
                previous_amount=pending.previous_amount,
                target_amount=outcome.target_amount,
                profile=ai_profile,
                now=outcome.completed_at,
                settings=settings,
            )
        return _AIOutcome(
            pending.offer_id, relevance, display_title, market_snapshot, applied_coupon
        )

    to_process = [
        item
        for item in outcome.offers
        if item.needs_relevance or item.needs_display_name or market_research_enabled
    ]
    if not to_process:
        return ()
    return tuple(await asyncio.gather(*(_classify(item) for item in to_process)))


async def _persist_phase_c(
    session_factory: async_sessionmaker[AsyncSession],
    outcome: _PhaseAOutcome,
    ai_outcomes: tuple[_AIOutcome, ...],
    *,
    settings: Settings | None = None,
) -> bool:
    """Fase C (TASK-079): seção crítica por `mission_id`.

    `SELECT missions ... FOR UPDATE` seguido só de trabalho local de banco
    -- nenhum `await` externo acontece com este lock retido. Revalida o
    estado do run (pode ter virado stale durante a Fase B) antes de
    finalizar, e usa upsert idempotente para a classificação de IA, já que
    outra claim/processo pode ter persistido o mesmo resultado nesse
    meio-tempo.
    """
    ai_by_offer = {item.offer_id: item for item in ai_outcomes}
    async with session_factory() as session, session.begin():
        mission = await session.scalar(
            select(Mission).where(Mission.id == outcome.mission_id).with_for_update()
        )
        if mission is None:
            return False
        run = await session.scalar(
            select(CollectionRun)
            .where(CollectionRun.id == outcome.run_id)
            .with_for_update()
        )
        if run is None or run.status is not CollectionRunStatus.RUNNING:
            # recover_stale_runs (ou outro worker) já finalizou este run
            # enquanto a Fase B rodava -- nada a fazer, sem duplicar.
            return False
        current_criteria = await session.scalar(
            select(MissionCriteria).where(MissionCriteria.mission_id == mission.id)
        )
        if current_criteria is None:
            return False

        for pending in outcome.offers:
            ai_outcome = ai_by_offer.get(pending.offer_id)
            relevance: OfferRelevance | None = pending.forced_relevance
            if pending.forced_relevance is not None:
                await session.execute(
                    postgresql_insert(MissionOfferRelevance)
                    .values(
                        mission_id=mission.id,
                        offer_id=pending.offer_id,
                        classification=pending.forced_relevance,
                        classified_at=utc_now(),
                        # TASK-112 (fase 3A): última observação que esta
                        # Mission processou -- fonte de "previous" na
                        # coleta compartilhada (DEC-048), onde
                        # `PriceObservation.collection_run_id` deixa de
                        # apontar para uma run desta Mission. Populado em
                        # todo caminho, inclusive missão única -- nunca
                        # lido por ela, só pelo fan-out compartilhado
                        # (`app.collection.shared_collection`).
                        last_observation_id=pending.observation_id,
                    )
                    .on_conflict_do_update(
                        index_elements=list(_MISSION_OFFER_RELEVANCE_PK),
                        set_={
                            "classification": pending.forced_relevance,
                            "classified_at": utc_now(),
                            "last_observation_id": pending.observation_id,
                        },
                    )
                )
            elif pending.needs_relevance:
                if ai_outcome is not None and ai_outcome.relevance is not None:
                    relevance = ai_outcome.relevance
                    await session.execute(
                        postgresql_insert(MissionOfferRelevance)
                        .values(
                            mission_id=mission.id,
                            offer_id=pending.offer_id,
                            classification=relevance,
                            classified_at=utc_now(),
                            last_observation_id=pending.observation_id,
                        )
                        .on_conflict_do_update(
                            index_elements=list(_MISSION_OFFER_RELEVANCE_PK),
                            # Corrida com outra claim/processo que já
                            # persistiu a MESMA classificação nesse
                            # meio-tempo (comentário original) -- nunca
                            # sobrescreve classification/classified_at
                            # (preserva a intenção original), mas sempre
                            # avança last_observation_id: mesmo quando a
                            # classificação já existia, esta Mission
                            # acabou de processar esta observação agora.
                            set_={"last_observation_id": pending.observation_id},
                        )
                    )
            else:
                cached = await session.get(
                    MissionOfferRelevance, (mission.id, pending.offer_id)
                )
                relevance = cached.classification if cached is not None else None
                if cached is not None:
                    cached.last_observation_id = pending.observation_id

            if (
                pending.needs_display_name
                and ai_outcome is not None
                and ai_outcome.display_title is not None
            ):
                product = await session.get(Product, pending.product_id)
                if product is not None and product.display_name is None:
                    product.display_name = ai_outcome.display_title

            if (
                relevance is OfferRelevance.MATCH
                and pending.alert_comparison
                is not PriceObservationComparison.UNCHANGED_REUSED
                and await _product_selected_for_mission(
                    session,
                    mission_id=mission.id,
                    product_id=pending.product_id,
                    request_kind=current_criteria.request_kind,
                    selection_mode=current_criteria.variant_selection_mode,
                )
            ):
                # UNCHANGED_REUSED nunca chega aqui (guard acima): a Fase A
                # já disse explicitamente que não há nova comparação a
                # fazer -- estado comercial reconfirmado idêntico ao já
                # avaliado antes para esta missão. Resultado normal, zero
                # alertas, sem chamar o evaluator. Só FIRST_OBSERVATION e
                # CHANGED chegam aqui, e ambos garantem `current.id !=
                # previous.id` (quando previous existe), preservando o
                # guard de distinção do evaluator (app/alerts/evaluator.py).
                # Consumo de cupons (2026-09-06): quando a Fase B calculou
                # um cupom aplicável, a decisão de alerta usa o preço FINAL
                # com cupom como "candidato" -- nunca o `PriceObservation`
                # persistido (que já foi gravado com `pending.amount`
                # original na Fase A, intocado). `current` aqui é só um
                # valor efêmero pro evaluator (mesmo padrão já existente
                # para `previous` logo abaixo), nunca uma segunda linha no
                # banco.
                #
                # Correção (revisão pós-implementação, mesma sessão): o
                # evento gerado pelo evaluator precisa PRESERVAR qual foi
                # esse cupom -- o Telegram não pode mais buscar "o melhor
                # cupom agora" na hora de montar a mensagem (o cupom pode
                # ter expirado, sido atualizado, ou um cupom melhor pode
                # ter aparecido nesse meio-tempo; nada disso pode mudar o
                # que ESTA decisão específica anunciou). `coupon_snapshot`
                # é montado a partir do MESMO `AppliedCoupon` usado para
                # `evaluation_amount` -- `coupon_snapshot.final_amount ==
                # evaluation_amount == current.amount == current_total`
                # por construção; `AppliedCouponPayload`/`PriceDecreased
                # Payload`/`PriceTargetReachedPayload` (catálogo) validam
                # essa igualdade de novo como segunda linha de defesa.
                evaluation_amount = pending.amount
                coupon_snapshot: AppliedCouponPayload | None = None
                if ai_outcome is not None and ai_outcome.applied_coupon is not None:
                    applied = ai_outcome.applied_coupon
                    evaluation_amount = applied.final_amount
                    coupon_snapshot = AppliedCouponPayload(
                        coupon_id=applied.coupon_id,
                        code=applied.code,
                        discount_kind=applied.discount_kind,
                        original_amount=applied.original_amount,
                        discount_amount=applied.discount_amount,
                        final_amount=applied.final_amount,
                        currency=applied.currency,
                        raw_rule_text=applied.raw_rule_text,
                    )
                current = PriceObservation(
                    id=pending.observation_id,
                    offer_id=pending.offer_id,
                    amount=evaluation_amount,
                    currency=pending.currency,
                    availability=pending.availability,
                    observed_at=pending.observed_at,
                )
                previous = None
                if pending.previous_observation_id is not None:
                    previous = PriceObservation(
                        id=pending.previous_observation_id,
                        offer_id=pending.offer_id,
                        amount=pending.previous_amount,
                        currency=pending.previous_currency,
                        availability=pending.previous_availability,
                        observed_at=pending.previous_observed_at,
                    )
                alert_mission = Mission(id=mission.id, status=mission.status)
                alert_criteria = MissionCriteria(
                    mission_id=mission.id,
                    target_amount=outcome.target_amount,
                    target_currency=outcome.target_currency,
                )
                # TASK-113 (correção pós-plano, ponto 2): a decisão final
                # precisa reler o checkpoint sob lock, no momento exato da
                # decisão -- satisfeito aqui pelo `SELECT missions ... FOR
                # UPDATE` já existente no TOPO desta função (achado real:
                # serializa TODA esta Fase C por `mission_id`, então duas
                # stores do mesmo Product dentro da mesma Mission NUNCA
                # decidem em paralelo -- a segunda espera o commit da
                # primeira antes de sequer começar a ler `MissionProduct
                # AlertState`). Um `SELECT ... FOR UPDATE` adicional só
                # nesta linha seria redundante com o lock que já existe,
                # nunca mais seguro.
                checkpoint_row: MissionProductAlertState | None = None
                checkpoint: AlertCheckpoint | None = None
                realert_window: timedelta | None = None
                material_policy: MaterialImprovementPolicy | None = None
                market_assessment_supports_realert = False
                if settings is not None:
                    checkpoint_row = await session.get(
                        MissionProductAlertState, (mission.id, pending.product_id)
                    )
                    if checkpoint_row is not None:
                        checkpoint = AlertCheckpoint(
                            best_notified_amount=checkpoint_row.best_notified_amount,
                            last_notified_amount=checkpoint_row.last_notified_amount,
                            last_notified_at=checkpoint_row.last_notified_at,
                            rearmed_at=checkpoint_row.rearmed_at,
                        )
                    realert_window = await resolve_realert_window(
                        session,
                        product_id=pending.product_id,
                        now=outcome.completed_at,
                        settings=settings,
                    )
                    material_policy = MaterialImprovementPolicy(
                        settings.material_improvement_percent,
                        settings.material_improvement_min_amount,
                        settings.material_improvement_max_amount,
                    )
                    market_assessment_supports_realert = bool(
                        ai_outcome is not None
                        and ai_outcome.market_snapshot is not None
                        and ai_outcome.market_snapshot.is_good_or_excellent
                    )
                try:
                    candidates = evaluate_price_alerts(
                        alert_mission,
                        alert_criteria,
                        current,
                        previous,
                        checkpoint=checkpoint,
                        material_improvement_policy=material_policy,
                        market_assessment_supports_realert=market_assessment_supports_realert,
                        realert_window=realert_window,
                        now=outcome.completed_at,
                        coupon=coupon_snapshot,
                    )
                except Exception:
                    # Avaliação/notificação de alerta é uma etapa derivada
                    # da coleta, não parte atômica dela: a oferta já foi
                    # coletada e persistida corretamente (Fase A, transação
                    # própria já commitada) e a classificação de relevância
                    # desta oferta já foi persistida acima nesta mesma
                    # transação. Um erro real do evaluator (dado
                    # inconsistente, bug) não deve falsificar o resultado
                    # da coleta nem derrubar o CollectionRun inteiro --
                    # isola-se aqui, loga, e segue para a próxima oferta.
                    logger.warning(
                        "price_alert_evaluation_failed",
                        extra={
                            "mission_id": str(mission.id),
                            "offer_id": str(pending.offer_id),
                            "observation_id": str(pending.observation_id),
                        },
                        exc_info=True,
                    )
                    candidates = ()
                last_alert_event = None
                for candidate in candidates:
                    last_alert_event = await publish_event_async(
                        session,
                        event_type=candidate.event_type,
                        aggregate_type=candidate.aggregate_type,
                        aggregate_id=candidate.aggregate_id,
                        payload=candidate.payload,
                        occurred_at=outcome.completed_at,
                        mission_id=mission.id,
                    )

                # TASK-113 (§33.9/§33.21): checkpoint atualizado na MESMA
                # transação/commit que criou o(s) Event(s) -- nunca um
                # segundo commit, nunca um "quase certo" (retry de entrega
                # do Telegram nunca revisita esta decisão, outbox já
                # existente cuida disso à parte).
                if settings is not None:
                    if candidates:
                        best_amount = (
                            current.amount
                            if checkpoint_row is None
                            else min(
                                checkpoint_row.best_notified_amount, current.amount
                            )
                        )
                        await session.execute(
                            postgresql_insert(MissionProductAlertState)
                            .values(
                                mission_id=mission.id,
                                product_id=pending.product_id,
                                best_notified_amount=best_amount,
                                best_notified_currency=current.currency,
                                last_notified_amount=current.amount,
                                last_notified_at=outcome.completed_at,
                                rearmed_at=None,
                                last_alert_event_id=(
                                    last_alert_event.id
                                    if last_alert_event is not None
                                    else None
                                ),
                                updated_at=outcome.completed_at,
                            )
                            .on_conflict_do_update(
                                index_elements=[
                                    MissionProductAlertState.mission_id,
                                    MissionProductAlertState.product_id,
                                ],
                                set_={
                                    "best_notified_amount": best_amount,
                                    "best_notified_currency": current.currency,
                                    "last_notified_amount": current.amount,
                                    "last_notified_at": outcome.completed_at,
                                    "rearmed_at": None,
                                    "last_alert_event_id": (
                                        last_alert_event.id
                                        if last_alert_event is not None
                                        else None
                                    ),
                                    "updated_at": outcome.completed_at,
                                },
                            )
                        )
                    elif checkpoint_row is not None and should_rearm(
                        checkpoint=checkpoint,
                        current_amount=pending.amount,
                        rearm_rise_percent=settings.rearm_rise_percent,
                    ):
                        # §33.8: só habilita um futuro re-alert (caminho
                        # C) -- nunca gera alerta por si só.
                        checkpoint_row.rearmed_at = outcome.completed_at
                        checkpoint_row.updated_at = outcome.completed_at

        await finish_collection_run(
            session,
            run.id,
            CollectionRunStatus.SUCCEEDED,
            finished_at=outcome.completed_at,
        )
        await publish_event_async(
            session,
            event_type=EventType.COLLECTION_COMPLETED_V1,
            aggregate_type=AggregateType.COLLECTION_RUN,
            aggregate_id=run.id,
            payload=CollectionCompletedPayload(
                run.id, run.store_id, run.mission_id, len(outcome.offers)
            ),
            occurred_at=outcome.completed_at,
            mission_id=run.mission_id,
        )
        await _reset_source_backoff(session, run.mission_id, run.store_id)
        await _evaluate_mission_prelist(session, run.mission_id, outcome.completed_at)
        return True


def _maybe_set_canonical_image(product: Product, image_url: str) -> None:
    """Regra mínima e segura (subtask 4): a primeira imagem válida de um
    produto com identidade resolvida vira a canônica e nunca é
    sobrescrita automaticamente depois -- sem heurística de qualidade
    (sem request HTTP extra para checar dimensão/placeholder, sem
    "última imagem sempre vence"). Trocar a canônica por uma melhor,
    se um dia for preciso, é uma decisão explícita, não automática.
    Só chamado para `Product` com `identity_key` resolvido -- produtos
    sem identidade nunca são "o mesmo produto entre lojas" de verdade."""
    if product.canonical_image_url is None:
        product.canonical_image_url = image_url


async def _preview_existing_offer_and_product(
    session: AsyncSession, store_id: UUID, item: Any
) -> tuple[Offer | None, Product | None]:
    """Subtask 6 (correção estrutural de deadlock): identificação SOMENTE
    LEITURA -- nenhum `session.add`, nenhuma mutação, nenhum
    `begin_nested()`. `Session.begin_nested()` força `flush()`
    incondicional de TODO o estado pendente da sessão antes do SAVEPOINT
    (`SessionTransaction._take_snapshot`, `if not is_begin and not
    self.session._flushing: self.session.flush()`) -- isso vale mesmo com
    `autoflush=False`, que só afeta o flush automático antes de queries.
    `_resolve_offer` (chamado depois, só após a fase global de travas)
    pode disparar `begin_nested()` ao criar Seller/Offer/Product novos; se
    isso acontecesse ANTES da ordem global estar completa, uma mutação
    pendente de uma Offer JÁ processada (ex. `canonical_image_url`) seria
    flushada -- e travada -- na ordem de iteração comercial, reabrindo o
    mesmo risco de deadlock. Por isso esta função só enxerga o que JÁ
    EXISTE (sem criar nada), para a fase de travas rodar sobre um estado
    de sessão garantidamente limpo."""
    external_id = item.seller_external_id
    seller_id: UUID | None = None
    if external_id:
        seller = await session.scalar(
            select(Seller).where(
                Seller.store_id == store_id, Seller.external_id == external_id
            )
        )
        seller_id = seller.id if seller is not None else None
    offer = await _find_offer(
        session, store_id, seller_id, item.raw_offer.external_id, item.raw_offer.url
    )
    if offer is None:
        return None, None
    product = await session.get(Product, offer.product_id)
    return offer, product


def _creation_lock_keys(store_id: UUID, item: Any) -> frozenset[str]:
    """Subtask 6 (correção estrutural de deadlock, segunda rodada):
    chaves lógicas (naturais, conhecidas ANTES de qualquer `INSERT`) dos
    recursos que `_resolve_offer` pode precisar CRIAR para este item --
    Seller (`uq_sellers_store_external_id`), Product
    (`uq_products_identity_key`) e a própria Offer (as 4 unique indexes
    em `_OFFER_IDENTITY_INDEXES`, todas cobertas pela mesma tupla de
    identidade já usada para deduplicar itens no loop).

    Travar por Offer/Product JÁ EXISTENTES (`SELECT ... FOR UPDATE`, mais
    acima) não basta: dois `INSERT` concorrentes na MESMA unique
    constraint (nenhuma linha existe ainda, então não há o que
    `FOR UPDATE`) também podem deadlockear -- o Postgres bloqueia um
    `INSERT` quando outra transação já tem um `INSERT` não commitado na
    mesma chave, exatamente como um lock real; se TX1 insere A e espera
    por B enquanto TX2 insere B e espera por A, é um ciclo genuíno.
    `resolve_product_variant`/`item.seller_external_id` já são conhecidos
    aqui, sem consulta nenhuma -- por isso dá para travar por CHAVE
    LÓGICA, não por linha física (que ainda não existe)."""
    keys: set[str] = set()
    identity_key = (
        item.seller_external_id,
        item.raw_offer.external_id,
        item.raw_offer.external_id or item.raw_offer.url,
    )
    keys.add(f"offer:{store_id}:{identity_key}")
    if item.seller_external_id:
        keys.add(f"seller:{store_id}:{item.seller_external_id}")
    product_identity = resolve_product_variant(item.raw_offer.title)
    if product_identity is not None:
        keys.add(f"product:{product_identity.identity_key}")
    return frozenset(keys)


async def _acquire_creation_locks(session: AsyncSession, keys: frozenset[str]) -> None:
    """Subtask 6: `pg_advisory_xact_lock` por chave lógica (não um lock
    global único) -- travas transacionais, liberadas automaticamente no
    commit/rollback, uma por chave lógica distinta, adquiridas em ordem
    GLOBAL determinística (ordenação lexicográfica das chaves) para que
    nenhuma transação concorrente possa adquiri-las em ordem cruzada.
    `hashtextextended(..., 0)` gera o `bigint` que `pg_advisory_xact_lock`
    exige a partir de uma chave textual arbitrária -- colisão de hash só
    causaria serialização desnecessária entre chaves diferentes, nunca um
    problema de corretude."""
    for key in sorted(keys):
        await session.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0)))
        )


async def _resolve_offer(session: AsyncSession, store_id: UUID, item: Any) -> Offer:
    seller = await _resolve_seller(session, store_id, item)
    seller_id = seller.id if seller else None
    offer = await _find_offer(
        session,
        store_id,
        seller_id,
        item.raw_offer.external_id,
        item.raw_offer.url,
    )
    if offer is not None:
        resolved_product = await _resolve_global_product(session, item.raw_offer.title)
        target_product = None
        if resolved_product is not None:
            current_product = await session.get(Product, offer.product_id)
            if current_product is not None and current_product.identity_key is None:
                offer.product_id = resolved_product.id
                target_product = resolved_product
            elif current_product is not None:
                target_product = current_product
        if item.raw_offer.image_url is not None:
            offer.image_url = item.raw_offer.image_url
            if target_product is not None:
                _maybe_set_canonical_image(target_product, item.raw_offer.image_url)
        _apply_rating_snapshot(offer, item)
        return offer
    product = await _resolve_global_product(session, item.raw_offer.title)
    unresolved_product = product is None
    if product is None:
        product = Product(id=uuid4(), name=item.raw_offer.title[:300])
    if item.raw_offer.image_url is not None and product.identity_key is not None:
        _maybe_set_canonical_image(product, item.raw_offer.image_url)
    offer = Offer(
        product_id=product.id,
        store_id=store_id,
        seller_id=seller_id,
        external_id=item.raw_offer.external_id,
        url=item.raw_offer.url,
        image_url=item.raw_offer.image_url,
    )
    _apply_rating_snapshot(offer, item)
    try:
        async with session.begin_nested():
            if unresolved_product:
                session.add(product)
                await session.flush()
            session.add(offer)
            await session.flush()
    except IntegrityError as error:
        if _constraint_name(error) not in _OFFER_IDENTITY_INDEXES:
            raise
        winner = await _find_offer(
            session,
            store_id,
            seller_id,
            item.raw_offer.external_id,
            item.raw_offer.url,
        )
        if winner is None:
            raise
        if item.raw_offer.image_url is not None:
            winner.image_url = item.raw_offer.image_url
            winner_product = await session.get(Product, winner.product_id)
            if winner_product is not None and winner_product.identity_key is not None:
                _maybe_set_canonical_image(winner_product, item.raw_offer.image_url)
        _apply_rating_snapshot(winner, item)
        return winner
    return offer


async def _resolve_global_product(
    session: AsyncSession, raw_title: str
) -> Product | None:
    identity = resolve_product_variant(raw_title)
    if identity is None:
        return None
    existing = await session.scalar(
        select(Product).where(Product.identity_key == identity.identity_key)
    )
    if existing is not None:
        return existing
    product = Product(
        id=uuid4(),
        name=identity.label[:300],
        brand=identity.brand,
        model=identity.model,
        display_name=identity.label[:300],
        category=identity.category,
        family=identity.family,
        variant=identity.variant,
        attributes=dict(identity.attributes),
        family_key=identity.family_key,
        identity_key=identity.identity_key,
        identity_version=IDENTITY_VERSION,
    )
    try:
        async with session.begin_nested():
            session.add(product)
            await session.flush()
    except IntegrityError as error:
        if _constraint_name(error) != _PRODUCT_IDENTITY_INDEX:
            raise
        winner = await session.scalar(
            select(Product).where(Product.identity_key == identity.identity_key)
        )
        if winner is None:
            raise
        return winner
    return product


def _apply_rating_snapshot(offer: Offer, item: Any) -> None:
    """Atualiza só um par explícito; ausência nunca apaga o último snapshot."""
    if item.rating_average is None or item.review_count is None:
        return
    offer.rating_average = item.rating_average
    offer.review_count = item.review_count
    offer.rating_observed_at = item.raw_offer.collected_at


async def _find_offer(
    session: AsyncSession,
    store_id: UUID,
    seller_id: UUID | None,
    external_id: str | None,
    url: str,
) -> Offer | None:
    identity = [Offer.store_id == store_id]
    identity.append(
        Offer.seller_id.is_(None) if seller_id is None else Offer.seller_id == seller_id
    )
    if external_id:
        identity.append(Offer.external_id == external_id)
    else:
        identity.append(Offer.url == url)
    return await session.scalar(select(Offer).where(*identity).limit(1))


async def _resolve_seller(
    session: AsyncSession, store_id: UUID, item: Any
) -> Seller | None:
    external_id = item.seller_external_id
    if not external_id:
        return None
    existing = await session.scalar(
        select(Seller).where(
            Seller.store_id == store_id, Seller.external_id == external_id
        )
    )
    if existing is not None:
        return existing
    seller = Seller(
        store_id=store_id,
        external_id=external_id,
        name=(item.seller_name or external_id)[:200],
    )
    try:
        async with session.begin_nested():
            session.add(seller)
            await session.flush()
    except IntegrityError as error:
        if _constraint_name(error) != _SELLER_IDENTITY_INDEX:
            raise
        winner = await session.scalar(
            select(Seller).where(
                Seller.store_id == store_id, Seller.external_id == external_id
            )
        )
        if winner is None:
            raise
        return winner
    return seller


async def _record_failure(
    session: AsyncSession,
    claim: ClaimedCollection,
    failure_code: str,
    failed_at: datetime,
    *,
    confirmed_block: bool = False,
) -> bool:
    run = await session.scalar(
        select(CollectionRun).where(CollectionRun.id == claim.run_id).with_for_update()
    )
    if run is None or run.status is not CollectionRunStatus.RUNNING:
        return False
    await finish_collection_run(
        session, run.id, CollectionRunStatus.FAILED, finished_at=failed_at
    )
    await _publish_failure(session, run, failure_code, failed_at)
    if confirmed_block:
        await _apply_source_backoff(session, run.mission_id, run.store_id, failed_at)
    await _evaluate_mission_prelist(session, run.mission_id, failed_at)
    return True


async def _publish_failure(
    session: AsyncSession, run: CollectionRun, failure_code: str, failed_at: datetime
) -> None:
    await publish_event_async(
        session,
        event_type=EventType.COLLECTION_FAILED_V1,
        aggregate_type=AggregateType.COLLECTION_RUN,
        aggregate_id=run.id,
        payload=CollectionFailedPayload(
            run.id, run.store_id, run.mission_id, failure_code
        ),
        occurred_at=failed_at,
        mission_id=run.mission_id,
    )


def _is_confirmed_external_block(error: Exception) -> bool:
    """Só 403/429 confirmados acionam backoff persistente (DEC-047).

    401 fica de fora de propósito (autenticação/credencial/configuração,
    não proteção anti-bot). `ProviderBlockedError` também cobre seletor
    ausente/oferta vazia com outro status (possível markup change, não
    bloqueio confirmado) — esse caso ambíguo também não conta.
    """
    return (
        isinstance(error, ProviderBlockedError)
        and error.status in _CONFIRMED_BLOCK_STATUSES
    )


async def _apply_source_backoff(
    session: AsyncSession, mission_id: UUID, store_id: UUID, failed_at: datetime
) -> None:
    source = await session.get(MissionSource, (mission_id, store_id))
    if source is None:
        return
    base_interval_minutes = await session.scalar(
        select(MissionSchedule.interval_minutes).where(
            MissionSchedule.mission_id == mission_id
        )
    )
    if base_interval_minutes is None:
        return
    consecutive_blocks, delay_minutes = next_source_backoff(
        base_interval_minutes=base_interval_minutes,
        consecutive_blocks=source.consecutive_blocks,
        cap_minutes=_SOURCE_BACKOFF_CAP_MINUTES,
    )
    source.consecutive_blocks = consecutive_blocks
    source.next_eligible_at = failed_at + timedelta(minutes=delay_minutes)


async def _reset_source_backoff(
    session: AsyncSession, mission_id: UUID, store_id: UUID
) -> None:
    source = await session.get(MissionSource, (mission_id, store_id))
    if source is None:
        return
    already_reset = source.consecutive_blocks == 0 and source.next_eligible_at is None
    if already_reset:
        return
    source.consecutive_blocks = 0
    source.next_eligible_at = None


async def _evaluate_mission_prelist(
    session: AsyncSession, mission_id: UUID, occurred_at: datetime
) -> None:
    """TASK-068: pré-lista informativa (sem IA) após a primeira rodada completa.

    Dispara uma única vez por missão, quando toda `MissionSource` já teve
    pelo menos um `CollectionRun` terminal (sucesso ou falha) -- não fica
    esperando indefinidamente por uma fonte bloqueada; uma tentativa
    falha também conta como terminal. Reaproveita a classificação `MATCH`
    já calculada pela TASK-063 (nenhuma chamada de IA nova). Depois de
    enviada, no máximo uma mensagem de correção é publicada se uma coleta
    posterior encontrar uma oferta `MATCH` mais barata que a base já
    mostrada (`Mission.prelist_lowest_amount`).

    Ranqueia sempre por `PriceObservation.amount` (preço anunciado do
    produto), nunca por `total_amount` -- frete ainda não é conhecido/
    comparável de forma confiável entre lojas nesta TASK; não é estimado
    nem tratado como zero, e a mensagem final deixa isso explícito.
    """
    mission = await session.scalar(
        select(Mission).where(Mission.id == mission_id).with_for_update()
    )
    if mission is None or mission.status is not MissionStatus.ACTIVE:
        return
    criteria = await session.scalar(
        select(MissionCriteria).where(MissionCriteria.mission_id == mission_id)
    )
    if criteria is None:
        return
    if (
        getattr(criteria, "request_kind", ProductRequestKind.GENERIC_CATEGORY.value)
        == ProductRequestKind.PRODUCT_FAMILY.value
        and getattr(
            criteria, "variant_selection_mode", VariantSelectionMode.NOT_REQUIRED
        )
        is VariantSelectionMode.PENDING
    ):
        await _maybe_publish_variant_choices(session, mission, criteria, occurred_at)
        return
    if not mission.prelist_sent:
        await _maybe_publish_prelist_ready(session, mission, occurred_at)
        return
    if not mission.prelist_errata_sent:
        await _maybe_publish_prelist_errata(session, mission, occurred_at)


async def _mission_prelist_round_complete(
    session: AsyncSession, mission_id: UUID
) -> bool:
    total_sources = await session.scalar(
        select(func.count())
        .select_from(MissionSource)
        .where(MissionSource.mission_id == mission_id)
    )
    if not total_sources:
        return False
    completed_sources = await session.scalar(
        select(func.count(func.distinct(CollectionRun.store_id))).where(
            CollectionRun.mission_id == mission_id,
            CollectionRun.status != CollectionRunStatus.RUNNING,
        )
    )
    return (completed_sources or 0) >= total_sources


async def _mission_relevance_pending(session: AsyncSession, mission_id: UUID) -> bool:
    """True quando alguma oferta atualmente vinculada a esta missão ainda
    não tem `MissionOfferRelevance` persistida.

    Ausência de linha significa "classificação ainda não resolvida"
    (falhou ou nunca terminou) -- nunca "não é match" (`NO_MATCH`/
    `POSSIBLE_MATCH` já são uma linha persistida, portanto nunca contam
    como pendente aqui).

    "Oferta atual" de cada loja usa `Offer.last_seen_at` (TASK-093), não
    `PriceObservation.collection_run_id`: como a Fase A passou a
    reaproveitar a mesma `PriceObservation` quando o estado comercial não
    muda (sem gravar linha nova, só atualizando `last_seen_at`), uma
    oferta redundante mas ainda sem classificação pode ter
    `collection_run_id` apontando para um run bem mais antigo que o mais
    recente da loja -- escopar pelo `collection_run_id` do "run mais
    recente" perderia essa oferta silenciosamente. `last_seen_at` é
    atualizado em toda coleta bem-sucedida da oferta, redundante ou não,
    e é o sinal correto e atual de "confirmada pela coleta mais
    recente".
    """
    offer_rows = (
        await session.execute(
            select(PriceObservation.offer_id, Offer.store_id, Offer.last_seen_at)
            .join(CollectionRun, CollectionRun.id == PriceObservation.collection_run_id)
            .join(Offer, Offer.id == PriceObservation.offer_id)
            .where(CollectionRun.mission_id == mission_id)
            .distinct()
        )
    ).all()
    if not offer_rows:
        return False

    latest_per_store: dict[UUID, datetime] = {}
    for _offer_id, store_id, last_seen_at in offer_rows:
        current = latest_per_store.get(store_id)
        if current is None or last_seen_at > current:
            latest_per_store[store_id] = last_seen_at

    current_offer_ids = {
        offer_id
        for offer_id, store_id, last_seen_at in offer_rows
        if last_seen_at == latest_per_store[store_id]
    }

    resolved_offer_ids = set(
        await session.scalars(
            select(MissionOfferRelevance.offer_id).where(
                MissionOfferRelevance.mission_id == mission_id,
                MissionOfferRelevance.offer_id.in_(current_offer_ids),
            )
        )
    )
    return bool(current_offer_ids - resolved_offer_ids)


_PRELIST_PER_STORE_LIMIT = 5
_RELEVANCE_RANK = {
    OfferRelevance.MATCH: 0,
    OfferRelevance.POSSIBLE_MATCH: 1,
    OfferRelevance.NO_MATCH: 2,
}


@dataclass(frozen=True, slots=True)
class _PrelistCandidate:
    relevance: OfferRelevance
    observation: PriceObservation
    offer: Offer
    store: Store


def _prelist_commercial_key(candidate: _PrelistCandidate) -> tuple[Any, ...]:
    observation = candidate.observation
    return (
        _RELEVANCE_RANK[candidate.relevance],
        _CONDITION_RANK[observation.condition],
        _SELLER_RANK[observation.seller_kind],
        _AVAILABILITY_RANK[observation.availability],
        observation.amount,
        observation.total_amount,
        str(candidate.offer.id),
    )


def rank_prelist_candidates(
    candidates: Sequence[_PrelistCandidate], *, limit: int = _PRELIST_PER_STORE_LIMIT
) -> tuple[_PrelistCandidate, ...]:
    """Ordenação comercial única, determinística e limitada por loja."""
    ordered = sorted(
        (item for item in candidates if item.relevance is not OfferRelevance.NO_MATCH),
        key=lambda item: (item.store.code, *_prelist_commercial_key(item)),
    )
    counts: dict[UUID, int] = {}
    selected: list[_PrelistCandidate] = []
    for item in ordered:
        count = counts.get(item.store.id, 0)
        if count >= limit:
            continue
        counts[item.store.id] = count + 1
        selected.append(item)
    return tuple(selected)


async def _current_prelist_candidates(
    session: AsyncSession, mission_id: UUID
) -> tuple[_PrelistCandidate, ...]:
    """Última observação real de cada oferta confirmada na coleta atual da loja."""
    rows = (
        await session.execute(
            select(MissionOfferRelevance, PriceObservation, Offer, Store)
            .join(Offer, Offer.id == MissionOfferRelevance.offer_id)
            .join(Store, Store.id == Offer.store_id)
            .join(PriceObservation, PriceObservation.offer_id == Offer.id)
            .where(MissionOfferRelevance.mission_id == mission_id)
            .order_by(PriceObservation.observed_at.desc(), PriceObservation.id.desc())
        )
    ).all()
    latest_by_offer: dict[UUID, _PrelistCandidate] = {}
    for relevance, observation, offer, store in rows:
        latest_by_offer.setdefault(
            offer.id,
            _PrelistCandidate(relevance.classification, observation, offer, store),
        )
    latest_seen_by_store: dict[UUID, datetime] = {}
    for item in latest_by_offer.values():
        current = latest_seen_by_store.get(item.store.id)
        if current is None or item.offer.last_seen_at > current:
            latest_seen_by_store[item.store.id] = item.offer.last_seen_at
    current = [
        item
        for item in latest_by_offer.values()
        if item.offer.last_seen_at == latest_seen_by_store[item.store.id]
    ]
    criteria = await session.scalar(
        select(MissionCriteria).where(MissionCriteria.mission_id == mission_id)
    )
    if (
        criteria is not None
        and criteria.request_kind == ProductRequestKind.PRODUCT_FAMILY.value
    ):
        if criteria.variant_selection_mode is VariantSelectionMode.PENDING:
            return ()
        eligible_statement = select(Product.id).where(
            Product.family_key == criteria.requested_family_key,
            Product.identity_key.is_not(None),
        )
        if criteria.requested_variant is not None:
            eligible_statement = eligible_statement.where(
                Product.variant == criteria.requested_variant
            )
        eligible_product_ids = set(await session.scalars(eligible_statement))
        current = [
            item for item in current if item.offer.product_id in eligible_product_ids
        ]
        if criteria.variant_selection_mode is VariantSelectionMode.SELECTED:
            selected_ids = set(
                await session.scalars(
                    select(MissionProductSelection.product_id).where(
                        MissionProductSelection.mission_id == mission_id
                    )
                )
            )
            current = [
                item for item in current if item.offer.product_id in selected_ids
            ]
    return rank_prelist_candidates(current)


async def _available_family_variants(
    session: AsyncSession,
    *,
    mission_id: UUID,
    family_key: str,
    requested_variant: str | None,
) -> tuple[Product, ...]:
    statement = (
        select(Product)
        .join(Offer, Offer.product_id == Product.id)
        .join(
            MissionOfferRelevance,
            MissionOfferRelevance.offer_id == Offer.id,
        )
        .where(
            MissionOfferRelevance.mission_id == mission_id,
            MissionOfferRelevance.classification.in_(
                {OfferRelevance.MATCH, OfferRelevance.POSSIBLE_MATCH}
            ),
            Product.family_key == family_key,
            Product.identity_key.is_not(None),
        )
        .distinct()
        .order_by(Product.display_name, Product.name, Product.id)
        .limit(20)
    )
    if requested_variant is not None:
        statement = statement.where(Product.variant == requested_variant)
    return tuple(await session.scalars(statement))


async def _maybe_publish_variant_choices(
    session: AsyncSession,
    mission: Mission,
    criteria: MissionCriteria,
    occurred_at: datetime,
) -> None:
    if criteria.variant_prompted_at is not None:
        return
    if not await _mission_prelist_round_complete(session, mission.id):
        return
    if await _mission_relevance_pending(session, mission.id):
        return
    if criteria.requested_family_key is None:
        return
    variants = await _available_family_variants(
        session,
        mission_id=mission.id,
        family_key=criteria.requested_family_key,
        requested_variant=criteria.requested_variant,
    )
    if not variants:
        return
    await publish_event_async(
        session,
        event_type=EventType.MISSION_VARIANTS_READY_V1,
        aggregate_type=AggregateType.MISSION,
        aggregate_id=mission.id,
        payload=MissionVariantsReadyPayload(
            mission_id=mission.id,
            variants=tuple(
                ProductVariantOptionPayload(
                    product_id=product.id,
                    label=product.display_name or product.name,
                )
                for product in variants
            ),
            state_version=mission.state_version,
        ),
        occurred_at=occurred_at,
        mission_id=mission.id,
    )
    criteria.variant_prompted_at = occurred_at


def _prelist_payload(item: _PrelistCandidate) -> PrelistOfferPayload:
    observation = item.observation
    return PrelistOfferPayload(
        offer_id=item.offer.id,
        observation_id=observation.id,
        store_id=item.store.id,
        amount=observation.amount,
        total_amount=observation.total_amount,
        currency=observation.currency,
        relevance=item.relevance,
        condition=observation.condition,
        seller_kind=observation.seller_kind,
        availability=observation.availability,
    )


async def _maybe_publish_prelist_ready(
    session: AsyncSession, mission: Mission, occurred_at: datetime
) -> None:
    if not await _mission_prelist_round_complete(session, mission.id):
        return
    # Hotfix (relevância pendente): round completo (todas as lojas com
    # tentativa terminal) não é o mesmo que "toda oferta atual já foi
    # classificada" -- se a classificação de relevância de alguma oferta
    # corrente ainda não foi resolvida (falhou/não terminou), a pré-lista
    # não pode ser marcada como enviada com uma lista vazia; a próxima
    # coleta tenta de novo.
    if await _mission_relevance_pending(session, mission.id):
        return
    candidates = await _current_prelist_candidates(session, mission.id)
    mission.prelist_sent = True
    if not candidates:
        return
    lowest = min(candidates, key=lambda item: item.observation.amount)
    mission.prelist_lowest_amount = lowest.observation.amount
    mission.prelist_lowest_currency = lowest.observation.currency
    await publish_event_async(
        session,
        event_type=EventType.MISSION_PRELIST_READY_V2,
        aggregate_type=AggregateType.MISSION,
        aggregate_id=mission.id,
        payload=MissionPrelistReadyV2Payload(
            mission_id=mission.id,
            offers=tuple(_prelist_payload(item) for item in candidates),
        ),
        occurred_at=occurred_at,
        mission_id=mission.id,
    )


async def _maybe_publish_prelist_errata(
    session: AsyncSession, mission: Mission, occurred_at: datetime
) -> None:
    previous_event = await session.scalar(
        select(Event)
        .where(
            Event.mission_id == mission.id,
            Event.event_type.in_(
                {
                    EventType.MISSION_PRELIST_READY_V1.value,
                    EventType.MISSION_PRELIST_READY_V2.value,
                }
            ),
        )
        .order_by(Event.occurred_at.desc(), Event.id.desc())
        .limit(1)
    )
    if previous_event is None:
        return
    candidates = await _current_prelist_candidates(session, mission.id)
    by_store: dict[UUID, list[_PrelistCandidate]] = {}
    for candidate in candidates:
        by_store.setdefault(candidate.store.id, []).append(candidate)
    previous = await _previous_prelist_best_by_store(session, previous_event)
    improved = [
        group
        for store_id, group in by_store.items()
        if store_id not in previous
        or _prelist_commercial_key(group[0]) < previous[store_id]
    ]
    if not improved:
        return
    selected_group = min(
        improved,
        key=lambda group: (group[0].store.code, _prelist_commercial_key(group[0])),
    )
    mission.prelist_errata_sent = True
    await publish_event_async(
        session,
        event_type=EventType.MISSION_PRELIST_ERRATA_V2,
        aggregate_type=AggregateType.MISSION,
        aggregate_id=mission.id,
        payload=MissionPrelistErrataV2Payload(
            mission_id=mission.id,
            previous_event_id=previous_event.id,
            corrected_store_id=selected_group[0].store.id,
            offers=tuple(_prelist_payload(item) for item in selected_group),
        ),
        occurred_at=occurred_at,
        mission_id=mission.id,
    )


async def _previous_prelist_best_by_store(
    session: AsyncSession, event: Event
) -> dict[UUID, tuple[Any, ...]]:
    payload = event.payload
    if not isinstance(payload, dict):
        return {}
    references: list[tuple[UUID, UUID]] = []
    if event.event_type == EventType.MISSION_PRELIST_READY_V2.value:
        items = payload.get("offers")
        if not isinstance(items, list):
            return {}
        for item in items:
            if isinstance(item, dict):
                try:
                    references.append(
                        (UUID(str(item["offer_id"])), UUID(str(item["observation_id"])))
                    )
                except KeyError, ValueError:
                    continue
    else:
        for prefix in ("first", "second"):
            if payload.get(f"{prefix}_offer_id") is None:
                continue
            try:
                references.append(
                    (
                        UUID(str(payload[f"{prefix}_offer_id"])),
                        UUID(str(payload[f"{prefix}_observation_id"])),
                    )
                )
            except KeyError, ValueError:
                continue
    result: dict[UUID, tuple[Any, ...]] = {}
    for offer_id, observation_id in references:
        offer = await session.get(Offer, offer_id)
        observation = await session.get(PriceObservation, observation_id)
        relevance = await session.get(
            MissionOfferRelevance, (event.mission_id, offer_id)
        )
        if offer is None or observation is None or relevance is None:
            continue
        store = await session.get(Store, offer.store_id)
        if store is None:
            continue
        candidate = _PrelistCandidate(
            relevance.classification, observation, offer, store
        )
        key = _prelist_commercial_key(candidate)
        current = result.get(store.id)
        if current is None or key < current:
            result[store.id] = key
    return result


def _failure_code(error: Exception) -> str:
    if isinstance(error, ProviderCircuitOpenError):
        return "circuit_open"
    if isinstance(error, ProviderBlockedError):
        return "provider_blocked"
    if isinstance(error, (CollectionNormalizationError, CollectionContractError)):
        return "normalization_failed"
    if isinstance(error, (ProviderNavigationError, TimeoutError, asyncio.TimeoutError)):
        return "provider_unavailable"
    return "collection_failed"


def _failure_log_context(error: Exception) -> dict[str, Any]:
    """Produz diagnóstico local e limitado sem expor exceções externas brutas.

    A etapa é derivada da taxonomia já existente. Mensagem e traceback completos
    ficam restritos às exceções de domínio cujos construtores usam apenas valores
    fechados/estruturais; exceções inesperadas preservam a pilha, mas não o texto,
    que pode conter URL, query ou credencial de uma biblioteca externa.
    """
    if isinstance(error, ProviderNavigationError):
        stage = "navigation"
    elif isinstance(error, ProviderBlockedError):
        stage = "extraction"
    elif isinstance(error, (CollectionNormalizationError, CollectionContractError)):
        stage = "normalization"
    elif isinstance(error, IntegrityError):
        stage = "persistence"
    elif isinstance(error, ProviderCircuitOpenError):
        stage = "provider_availability"
    else:
        stage = "collection"

    context: dict[str, Any] = {
        "error_class": type(error).__name__,
        "failure_stage": stage,
    }
    status = getattr(error, "status", None)
    if isinstance(status, int):
        context["provider_status"] = status

    safe_domain_error = isinstance(
        error,
        (
            ProviderNavigationError,
            ProviderBlockedError,
            ProviderCircuitOpenError,
            CollectionNormalizationError,
            CollectionContractError,
        ),
    )
    if safe_domain_error:
        context["error_detail"] = _sanitize_json(str(error))
        formatted = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
    else:
        formatted = "".join(traceback.format_tb(error.__traceback__))
        formatted += f"{type(error).__name__}\n"
    context["failure_traceback"] = formatted[:_FAILURE_TRACEBACK_MAX_CHARS]
    return context


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


@dataclass(frozen=True, slots=True)
class _CommercialState:
    """Auditoria GG Oferta (Subtask 6, correção de causa raiz): estado
    comercial da coleta ATUAL materializado uma única vez a partir de
    `item`. `seller_kind`/`fulfillment_kind`/`installment_options` em
    `NormalizedCollectedOffer` são `@property` recalculadas a cada acesso
    a partir de `raw_offer` (de propósito, para refletir enriquecimento
    tardio -- ver `normalization.py`); ler `item` uma vez só aqui e reusar
    esta mesma instância tanto na checagem de redundância quanto na
    construção de `PriceObservation`/`OfferInstallmentOption` garante que
    a decisão e a persistência nunca leem valores potencialmente
    diferentes entre duas leituras de `item`."""

    amount: Decimal
    currency: str
    shipping_amount: Decimal | None
    total_amount: Decimal
    fulfillment: str | None
    seller_kind: MarketplacePartyKind | None
    fulfillment_kind: MarketplacePartyKind | None
    condition: OfferCondition
    availability: Availability
    installments: tuple[NormalizedInstallmentOption, ...]

    @classmethod
    def from_normalized_offer(cls, item: NormalizedCollectedOffer) -> _CommercialState:
        return cls(
            amount=item.amount,
            currency=item.currency,
            shipping_amount=item.shipping_amount,
            total_amount=item.total_amount,
            fulfillment=item.fulfillment,
            seller_kind=item.seller_kind,
            fulfillment_kind=item.fulfillment_kind,
            condition=item.condition,
            availability=item.availability,
            installments=item.installment_options,
        )


def _same_commercial_state(latest: PriceObservation, item: Any) -> bool:
    """TASK-093: compara só o estado comercialmente relevante entre a
    última `PriceObservation` da mesma `Offer` (qualquer missão -- a
    identidade correta aqui é a oferta, não a missão que a está
    observando) e a coleta atual. `observed_at`/`recorded_at`/
    `raw_evidence` ficam de fora de propósito -- variam a cada coleta sem
    representar mudança comercial real; incluí-los nunca deduplicaria
    nada. Parcelamento é comparado separadamente (`_installment_snapshot`)
    porque é uma relação 1:N, não um campo escalar aqui."""
    return (
        latest.amount == item.amount
        and latest.currency == item.currency
        and latest.shipping_amount == item.shipping_amount
        and latest.total_amount == item.total_amount
        and latest.fulfillment == item.fulfillment
        and latest.seller_kind == item.seller_kind
        and latest.fulfillment_kind == item.fulfillment_kind
        and latest.condition == item.condition
        and latest.availability == item.availability
    )


def _installment_snapshot(options: Sequence[Any]) -> frozenset[tuple[Any, ...]]:
    """TASK-093: snapshot comparável de condições de parcelamento -- serve
    tanto para `OfferInstallmentOption` (ORM) quanto para
    `NormalizedInstallmentOption` (coleta atual), mesmos nomes de campo.
    Preço à vista igual não basta: uma condição de parcelamento nova ou
    removida também é mudança comercial relevante."""
    return frozenset(
        (
            option.installment_count,
            option.installment_amount,
            option.installment_total_amount,
            option.discount_percent,
            option.interest_kind,
            option.is_highlighted,
        )
        for option in options
    )


def _raw_evidence(raw_offer: Any) -> dict[str, Any]:
    return {
        "source_code": _safe_source(raw_offer.source_code),
        "url": raw_offer.url[:2048],
        "title": raw_offer.title[:300],
        "external_id": _bounded(raw_offer.external_id, 255),
        "seller_external_id": _bounded(raw_offer.seller_external_id, 255),
        "seller_name": _bounded(raw_offer.seller_name, 200),
        "raw_price": _bounded(raw_offer.raw_price, 120),
        "raw_currency": _bounded(raw_offer.raw_currency, 16),
        "raw_shipping": _bounded(raw_offer.raw_shipping, 120),
        "raw_availability": _bounded(raw_offer.raw_availability, 120),
        "raw_fulfillment": _bounded(raw_offer.raw_fulfillment, 120),
        "raw_condition": _bounded(raw_offer.raw_condition, 32),
        "raw_rating_average": _bounded(raw_offer.raw_rating_average, 80),
        "raw_review_count": _bounded(raw_offer.raw_review_count, 80),
        "provider_evidence": _sanitize_json(raw_offer.evidence),
    }


def _sanitize_json(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return "truncated"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:2000]
    if isinstance(value, Mapping):
        return {
            str(key)[:100]: _sanitize_json(item, depth=depth + 1)
            for key, item in list(value.items())[:50]
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitize_json(item, depth=depth + 1) for item in list(value)[:50]]
    return str(value)[:200]


def _bounded(value: str | None, length: int) -> str | None:
    return value[:length] if value is not None else None


def _safe_source(source_code: str) -> str:
    return source_code if source_code in V1_SOURCE_CODES else "other"
