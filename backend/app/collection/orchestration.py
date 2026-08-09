"""Orquestra coletas agendadas sem manter transações durante acesso externo."""

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import exists, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.ai_provider import AIProviderManager
from app.alerts import evaluate_price_alerts
from app.collection.adapter import CollectionAdapter
from app.collection.contracts import CollectionRequest
from app.collection.errors import (
    CollectionContractError,
    CollectionNormalizationError,
    ProviderBlockedError,
    ProviderCircuitOpenError,
    ProviderNavigationError,
)
from app.collection.models import (
    CollectionRun,
    CollectionRunStatus,
    MissionOfferRelevance,
    PriceObservation,
)
from app.collection.normalization import NormalizedCollectionResult, PriceNormalizer
from app.collection.persistence import finish_collection_run, start_collection_run
from app.collection.relevance import (
    OfferRelevance,
    classify_offer_relevance,
    normalize_offer_title,
)
from app.database.time import utc_now
from app.events import (
    AggregateType,
    AvailabilityChangedPayload,
    CollectionCompletedPayload,
    CollectionFailedPayload,
    EventType,
    publish_event,
)
from app.missions.models import (
    Mission,
    MissionCriteria,
    MissionSchedule,
    MissionSource,
    MissionStatus,
)
from app.missions.schedule import (
    advance_schedule,
    find_due_schedules,
    next_source_backoff,
    staggered_next_run_at,
)
from app.offers.models import Offer
from app.products.models import Product
from app.stores.models import Seller, Store
from app.users.models import UserRole

logger = logging.getLogger("app.collection.orchestration")

V1_SOURCE_CODES = frozenset({"pichau", "terabyte", "amazon", "kabum"})
_RUNNING_INDEX = "uq_collection_runs_running_mission_store"
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
_OFFER_IDENTITY_INDEXES = frozenset(
    {
        "uq_offers_retailer_external_id",
        "uq_offers_marketplace_external_id",
        "uq_offers_retailer_url",
        "uq_offers_marketplace_url",
    }
)
_SELLER_IDENTITY_INDEX = "uq_sellers_store_external_id"


@dataclass(frozen=True, slots=True)
class ClaimedCollection:
    run_id: UUID
    mission_id: UUID
    store_id: UUID
    source_code: str
    search_query: str
    requested_at: datetime


@dataclass(frozen=True, slots=True)
class CollectionBatchResult:
    claimed: int
    succeeded: int
    failed: int
    recovered_stale: int


