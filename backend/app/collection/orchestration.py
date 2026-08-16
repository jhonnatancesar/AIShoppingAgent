"""Orquestra coletas agendadas sem manter transações durante acesso externo.

TASK-079: todo o caminho de persistência usa `AsyncSession` (nunca uma
`Session` síncrona bloqueante direto na thread do event loop -- causa raiz
comprovada de um autodeadlock em produção). A fronteira transacional segue
sempre Fase A (local, curta, commit) -> Fase B (IA, sem transação aberta)
-> Fase C (seção crítica por `mission_id`, curta, commit). Nenhuma dessas
transações jamais mantém um `await` externo (IA, HTTP, Playwright) aberto.
"""

import asyncio
import logging
import traceback
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai_provider import AIProviderManager
from app.alerts import evaluate_price_alerts
from app.collection.adapter import CollectionAdapter
from app.collection.contracts import (
    CollectionRequest,
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
from app.collection.model_matching import (
    normalize_for_matching as _normalize_for_matching,
)
from app.collection.model_matching import same_code as _same_code
from app.collection.model_matching import title_matches_model as _title_matches_model
from app.collection.models import (
    CollectionRun,
    CollectionRunStatus,
    MissionOfferRelevance,
    PriceObservation,
)
from app.collection.normalization import (
    Availability,
    NormalizedCollectionResult,
    PriceNormalizer,
)
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
    MissionPrelistErrataPayload,
    MissionPrelistReadyPayload,
)
from app.events.service import publish_event_async
from app.missions.models import (
    Mission,
    MissionCriteria,
    MissionSchedule,
    MissionSource,
    MissionStatus,
)
from app.missions.schedule import (
    advance_schedule,
    find_due_schedules_async,
    next_source_backoff,
    staggered_next_run_at,
)
from app.missions.service import promote_confirmed_product_identity_async
from app.offers.models import Offer
from app.products.models import Product
from app.stores.models import Seller, Store
from app.users.models import UserRole

logger = logging.getLogger("app.collection.orchestration")

V1_SOURCE_CODES = frozenset({"pichau", "terabyte", "amazon", "kabum"})
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
    claimed: int
    succeeded: int
    failed: int
    recovered_stale: int


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


async def claim_due_collections(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 25,
) -> tuple[ClaimedCollection, ...]:
    """Reserva fonte por fonte e avança a agenda numa transação curta."""
    effective_now = now or utc_now()
    claims: list[ClaimedCollection] = []
    schedules = await find_due_schedules_async(
        session, due_at=effective_now, limit=limit
    )
    for schedule in schedules:
        mission_id = schedule.mission_id
        running = await session.scalar(
            select(CollectionRun.id)
            .where(
                CollectionRun.mission_id == mission_id,
                CollectionRun.status == CollectionRunStatus.RUNNING,
            )
            .limit(1)
        )
        if running is not None:
            continue
        criteria = await session.scalar(
            select(MissionCriteria).where(MissionCriteria.mission_id == mission_id)
        )
        if criteria is None or not criteria.search_query.strip():
            continue
        sources = (
            await session.execute(
                select(Store.id, Store.code)
                .join(MissionSource, MissionSource.store_id == Store.id)
                .where(
                    MissionSource.mission_id == mission_id,
                    Store.is_active.is_(True),
                    Store.code.in_(V1_SOURCE_CODES),
                    # DEC-046: fonte específica em backoff (bloqueio externo
                    # confirmado) fica de fora deste ciclo; as demais fontes
                    # da mesma missão continuam normalmente.
                    or_(
                        MissionSource.next_eligible_at.is_(None),
                        MissionSource.next_eligible_at <= effective_now,
                    ),
                )
                .order_by(Store.code)
            )
        ).all()
        mission_claims: list[ClaimedCollection] = []
        for store_id, source_code in sources:
            try:
                async with session.begin_nested():
                    run = await start_collection_run(
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
                    criteria.model,
                )
            )
        if mission_claims:
            advance_schedule(schedule, started_at=effective_now)
            claims.extend(mission_claims)
    await session.flush()
    return tuple(claims)


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
        normalizer: PriceNormalizer | None = None,
        identity_resolver: ProductIdentityResolver | None = None,
        schedule_interval_minutes: int = 60,
        schedule_stagger_seconds: int = 0,
        stale_run_minutes: int = 10,
        max_concurrency: int = 4,
        claim_deadline_seconds: float = 300.0,
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
        self._session_factory = session_factory
        self._adapter = adapter
        self._ai_manager = ai_manager
        self._ai_profile = ai_profile
        self._normalizer = normalizer or PriceNormalizer()
        self._identity_resolver = identity_resolver
        self._schedule_interval_minutes = schedule_interval_minutes
        self._schedule_stagger_seconds = schedule_stagger_seconds
        self._stale_after = timedelta(minutes=stale_run_minutes)
        self._claim_deadline_seconds = claim_deadline_seconds
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def run_batch(
        self, *, now: datetime | None = None, limit: int = 25
    ) -> CollectionBatchResult:
        effective_now = now or utc_now()
        # Fase A: transação curta, só dados locais -- nenhum Playwright,
        # HTTP ou IA acontece dentro deste bloco.
        async with self._session_factory() as session, session.begin():
            schedules = await ensure_missing_schedules(
                session,
                now=effective_now,
                interval_minutes=self._schedule_interval_minutes,
                stagger_seconds=self._schedule_stagger_seconds,
            )
            stale = await recover_stale_runs(
                session, now=effective_now, stale_after=self._stale_after
            )
            claims = await claim_due_collections(
                session, now=effective_now, limit=limit
            )
        # Transação da Fase A já fechada neste ponto (fim do `async with`
        # acima) -- a Fase B (TASK-083) roda inteiramente fora dela.
        if schedules:
            logger.info(
                "collection_schedules_created", extra={"schedule_count": schedules}
            )
        claims = await self._resolve_identities(claims)
        outcomes = await asyncio.gather(*(self._process(claim) for claim in claims))
        succeeded = sum(outcome for outcome in outcomes)
        return CollectionBatchResult(
            len(claims), succeeded, len(claims) - succeeded, stale
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
            phase_a = await _persist_phase_a(self._session_factory, claim, normalized)
            if phase_a is None:
                return False
            ai_outcomes = await _run_phase_b(
                phase_a, self._ai_manager, self._ai_profile
            )
            return await _persist_phase_c(self._session_factory, phase_a, ai_outcomes)
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


_GENERIC_SEARCH_CANDIDATE_LIMIT = 3
"""TASK-082: nenhum número foi fixado durante a auditoria original --
derivado por analogia do único precedente já existente no pipeline de
coleta para "quantos candidatos merecem atenção determinística extra por
loja": `availability_fallback_max_candidates` (default 3,
`Settings`/`PlaywrightStoreProvider`, TASK-075). Mesma ordem de grandeza,
mesmo espírito -- poucos candidatos, por loja, antes de qualquer chamada
de IA."""


def _limit_generic_candidates(offers: tuple, *, limit: int) -> tuple:
    """TASK-082: reduz candidatos de uma busca GENÉRICA (`criteria.model
    is None`) a, no máximo, `limit` por loja -- nunca chamada quando
    `criteria.model` está preenchido (busca específica já reduzida pelo
    filtro de modelo da TASK-075, comportamento preservado). Reaproveita
    o mesmo princípio já comprovado por `_select_amazon_lowest_price`
    (menor preço, desempate determinístico por external_id/URL),
    generalizado para manter mais de um candidato -- aqui não há
    identidade confirmada que justifique colapsar para um só vencedor."""
    if len(offers) <= limit:
        return offers
    ordered = sorted(
        offers,
        key=lambda item: (
            item.amount,
            item.raw_offer.external_id or item.raw_offer.url,
        ),
    )
    return tuple(ordered[:limit])


def _select_amazon_lowest_price(offers: tuple) -> tuple:
    """TASK-075: exclusivo da Amazon: só é chamada pelo chamador quando
    `criteria.model` já confirmou identidade forte (gate obrigatório --
    ver `_persist_phase_a`). Mantém só as ofertas no menor `amount`;
    entre as empatadas, persiste/classifica só a vencedora do desempate
    determinístico por `external_id` (ASIN) crescente -- as demais
    empatadas não geram Offer/PriceObservation/relevância, para não
    multiplicar chamada de IA por vendedores redundantes no mesmo preço."""
    if not offers:
        return offers
    lowest = min(item.amount for item in offers)
    tied = [item for item in offers if item.amount == lowest]
    if len(tied) == 1:
        return tuple(tied)
    winner = min(
        tied, key=lambda item: item.raw_offer.external_id or item.raw_offer.url
    )
    return (winner,)


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
    previous_observation_id: UUID | None
    previous_amount: Decimal | None
    previous_currency: str | None
    previous_availability: Availability | None
    previous_observed_at: datetime | None


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


async def _persist_phase_a(
    session_factory: async_sessionmaker[AsyncSession],
    claim: ClaimedCollection,
    normalized: NormalizedCollectionResult,
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
        survivors = _filter_deterministic_candidates(criteria, normalized.offers)
        # TASK-082: busca genérica (sem model) não tem o filtro de modelo
        # da TASK-075 reduzindo nada -- limita aqui, por loja, antes de
        # persistir/classificar. Busca específica (model preenchido)
        # nunca passa por este passo, comportamento idêntico ao anterior.
        if criteria.model is None:
            survivors = _limit_generic_candidates(
                survivors, limit=_GENERIC_SEARCH_CANDIDATE_LIMIT
            )
        final_offers = (
            _select_amazon_lowest_price(survivors)
            if claim.source_code == "amazon" and criteria.model is not None
            else survivors
        )

        pending: list[_PendingOffer] = []
        seen_offer_keys: set[tuple[str | None, str | None, str]] = set()
        for item in final_offers:
            identity_key = (
                item.seller_external_id,
                item.raw_offer.external_id,
                item.raw_offer.external_id or item.raw_offer.url,
            )
            if identity_key in seen_offer_keys:
                continue
            seen_offer_keys.add(identity_key)
            offer = await _resolve_offer(session, claim.store_id, item)
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
            await session.flush()

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
                    needs_relevance=existing_relevance is None,
                    needs_display_name=product.display_name is None,
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
) -> tuple[_AIOutcome, ...]:
    """Fase B (TASK-079): nenhuma transação aberta -- só chamadas de IA.

    As chamadas de ofertas diferentes podem rodar em paralelo entre si,
    já que não há lock nem sessão de banco envolvida aqui.
    """

    async def _classify(pending: _PendingOffer) -> _AIOutcome:
        relevance = None
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
        return _AIOutcome(pending.offer_id, relevance, display_title)

    to_classify = [
        item
        for item in outcome.offers
        if item.needs_relevance or item.needs_display_name
    ]
    if not to_classify:
        return ()
    return tuple(await asyncio.gather(*(_classify(item) for item in to_classify)))


async def _persist_phase_c(
    session_factory: async_sessionmaker[AsyncSession],
    outcome: _PhaseAOutcome,
    ai_outcomes: tuple[_AIOutcome, ...],
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

        for pending in outcome.offers:
            ai_outcome = ai_by_offer.get(pending.offer_id)
            relevance: OfferRelevance | None = None
            if pending.needs_relevance:
                if ai_outcome is not None and ai_outcome.relevance is not None:
                    relevance = ai_outcome.relevance
                    await session.execute(
                        postgresql_insert(MissionOfferRelevance)
                        .values(
                            mission_id=mission.id,
                            offer_id=pending.offer_id,
                            classification=relevance,
                            classified_at=utc_now(),
                        )
                        .on_conflict_do_nothing(
                            index_elements=list(_MISSION_OFFER_RELEVANCE_PK)
                        )
                    )
            else:
                cached = await session.get(
                    MissionOfferRelevance, (mission.id, pending.offer_id)
                )
                relevance = cached.classification if cached is not None else None

            if (
                pending.needs_display_name
                and ai_outcome is not None
                and ai_outcome.display_title is not None
            ):
                product = await session.get(Product, pending.product_id)
                if product is not None and product.display_name is None:
                    product.display_name = ai_outcome.display_title

            if relevance is OfferRelevance.MATCH:
                current = PriceObservation(
                    id=pending.observation_id,
                    offer_id=pending.offer_id,
                    amount=pending.amount,
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
                for candidate in evaluate_price_alerts(
                    alert_mission, alert_criteria, current, previous
                ):
                    await publish_event_async(
                        session,
                        event_type=candidate.event_type,
                        aggregate_type=candidate.aggregate_type,
                        aggregate_id=candidate.aggregate_id,
                        payload=candidate.payload,
                        occurred_at=outcome.completed_at,
                        mission_id=mission.id,
                    )

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
        async with session.begin_nested():
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
        return winner
    return offer


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


async def _latest_match_observations_by_store(
    session: AsyncSession, mission_id: UUID
) -> list[PriceObservation]:
    rows = (
        await session.execute(
            select(PriceObservation, CollectionRun.store_id)
            .join(CollectionRun, CollectionRun.id == PriceObservation.collection_run_id)
            .join(
                MissionOfferRelevance,
                (MissionOfferRelevance.mission_id == CollectionRun.mission_id)
                & (MissionOfferRelevance.offer_id == PriceObservation.offer_id),
            )
            .where(
                CollectionRun.mission_id == mission_id,
                MissionOfferRelevance.classification == OfferRelevance.MATCH,
            )
            .order_by(PriceObservation.observed_at.desc(), PriceObservation.id.desc())
        )
    ).all()
    best_per_store: dict[UUID, PriceObservation] = {}
    for observation, store_id in rows:
        best_per_store.setdefault(store_id, observation)
    return list(best_per_store.values())


async def _maybe_publish_prelist_ready(
    session: AsyncSession, mission: Mission, occurred_at: datetime
) -> None:
    if not await _mission_prelist_round_complete(session, mission.id):
        return
    candidates = sorted(
        await _latest_match_observations_by_store(session, mission.id),
        key=lambda observation: observation.amount,
    )
    mission.prelist_sent = True
    if not candidates:
        return
    top = candidates[:2]
    first = top[0]
    second = top[1] if len(top) > 1 else None
    mission.prelist_lowest_amount = first.amount
    mission.prelist_lowest_currency = first.currency
    await publish_event_async(
        session,
        event_type=EventType.MISSION_PRELIST_READY_V1,
        aggregate_type=AggregateType.MISSION,
        aggregate_id=mission.id,
        payload=MissionPrelistReadyPayload(
            mission_id=mission.id,
            first_offer_id=first.offer_id,
            first_observation_id=first.id,
            first_amount=first.amount,
            first_currency=first.currency,
            second_offer_id=second.offer_id if second else None,
            second_observation_id=second.id if second else None,
            second_amount=second.amount if second else None,
            second_currency=second.currency if second else None,
        ),
        occurred_at=occurred_at,
        mission_id=mission.id,
    )


async def _maybe_publish_prelist_errata(
    session: AsyncSession, mission: Mission, occurred_at: datetime
) -> None:
    query = (
        select(PriceObservation)
        .join(CollectionRun, CollectionRun.id == PriceObservation.collection_run_id)
        .join(
            MissionOfferRelevance,
            (MissionOfferRelevance.mission_id == CollectionRun.mission_id)
            & (MissionOfferRelevance.offer_id == PriceObservation.offer_id),
        )
        .where(
            CollectionRun.mission_id == mission.id,
            MissionOfferRelevance.classification == OfferRelevance.MATCH,
        )
    )
    if mission.prelist_lowest_amount is not None:
        query = query.where(
            PriceObservation.currency == mission.prelist_lowest_currency,
            PriceObservation.amount < mission.prelist_lowest_amount,
        )
    observation = await session.scalar(
        query.order_by(
            PriceObservation.amount.asc(), PriceObservation.observed_at.desc()
        ).limit(1)
    )
    if observation is None:
        return
    mission.prelist_errata_sent = True
    await publish_event_async(
        session,
        event_type=EventType.MISSION_PRELIST_ERRATA_V1,
        aggregate_type=AggregateType.MISSION,
        aggregate_id=mission.id,
        payload=MissionPrelistErrataPayload(
            mission_id=mission.id,
            offer_id=observation.offer_id,
            observation_id=observation.id,
            current_amount=observation.amount,
            currency=observation.currency,
            previous_lowest_amount=mission.prelist_lowest_amount,
        ),
        occurred_at=occurred_at,
        mission_id=mission.id,
    )


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