def ensure_missing_schedules(
    session: Session,
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
    rows = session.execute(eligible_missions).all()
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
        created += int((session.execute(statement).rowcount or 0) > 0)
    session.flush()
    return created


def recover_stale_runs(
    session: Session,
    *,
    now: datetime | None = None,
    stale_after: timedelta = timedelta(minutes=10),
    limit: int = 100,
) -> int:
    """Finaliza claims abandonados; resultado tardio não poderá ser persistido."""
    effective_now = now or utc_now()
    cutoff = effective_now - stale_after
    runs = list(
        session.scalars(
            select(CollectionRun)
            .where(
                CollectionRun.status == CollectionRunStatus.RUNNING,
                CollectionRun.started_at <= cutoff,
            )
            .order_by(CollectionRun.started_at, CollectionRun.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    for run in runs:
        finish_collection_run(
            session, run.id, CollectionRunStatus.FAILED, finished_at=effective_now
        )
        _publish_failure(session, run, "stale_execution", effective_now)
    return len(runs)


def claim_due_collections(
    session: Session,
    *,
    now: datetime | None = None,
    limit: int = 25,
) -> tuple[ClaimedCollection, ...]:
    """Reserva fonte por fonte e avança a agenda numa transação curta."""
    effective_now = now or utc_now()
    claims: list[ClaimedCollection] = []
    for schedule in find_due_schedules(session, due_at=effective_now, limit=limit):
        mission_id = schedule.mission_id
        running = session.scalar(
            select(CollectionRun.id)
            .where(
                CollectionRun.mission_id == mission_id,
                CollectionRun.status == CollectionRunStatus.RUNNING,
            )
            .limit(1)
        )
        if running is not None:
            continue
        criteria = session.scalar(
            select(MissionCriteria).where(MissionCriteria.mission_id == mission_id)
        )
        if criteria is None or not criteria.search_query.strip():
            continue
        sources = session.execute(
            select(Store.id, Store.code)
            .join(MissionSource, MissionSource.store_id == Store.id)
            .where(
                MissionSource.mission_id == mission_id,
                Store.is_active.is_(True),
                Store.code.in_(V1_SOURCE_CODES),
                # DEC-046: fonte específica em backoff (bloqueio externo
                # confirmado) fica de fora deste ciclo; as demais fontes da
                # mesma missão continuam normalmente.
                or_(
                    MissionSource.next_eligible_at.is_(None),
                    MissionSource.next_eligible_at <= effective_now,
                ),
            )
            .order_by(Store.code)
        ).all()
        mission_claims: list[ClaimedCollection] = []
        for store_id, source_code in sources:
            try:
                with session.begin_nested():
                    run = start_collection_run(
                        session,
                        store_id,
                        mission_id,
                        started_at=effective_now,
                    )
            except IntegrityError as error:
                if _constraint_name(error) != _RUNNING_INDEX:
                    raise
                continue
            mission_claims.append(
                ClaimedCollection(
                    run.id,
                    mission_id,
                    store_id,
                    source_code,
                    criteria.search_query,
                    effective_now,
                )
            )
        if mission_claims:
            advance_schedule(schedule, started_at=effective_now)
            claims.extend(mission_claims)
    session.flush()
    return tuple(claims)


class CollectionOrchestrator:
    """Executa claims em paralelo e persiste cada fonte atomicamente."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        adapter: CollectionAdapter,
        *,
        ai_manager: AIProviderManager,
        ai_profile: UserRole = UserRole.ADMIN,
        normalizer: PriceNormalizer | None = None,
        schedule_interval_minutes: int = 60,
        schedule_stagger_seconds: int = 0,
        stale_run_minutes: int = 10,
        max_concurrency: int = 4,
    ) -> None:
        if schedule_interval_minutes <= 0:
            raise ValueError("schedule_interval_minutes must be positive")
        if schedule_stagger_seconds < 0:
            raise ValueError("schedule_stagger_seconds must not be negative")
        if stale_run_minutes <= 0:
            raise ValueError("stale_run_minutes must be positive")
        if not 1 <= max_concurrency <= 4:
            raise ValueError("max_concurrency must be between 1 and 4")
        self._session_factory = session_factory
        self._adapter = adapter
        self._ai_manager = ai_manager
        self._ai_profile = ai_profile
        self._normalizer = normalizer or PriceNormalizer()
        self._schedule_interval_minutes = schedule_interval_minutes
        self._schedule_stagger_seconds = schedule_stagger_seconds
        self._stale_after = timedelta(minutes=stale_run_minutes)
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def run_batch(
        self, *, now: datetime | None = None, limit: int = 25
    ) -> CollectionBatchResult:
        effective_now = now or utc_now()
        with self._session_factory.begin() as session:
            schedules = ensure_missing_schedules(
                session,
                now=effective_now,
                interval_minutes=self._schedule_interval_minutes,
                stagger_seconds=self._schedule_stagger_seconds,
            )
            stale = recover_stale_runs(
                session, now=effective_now, stale_after=self._stale_after
            )
            claims = claim_due_collections(session, now=effective_now, limit=limit)
        if schedules:
            logger.info(
                "collection_schedules_created", extra={"schedule_count": schedules}
            )
        outcomes = await asyncio.gather(*(self._process(claim) for claim in claims))
        succeeded = sum(outcome for outcome in outcomes)
        return CollectionBatchResult(
            len(claims), succeeded, len(claims) - succeeded, stale
        )

    async def _process(self, claim: ClaimedCollection) -> bool:
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
            with self._session_factory.begin() as session:
                return await _persist_success(
                    session,
                    claim,
                    normalized,
                    self._ai_manager,
                    self._ai_profile,
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
            try:
                with self._session_factory.begin() as session:
                    _record_failure(
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
            logger.warning(
                "collection_source_failed",
                extra={
                    "source_code": _safe_source(claim.source_code),
                    "failure_code": failure_code,
                },
            )
            return False


async def _persist_success(
    session: Session,
    claim: ClaimedCollection,
    normalized: NormalizedCollectionResult,
    ai_manager: AIProviderManager,
    ai_profile: UserRole,
) -> bool:
    """Persiste as ofertas normalizadas de uma coleta bem-sucedida.

    Classificação de relevância (`MissionOfferRelevance`) e normalização de
    título (`Product.display_name`) via IA acontecem aqui, dentro desta
    mesma transação curta, só na primeira vez que uma `(mission, offer)` ou
    `Product` é vista — nas coletas seguintes, ambas já estão em cache e
    nenhuma chamada de IA acontece. Isso mantém uma transação aberta um
    pouco além do puramente síncrono, mas só para ofertas novas: a chamada
    de IA já é protegida por timeout/circuit breaker (bem mais curta e
    previsível que Playwright, que é o motivo original de nunca segurar
    transação durante acesso externo neste módulo) e nunca impede a
    persistência da observação em si — falha de IA só faz a relevância
    ficar indefinida (comportamento conservador: sem alerta) e o título de
    exibição ficar pendente (o notifier usa o título bruto como
    alternativa), tentando de novo na próxima coleta.
    """
    run = session.scalar(
        select(CollectionRun).where(CollectionRun.id == claim.run_id).with_for_update()
    )
    if run is None or run.status is not CollectionRunStatus.RUNNING:
        return False
    mission = session.get(Mission, claim.mission_id)
    criteria = session.scalar(
        select(MissionCriteria).where(MissionCriteria.mission_id == claim.mission_id)
    )
    if mission is None or criteria is None:
        raise RuntimeError("claimed mission data no longer exists")

    observation_count = 0
    seen_offer_keys: set[tuple[str | None, str | None, str]] = set()
    for item in normalized.offers:
        identity_key = (
            item.seller_external_id,
            item.raw_offer.external_id,
            item.raw_offer.external_id or item.raw_offer.url,
        )
        if identity_key in seen_offer_keys:
            continue
        seen_offer_keys.add(identity_key)
        offer = _resolve_offer(session, claim.store_id, item)
        # DEC-048/TASK-063: escopado por missão -- duas missões diferentes
        # que coletem a mesma Offer nao compartilham mais o "ultimo preco
        # visto" para fins de cruzamento de alvo (bug corrigido).
        previous = session.scalar(
            select(PriceObservation)
            .join(CollectionRun, CollectionRun.id == PriceObservation.collection_run_id)
            .where(
                PriceObservation.offer_id == offer.id,
                CollectionRun.mission_id == claim.mission_id,
            )
            .order_by(PriceObservation.observed_at.desc(), PriceObservation.id.desc())
            .limit(1)
        )
        observation = PriceObservation(
            offer_id=offer.id,
            collection_run_id=run.id,
            amount=item.amount,
            currency=item.currency,
            shipping_amount=item.shipping_amount,
            total_amount=item.total_amount,
            fulfillment=item.fulfillment,
            availability=item.availability,
            observed_at=item.raw_offer.collected_at,
            raw_evidence=_raw_evidence(item.raw_offer),
        )
        session.add(observation)
        session.flush()

        relevance = await _resolve_offer_relevance(
            session,
            mission,
            criteria,
            offer,
            item.raw_offer.title,
            ai_manager,
            ai_profile,
        )
        if relevance is OfferRelevance.MATCH:
            for candidate in evaluate_price_alerts(
                mission, criteria, observation, previous
            ):
                publish_event(
                    session,
                    event_type=candidate.event_type,
                    aggregate_type=candidate.aggregate_type,
                    aggregate_id=candidate.aggregate_id,
                    payload=candidate.payload,
                    occurred_at=normalized.raw_result.completed_at,
                    mission_id=mission.id,
                )
        await _ensure_display_name(
            session, offer, item.raw_offer.title, ai_manager, ai_profile
        )
        if previous is not None and previous.availability != observation.availability:
            publish_event(
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
        observation_count += 1

    finish_collection_run(
        session,
        run.id,
        CollectionRunStatus.SUCCEEDED,
        finished_at=normalized.raw_result.completed_at,
    )
    publish_event(
        session,
        event_type=EventType.COLLECTION_COMPLETED_V1,
        aggregate_type=AggregateType.COLLECTION_RUN,
        aggregate_id=run.id,
        payload=CollectionCompletedPayload(
            run.id, run.store_id, run.mission_id, observation_count
        ),
        occurred_at=normalized.raw_result.completed_at,
        mission_id=run.mission_id,
    )
    _reset_source_backoff(session, run.mission_id, run.store_id)
    return True


async def _resolve_offer_relevance(
    session: Session,
    mission: Any,
    criteria: Any,
    offer: Offer,
    raw_title: str,
    ai_manager: AIProviderManager,
    ai_profile: UserRole,
) -> OfferRelevance | None:
    """Classifica `(mission, offer)` uma única vez; devolve o cache depois.

    Os insumos (busca da missão, título bruto da oferta) são imutáveis
    depois que a missão e a oferta existem, então uma classificação válida
    nunca precisa ser refeita. `None` (IA falhou ou respondeu fora do
    contrato) não é persistido -- a próxima coleta tenta de novo, e a
    observação atual é tratada como não elegível para alerta (DEC-048:
    comportamento conservador para `POSSIBLE_MATCH`/ausência de
    classificação, igual a `NO_MATCH`).
    """
    existing = session.get(MissionOfferRelevance, (mission.id, offer.id))
    if existing is not None:
        return existing.classification
    classification = await classify_offer_relevance(
        ai_manager,
        mission_search_query=criteria.search_query,
        raw_title=raw_title,
        profile=ai_profile,
    )
    if classification is None:
        return None
    session.add(
        MissionOfferRelevance(
            mission_id=mission.id,
            offer_id=offer.id,
            classification=classification,
            classified_at=utc_now(),
        )
    )
    session.flush()
    return classification


async def _ensure_display_name(
    session: Session,
    offer: Offer,
    raw_title: str,
    ai_manager: AIProviderManager,
    ai_profile: UserRole,
) -> None:
    """Normaliza `Product.display_name` uma única vez; nunca a sobrescreve.

    Não depende da missão (`Product` é 1:1 com `Offer` hoje) -- por isso é
    resolvido separado de `_resolve_offer_relevance`, mesmo que ambos
    costumem ser acionados no mesmo momento (primeira vez que a oferta é
    vista). Se a IA falhar, `Product.display_name` continua `None` e a
    próxima coleta tenta de novo; o notifier usa o título bruto
    (`Product.name`) como alternativa enquanto isso.
    """
    product = session.get(Product, offer.product_id)
    if product is None or product.display_name is not None:
        return
    display_title = await normalize_offer_title(
        ai_manager, raw_title=raw_title, profile=ai_profile
    )
    if display_title is not None:
        product.display_name = display_title


def _resolve_offer(session: Session, store_id: UUID, item: Any) -> Offer:
    seller = _resolve_seller(session, store_id, item)
    seller_id = seller.id if seller else None
    offer = _find_offer(
        session,
        store_id,
        seller_id,
        item.raw_offer.external_id,
        item.raw_offer.url,
    )
    if offer is not None:
        return offer
    product = Product(id=uuid4(), name=item.raw_offer.title[:300])
    offer = Offer(
        product_id=product.id,
        store_id=store_id,
        seller_id=seller_id,
        external_id=item.raw_offer.external_id,
        url=item.raw_offer.url,
    )
    try:
        with session.begin_nested():
            session.add(product)
            session.flush()
            session.add(offer)
            session.flush()
    except IntegrityError as error:
        if _constraint_name(error) not in _OFFER_IDENTITY_INDEXES:
            raise
        winner = _find_offer(
            session,
            store_id,
            seller_id,
            item.raw_offer.external_id,
            item.raw_offer.url,
        )
        if winner is None:
            raise
        return winner
    return offer


def _find_offer(
    session: Session,
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
    return session.scalar(select(Offer).where(*identity).limit(1))


def _resolve_seller(session: Session, store_id: UUID, item: Any) -> Seller | None:
    external_id = item.seller_external_id
    if not external_id:
        return None
    existing = session.scalar(
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
        with session.begin_nested():
            session.add(seller)
            session.flush()
    except IntegrityError as error:
        if _constraint_name(error) != _SELLER_IDENTITY_INDEX:
            raise
        winner = session.scalar(
            select(Seller).where(
                Seller.store_id == store_id, Seller.external_id == external_id
            )
        )
        if winner is None:
            raise
        return winner
    return seller


def _record_failure(
    session: Session,
    claim: ClaimedCollection,
    failure_code: str,
    failed_at: datetime,
    *,
    confirmed_block: bool = False,
) -> bool:
    run = session.scalar(
        select(CollectionRun).where(CollectionRun.id == claim.run_id).with_for_update()
    )
    if run is None or run.status is not CollectionRunStatus.RUNNING:
        return False
    finish_collection_run(
        session, run.id, CollectionRunStatus.FAILED, finished_at=failed_at
    )
    _publish_failure(session, run, failure_code, failed_at)
    if confirmed_block:
        _apply_source_backoff(session, run.mission_id, run.store_id, failed_at)
    return True


def _publish_failure(
    session: Session, run: CollectionRun, failure_code: str, failed_at: datetime
) -> None:
    publish_event(
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


def _apply_source_backoff(
    session: Session, mission_id: UUID, store_id: UUID, failed_at: datetime
) -> None:
    source = session.get(MissionSource, (mission_id, store_id))
    if source is None:
        return
    base_interval_minutes = session.scalar(
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


def _reset_source_backoff(session: Session, mission_id: UUID, store_id: UUID) -> None:
    source = session.get(MissionSource, (mission_id, store_id))
    if source is None:
        return
    already_reset = source.consecutive_blocks == 0 and source.next_eligible_at is None
    if already_reset:
        return
    source.consecutive_blocks = 0
    source.next_eligible_at = None


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


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


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
