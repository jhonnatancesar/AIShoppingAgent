"""Fluxo real de agenda, coleta, persistência e eventos da TASK-062."""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from app.ai_provider import AIResponse
from app.collection.adapter import CollectionAdapter
from app.collection.cadence import (
    STALE_GRACE_MULTIPLIER,
    CadenceConfig,
    OfferFreshnessStatus,
    resolve_offer_freshness,
)
from app.collection.contracts import (
    CollectionRequest,
    CollectionResult,
    InstallmentInterestKind,
    MarketplacePartyKind,
    OfferCondition,
    RawCollectedOffer,
    RawInstallmentOption,
)
from app.collection.errors import ProviderBlockedError
from app.collection.models import (
    CollectionQueueConfig,
    CollectionRun,
    CollectionRunStatus,
    MissionOfferRelevance,
    PriceObservation,
    PromotionalWindow,
    SharedCollectionOffer,
    UserCollectionQueueState,
)
from app.collection.normalization import Availability
from app.collection.orchestration import CollectionOrchestrator, claim_due_collections
from app.collection.relevance import OfferRelevance
from app.core.config import Settings
from app.database.session import (
    create_async_session_factory,
    create_collection_async_database_engine,
)
from app.events import Event, EventType
from app.missions.models import (
    Mission,
    MissionCommand,
    MissionCriteria,
    MissionSchedule,
    MissionSource,
    MissionStatus,
)
from app.missions.service import transition_mission
from app.offers.models import Offer
from app.offers.query import get_offer_comparison_for_user, get_offer_detail_for_user
from app.products.identity_candidates import ProductIdentityCandidate
from app.products.models import Product
from app.stores.models import Seller, Store
from app.users.models import User, UserRole
from sqlalchemy import delete, func, select, text

pytestmark = pytest.mark.integration

# TASK-108: testes legados anteriores à fila justa por usuário --
# exercitam backoff por PROVIDER (DEC-046), chamando `run_batch()`
# várias vezes em sequência real para o MESMO usuário único. O cooldown
# individual (default 60-180s) os bloquearia na 2a/3a chamada sem
# relação nenhuma com o que o teste testa. Neutralizado explicitamente
# aqui -- a lógica da fila em si não muda por causa disso. Literalmente
# 0 (não só "pequeno"): `now`/`due_at` nestes testes são truncados a
# segundo inteiro (`.replace(microsecond=0)`) e podem colidir no MESMO
# segundo entre duas chamadas reais -- qualquer intervalo > 0 arriscaria
# `next_eligible_at` ficar estritamente à frente do `due_at` seguinte só
# por causa do truncamento, não da lógica de cooldown em si.
_NEUTRAL_USER_COOLDOWN_SECONDS = 0.0
# TASK-108: mesmo raciocínio para o pacing GLOBAL por loja (default 2s no
# `CollectionOrchestrator`) -- testes que chamam `run_batch`/
# `claim_due_collections` várias vezes em sequência real para a MESMA
# loja (backoff por provider, ou dois usuários due na mesma loja só para
# testar fairness) não podem ser bloqueados por um throttle que não têm
# relação com o que testam. Literalmente 0 (não só "pequeno") de
# propósito: `now`/`due_at` nestes testes são truncados a segundo inteiro
# (`.replace(microsecond=0)`) e podem colidir no MESMO segundo entre duas
# chamadas reais -- qualquer intervalo > 0 arriscaria `next_allowed_at`
# ficar estritamente à frente do `due_at` seguinte só por causa do
# truncamento, não da lógica. `0` é permitido só na API direta do
# `CollectionOrchestrator` (nunca via `Settings`/ADMIN, que exigem > 0) --
# equivale a desligar o throttle, uso exclusivo de teste.
_NEUTRAL_STORE_INTERVAL_SECONDS = 0.0


class _AlwaysMatchAIManager:
    """AIProviderManager de teste (TASK-063): sempre MATCH + título fixo.

    Fronteira de IA controlada, como toda fronteira externa nos testes de
    integração desta suíte -- não chama nenhum provedor real.
    """

    async def generate(self, request):
        content = (
            '{"relevance": "match"}'
            if request.purpose == "classify_offer_relevance"
            else '{"display_title": "Synthetic GPU"}'
        )
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub",
            content=content,
            finished_at=datetime.now(UTC),
        )


class _SuccessfulProvider:
    source_code = "pichau"

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        completed = request.requested_at.replace(microsecond=500000)
        return CollectionResult(
            self.source_code,
            request.requested_at,
            completed,
            (
                RawCollectedOffer(
                    source_code=self.source_code,
                    url=f"https://example.invalid/{uuid4().hex}",
                    title="TASK-062 synthetic GPU",
                    collected_at=completed,
                    external_id="task062-stable-offer",
                    raw_price="R$ 1.900,00",
                    raw_currency="BRL",
                    raw_shipping="Frete grátis",
                    raw_availability="Em estoque",
                    evidence={"card_text": "safe synthetic evidence"},
                ),
            ),
        )


class _FailingProvider:
    source_code = "kabum"

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        raise ProviderBlockedError(self.source_code, 403)


class _CountingProvider:
    """DEC-070: usada para provar que uma `Store` com `is_active=False`
    nunca chega a ter `collect()` chamado -- zero navegações externas,
    não só zero linhas persistidas."""

    def __init__(self, source_code: str) -> None:
        self.source_code = source_code
        self.calls = 0

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        self.calls += 1
        raise AssertionError(
            f"{self.source_code} está desativada (is_active=False) -- "
            "collect() nunca deveria ser chamado"
        )


class _EmptyProvider:
    source_code = "pichau"

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        return CollectionResult(
            self.source_code,
            request.requested_at,
            request.requested_at,
        )


def _seed_due_mission(sessions, now: datetime) -> tuple:
    with sessions.begin() as session:
        stores = {
            store.code: store
            for store in session.scalars(
                select(Store).where(Store.code.in_(("pichau", "kabum")))
            )
        }
        user = User(display_name="TASK-062 synthetic", role=UserRole.USER)
        session.add(user)
        session.flush()
        mission = Mission(
            user_id=user.id,
            title="TASK-062 orchestration",
            status=MissionStatus.ACTIVE,
        )
        session.add(mission)
        session.flush()
        session.add_all(
            (
                MissionCriteria(
                    mission_id=mission.id,
                    search_query="synthetic GPU",
                    target_amount=Decimal("2000"),
                    target_currency="BRL",
                ),
                MissionSchedule(
                    mission_id=mission.id,
                    interval_minutes=60,
                    next_run_at=now,
                    is_enabled=True,
                ),
                *(
                    MissionSource(mission_id=mission.id, store_id=stores[code].id)
                    for code in ("pichau", "kabum")
                ),
            )
        )
        return mission.id, stores["pichau"].id, stores["kabum"].id


def _seed_due_mission_for_new_user(sessions, now: datetime, *, label: str) -> tuple:
    """TASK-108: um usuário próprio, uma missão due, uma única fonte
    (pichau) -- o mínimo para exercitar a fila justa por usuário sem o
    ruído de múltiplas fontes por missão."""
    with sessions.begin() as session:
        store = session.scalar(select(Store).where(Store.code == "pichau"))
        user = User(display_name=f"TASK-108 {label}", role=UserRole.USER)
        session.add(user)
        session.flush()
        mission = Mission(
            user_id=user.id, title=f"TASK-108 {label}", status=MissionStatus.ACTIVE
        )
        session.add(mission)
        session.flush()
        session.add_all(
            (
                MissionCriteria(mission_id=mission.id, search_query="synthetic GPU"),
                MissionSchedule(
                    mission_id=mission.id,
                    interval_minutes=60,
                    next_run_at=now,
                    is_enabled=True,
                ),
                MissionSource(mission_id=mission.id, store_id=store.id),
            )
        )
        return user.id, mission.id


def test_fair_queue_claims_only_max_users_and_leaves_others_untouched(
    integration_database,
) -> None:
    """TASK-108: com `max_users=1`, só um dos dois usuários elegíveis tem
    claim reivindicada nesta rodada; o outro fica intocado (schedule
    continua due, sem `CollectionRun` criada)."""
    now = datetime.now(UTC).replace(microsecond=0)
    user_a, mission_a = _seed_due_mission_for_new_user(
        integration_database.sessions, now, label="A"
    )
    user_b, mission_b = _seed_due_mission_for_new_user(
        integration_database.sessions, now, label="B"
    )

    async def _claim():
        async with integration_database.async_sessions() as session, session.begin():
            return await claim_due_collections(session, now=now, limit=25, max_users=1)

    claims = asyncio.run(_claim())

    assert len(claims) == 1
    claimed_mission_ids = {claim.mission_id for claim in claims}
    assert claimed_mission_ids in ({mission_a}, {mission_b})

    with integration_database.sessions() as session:
        runs = list(session.scalars(select(CollectionRun)))
        assert len(runs) == 1
        untouched_mission = (
            mission_b if claimed_mission_ids == {mission_a} else mission_a
        )
        schedule = session.scalar(
            select(MissionSchedule).where(
                MissionSchedule.mission_id == untouched_mission
            )
        )
        assert schedule.next_run_at == now  # não avançou -- não foi tocada

        claimed_user = user_a if claimed_mission_ids == {mission_a} else user_b
        untouched_user = user_b if claimed_mission_ids == {mission_a} else user_a
        assert session.get(UserCollectionQueueState, claimed_user) is not None
        assert session.get(UserCollectionQueueState, untouched_user) is None


def test_fair_queue_cooldown_is_per_user_and_does_not_block_others(
    integration_database,
) -> None:
    """TASK-108: depois que A é processado (fica em cooldown), uma nova
    rodada no MESMO instante escolhe B -- o cooldown de A nunca bloqueia
    B, que segue elegível imediatamente."""
    now = datetime.now(UTC).replace(microsecond=0)
    user_a, mission_a = _seed_due_mission_for_new_user(
        integration_database.sessions, now, label="A"
    )
    user_b, mission_b = _seed_due_mission_for_new_user(
        integration_database.sessions, now, label="B"
    )

    async def _claim():
        async with integration_database.async_sessions() as session, session.begin():
            return await claim_due_collections(
                session,
                now=now,
                limit=25,
                max_users=1,
                user_cooldown_min_seconds=60.0,
                user_cooldown_max_seconds=60.0,
                store_min_interval_seconds=_NEUTRAL_STORE_INTERVAL_SECONDS,
            )

    first = asyncio.run(_claim())
    assert len(first) == 1
    first_user = user_a if first[0].mission_id == mission_a else user_b

    with integration_database.sessions() as session:
        state = session.get(UserCollectionQueueState, first_user)
        assert state.last_processed_at == now
        assert state.next_eligible_at > now

    # TASK-108: a missão do usuário já processado ganha outra execução due
    # no mesmo instante (simula "ainda tinha trabalho pendente") --
    # reforça só o `next_run_at`, sem tocar no cooldown do usuário.
    with integration_database.sessions.begin() as session:
        schedule = session.scalar(
            select(MissionSchedule).where(
                MissionSchedule.mission_id
                == (mission_a if first_user == user_a else mission_b)
            )
        )
        schedule.next_run_at = now

    second = asyncio.run(_claim())
    assert len(second) == 1
    second_mission = second[0].mission_id
    second_user = user_a if second_mission == mission_a else user_b

    # O usuário do primeiro ciclo está em cooldown -- a segunda rodada,
    # no mesmo `now`, nunca o escolhe de novo; escolhe o outro usuário.
    assert second_user != first_user


def test_store_throttle_blocks_second_user_same_store_within_batch(
    integration_database,
) -> None:
    """TASK-108: pacing GLOBAL por loja -- dois usuários diferentes com
    missões due na MESMA loja, no MESMO batch; só a primeira claim passa,
    a segunda fica de fora por causa do throttle da loja (não da fila por
    usuário -- `max_users=2` deixa os dois elegíveis)."""
    now = datetime.now(UTC).replace(microsecond=0)
    user_a, mission_a = _seed_due_mission_for_new_user(
        integration_database.sessions, now, label="StoreA"
    )
    user_b, mission_b = _seed_due_mission_for_new_user(
        integration_database.sessions, now, label="StoreB"
    )

    async def _claim():
        async with integration_database.async_sessions() as session, session.begin():
            return await claim_due_collections(
                session,
                now=now,
                limit=25,
                max_users=2,
                store_min_interval_seconds=60.0,
            )

    claims = asyncio.run(_claim())

    assert len(claims) == 1
    claimed_mission = claims[0].mission_id
    assert claimed_mission in (mission_a, mission_b)

    with integration_database.sessions() as session:
        untouched_mission = mission_b if claimed_mission == mission_a else mission_a
        schedule = session.scalar(
            select(MissionSchedule).where(
                MissionSchedule.mission_id == untouched_mission
            )
        )
        # Nunca avançou -- ficou de fora pelo throttle GLOBAL da loja, não
        # por cooldown/fairness (os dois usuários eram elegíveis).
        assert schedule.next_run_at == now


def test_store_throttle_persists_across_restart(integration_database) -> None:
    """TASK-108: o throttle da loja sobrevive a um "restart" do worker --
    sessão/transação novas a cada chamada, mesmo idioma de teste já usado
    por `test_source_backoff_lifecycle_across_batches` (equivalente a um
    restart real: nenhum estado sobrevive em memória entre as chamadas,
    só o que está persistido)."""
    now = datetime.now(UTC).replace(microsecond=0)
    _seed_due_mission_for_new_user(integration_database.sessions, now, label="RestartA")

    async def _claim(claim_now):
        async with integration_database.async_sessions() as session, session.begin():
            return await claim_due_collections(
                session, now=claim_now, limit=25, store_min_interval_seconds=120.0
            )

    first = asyncio.run(_claim(now))
    assert len(first) == 1

    # Segunda missão due na MESMA loja (pichau, fixo em
    # `_seed_due_mission_for_new_user`), quase imediatamente depois --
    # "restart" simulado só pela sessão/transação novas.
    _, mission_2 = _seed_due_mission_for_new_user(
        integration_database.sessions, now, label="RestartB"
    )
    second = asyncio.run(_claim(now + timedelta(seconds=1)))

    assert len(second) == 0
    with integration_database.sessions() as session:
        schedule = session.scalar(
            select(MissionSchedule).where(MissionSchedule.mission_id == mission_2)
        )
        assert schedule.next_run_at == now  # nunca avançou -- throttle persistido


def test_queue_config_admin_override_applies_without_worker_restart(
    integration_database,
) -> None:
    """TASK-108: um override do ADMIN (`CollectionQueueConfig`,
    persistido) já vale no PRÓXIMO `run_batch` -- nunca precisa recriar o
    `CollectionOrchestrator` nem reiniciar o worker."""
    now = datetime.now(UTC).replace(microsecond=0)
    user_a, mission_a = _seed_due_mission_for_new_user(
        integration_database.sessions, now, label="ConfigA"
    )

    # Override persistido ANTES de qualquer batch: intervalo de loja bem
    # maior que o default de fábrica do orchestrator (2s) -- se o
    # override não fosse aplicado, a segunda claim abaixo passaria.
    with integration_database.sessions.begin() as session:
        session.add(
            CollectionQueueConfig(id=1, store_min_interval_seconds_override=120.0)
        )

    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((_SuccessfulProvider(),)),
        ai_manager=_AlwaysMatchAIManager(),
        # Default do orchestrator propositalmente baixo -- só o override
        # persistido, resolvido a cada `run_batch`, deve valer.
        store_min_interval_seconds=1.0,
    )

    first = asyncio.run(orchestrator.run_batch(now=now))
    assert (first.claimed, first.succeeded) == (1, 1)

    user_b, mission_b = _seed_due_mission_for_new_user(
        integration_database.sessions, now, label="ConfigB"
    )
    second = asyncio.run(orchestrator.run_batch(now=now + timedelta(seconds=1)))

    # Sem o override (1s já teria expirado), a claim de B passaria --
    # bloqueada porque o override (120s) foi lido de novo neste batch.
    assert (second.claimed, second.succeeded) == (0, 0)
    with integration_database.sessions() as session:
        schedule = session.scalar(
            select(MissionSchedule).where(MissionSchedule.mission_id == mission_b)
        )
        assert schedule.next_run_at == now


def test_orchestrator_isolates_source_failure_and_publishes_real_events(
    integration_database,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, pichau_id, kabum_id = _seed_due_mission(
        integration_database.sessions, now
    )
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((_SuccessfulProvider(), _FailingProvider())),
        ai_manager=_AlwaysMatchAIManager(),
    )

    result = asyncio.run(orchestrator.run_batch(now=now))

    assert result.claimed == 2
    assert result.succeeded == 1
    assert result.failed == 1
    with integration_database.sessions.begin() as session:
        runs = list(
            session.scalars(
                select(CollectionRun)
                .where(CollectionRun.mission_id == mission_id)
                .order_by(CollectionRun.store_id)
            )
        )
        status_by_store = {run.store_id: run.status for run in runs}
        assert status_by_store == {
            pichau_id: CollectionRunStatus.SUCCEEDED,
            kabum_id: CollectionRunStatus.FAILED,
        }
        assert session.scalar(select(func.count(PriceObservation.id))) == 1
        event_types = set(
            session.scalars(
                select(Event.event_type).where(Event.mission_id == mission_id)
            )
        )
        assert event_types == {
            EventType.COLLECTION_COMPLETED_V1.value,
            EventType.COLLECTION_FAILED_V1.value,
            EventType.PRICE_TARGET_REACHED_V1.value,
            # TASK-068: os dois sources ficaram terminais nesta mesma rodada
            # (pichau sucesso, kabum bloqueado) -- a pré-lista dispara junto.
            EventType.MISSION_PRELIST_READY_V2.value,
        }
        failed = session.scalar(
            select(Event).where(
                Event.mission_id == mission_id,
                Event.event_type == EventType.COLLECTION_FAILED_V1.value,
            )
        )
        assert failed is not None
        assert failed.payload["failure_code"] == "provider_blocked"


def test_disabled_store_generates_zero_claims_and_zero_provider_calls(
    integration_database,
) -> None:
    """DEC-070: `stores.is_active=false` é o mecanismo real de
    habilitar/desabilitar uma fonte -- uma missão com as duas fontes
    (uma ativa, uma desativada) só reivindica/coleta a ativa; a
    desativada gera zero `CollectionRun`, zero evento e zero chamada ao
    provider (não só zero linhas persistidas -- o provider nunca é
    sequer invocado)."""
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, pichau_id, kabum_id = _seed_due_mission(
        integration_database.sessions, now
    )
    with integration_database.sessions.begin() as session:
        kabum = session.get(Store, kabum_id)
        kabum.is_active = False

    kabum_provider = _CountingProvider("kabum")
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((_SuccessfulProvider(), kabum_provider)),
        ai_manager=_AlwaysMatchAIManager(),
    )

    result = asyncio.run(orchestrator.run_batch(now=now))

    assert result.claimed == 1  # só a pichau, ativa
    assert result.succeeded == 1
    assert kabum_provider.calls == 0
    with integration_database.sessions.begin() as session:
        runs = list(
            session.scalars(
                select(CollectionRun).where(CollectionRun.mission_id == mission_id)
            )
        )
        assert {run.store_id for run in runs} == {pichau_id}
        assert (
            session.scalar(
                select(func.count(Event.id)).where(
                    Event.mission_id == mission_id,
                    Event.event_type == EventType.COLLECTION_FAILED_V1.value,
                )
            )
            == 0
        )

    assert asyncio.run(orchestrator.run_batch(now=now)).claimed == 0


class _FixedOfferProvider:
    """Sempre devolve a mesma oferta real (mesmo `external_id`/URL).

    Simula duas missões diferentes encontrando o mesmo anúncio de verdade
    numa loja -- usado para provar que o estado de "alvo já atingido"
    (TASK-063/DEC-048) não vaza de uma missão para outra só porque as duas
    coletaram a mesma `Offer`.
    """

    source_code = "pichau"

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        completed = request.requested_at.replace(microsecond=500000)
        return CollectionResult(
            self.source_code,
            request.requested_at,
            completed,
            (
                RawCollectedOffer(
                    source_code=self.source_code,
                    url="https://example.invalid/shared-task-063-offer",
                    title="TASK-063 shared GPU",
                    collected_at=completed,
                    external_id="task063-shared-offer",
                    raw_price="R$ 900,00",
                    raw_currency="BRL",
                    raw_shipping="Frete grátis",
                    raw_availability="Em estoque",
                    evidence={"card_text": "safe synthetic evidence"},
                ),
            ),
        )


def test_target_reached_state_does_not_leak_between_missions_sharing_an_offer(
    integration_database,
) -> None:
    """DEC-048: duas missões que encontram a mesma `Offer` não interferem.

    Antes da correção, `previous` era a última `PriceObservation` daquela
    `Offer` em qualquer missão -- a segunda missão a coletar o mesmo anúncio
    herdava o "alvo já atingido" da primeira e nunca gerava seu próprio
    evento. Cada missão precisa avaliar cruzamento de alvo pela sua própria
    história, mesmo compartilhando a `Offer`.
    """
    now = datetime.now(UTC).replace(microsecond=0)
    with integration_database.sessions.begin() as session:
        store = session.scalar(select(Store).where(Store.code == "pichau"))
        assert store is not None
        user = User(display_name="DEC-048 shared offer", role=UserRole.USER)
        session.add(user)
        session.flush()
        mission_a = Mission(
            user_id=user.id, title="mission a shared offer", status=MissionStatus.ACTIVE
        )
        mission_b = Mission(
            user_id=user.id, title="mission b shared offer", status=MissionStatus.ACTIVE
        )
        session.add_all((mission_a, mission_b))
        session.flush()
        session.add_all(
            (
                MissionCriteria(
                    mission_id=mission_a.id,
                    search_query="TASK-063 shared GPU",
                    target_amount=Decimal("2000"),
                    target_currency="BRL",
                ),
                MissionSource(mission_id=mission_a.id, store_id=store.id),
                # missao B so ganha MissionSource (e agenda) antes do ciclo 2,
                # de proposito: ensure_missing_schedules so cria agenda para
                # missao com fonte selecionada, entao a missao B fica fora do
                # ciclo 1 (nao existe MissionSource dela ainda).
                MissionCriteria(
                    mission_id=mission_b.id,
                    search_query="TASK-063 shared GPU",
                    target_amount=Decimal("2000"),
                    target_currency="BRL",
                ),
            )
        )
        mission_a_id, mission_b_id, store_id = mission_a.id, mission_b.id, store.id

    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((_FixedOfferProvider(),)),
        ai_manager=_AlwaysMatchAIManager(),
    )

    def _target_events(mission_id):
        with integration_database.sessions() as session:
            return session.scalar(
                select(func.count(Event.id)).where(
                    Event.mission_id == mission_id,
                    Event.event_type == EventType.PRICE_TARGET_REACHED_V1.value,
                )
            )

    with integration_database.sessions.begin() as session:
        session.add(
            MissionSchedule(
                mission_id=mission_a_id,
                interval_minutes=60,
                next_run_at=now,
                is_enabled=True,
            )
        )

    # Ciclo 1: só a missão A está due -- primeira vez que a oferta é vista,
    # ela cruza o alvo e gera seu próprio evento.
    result_a = asyncio.run(orchestrator.run_batch(now=now))
    assert (result_a.claimed, result_a.succeeded) == (1, 1)
    assert _target_events(mission_a_id) == 1
    assert _target_events(mission_b_id) == 0

    # TASK-112 fase 3B (achado real): a agenda do caminho antigo agora
    # avança pela política de cadência (`app.collection.cadence`), que
    # ignora `MissionSchedule.interval_minutes` de propósito -- não dá
    # mais para isolar a missão A do ciclo 2 configurando um intervalo
    # gigante (`interval_minutes=100_000`, técnica antiga deste teste).
    # Força a agenda da missão A para bem longe do ciclo 2 diretamente --
    # o teste é sobre isolamento de alerta entre missões (DEC-048), não
    # sobre o valor exato da próxima agenda da missão A.
    # TASK-112 fase 3B (correção estrutural, achado real da auditoria):
    # a unidade autoritativa de cadência agora é `MissionSource`, não
    # `MissionSchedule` (agregado derivado, só exibição) -- forçar só o
    # agregado não bastaria mais, o ciclo 2 reclamaria a missão A de novo
    # pela cadência real do ciclo 1 (45-75min, já vencida em `later`).
    with integration_database.sessions.begin() as session:
        schedule_a = session.scalar(
            select(MissionSchedule).where(MissionSchedule.mission_id == mission_a_id)
        )
        schedule_a.next_run_at = now + timedelta(days=365)
        source_a = session.get(MissionSource, (mission_a_id, store_id))
        source_a.next_run_at = now + timedelta(days=365)

    # Ciclo 2: agora a missão B fica due e coleta a MESMA Offer pela
    # primeira vez -- sem a correção, herdaria o "já atingido" da missão A
    # e não geraria evento nenhum.
    later = now + timedelta(minutes=90)
    with integration_database.sessions.begin() as session:
        session.add(MissionSource(mission_id=mission_b_id, store_id=store_id))
        session.add(
            MissionSchedule(
                mission_id=mission_b_id,
                interval_minutes=60,
                next_run_at=later,
                is_enabled=True,
            )
        )
    result_b = asyncio.run(orchestrator.run_batch(now=later))
    assert (result_b.claimed, result_b.succeeded) == (1, 1)
    assert _target_events(mission_a_id) == 1  # intocado
    assert _target_events(mission_b_id) == 1  # não suprimido pela missão A


class _SingleTitleImageProvider:
    """Sempre devolve o mesmo título (mesma identidade resolvida) com uma
    URL de imagem controlável -- usada para provar a regra de imagem
    canônica do Product (subtask 4, auditoria GG Oferta)."""

    def __init__(
        self, *, source_code: str, external_id: str, image_url: str, title: str
    ) -> None:
        self.source_code = source_code
        self._external_id = external_id
        self._image_url = image_url
        self._title = title

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        completed = request.requested_at.replace(microsecond=500000)
        return CollectionResult(
            self.source_code,
            request.requested_at,
            completed,
            (
                RawCollectedOffer(
                    source_code=self.source_code,
                    url=f"https://example.invalid/{self._external_id}",
                    title=self._title,
                    collected_at=completed,
                    external_id=self._external_id,
                    raw_price="R$ 5.000,00",
                    raw_currency="BRL",
                    raw_shipping="Frete grátis",
                    raw_availability="Em estoque",
                    image_url=self._image_url,
                    evidence={"card_text": "safe synthetic evidence"},
                ),
            ),
        )


def test_canonical_image_is_set_once_and_never_overwritten_across_stores(
    integration_database,
) -> None:
    """Subtask 4 (auditoria GG Oferta): a primeira imagem válida de um
    produto com identidade resolvida vira `Product.canonical_image_url` e
    nunca é sobrescrita automaticamente depois -- nem por uma coleta
    posterior de OUTRA loja do mesmo produto (sem heurística de
    qualidade, sem request HTTP extra, sem "última imagem sempre
    vence"). A `Offer` de cada loja preserva sua própria imagem; só a
    apresentação (`resolve_offer_image_url`, testado à parte) decide
    quando cair para a canônica."""
    now = datetime.now(UTC).replace(microsecond=0)
    title = "Apple iPhone 17 Pro, 256 GB, preto"
    with integration_database.sessions.begin() as session:
        pichau = session.scalar(select(Store).where(Store.code == "pichau"))
        kabum = session.scalar(select(Store).where(Store.code == "kabum"))
        user = User(display_name="subtask-4 canonical image", role=UserRole.USER)
        session.add(user)
        session.flush()
        mission = Mission(
            user_id=user.id,
            title="subtask 4 canonical image",
            status=MissionStatus.ACTIVE,
        )
        session.add(mission)
        session.flush()
        session.add_all(
            (
                MissionCriteria(mission_id=mission.id, search_query=title),
                MissionSchedule(
                    mission_id=mission.id,
                    interval_minutes=60,
                    next_run_at=now,
                    is_enabled=True,
                ),
                MissionSource(mission_id=mission.id, store_id=pichau.id),
            )
        )
        mission_id, kabum_id = mission.id, kabum.id

    def _product_by_offer(external_id: str) -> Product:
        with integration_database.sessions() as session:
            offer = session.scalar(
                select(Offer).where(Offer.external_id == external_id)
            )
            assert offer is not None
            product = session.get(Product, offer.product_id)
            assert product is not None
            session.expunge(product)
            return product

    orchestrator_1 = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter(
            (
                _SingleTitleImageProvider(
                    source_code="pichau",
                    external_id="subtask4-pichau",
                    image_url="https://media.pichau.com.br/iphone.jpg",
                    title=title,
                ),
            )
        ),
        ai_manager=_AlwaysMatchAIManager(),
    )
    result_1 = asyncio.run(orchestrator_1.run_batch(now=now))
    assert (result_1.claimed, result_1.succeeded) == (1, 1)

    product_after_cycle_1 = _product_by_offer("subtask4-pichau")
    assert product_after_cycle_1.identity_key is not None
    assert (
        product_after_cycle_1.canonical_image_url
        == "https://media.pichau.com.br/iphone.jpg"
    )

    # Ciclo 2: outra loja (Kabum) encontra o MESMO produto (mesmo título/
    # identidade), com uma imagem DIFERENTE -- não pode virar a canônica.
    later = now + timedelta(minutes=90)
    with integration_database.sessions.begin() as session:
        schedule = session.scalar(
            select(MissionSchedule).where(MissionSchedule.mission_id == mission_id)
        )
        schedule.next_run_at = later
        session.add(MissionSource(mission_id=mission_id, store_id=kabum_id))

    orchestrator_2 = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter(
            (
                _SingleTitleImageProvider(
                    source_code="kabum",
                    external_id="subtask4-kabum",
                    image_url="https://images.kabum.com.br/iphone-outra-foto.jpg",
                    title=title,
                ),
            )
        ),
        ai_manager=_AlwaysMatchAIManager(),
    )
    result_2 = asyncio.run(orchestrator_2.run_batch(now=later))
    assert result_2.succeeded >= 1

    product_after_cycle_2 = _product_by_offer("subtask4-pichau")
    kabum_offer_product = _product_by_offer("subtask4-kabum")
    assert kabum_offer_product.id == product_after_cycle_2.id  # mesma identidade global
    assert (
        product_after_cycle_2.canonical_image_url
        == "https://media.pichau.com.br/iphone.jpg"
    )  # nunca sobrescrita pela foto do Kabum

    with integration_database.sessions() as session:
        kabum_offer = session.scalar(
            select(Offer).where(Offer.external_id == "subtask4-kabum")
        )
        assert (
            kabum_offer.image_url == "https://images.kabum.com.br/iphone-outra-foto.jpg"
        )  # a Offer do Kabum preserva a própria imagem -- só não vira canônica


def test_canonical_image_is_independent_across_different_products(
    integration_database,
) -> None:
    """Subtask 4 (auditoria GG Oferta, item 10 da checagem final): a regra
    de imagem canônica é POR produto/variante -- dois produtos diferentes
    (identidades distintas) nunca compartilham nem misturam a
    `canonical_image_url` um do outro. A imagem do iPhone jamais aparece
    no Galaxy, e vice-versa, mesmo coletados na mesma janela de tempo."""
    now = datetime.now(UTC).replace(microsecond=0)
    title_a = "Apple iPhone 17 Pro, 256 GB, preto"
    title_b = "Samsung Galaxy S24 Ultra 512 GB Titânio"
    with integration_database.sessions.begin() as session:
        pichau = session.scalar(select(Store).where(Store.code == "pichau"))
        kabum = session.scalar(select(Store).where(Store.code == "kabum"))
        user = User(display_name="subtask-4 variant independence", role=UserRole.USER)
        session.add(user)
        session.flush()
        mission_a = Mission(
            user_id=user.id,
            title="subtask 4 variant independence -- iPhone",
            status=MissionStatus.ACTIVE,
        )
        mission_b = Mission(
            user_id=user.id,
            title="subtask 4 variant independence -- Galaxy",
            status=MissionStatus.ACTIVE,
        )
        session.add_all((mission_a, mission_b))
        session.flush()
        session.add_all(
            (
                MissionCriteria(mission_id=mission_a.id, search_query=title_a),
                MissionSchedule(
                    mission_id=mission_a.id,
                    interval_minutes=60,
                    next_run_at=now,
                    is_enabled=True,
                ),
                MissionSource(mission_id=mission_a.id, store_id=pichau.id),
                MissionCriteria(mission_id=mission_b.id, search_query=title_b),
                MissionSchedule(
                    mission_id=mission_b.id,
                    interval_minutes=60,
                    next_run_at=now,
                    is_enabled=True,
                ),
                MissionSource(mission_id=mission_b.id, store_id=kabum.id),
            )
        )

    def _product_by_offer(external_id: str) -> Product:
        with integration_database.sessions() as session:
            offer = session.scalar(
                select(Offer).where(Offer.external_id == external_id)
            )
            assert offer is not None
            product = session.get(Product, offer.product_id)
            assert product is not None
            session.expunge(product)
            return product

    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter(
            (
                _SingleTitleImageProvider(
                    source_code="pichau",
                    external_id="subtask4-variant-iphone",
                    image_url="https://media.pichau.com.br/iphone.jpg",
                    title=title_a,
                ),
                _SingleTitleImageProvider(
                    source_code="kabum",
                    external_id="subtask4-variant-galaxy",
                    image_url="https://images.kabum.com.br/galaxy.jpg",
                    title=title_b,
                ),
            )
        ),
        ai_manager=_AlwaysMatchAIManager(),
    )
    result = asyncio.run(orchestrator.run_batch(now=now))
    assert (result.claimed, result.succeeded) == (2, 2)

    product_a = _product_by_offer("subtask4-variant-iphone")
    product_b = _product_by_offer("subtask4-variant-galaxy")
    assert product_a.id != product_b.id  # identidades distintas, produtos distintos
    assert product_a.canonical_image_url == "https://media.pichau.com.br/iphone.jpg"
    assert product_b.canonical_image_url == "https://images.kabum.com.br/galaxy.jpg"
    assert product_a.canonical_image_url != product_b.canonical_image_url


def test_concurrent_claimers_never_duplicate_a_source(integration_database) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, _, _ = _seed_due_mission(integration_database.sessions, now)
    barrier = Barrier(2)

    def claim() -> tuple:
        # TASK-079: cada thread cria seu PRÓPRIO AsyncEngine (não
        # reaproveita `integration_database.async_sessions`) -- um único
        # AsyncEngine/pool de conexões não é seguro entre threads com
        # event loops diferentes (os objetos assíncronos internos do
        # psycopg ficam presos ao loop que os criou). Cada thread roda seu
        # próprio event loop com sua própria conexão, sincronizadas pelo
        # Barrier para forçar a simultaneidade exata que o `FOR UPDATE
        # SKIP LOCKED` precisa resolver corretamente -- igual a dois
        # processos de worker reais fariam.
        async def _claim_async() -> tuple:
            engine = create_collection_async_database_engine(
                integration_database.settings
            )
            sessions = create_async_session_factory(engine)
            try:
                async with sessions() as session, session.begin():
                    barrier.wait(timeout=10)
                    return await claim_due_collections(session, now=now)
            finally:
                await engine.dispose()

        return asyncio.run(_claim_async())

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: claim(), range(2)))

    assert sorted(len(result) for result in results) == [0, 2]
    with integration_database.sessions.begin() as session:
        runs = list(
            session.scalars(
                select(CollectionRun).where(CollectionRun.mission_id == mission_id)
            )
        )
        assert len(runs) == 2
        assert len({(run.mission_id, run.store_id) for run in runs}) == 2


def test_source_backoff_lifecycle_across_batches(integration_database) -> None:
    """Ciclo completo do backoff por fonte (DEC-046) num intervalo de 30 min.

    Usa sempre `now=datetime.now(UTC)` real em cada `run_batch`: quando uma
    fonte falha, `finished_at` vem de `utc_now()` real, então `started_at`
    nunca pode estar no futuro em relação a isso. Simular "o tempo passou"
    é feito escrevendo direto no banco entre os ciclos (agenda/backoff),
    não avançando `now` para o futuro. Cada chamada abre sessão/transação
    novas e só lê o estado já persistido -- equivalente a um restart do
    worker entre os ciclos, então este teste também comprova que o backoff
    sobrevive a isso.
    """
    # microsecond=0: _SuccessfulProvider faz completed_at = requested_at com
    # microsegundo fixo em 500000, e completed_at >= started_at é exigido;
    # sem truncar, um requested_at com microsegundo > 500000 quebraria isso.
    now0 = datetime.now(UTC).replace(microsecond=0)
    with integration_database.sessions.begin() as session:
        stores = {
            store.code: store
            for store in session.scalars(
                select(Store).where(Store.code.in_(("pichau", "kabum")))
            )
        }
        user = User(display_name="DEC-046 lifecycle", role=UserRole.USER)
        session.add(user)
        session.flush()
        mission = Mission(
            user_id=user.id, title="backoff lifecycle", status=MissionStatus.ACTIVE
        )
        session.add(mission)
        session.flush()
        session.add_all(
            (
                MissionCriteria(mission_id=mission.id, search_query="synthetic GPU"),
                MissionSchedule(
                    mission_id=mission.id,
                    interval_minutes=30,
                    next_run_at=now0,
                    is_enabled=True,
                ),
                *(
                    MissionSource(mission_id=mission.id, store_id=stores[code].id)
                    for code in ("pichau", "kabum")
                ),
            )
        )
        mission_id = mission.id
        pichau_id, kabum_id = stores["pichau"].id, stores["kabum"].id

    def _source_state(store_id):
        with integration_database.sessions() as session:
            source = session.get(MissionSource, (mission_id, store_id))
            return source.consecutive_blocks, source.next_eligible_at

    def _run_count_for(store_id):
        with integration_database.sessions() as session:
            return session.scalar(
                select(func.count(CollectionRun.id)).where(
                    CollectionRun.mission_id == mission_id,
                    CollectionRun.store_id == store_id,
                )
            )

    def _make_due_again(due_at):
        """TASK-112 fase 3B (correção estrutural): força a cadência de
        CADA `MissionSource` da missão, não só o agregado derivado
        (`MissionSchedule`) -- o backoff independente de cada source
        (`next_eligible_at`) continua decidindo quem de fato é
        reivindicado neste ciclo."""
        with integration_database.sessions.begin() as session:
            schedule = session.scalar(
                select(MissionSchedule).where(MissionSchedule.mission_id == mission_id)
            )
            schedule.next_run_at = due_at
            for store_id in (pichau_id, kabum_id):
                source = session.get(MissionSource, (mission_id, store_id))
                source.next_run_at = due_at

    def _expire_backoff(store_id, due_at):
        with integration_database.sessions.begin() as session:
            source = session.get(MissionSource, (mission_id, store_id))
            source.next_eligible_at = due_at - timedelta(seconds=1)

    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((_SuccessfulProvider(), _FailingProvider())),
        ai_manager=_AlwaysMatchAIManager(),
        user_cooldown_min_seconds=_NEUTRAL_USER_COOLDOWN_SECONDS,
        user_cooldown_max_seconds=_NEUTRAL_USER_COOLDOWN_SECONDS,
        store_min_interval_seconds=_NEUTRAL_STORE_INTERVAL_SECONDS,
    )

    # Ciclo 1: pichau sucede, kabum leva 403 confirmado -> 1o bloqueio.
    before = datetime.now(UTC)
    result = asyncio.run(orchestrator.run_batch(now=now0))
    after = datetime.now(UTC)
    assert (result.claimed, result.succeeded, result.failed) == (2, 1, 1)
    assert _source_state(pichau_id) == (0, None)
    kabum_blocks, kabum_eligible = _source_state(kabum_id)
    assert kabum_blocks == 1
    assert (
        before + timedelta(minutes=60)
        <= kabum_eligible
        <= after + timedelta(minutes=60)
    )  # 30 * 2**1

    # Ciclo 2: agenda due de novo, kabum ainda em backoff real (só
    # elegível daqui a ~60 min) -> só pichau é reivindicada; kabum intocado.
    # `due_at` único evita corrida entre a escrita da agenda e o `now`
    # passado ao run_batch (ambos precisam concordar exatamente).
    due_at = datetime.now(UTC).replace(microsecond=0)
    _make_due_again(due_at)
    result = asyncio.run(orchestrator.run_batch(now=due_at))
    assert (result.claimed, result.succeeded, result.failed) == (1, 1, 0)
    assert _run_count_for(kabum_id) == 1  # não ganhou run novo neste ciclo
    assert _run_count_for(pichau_id) == 2
    assert _source_state(kabum_id) == (kabum_blocks, kabum_eligible)  # intocado

    # Ciclo 3: backoff do kabum expira (simulado) e a agenda fica due de
    # novo -> kabum volta a ser reivindicada, falha de novo (2o bloqueio).
    due_at = datetime.now(UTC).replace(microsecond=0)
    _expire_backoff(kabum_id, due_at)
    _make_due_again(due_at)
    before = datetime.now(UTC)
    result = asyncio.run(orchestrator.run_batch(now=due_at))
    after = datetime.now(UTC)
    assert (result.claimed, result.succeeded, result.failed) == (2, 1, 1)
    assert _run_count_for(kabum_id) == 2
    kabum_blocks, kabum_eligible = _source_state(kabum_id)
    assert kabum_blocks == 2
    assert (
        before + timedelta(minutes=120)
        <= kabum_eligible
        <= after + timedelta(minutes=120)
    )  # 30 * 2**2
    assert _source_state(pichau_id) == (0, None)  # sucesso continua resetado


def test_all_sources_in_backoff_creates_no_run_and_schedule_stays_due(
    integration_database,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, pichau_id, kabum_id = _seed_due_mission(
        integration_database.sessions, now
    )
    future = now + timedelta(minutes=90)
    with integration_database.sessions.begin() as session:
        for store_id in (pichau_id, kabum_id):
            source = session.get(MissionSource, (mission_id, store_id))
            source.next_eligible_at = future
            source.consecutive_blocks = 3

    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((_SuccessfulProvider(), _FailingProvider())),
        ai_manager=_AlwaysMatchAIManager(),
    )

    result = asyncio.run(orchestrator.run_batch(now=now))

    assert (result.claimed, result.succeeded, result.failed) == (0, 0, 0)
    with integration_database.sessions.begin() as session:
        assert (
            session.scalar(
                select(func.count(CollectionRun.id)).where(
                    CollectionRun.mission_id == mission_id
                )
            )
            == 0
        )
        # a missao continua due (nao avancou): sera reexaminada no proximo
        # poll, sem esperar um intervalo inteiro quando uma fonte liberar.
        next_run_at = session.scalar(
            select(MissionSchedule.next_run_at).where(
                MissionSchedule.mission_id == mission_id
            )
        )
        assert next_run_at == now


class _KabumOfferProvider:
    """Sempre `kabum`; preço configurável por instância (TASK-068)."""

    source_code = "kabum"

    def __init__(self, raw_price: str) -> None:
        self._raw_price = raw_price

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        completed = request.requested_at.replace(microsecond=500000)
        return CollectionResult(
            self.source_code,
            request.requested_at,
            completed,
            (
                RawCollectedOffer(
                    source_code=self.source_code,
                    url="https://example.invalid/task068-kabum-offer",
                    title="TASK-068 synthetic GPU (kabum)",
                    collected_at=completed,
                    external_id="task068-kabum-offer",
                    raw_price=self._raw_price,
                    raw_currency="BRL",
                    raw_shipping="Frete grátis",
                    raw_availability="Em estoque",
                    evidence={"card_text": "safe synthetic evidence"},
                ),
            ),
        )


def test_prelist_ready_fires_once_then_errata_corrects_a_cheaper_late_offer(
    integration_database,
) -> None:
    """TASK-068: pré-lista dispara uma vez após a 1a rodada completa; uma
    coleta posterior mais barata que a base já enviada gera uma única
    correção -- nunca duas, nunca antes da rodada completar.
    """
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, pichau_id, kabum_id = _seed_due_mission(
        integration_database.sessions, now
    )

    # Rodada 1: pichau sucede (R$ 1.900,00), kabum leva bloqueio confirmado
    # (403) -- a rodada conta como completa mesmo assim (falha é terminal).
    orchestrator_round1 = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((_SuccessfulProvider(), _FailingProvider())),
        ai_manager=_AlwaysMatchAIManager(),
        user_cooldown_min_seconds=_NEUTRAL_USER_COOLDOWN_SECONDS,
        user_cooldown_max_seconds=_NEUTRAL_USER_COOLDOWN_SECONDS,
        store_min_interval_seconds=_NEUTRAL_STORE_INTERVAL_SECONDS,
    )
    result1 = asyncio.run(orchestrator_round1.run_batch(now=now))
    assert (result1.claimed, result1.succeeded, result1.failed) == (2, 1, 1)

    with integration_database.sessions.begin() as session:
        mission = session.get(Mission, mission_id)
        assert mission.prelist_sent is True
        assert mission.prelist_errata_sent is False
        assert mission.prelist_lowest_amount == Decimal("1900.0000")
        assert mission.prelist_lowest_currency == "BRL"
        ready_event = session.scalar(
            select(Event).where(
                Event.mission_id == mission_id,
                Event.event_type == EventType.MISSION_PRELIST_READY_V2.value,
            )
        )
        assert ready_event is not None
        assert len(ready_event.payload["offers"]) == 1
        assert Decimal(ready_event.payload["offers"][0]["amount"]) == Decimal("1900.00")
        # kabum falhou -- nenhuma segunda loja para mostrar ainda.
        errata_before = session.scalar(
            select(func.count(Event.id)).where(
                Event.mission_id == mission_id,
                Event.event_type == EventType.MISSION_PRELIST_ERRATA_V2.value,
            )
        )
        assert errata_before == 0

    # Libera kabum do backoff e deixa AMBAS as sources due de novo,
    # simulando a rodada seguinte -- desta vez kabum responde mais barato
    # (R$ 1.500,00) que a base já enviada (R$ 1.900,00): deve gerar a
    # única correção. TASK-112 fase 3B (correção estrutural): cada
    # `MissionSource` tem sua própria cadência -- forçar só o agregado
    # derivado (`MissionSchedule`) não bastaria mais.
    due_at = datetime.now(UTC).replace(microsecond=0)
    with integration_database.sessions.begin() as session:
        source = session.get(MissionSource, (mission_id, kabum_id))
        source.next_eligible_at = due_at - timedelta(seconds=1)
        source.next_run_at = due_at - timedelta(seconds=1)
        pichau_source = session.get(MissionSource, (mission_id, pichau_id))
        pichau_source.next_run_at = due_at - timedelta(seconds=1)
        schedule = session.scalar(
            select(MissionSchedule).where(MissionSchedule.mission_id == mission_id)
        )
        schedule.next_run_at = due_at

    orchestrator_round2 = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((_SuccessfulProvider(), _KabumOfferProvider("R$ 1.500,00"))),
        ai_manager=_AlwaysMatchAIManager(),
        user_cooldown_min_seconds=_NEUTRAL_USER_COOLDOWN_SECONDS,
        user_cooldown_max_seconds=_NEUTRAL_USER_COOLDOWN_SECONDS,
        store_min_interval_seconds=_NEUTRAL_STORE_INTERVAL_SECONDS,
    )
    result2 = asyncio.run(orchestrator_round2.run_batch(now=due_at))
    assert (result2.claimed, result2.succeeded, result2.failed) == (2, 2, 0)

    with integration_database.sessions.begin() as session:
        mission = session.get(Mission, mission_id)
        assert mission.prelist_sent is True
        assert mission.prelist_errata_sent is True
        # a base de comparação (prelist_lowest_amount) não é reescrita pela
        # correção -- continua registrando o valor original enviado.
        assert mission.prelist_lowest_amount == Decimal("1900.0000")
        errata_events = list(
            session.scalars(
                select(Event).where(
                    Event.mission_id == mission_id,
                    Event.event_type == EventType.MISSION_PRELIST_ERRATA_V2.value,
                )
            )
        )
        assert len(errata_events) == 1
        assert len(errata_events[0].payload["offers"]) == 1
        assert Decimal(errata_events[0].payload["offers"][0]["amount"]) == Decimal(
            "1500.00"
        )
        assert errata_events[0].aggregate_id == mission_id

    # Rodada 3: kabum encontra um preço ainda mais barato (R$ 1.000,00) --
    # a correção já foi usada; nenhuma segunda correção deve ser publicada.
    due_at_3 = datetime.now(UTC).replace(microsecond=0)
    with integration_database.sessions.begin() as session:
        source = session.get(MissionSource, (mission_id, kabum_id))
        source.next_eligible_at = due_at_3 - timedelta(seconds=1)
        source.next_run_at = due_at_3 - timedelta(seconds=1)
        pichau_source = session.get(MissionSource, (mission_id, pichau_id))
        pichau_source.next_run_at = due_at_3 - timedelta(seconds=1)
        schedule = session.scalar(
            select(MissionSchedule).where(MissionSchedule.mission_id == mission_id)
        )
        schedule.next_run_at = due_at_3

    orchestrator_round3 = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((_SuccessfulProvider(), _KabumOfferProvider("R$ 1.000,00"))),
        ai_manager=_AlwaysMatchAIManager(),
        user_cooldown_min_seconds=_NEUTRAL_USER_COOLDOWN_SECONDS,
        user_cooldown_max_seconds=_NEUTRAL_USER_COOLDOWN_SECONDS,
        store_min_interval_seconds=_NEUTRAL_STORE_INTERVAL_SECONDS,
    )
    result3 = asyncio.run(orchestrator_round3.run_batch(now=due_at_3))
    assert (result3.claimed, result3.succeeded, result3.failed) == (2, 2, 0)

    with integration_database.sessions.begin() as session:
        errata_count = session.scalar(
            select(func.count(Event.id)).where(
                Event.mission_id == mission_id,
                Event.event_type == EventType.MISSION_PRELIST_ERRATA_V2.value,
            )
        )
        assert errata_count == 1  # continua uma única correção, nunca duas


class _PricedProvider:
    """Loja/preço/frete configuráveis -- usado para provar que a pré-lista
    ranqueia por `PriceObservation.amount` (preço do produto), nunca por
    `total_amount` (preço+frete), já que o frete não é confiável/
    comparável entre lojas nesta TASK."""

    def __init__(self, source_code: str, raw_price: str, raw_shipping: str) -> None:
        self.source_code = source_code
        self._raw_price = raw_price
        self._raw_shipping = raw_shipping

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        completed = request.requested_at.replace(microsecond=500000)
        return CollectionResult(
            self.source_code,
            request.requested_at,
            completed,
            (
                RawCollectedOffer(
                    source_code=self.source_code,
                    url=f"https://example.invalid/task068-{self.source_code}-shipping",
                    title=f"TASK-068 synthetic GPU ({self.source_code})",
                    collected_at=completed,
                    external_id=f"task068-{self.source_code}-shipping-offer",
                    raw_price=self._raw_price,
                    raw_currency="BRL",
                    raw_shipping=self._raw_shipping,
                    raw_availability="Em estoque",
                    evidence={"card_text": "safe synthetic evidence"},
                ),
            ),
        )


def test_prelist_ranks_by_product_amount_ignoring_shipping(
    integration_database,
) -> None:
    """TASK-068 (correção pós-revisão do usuário): a base de comparação é
    sempre `amount` (preço anunciado do produto) -- nunca `total_amount`
    (preço+frete). Cenário desenhado para que as duas bases discordem: se
    o código usasse `total_amount` por engano, a loja com frete pago
    venceria; usando `amount`, ela perde porque o preço do produto sozinho
    é mais barato.
    """
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, pichau_id, _kabum_id = _seed_due_mission(
        integration_database.sessions, now
    )
    # pichau: amount=1000,00, frete=500,00 -> total=1500,00 (mais barato em
    # amount, mais caro em total).
    # kabum: amount=1100,00, frete grátis -> total=1100,00 (mais barato em
    # total, mais caro em amount).
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter(
            (
                _PricedProvider("pichau", "R$ 1.000,00", "R$ 500,00"),
                _PricedProvider("kabum", "R$ 1.100,00", "Frete grátis"),
            )
        ),
        ai_manager=_AlwaysMatchAIManager(),
    )

    result = asyncio.run(orchestrator.run_batch(now=now))
    assert (result.claimed, result.succeeded, result.failed) == (2, 2, 0)

    with integration_database.sessions.begin() as session:
        mission = session.get(Mission, mission_id)
        # amount (sem frete) é a base -- pichau (1000) vence kabum (1100),
        # mesmo pichau custando mais no total (1500 > 1100).
        assert mission.prelist_lowest_amount == Decimal("1000.0000")
        ready_event = session.scalar(
            select(Event).where(
                Event.mission_id == mission_id,
                Event.event_type == EventType.MISSION_PRELIST_READY_V2.value,
            )
        )
        assert ready_event is not None
        offers_by_store = {
            item["store_id"]: item for item in ready_event.payload["offers"]
        }
        assert {Decimal(item["amount"]) for item in offers_by_store.values()} == {
            Decimal("1000.00"),
            Decimal("1100.00"),
        }
        pichau_offer_id = session.scalar(
            select(Offer.id).where(Offer.store_id == pichau_id)
        )
        assert any(
            item["offer_id"] == str(pichau_offer_id)
            for item in offers_by_store.values()
        )


def test_backfill_runs_old_active_mission_but_ignores_paused(
    integration_database,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    with integration_database.sessions.begin() as session:
        store = session.scalar(select(Store).where(Store.code == "pichau"))
        assert store is not None
        user = User(display_name="TASK-062 backfill", role=UserRole.USER)
        session.add(user)
        session.flush()
        active = Mission(
            user_id=user.id, title="old active mission", status=MissionStatus.ACTIVE
        )
        paused = Mission(
            user_id=user.id, title="paused mission", status=MissionStatus.PAUSED
        )
        session.add_all((active, paused))
        session.flush()
        session.add_all(
            (
                MissionCriteria(mission_id=active.id, search_query="GPU"),
                MissionSource(mission_id=active.id, store_id=store.id),
                MissionCriteria(mission_id=paused.id, search_query="GPU"),
                MissionSource(mission_id=paused.id, store_id=store.id),
                MissionSchedule(
                    mission_id=paused.id,
                    interval_minutes=60,
                    next_run_at=now,
                    is_enabled=True,
                ),
            )
        )
        active_id, paused_id = active.id, paused.id

    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((_EmptyProvider(),)),
        ai_manager=_AlwaysMatchAIManager(),
    )
    result = asyncio.run(orchestrator.run_batch(now=now))

    assert result.claimed == 1
    assert result.succeeded == 1
    with integration_database.sessions.begin() as session:
        assert (
            session.scalar(
                select(func.count(CollectionRun.id)).where(
                    CollectionRun.mission_id == active_id
                )
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count(CollectionRun.id)).where(
                    CollectionRun.mission_id == paused_id
                )
            )
            == 0
        )
        completed = session.scalar(
            select(Event).where(
                Event.mission_id == active_id,
                Event.event_type == EventType.COLLECTION_COMPLETED_V1.value,
            )
        )
        assert completed is not None
        assert completed.payload["observation_count"] == 0


# ---------------------------------------------------------------------------
# TASK-079: prova de que o autodeadlock (duas claims da mesma missão, IA
# lenta, chamada síncrona bloqueante do event loop) está resolvido. Cada
# teste abaixo usa PostgreSQL real -- não mocks -- porque a garantia que
# importa aqui (locks/isolamento/transações) só é verificável de verdade
# contra o banco.
# ---------------------------------------------------------------------------


class _SlowAIManager:
    """IA artificialmente lenta e controlada -- nunca chama provedor real.

    Usada para reproduzir a janela exata em que o bug original travava: a
    Fase B (`await` de IA) precisa durar tempo suficiente para provar que
    o event loop continua respondendo enquanto ela está em andamento.
    """

    def __init__(self, delay_seconds: float = 0.25) -> None:
        self._delay_seconds = delay_seconds
        self.calls = 0
        self.started = threading.Event()

    async def generate(self, request):
        self.calls += 1
        self.started.set()
        await asyncio.sleep(self._delay_seconds)
        content = (
            '{"relevance": "match"}'
            if request.purpose == "classify_offer_relevance"
            else '{"display_title": "Synthetic GPU"}'
        )
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub",
            content=content,
            finished_at=datetime.now(UTC),
        )


_SUSTAINED_IDLE_THRESHOLD_SECONDS = 0.1


def _poll_idle_in_transaction(
    engine, stop_event: threading.Event, sustained: list
) -> None:
    """Amostra `pg_stat_activity` a cada 20ms até `stop_event` -- roda numa
    thread separada, concorrente com o `run_batch()` sob teste.

    Mede a duração real de cada `idle in transaction` via
    `now() - state_change`, não a contagem de amostras -- uma transação
    curta e local (sem `await` externo) pode legitimamente aparecer idle
    por alguns ms de jitter real de I/O do Postgres em container; isso não
    é o bug. O padrão do bug original era a MESMA conexão parada em `idle
    in transaction` por centenas de ms (o delay de IA controlada usada
    neste teste), porque estava presa esperando uma chamada externa. Só
    entra em `sustained` uma linha cuja duração já ultrapassa
    `_SUSTAINED_IDLE_THRESHOLD_SECONDS` -- bem abaixo do delay de IA
    simulado, bem acima do jitter normal de uma transação só local."""
    with engine.connect() as connection:
        while not stop_event.is_set():
            rows = connection.execute(
                text(
                    "SELECT pid, query, "
                    "extract(epoch FROM (now() - state_change)) AS idle_seconds "
                    "FROM pg_stat_activity "
                    "WHERE state = 'idle in transaction' AND pid <> pg_backend_pid()"
                )
            ).all()
            connection.rollback()
            for row in rows:
                if row.idle_seconds >= _SUSTAINED_IDLE_THRESHOLD_SECONDS:
                    sustained.append((row.pid, row.idle_seconds, row.query))
            time.sleep(0.02)


def test_two_claims_of_same_mission_do_not_deadlock_and_event_loop_stays_responsive(
    integration_database,
) -> None:
    """TASK-079: reproduz a condição exata do autodeadlock comprovado em
    produção -- duas claims da mesma missão (`AISHOPPING_COLLECTION_MAX_
    CONCURRENCY=2` real, via `max_concurrency=2`), IA controladamente
    lenta numa Fase B que dura tempo suficiente para expor um event loop
    congelado, se ele existisse.

    Prova exigida: (1) o event loop nunca trava -- um heartbeat batendo a
    cada 10ms continua batendo durante toda a IA lenta; (2) nenhuma
    conexão do orquestrador fica `idle in transaction` durante a espera de
    IA (amostrado ao vivo via `pg_stat_activity`); (3) as duas claims
    terminam; (4) nenhum `collection_run` fica `running` para sempre.
    """
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, pichau_id, kabum_id = _seed_due_mission(
        integration_database.sessions, now
    )
    ai_manager = _SlowAIManager(delay_seconds=0.25)
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((_SuccessfulProvider(), _KabumOfferProvider("R$ 1.000,00"))),
        ai_manager=ai_manager,
        max_concurrency=2,
    )

    heartbeat_ticks = 0

    async def _heartbeat() -> None:
        nonlocal heartbeat_ticks
        while True:
            await asyncio.sleep(0.01)
            heartbeat_ticks += 1

    stop_event = threading.Event()
    sustained_idle: list = []
    poller = threading.Thread(
        target=_poll_idle_in_transaction,
        args=(integration_database.engine, stop_event, sustained_idle),
        daemon=True,
    )
    poller.start()

    async def _run():
        heartbeat = asyncio.create_task(_heartbeat())
        started = asyncio.get_event_loop().time()
        try:
            return await orchestrator.run_batch(now=now), (
                asyncio.get_event_loop().time() - started
            )
        finally:
            heartbeat.cancel()

    try:
        result, elapsed = asyncio.run(_run())
    finally:
        stop_event.set()
        poller.join(timeout=5)

    assert result.claimed == 2
    assert result.succeeded == 2
    assert ai_manager.calls > 0
    # Se o event loop tivesse travado durante a IA lenta (o bug original,
    # comprovado por py-spy em produção: uma chamada síncrona do SQLAlchemy
    # bloqueando a thread inteira), o heartbeat pararia de bater. Limite
    # bem abaixo do esperado (~100 ticks/s) para tolerar jitter do CI.
    assert heartbeat_ticks >= elapsed / 0.05
    # Nenhuma conexão do orquestrador (nem a que segura o lock da missão,
    # nem a outra claim aguardando IA) fica presa em `idle in transaction`
    # por mais de uma amostra seguida -- exatamente o padrão observado em
    # produção antes da correção (`idle in transaction` por dezenas de
    # segundos enquanto aguardava `normalize_offer_title`). Uma única
    # amostra isolada é o gap normal entre duas instruções locais rápidas
    # dentro da mesma transação curta (sem `await` externo) e não conta.
    assert sustained_idle == []

    with integration_database.sessions.begin() as session:
        runs = list(
            session.scalars(
                select(CollectionRun).where(CollectionRun.mission_id == mission_id)
            )
        )
        assert len(runs) == 2
        assert {run.status for run in runs} == {CollectionRunStatus.SUCCEEDED}


def _seed_due_mission_all_stores(sessions, now: datetime) -> tuple:
    with sessions.begin() as session:
        stores = {
            store.code: store
            for store in session.scalars(
                select(Store).where(
                    Store.code.in_(("pichau", "kabum", "amazon", "terabyte"))
                )
            )
        }
        user = User(display_name="TASK-079 four stores", role=UserRole.USER)
        session.add(user)
        session.flush()
        mission = Mission(
            user_id=user.id,
            title="TASK-079 four stores",
            status=MissionStatus.ACTIVE,
        )
        session.add(mission)
        session.flush()
        session.add_all(
            (
                MissionCriteria(mission_id=mission.id, search_query="synthetic GPU"),
                MissionSchedule(
                    mission_id=mission.id,
                    interval_minutes=60,
                    next_run_at=now,
                    is_enabled=True,
                ),
                *(
                    MissionSource(mission_id=mission.id, store_id=store.id)
                    for store in stores.values()
                ),
            )
        )
        return mission.id, {code: store.id for code, store in stores.items()}


class _StoreOfferProvider:
    """Provider genérico com `source_code` configurável -- usado para os
    quatro stores V1 na mesma missão (TASK-079, item 10)."""

    def __init__(self, source_code: str) -> None:
        self.source_code = source_code

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        completed = request.requested_at.replace(microsecond=500000)
        return CollectionResult(
            self.source_code,
            request.requested_at,
            completed,
            (
                RawCollectedOffer(
                    source_code=self.source_code,
                    url=f"https://example.invalid/{self.source_code}-task079",
                    title="TASK-079 synthetic GPU",
                    collected_at=completed,
                    external_id=f"task079-{self.source_code}-offer",
                    raw_price="R$ 1.800,00",
                    raw_currency="BRL",
                    raw_shipping="Frete grátis",
                    raw_availability="Em estoque",
                    evidence={"card_text": "safe synthetic evidence"},
                ),
            ),
        )


def test_four_stores_same_mission_collect_concurrently_and_critical_section_is_serialized(
    integration_database,
) -> None:
    """TASK-079, itens 3 e 10: as quatro lojas continuam coletando em
    paralelo (não serializadas entre si); só a seção crítica por
    `mission_id` (Fase C) é serializada. Prova: as quatro claims terminam
    com sucesso, nenhuma transação abandonada, nenhum lock indefinido,
    nenhuma duplicidade, pré-lista correta, worker permanece vivo."""
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, store_ids = _seed_due_mission_all_stores(
        integration_database.sessions, now
    )
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter(
            tuple(
                _StoreOfferProvider(code)
                for code in ("pichau", "kabum", "amazon", "terabyte")
            )
        ),
        ai_manager=_SlowAIManager(delay_seconds=0.05),
        max_concurrency=4,
    )

    result = asyncio.run(orchestrator.run_batch(now=now))

    assert result.claimed == 4
    assert result.succeeded == 4
    with integration_database.sessions.begin() as session:
        runs = list(
            session.scalars(
                select(CollectionRun).where(CollectionRun.mission_id == mission_id)
            )
        )
        assert len(runs) == 4
        assert {run.status for run in runs} == {CollectionRunStatus.SUCCEEDED}
        # nenhuma duplicidade: uma PriceObservation por loja (external_id
        # distinto por store, sem sobreposição entre elas).
        assert session.scalar(select(func.count(PriceObservation.id))) == 4
        mission = session.get(Mission, mission_id)
        # rodada unica e completa: pre-lista dispara com as quatro fontes.
        assert mission.prelist_sent is True
        idle = session.execute(
            text(
                "SELECT pid FROM pg_stat_activity "
                "WHERE state = 'idle in transaction' AND pid <> pg_backend_pid()"
            )
        ).all()
        assert idle == []

    # worker permanece vivo e apto a buscar o proximo lote.
    assert asyncio.run(orchestrator.run_batch(now=now)).claimed == 0


def test_two_worker_processes_do_not_corrupt_or_duplicate_mission_state(
    integration_database,
) -> None:
    """TASK-079, item 11: a serialização por `mission_id` usa `SELECT ...
    FOR UPDATE` no PostgreSQL, não `asyncio.Lock` -- por isso continua
    correta mesmo com dois processos de worker totalmente independentes
    (aqui: dois `CollectionOrchestrator`, cada um com seu próprio engine
    assíncrono/conexão, rodando em threads e event loops separados, como
    dois processos reais fariam)."""
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, pichau_id, kabum_id = _seed_due_mission(
        integration_database.sessions, now
    )
    barrier = Barrier(2)

    def run_worker_instance(provider) -> None:
        async def _run():
            engine = create_collection_async_database_engine(
                integration_database.settings
            )
            sessions = create_async_session_factory(engine)
            orchestrator = CollectionOrchestrator(
                sessions,
                CollectionAdapter((provider,)),
                ai_manager=_SlowAIManager(delay_seconds=0.05),
                max_concurrency=2,
            )
            try:
                barrier.wait(timeout=10)
                return await orchestrator.run_batch(now=now)
            finally:
                await engine.dispose()

        return asyncio.run(_run())

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                run_worker_instance,
                (_SuccessfulProvider(), _KabumOfferProvider("R$ 1.200,00")),
            )
        )

    # cada "processo" só tem o provider da sua própria loja registrado --
    # a claim da outra loja (que também aparece no lote, já que ambos leem
    # a mesma agenda) falha só por não ter provider, sem corromper nada;
    # o que importa é que a loja de CADA "processo" termina com sucesso e
    # nenhum estado fica corrompido/duplicado.
    total_claimed = sum(result.claimed for result in results)
    assert total_claimed >= 2  # nenhuma fonte perdida entre os dois "processos"

    with integration_database.sessions.begin() as session:
        runs = list(
            session.scalars(
                select(CollectionRun).where(CollectionRun.mission_id == mission_id)
            )
        )
        # nenhuma duplicidade: no maximo um run por (mission, store), mesmo
        # com dois "processos" lendo a mesma agenda simultaneamente.
        assert len({(run.mission_id, run.store_id) for run in runs}) == len(runs)
        succeeded_stores = {
            run.store_id for run in runs if run.status is CollectionRunStatus.SUCCEEDED
        }
        assert {pichau_id, kabum_id} <= succeeded_stores | {
            run.store_id for run in runs if run.status is CollectionRunStatus.FAILED
        }
        assert session.scalar(select(func.count(PriceObservation.id))) == len(
            succeeded_stores
        )


def test_api_stays_responsive_during_concurrent_collection_claim(
    integration_database,
) -> None:
    """TASK-079, item 12: reproduz o achado adicional da investigação --
    uma conexão da API travou presa no lock de missão que o worker
    segurava durante a IA. Prova: com a correção, uma operação real da API
    (`transition_mission`, mesmo `SELECT ... FOR UPDATE` usado em
    produção) sobre a MESMA missão, concorrente com uma claim que está no
    meio de uma IA lenta, completa em tempo curto -- não trava
    indefinidamente."""
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, pichau_id, kabum_id = _seed_due_mission(
        integration_database.sessions, now
    )
    ai_manager = _SlowAIManager(delay_seconds=0.4)
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((_SuccessfulProvider(), _KabumOfferProvider("R$ 1.100,00"))),
        ai_manager=ai_manager,
        max_concurrency=2,
    )
    api_elapsed: list[float] = []
    api_started = threading.Event()

    def api_operation() -> None:
        # da perspectiva da API: Session síncrona normal, como qualquer
        # endpoint real usa hoje.
        with integration_database.sessions.begin() as session:
            api_started.set()
            started = time.monotonic()
            transition_mission(
                session,
                mission_id=mission_id,
                command=MissionCommand.PAUSE,
                expected_state_version=0,
                actor_type="user",
            )
            api_elapsed.append(time.monotonic() - started)

    async def _run():
        batch_task = asyncio.ensure_future(orchestrator.run_batch(now=now))
        # Sincroniza pela evidência real de que a claim chegou à IA lenta.
        # Um sleep fixo permitia a API pausar a missão antes do claim em
        # hosts mais lentos, medindo a corrida de setup em vez do lock.
        assert await asyncio.to_thread(ai_manager.started.wait, 5)
        api_thread = threading.Thread(target=api_operation, daemon=True)
        api_thread.start()
        result = await batch_task
        api_thread.join(timeout=5)
        return result

    result = asyncio.run(_run())

    assert result.claimed == 2
    assert api_started.is_set()
    assert len(api_elapsed) == 1
    # espera curta e legitima por lock pode existir (até a Fase C de uma
    # claim liberar), mas nunca bloqueio indefinido -- bem abaixo do delay
    # de IA total possível (2 claims x ate 2 chamadas x 0.4s = 1.6s).
    assert api_elapsed[0] < 3.0


# ---------------------------------------------------------------------------
# TASK-093 (redução de PriceObservation redundante)
# ---------------------------------------------------------------------------


class _ControllableOfferProvider:
    """Mesma `Offer` (mesmo `external_id`) em toda rodada -- preço e
    disponibilidade ajustáveis entre `run_batch`, para provar redundância
    real contra PostgreSQL."""

    source_code = "pichau"

    def __init__(self, raw_price: str, raw_availability: str = "Em estoque") -> None:
        self.raw_price = raw_price
        self.raw_availability = raw_availability

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        completed = request.requested_at.replace(microsecond=500000)
        return CollectionResult(
            self.source_code,
            request.requested_at,
            completed,
            (
                RawCollectedOffer(
                    source_code=self.source_code,
                    url="https://example.invalid/task093-offer",
                    title="TASK-093 synthetic GPU",
                    collected_at=completed,
                    external_id="task093-stable-offer",
                    raw_price=self.raw_price,
                    raw_currency="BRL",
                    raw_shipping="Frete grátis",
                    raw_availability=self.raw_availability,
                    evidence={"card_text": "safe synthetic evidence"},
                ),
            ),
        )


def _rearm_schedule(sessions, mission_id, pichau_id, due_at: datetime) -> None:
    """TASK-112 fase 3B (correção estrutural): `MissionSource.next_run_at`
    -- não mais `MissionSchedule.next_run_at` -- é quem decide se a store
    está due; forçar só o agregado derivado não adiantaria nada."""
    with sessions.begin() as session:
        source = session.get(MissionSource, (mission_id, pichau_id))
        source.next_eligible_at = due_at - timedelta(seconds=1)
        source.next_run_at = due_at - timedelta(seconds=1)
        schedule = session.scalar(
            select(MissionSchedule).where(MissionSchedule.mission_id == mission_id)
        )
        schedule.next_run_at = due_at


def test_identical_commercial_state_does_not_create_redundant_observation(
    integration_database,
) -> None:
    """Mesma oferta, mesmo preço/disponibilidade em duas coletas
    seguidas -- a segunda não grava `PriceObservation` nova; o histórico
    da primeira permanece intocado; `Offer.last_seen_at` avança."""
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, pichau_id, _kabum_id = _seed_due_mission(
        integration_database.sessions, now
    )
    provider = _ControllableOfferProvider("R$ 1.900,00")
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((provider, _FailingProvider())),
        ai_manager=_AlwaysMatchAIManager(),
    )
    asyncio.run(orchestrator.run_batch(now=now))

    with integration_database.sessions.begin() as session:
        original = session.scalar(select(PriceObservation))
        assert original is not None
        original_id = original.id
        original_amount = original.amount
        original_observed_at = original.observed_at
        offer = session.get(Offer, original.offer_id)
        first_seen = offer.last_seen_at

    due_at = now + timedelta(hours=1)
    _rearm_schedule(integration_database.sessions, mission_id, pichau_id, due_at)
    asyncio.run(orchestrator.run_batch(now=due_at))

    with integration_database.sessions.begin() as session:
        observations = list(session.scalars(select(PriceObservation)))
        assert len(observations) == 1  # nenhuma nova gravada
        kept = observations[0]
        # histórico da primeira observação intocado
        assert kept.id == original_id
        assert kept.amount == original_amount
        assert kept.observed_at == original_observed_at
        offer = session.get(Offer, kept.offer_id)
        assert offer.last_seen_at == due_at.replace(microsecond=500000)
        assert offer.last_seen_at > first_seen


def test_legacy_path_confirms_price_change_and_back_same_day(
    integration_database,
) -> None:
    """Preço A -> B -> A no mesmo dia comercial: 3 observações distintas
    (a 3a NUNCA reaproveita a 1a -- dedupe só compara contra a ÚLTIMA
    observação, TASK-093) e 3 confirmações, cada uma identificando sem
    ambiguidade qual observação ela confirmou -- nunca uma tabela que só
    saiba dizer "a última", incapaz de reconstruir a mudança
    intradiária. Incrementos pequenos (minutos, não horas) para não
    reativar o backoff real da fonte que falha neste mesmo batch."""
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, pichau_id, _kabum_id = _seed_due_mission(
        integration_database.sessions, now
    )
    provider = _ControllableOfferProvider("R$ 1.900,00")
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((provider, _FailingProvider())),
        ai_manager=_AlwaysMatchAIManager(),
    )
    asyncio.run(orchestrator.run_batch(now=now))

    at_b = now + timedelta(minutes=10)
    _rearm_schedule(integration_database.sessions, mission_id, pichau_id, at_b)
    provider.raw_price = "R$ 1.999,00"
    asyncio.run(orchestrator.run_batch(now=at_b))

    at_a_again = now + timedelta(minutes=20)
    _rearm_schedule(integration_database.sessions, mission_id, pichau_id, at_a_again)
    provider.raw_price = "R$ 1.900,00"
    asyncio.run(orchestrator.run_batch(now=at_a_again))

    at_a_redundant = now + timedelta(minutes=30)
    _rearm_schedule(
        integration_database.sessions, mission_id, pichau_id, at_a_redundant
    )
    # preço repete o da rodada anterior -- redundante, sem observação nova.
    asyncio.run(orchestrator.run_batch(now=at_a_redundant))

    with integration_database.sessions.begin() as session:
        observations = list(
            session.scalars(
                select(PriceObservation).order_by(PriceObservation.observed_at)
            )
        )
        assert [o.amount for o in observations] == [
            Decimal("1900.00"),
            Decimal("1999.00"),
            Decimal("1900.00"),
        ]
        runs = list(
            session.scalars(
                select(CollectionRun)
                .where(CollectionRun.store_id == pichau_id)
                .order_by(CollectionRun.started_at)
            )
        )
        assert len(runs) == 4
        assert all(run.status == CollectionRunStatus.SUCCEEDED for run in runs)
        confirmations = {
            row.collection_run_id: row.observation_id
            for row in session.scalars(select(SharedCollectionOffer))
        }
        # 1 confirmação por run, nunca menos (perdida) nem mais (duplicada).
        assert len(confirmations) == 4
        assert confirmations[runs[0].id] == observations[0].id  # A
        assert confirmations[runs[1].id] == observations[1].id  # B
        assert confirmations[runs[2].id] == observations[2].id  # A de novo
        # a 4a run (redundante) confirma a MESMA observação da 3a --
        # nunca reaproveita a 1a (que também era "A") só porque o valor
        # é igual: dedupe compara contra a ÚLTIMA, não contra qualquer
        # observação histórica com o mesmo preço.
        assert confirmations[runs[3].id] == observations[2].id
        assert confirmations[runs[0].id] != confirmations[runs[2].id]


def _seed_confirmed_offer(integration_database, now: datetime) -> tuple:
    """1 coleta bem-sucedida real (Pichau) -- devolve (offer_id,
    product_id, store_id, confirmed_at) prontos para
    `resolve_offer_freshness`."""
    mission_id, pichau_id, _kabum_id = _seed_due_mission(
        integration_database.sessions, now
    )
    provider = _ControllableOfferProvider("R$ 1.900,00")
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((provider, _FailingProvider())),
        ai_manager=_AlwaysMatchAIManager(),
    )
    asyncio.run(orchestrator.run_batch(now=now))
    with integration_database.sessions.begin() as session:
        observation = session.scalar(select(PriceObservation))
        offer = session.get(Offer, observation.offer_id)
        return offer.id, offer.product_id, pichau_id, mission_id, now


def test_offer_freshness_confirmed_recent_right_after_collection(
    integration_database,
) -> None:
    """Logo após uma coleta bem-sucedida, a confirmação é "recente" --
    caso feliz, base de comparação para os demais."""
    now = datetime.now(UTC).replace(microsecond=0)
    offer_id, product_id, pichau_id, _mission_id, confirmed_at = _seed_confirmed_offer(
        integration_database, now
    )

    async def check():
        async with integration_database.async_sessions() as session:
            return await resolve_offer_freshness(
                session,
                offer_id=offer_id,
                product_id=product_id,
                store_id=pichau_id,
                now=confirmed_at + timedelta(minutes=5),
                config=CadenceConfig(),
            )

    assert asyncio.run(check()) == OfferFreshnessStatus.CONFIRMED_RECENT


def test_offer_freshness_stale_after_grace_multiplier_normal_mode(
    integration_database,
) -> None:
    """NORMAL: máximo real de cadência é 75min (`CadenceConfig()`
    default); tolerância = `STALE_GRACE_MULTIPLIER` (2) * 75 = 150min.
    149min ainda é "recente" (absorve 1 ciclo perdido); 151min já é
    "antiga" -- limiar exato, não um número solto."""
    now = datetime.now(UTC).replace(microsecond=0)
    offer_id, product_id, pichau_id, _mission_id, confirmed_at = _seed_confirmed_offer(
        integration_database, now
    )
    config = CadenceConfig()
    assert STALE_GRACE_MULTIPLIER * config.normal_max_minutes == 150

    async def check(elapsed_minutes: int):
        async with integration_database.async_sessions() as session:
            return await resolve_offer_freshness(
                session,
                offer_id=offer_id,
                product_id=product_id,
                store_id=pichau_id,
                now=confirmed_at + timedelta(minutes=elapsed_minutes),
                config=config,
            )

    assert asyncio.run(check(149)) == OfferFreshnessStatus.CONFIRMED_RECENT
    assert asyncio.run(check(151)) == OfferFreshnessStatus.CONFIRMED_STALE


def test_offer_freshness_fallback_uses_last_seen_at_when_no_run_confirmation(
    integration_database,
) -> None:
    """Caso explícito pedido pelo dono do produto: `PriceObservation`
    ANTIGA (o preço nunca mudou desde então, TASK-093 nunca gravou linha
    nova), `Offer.last_seen_at` RECENTE (dedupe reconfirmou o MESMO
    estado depois, sem observação nova) e NENHUMA confirmação por run
    disponível (`shared_collection_offers` vazia -- simula dado legado
    de antes desta correção existir). `last_seen_at` sozinho já prova
    reconfirmação recente daquele estado -- não pode classificar como
    "antiga" só pela data da PriceObservation."""
    now = datetime.now(UTC).replace(microsecond=0)
    offer_id, product_id, pichau_id, _mission_id, confirmed_at = _seed_confirmed_offer(
        integration_database, now
    )
    old_observed_at = confirmed_at
    # `last_seen_at` avançou bem depois da última observação (dedupe
    # reconfirmou o mesmo preço várias vezes sem gravar linha nova).
    recent_last_seen_at = confirmed_at + timedelta(minutes=140)
    with integration_database.sessions.begin() as session:
        # Remove a confirmação por run real (simula dado legado sem ela)
        # -- o único jeito de provar frescor aqui passa a ser
        # `last_seen_at`.
        session.execute(
            delete(SharedCollectionOffer).where(
                SharedCollectionOffer.offer_id == offer_id
            )
        )
        offer = session.get(Offer, offer_id)
        offer.last_seen_at = recent_last_seen_at

    # `now` fica a 285min da OBSERVAÇÃO (já seria "antiga" sozinha, muito
    # além dos 150min de tolerância NORMAL) mas só 145min de
    # `last_seen_at` (dentro da tolerância) -- só `last_seen_at` consegue
    # justificar "recente" aqui.
    read_at = recent_last_seen_at + timedelta(minutes=145)
    assert (read_at - old_observed_at) > timedelta(minutes=150)
    assert (read_at - recent_last_seen_at) < timedelta(minutes=150)

    async def check():
        async with integration_database.async_sessions() as session:
            return await resolve_offer_freshness(
                session,
                offer_id=offer_id,
                product_id=product_id,
                store_id=pichau_id,
                now=read_at,
                config=CadenceConfig(),
            )

    assert asyncio.run(check()) == OfferFreshnessStatus.CONFIRMED_RECENT


def test_offer_freshness_reacts_to_current_cadence_mode_not_frozen_one(
    integration_database,
) -> None:
    """Pedido explícito do dono do produto, em duas rodadas de correção:

    1a rodada: mudança de cadência nunca reclassifica SILENCIOSAMENTE --
    o comportamento é DEFINIDO e testado (não fica implícito/indefinido).

    2a rodada (correção sobre a 1a): "definido" não significa "aplicar o
    limiar mais curto retroativamente à confirmação inteira". O simples
    INÍCIO de uma promoção não pode tornar antiga uma oferta que já era
    recente -- o worker ainda não teve OPORTUNIDADE de recoletar sob a
    cadência nova. Dois cenários com a MESMA confirmação (100min atrás):

    (a) `PromotionalWindow` que começa AGORA (na leitura): ainda
        "recente" -- o relógio do limiar de 90min só começa a contar do
        início da janela, não da confirmação original (100min > 90min
        seria "antiga" pelo cálculo ingênuo, mas o worker acabou de
        ganhar a chance de recoletar).
    (b) A MESMA janela, agora efetivamente ativa há 95min (bem além do
        seu próprio limiar de 90min): a confirmação de 100min atrás
        (anterior ao início da janela) passa a ser "antiga" -- o worker
        JÁ teve tempo de sobra pra recoletar sob a cadência acelerada e
        não o fez."""
    now = datetime.now(UTC).replace(microsecond=0)
    offer_id, product_id, pichau_id, _mission_id, confirmed_at = _seed_confirmed_offer(
        integration_database, now
    )
    read_at = confirmed_at + timedelta(minutes=100)
    config = CadenceConfig()

    async def check(window_starts_at, window_ends_at):
        with integration_database.sessions.begin() as session:
            session.execute(delete(PromotionalWindow))
            session.add(
                PromotionalWindow(
                    starts_at=window_starts_at,
                    ends_at=window_ends_at,
                    label="teste de transição de cadência",
                )
            )
        async with integration_database.async_sessions() as session:
            return await resolve_offer_freshness(
                session,
                offer_id=offer_id,
                product_id=product_id,
                store_id=pichau_id,
                now=read_at,
                config=config,
            )

    # (a) janela recém-iniciada (começa exatamente agora) -- confirmação
    # anterior de 100min continua "recente": o relógio de 90min do modo
    # promocional só começa a contar a partir de agora.
    assert (
        asyncio.run(
            check(read_at - timedelta(minutes=1), read_at + timedelta(minutes=60))
        )
        == OfferFreshnessStatus.CONFIRMED_RECENT
    )

    # (b) MESMA janela, mas já ativa há 95min (`window_start` = 5min
    # DEPOIS de `confirmed_at`, ainda posterior à confirmação -- o
    # relógio conta a partir do início da janela, não da confirmação
    # original) -- 95min decorridos desde o início da janela > 90min de
    # limiar promocional -- agora "antiga": o worker já teve tempo de
    # sobra pra recoletar sob a cadência acelerada e não o fez.
    assert (
        asyncio.run(
            check(read_at - timedelta(minutes=95), read_at + timedelta(minutes=60))
        )
        == OfferFreshnessStatus.CONFIRMED_STALE
    )


def test_offer_freshness_unavailable_overrides_age(integration_database) -> None:
    """Indisponibilidade comprovada nunca "envelhece" para virar outra
    coisa -- vence qualquer idade, inclusive uma confirmação muito
    recente."""
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, pichau_id, _kabum_id = _seed_due_mission(
        integration_database.sessions, now
    )
    provider = _ControllableOfferProvider("R$ 1.900,00", "Esgotado")
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((provider, _FailingProvider())),
        ai_manager=_AlwaysMatchAIManager(),
    )
    asyncio.run(orchestrator.run_batch(now=now))
    with integration_database.sessions.begin() as session:
        observation = session.scalar(select(PriceObservation))
        assert observation.availability == Availability.UNAVAILABLE
        offer_id = observation.offer_id
        product_id = session.get(Offer, offer_id).product_id

    async def check():
        async with integration_database.async_sessions() as session:
            return await resolve_offer_freshness(
                session,
                offer_id=offer_id,
                product_id=product_id,
                store_id=pichau_id,
                now=now + timedelta(minutes=1),
                config=CadenceConfig(),
            )

    assert asyncio.run(check()) == OfferFreshnessStatus.UNAVAILABLE


def test_offer_freshness_never_confirmed_without_any_collection(
    integration_database,
) -> None:
    """Offer existente mas sem NENHUMA confirmação em `shared_collection_
    offers` -- estado distinto de "antiga"/"sumiço": nunca chegou a ser
    vista por este mecanismo."""
    now = datetime.now(UTC).replace(microsecond=0)
    with integration_database.sessions.begin() as session:
        store = session.scalar(select(Store).where(Store.code == "pichau"))
        product = Product(name="Produto nunca coletado", category="gpu")
        session.add(product)
        session.flush()
        offer = Offer(
            product_id=product.id,
            store_id=store.id,
            external_id="never-confirmed-offer",
            url="https://example.invalid/never-confirmed",
        )
        session.add(offer)
        session.flush()
        offer_id, product_id, store_id = offer.id, product.id, store.id

    async def check():
        async with integration_database.async_sessions() as session:
            return await resolve_offer_freshness(
                session,
                offer_id=offer_id,
                product_id=product_id,
                store_id=store_id,
                now=now,
                config=CadenceConfig(),
            )

    assert asyncio.run(check()) == OfferFreshnessStatus.NEVER_CONFIRMED


def test_offer_freshness_collection_failed_after_confirmation(
    integration_database,
) -> None:
    """Coleta que falha DEPOIS da última confirmação não é a mesma coisa
    que "oferta indisponível" nem "confirmação antiga" simples -- é a
    infraestrutura de coleta com problema agora, sem informação nova
    sobre a oferta em si. `CollectionRun` FAILED inserida diretamente
    (mesmo formato real que `_record_failure` grava, `orchestration.py:
    3043-3062`) -- o alvo deste teste é a CLASSIFICAÇÃO de
    `resolve_offer_freshness`, não o subsistema de fairness/agendamento
    do orquestrador (já coberto à parte, ex. `test_two_missions_racing_
    the_same_offer_never_duplicate`)."""
    now = datetime.now(UTC).replace(microsecond=0)
    offer_id, product_id, pichau_id, mission_id, confirmed_at = _seed_confirmed_offer(
        integration_database, now
    )
    failed_at = confirmed_at + timedelta(minutes=5)
    with integration_database.sessions.begin() as session:
        session.add(
            CollectionRun(
                mission_id=mission_id,
                store_id=pichau_id,
                status=CollectionRunStatus.FAILED,
                started_at=failed_at,
                finished_at=failed_at,
            )
        )

    async def check():
        async with integration_database.async_sessions() as session:
            return await resolve_offer_freshness(
                session,
                offer_id=offer_id,
                product_id=product_id,
                store_id=pichau_id,
                now=failed_at + timedelta(minutes=1),
                config=CadenceConfig(),
            )

    assert asyncio.run(check()) == OfferFreshnessStatus.COLLECTION_FAILED


def test_offer_freshness_missing_when_later_successful_run_omits_offer(
    integration_database,
) -> None:
    """A coleta RODA com sucesso depois da última confirmação, mas esta
    oferta especificamente não aparece mais -- "sumiço silencioso",
    sinal mais forte que "confirmação antiga" simples e diferente de
    "coleta falhou" (a infraestrutura funciona normalmente). Mesmo
    raciocínio de inserção direta do teste anterior: o alvo é a
    CLASSIFICAÇÃO (uma `CollectionRun` SUCCEEDED da mesma loja, mais
    nova que a confirmação, sem nenhuma linha de `SharedCollectionOffer`
    para esta oferta nela), não o subsistema de agendamento real."""
    now = datetime.now(UTC).replace(microsecond=0)
    offer_id, product_id, pichau_id, mission_id, confirmed_at = _seed_confirmed_offer(
        integration_database, now
    )
    later = confirmed_at + timedelta(minutes=5)
    with integration_database.sessions.begin() as session:
        # Run bem-sucedida real da mesma loja, mais nova -- de propósito
        # SEM nenhuma SharedCollectionOffer para `offer_id` (é exatamente
        # isso que caracteriza o sumiço: a coleta rodou e teve sucesso,
        # mas esta oferta não apareceu nela).
        session.add(
            CollectionRun(
                mission_id=mission_id,
                store_id=pichau_id,
                status=CollectionRunStatus.SUCCEEDED,
                started_at=later,
                finished_at=later,
            )
        )

    async def check():
        async with integration_database.async_sessions() as session:
            return await resolve_offer_freshness(
                session,
                offer_id=offer_id,
                product_id=product_id,
                store_id=pichau_id,
                now=later + timedelta(minutes=1),
                config=CadenceConfig(),
            )

    assert asyncio.run(check()) == OfferFreshnessStatus.MISSING_NO_CONFIRMATION


def test_availability_change_creates_new_observation_even_with_same_price(
    integration_database,
) -> None:
    """Preço à vista igual, disponibilidade muda -- é mudança comercial
    relevante, precisa gravar `PriceObservation` nova."""
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, pichau_id, _kabum_id = _seed_due_mission(
        integration_database.sessions, now
    )
    provider = _ControllableOfferProvider("R$ 1.900,00", "Em estoque")
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((provider, _FailingProvider())),
        ai_manager=_AlwaysMatchAIManager(),
    )
    asyncio.run(orchestrator.run_batch(now=now))

    provider.raw_availability = "Indisponível"
    due_at = now + timedelta(hours=1)
    _rearm_schedule(integration_database.sessions, mission_id, pichau_id, due_at)
    asyncio.run(orchestrator.run_batch(now=due_at))

    with integration_database.sessions.begin() as session:
        observations = list(
            session.scalars(
                select(PriceObservation).order_by(PriceObservation.observed_at)
            )
        )
        assert len(observations) == 2
        assert observations[0].availability == Availability.AVAILABLE
        assert observations[1].availability == Availability.UNAVAILABLE


def test_price_change_creates_new_observation(integration_database) -> None:
    """Preço mudou -- precisa gravar `PriceObservation` nova, mesmo com
    disponibilidade/demais campos iguais."""
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, pichau_id, _kabum_id = _seed_due_mission(
        integration_database.sessions, now
    )
    provider = _ControllableOfferProvider("R$ 1.900,00")
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((provider, _FailingProvider())),
        ai_manager=_AlwaysMatchAIManager(),
    )
    asyncio.run(orchestrator.run_batch(now=now))

    provider.raw_price = "R$ 1.500,00"
    due_at = now + timedelta(hours=1)
    _rearm_schedule(integration_database.sessions, mission_id, pichau_id, due_at)
    asyncio.run(orchestrator.run_batch(now=due_at))

    with integration_database.sessions.begin() as session:
        observations = list(
            session.scalars(
                select(PriceObservation).order_by(PriceObservation.observed_at)
            )
        )
        assert len(observations) == 2
        assert observations[0].amount == Decimal("1900.0000")
        assert observations[1].amount == Decimal("1500.0000")


class _MinimalEvidenceOfferProvider:
    """Subtask 6 (auditoria de preços/dedupe): reproduz a forma REAL de uma
    observação de PROD com duplicação confirmada (offer KaBuM! real,
    `raw_evidence` sem frete/disponibilidade/parcelamento) -- ao contrário
    de `_ControllableOfferProvider` (sempre define `raw_shipping`), aqui
    esses campos ficam `None`/vazios de propósito, igual ao card real."""

    def __init__(self, source_code: str, raw_price: str) -> None:
        self.source_code = source_code
        self.raw_price = raw_price

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        completed = request.requested_at.replace(microsecond=500000)
        return CollectionResult(
            self.source_code,
            request.requested_at,
            completed,
            (
                RawCollectedOffer(
                    source_code=self.source_code,
                    url="https://example.invalid/subtask6-minimal-evidence",
                    title="Subtask 6 minimal evidence GPU",
                    collected_at=completed,
                    external_id="subtask6-minimal-evidence-offer",
                    raw_price=self.raw_price,
                    raw_currency="BRL",
                    evidence={"card_text": "safe synthetic evidence"},
                ),
            ),
        )


def test_kabum_minimal_evidence_identical_state_does_not_duplicate(
    integration_database,
) -> None:
    """Subtask 6: tentativa de reprodução do bug confirmado em PROD (~30%
    de redundância real em KaBuM!/Amazon, 0% em Pichau/Terabyte) usando a
    forma mínima de evidência real (sem frete/disponibilidade/parcelamento
    -- `condition`/`availability` caem em UNKNOWN, `shipping_amount` fica
    `None`), com o código ATUAL do orquestrador."""
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, pichau_id, kabum_id = _seed_due_mission(
        integration_database.sessions, now
    )
    provider = _MinimalEvidenceOfferProvider("kabum", "R$ 1.999,98")
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((provider,)),
        ai_manager=_AlwaysMatchAIManager(),
    )
    asyncio.run(orchestrator.run_batch(now=now))

    due_at = now + timedelta(minutes=30)
    _rearm_schedule(integration_database.sessions, mission_id, kabum_id, due_at)
    asyncio.run(orchestrator.run_batch(now=due_at))

    with integration_database.sessions.begin() as session:
        observations = list(
            session.scalars(
                select(PriceObservation).order_by(PriceObservation.observed_at)
            )
        )
        assert len(observations) == 1, (
            "reproduziu o bug de PROD: 2a coleta comercialmente idêntica "
            "gravou observação redundante com evidência mínima (kabum)"
        )


class _CommercialStateOfferProvider:
    """Subtask 6 (correção de causa raiz do dedupe Amazon/KaBuM!):
    provider controlável cobrindo os campos que distinguem essas lojas do
    controle Pichau/Terabyte -- disponibilidade, seller_kind/
    fulfillment_kind, parcelamento -- todos ajustáveis entre `run_batch`."""

    def __init__(
        self,
        source_code: str,
        raw_price: str,
        *,
        raw_availability: str | None = "Em estoque",
        raw_fulfillment: str | None = None,
        raw_condition: str | None = None,
        seller_kind: MarketplacePartyKind | None = None,
        fulfillment_kind: MarketplacePartyKind | None = None,
        installment_options: tuple[RawInstallmentOption, ...] = (),
        url: str = "https://example.invalid/subtask6-commercial-state-offer",
        external_id: str = "subtask6-commercial-state-offer",
        seller_external_id: str | None = None,
        seller_name: str | None = None,
        title: str = "Subtask 6 commercial state GPU",
    ) -> None:
        self.source_code = source_code
        self.raw_price = raw_price
        self.raw_availability = raw_availability
        self.raw_fulfillment = raw_fulfillment
        self.raw_condition = raw_condition
        self.seller_kind = seller_kind
        self.fulfillment_kind = fulfillment_kind
        self.installment_options = installment_options
        self.url = url
        self.external_id = external_id
        self.seller_external_id = seller_external_id
        self.seller_name = seller_name
        self.title = title

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        completed = request.requested_at.replace(microsecond=500000)
        return CollectionResult(
            self.source_code,
            request.requested_at,
            completed,
            (
                RawCollectedOffer(
                    source_code=self.source_code,
                    url=self.url,
                    title=self.title,
                    collected_at=completed,
                    external_id=self.external_id,
                    raw_price=self.raw_price,
                    raw_currency="BRL",
                    raw_availability=self.raw_availability,
                    raw_fulfillment=self.raw_fulfillment,
                    raw_condition=self.raw_condition,
                    seller_kind=self.seller_kind,
                    fulfillment_kind=self.fulfillment_kind,
                    seller_external_id=self.seller_external_id,
                    seller_name=self.seller_name,
                    evidence={"card_text": "safe synthetic evidence"},
                    installment_options=self.installment_options,
                ),
            ),
        )


def _seed_due_mission_for_store(sessions, now: datetime, *, store_code: str) -> tuple:
    """Subtask 6: mesma forma de `_seed_due_mission`, mas para uma loja
    arbitrária (Amazon aqui) -- `_seed_due_mission` é fixo em Pichau/
    KaBuM! e é reaproveitado por muitos outros testes, não deve mudar."""
    with sessions.begin() as session:
        store = session.scalar(select(Store).where(Store.code == store_code))
        user = User(display_name="Subtask 6 synthetic", role=UserRole.USER)
        session.add(user)
        session.flush()
        mission = Mission(
            user_id=user.id,
            title="Subtask 6 commercial state",
            status=MissionStatus.ACTIVE,
        )
        session.add(mission)
        session.flush()
        session.add_all(
            (
                MissionCriteria(mission_id=mission.id, search_query="synthetic GPU"),
                MissionSchedule(
                    mission_id=mission.id,
                    interval_minutes=60,
                    next_run_at=now,
                    is_enabled=True,
                ),
                MissionSource(mission_id=mission.id, store_id=store.id),
            )
        )
        return mission.id, store.id


def test_amazon_identical_commercial_state_does_not_duplicate(
    integration_database,
) -> None:
    """Subtask 6 (regressão do bug real de PROD): coleta 1 grava; coleta 2
    comercialmente idêntica -- mesmo preço, mesma disponibilidade, mesmo
    seller/fulfillment já enriquecidos (a forma real de uma Offer Amazon
    confirmada em PROD) -- NÃO grava nova PriceObservation."""
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, amazon_id = _seed_due_mission_for_store(
        integration_database.sessions, now, store_code="amazon"
    )
    provider = _CommercialStateOfferProvider(
        "amazon",
        "R$ 919,98",
        raw_availability="Disponível",
        raw_fulfillment="Prime",
        seller_kind=MarketplacePartyKind.PLATFORM,
        fulfillment_kind=MarketplacePartyKind.PLATFORM,
    )
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((provider,)),
        ai_manager=_AlwaysMatchAIManager(),
    )
    asyncio.run(orchestrator.run_batch(now=now))

    due_at = now + timedelta(minutes=30)
    _rearm_schedule(integration_database.sessions, mission_id, amazon_id, due_at)
    asyncio.run(orchestrator.run_batch(now=due_at))

    with integration_database.sessions.begin() as session:
        observations = list(
            session.scalars(
                select(PriceObservation).order_by(PriceObservation.observed_at)
            )
        )
        assert len(observations) == 1


def test_amazon_availability_change_creates_new_observation(
    integration_database,
) -> None:
    """Subtask 6: mesmo preço, mesmo seller/fulfillment -- só a
    disponibilidade muda -- precisa gravar nova observação (Amazon)."""
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, amazon_id = _seed_due_mission_for_store(
        integration_database.sessions, now, store_code="amazon"
    )
    provider = _CommercialStateOfferProvider(
        "amazon",
        "R$ 919,98",
        raw_availability="Disponível",
        raw_fulfillment="Prime",
        seller_kind=MarketplacePartyKind.PLATFORM,
        fulfillment_kind=MarketplacePartyKind.PLATFORM,
    )
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((provider,)),
        ai_manager=_AlwaysMatchAIManager(),
    )
    asyncio.run(orchestrator.run_batch(now=now))

    provider.raw_availability = "Indisponível"
    due_at = now + timedelta(minutes=30)
    _rearm_schedule(integration_database.sessions, mission_id, amazon_id, due_at)
    asyncio.run(orchestrator.run_batch(now=due_at))

    with integration_database.sessions.begin() as session:
        observations = list(
            session.scalars(
                select(PriceObservation).order_by(PriceObservation.observed_at)
            )
        )
        assert len(observations) == 2


def test_amazon_seller_identification_supersedes_old_unattributed_offer(
    integration_database,
) -> None:
    """Rodada de frescor (2026-09-11), correção sobre a exclusão só por
    idade: identificar o vendedor pela primeira vez para um ASIN que
    antes só existia sem vendedor precisa marcar a Offer antiga como
    substituída IMEDIATAMENTE (mesma transação, não depois de
    envelhecer via frescor) -- sem isso existe uma janela real onde
    comparação/listagem mostram as duas como alternativas distintas.
    Preserva o histórico da antiga intocado, nunca reatribuído ao
    vendedor novo, e nunca esconde outras ofertas sem vendedor do MESMO
    produto (aqui, uma 3a oferta Kabum também sem vendedor)."""
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, amazon_id = _seed_due_mission_for_store(
        integration_database.sessions, now, store_code="amazon"
    )
    # Título RECONHECIDO pelo extrator real (`resolve_product_variant`) --
    # necessário para as duas ofertas (Amazon e Kabum, mais abaixo)
    # unificarem no MESMO Product por `identity_key`; um título genérico
    # sem extrator faria cada uma criar seu próprio Product ad-hoc.
    real_title = "NVIDIA GeForce RTX 5070 Ti"
    provider = _CommercialStateOfferProvider(
        "amazon",
        "R$ 919,98",
        raw_availability="Disponível",
        seller_kind=MarketplacePartyKind.UNKNOWN,
        fulfillment_kind=MarketplacePartyKind.UNKNOWN,
        external_id="B0SUPERSEDE01",
        title=real_title,
        # seller_external_id ausente -- reproduz o estado ANTIGO (Amazon
        # nunca implementava resolve_seller_name/resolve_seller_external_id).
    )
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((provider,)),
        ai_manager=_AlwaysMatchAIManager(),
    )
    asyncio.run(orchestrator.run_batch(now=now))

    with integration_database.sessions.begin() as session:
        old_offer = session.scalar(
            select(Offer).where(Offer.external_id == "B0SUPERSEDE01")
        )
        assert old_offer is not None
        assert old_offer.seller_id is None
        old_offer_id = old_offer.id
        product_id = old_offer.product_id
        old_observation_id = session.scalar(
            select(PriceObservation.id).where(PriceObservation.offer_id == old_offer_id)
        )
        assert old_observation_id is not None

    # 3a oferta do MESMO produto, loja diferente (Kabum), também sem
    # vendedor -- não pode ser afetada pela supersessão do ASIN Amazon.
    # Inserida diretamente (não via orquestrador/fila justa -- o alvo
    # aqui é só provar que a supersessão do ASIN Amazon não esconde
    # outras ofertas sem vendedor do MESMO produto, não exercitar
    # fairness/claim, já coberto em outros testes desta suíte). Mesmo
    # `product_id` da Offer Amazon, direto -- sem depender de
    # `resolve_product_variant` unificar os dois pelo título.
    with integration_database.sessions.begin() as session:
        kabum_store = session.scalar(select(Store).where(Store.code == "kabum"))
        kabum_offer = Offer(
            product_id=product_id,
            store_id=kabum_store.id,
            external_id="unrelated-kabum-offer",
            url="https://example.invalid/subtask6-kabum-unrelated-offer",
        )
        session.add(kabum_offer)
        session.flush()
        kabum_run = CollectionRun(
            mission_id=mission_id,
            store_id=kabum_store.id,
            status=CollectionRunStatus.SUCCEEDED,
            started_at=now,
            finished_at=now,
        )
        session.add(kabum_run)
        session.flush()
        session.add(
            PriceObservation(
                offer_id=kabum_offer.id,
                collection_run_id=kabum_run.id,
                amount=Decimal("899.90"),
                currency="BRL",
                total_amount=Decimal("899.90"),
                condition=OfferCondition.NEW,
                availability=Availability.AVAILABLE,
                observed_at=now,
            )
        )
        session.add(
            MissionOfferRelevance(
                mission_id=mission_id,
                offer_id=kabum_offer.id,
                classification=OfferRelevance.MATCH,
                classified_at=now,
            )
        )
        kabum_offer_id = kabum_offer.id

    # Vendedor identificado pela primeira vez para o MESMO ASIN --
    # simula a correção de hoje (resolve_seller_external_id da Amazon).
    provider.seller_external_id = "A2SUPERSEDE01"
    provider.seller_name = "Loja Parceira Teste"
    provider.seller_kind = MarketplacePartyKind.MARKETPLACE_PARTNER
    provider.raw_price = "R$ 929,98"  # muda -- observação nova de verdade
    due_at = now + timedelta(minutes=30)
    _rearm_schedule(integration_database.sessions, mission_id, amazon_id, due_at)
    asyncio.run(orchestrator.run_batch(now=due_at))

    with integration_database.sessions.begin() as session:
        offers = list(
            session.scalars(
                select(Offer).where(
                    Offer.store_id == amazon_id,
                    Offer.external_id == "B0SUPERSEDE01",
                )
            )
        )
        assert len(offers) == 2, "esperava a Offer antiga + a nova com vendedor"
        new_offer = next(o for o in offers if o.id != old_offer_id)
        old_offer_after = next(o for o in offers if o.id == old_offer_id)

        # Supersessão imediata, mesma transação da coleta que identificou
        # o vendedor -- não depende de frescor/idade.
        assert old_offer_after.superseded_by_id == new_offer.id
        assert old_offer_after.superseded_at is not None
        assert new_offer.seller_id is not None
        assert new_offer.superseded_by_id is None

        # Histórico da antiga intocado -- nem apagado, nem movido, nem
        # reatribuído ao vendedor novo.
        old_observations = list(
            session.scalars(
                select(PriceObservation).where(
                    PriceObservation.offer_id == old_offer_id
                )
            )
        )
        assert len(old_observations) == 1
        assert old_observations[0].id == old_observation_id
        assert (
            old_observations[0].seller_kind != MarketplacePartyKind.MARKETPLACE_PARTNER
        )

        # Nova Offer começa história PRÓPRIA -- não herda a observação antiga.
        new_observations = list(
            session.scalars(
                select(PriceObservation).where(
                    PriceObservation.offer_id == new_offer.id
                )
            )
        )
        assert len(new_observations) == 1
        assert new_observations[0].amount == Decimal("929.98")

    async def comparison():
        async with integration_database.async_sessions() as session:
            return await get_offer_comparison_for_user(
                session,
                offer_id=new_offer.id,
                user_id=await _mission_owner(session, mission_id),
            )

    comparison_result = asyncio.run(comparison())
    comparable_ids = {offer.offer.id for offer in comparison_result.offers}
    assert old_offer_id not in comparable_ids  # substituída -- não é mais alternativa
    assert new_offer.id in comparable_ids  # a nova, sim
    assert (
        kabum_offer_id in comparable_ids
    )  # outra oferta sem vendedor do MESMO produto -- preservada

    # Acesso DIRETO à Offer antiga continua funcionando (histórico
    # preservado, só não é mais oferecida como alternativa de comparação).
    async def direct_detail():
        async with integration_database.async_sessions() as session:
            return await get_offer_detail_for_user(
                session,
                offer_id=old_offer_id,
                user_id=await _mission_owner(session, mission_id),
            )

    direct_result = asyncio.run(direct_detail())
    assert direct_result is not None
    assert direct_result.offer.id == old_offer_id


async def _mission_owner(session, mission_id) -> UUID:
    return await session.scalar(select(Mission.user_id).where(Mission.id == mission_id))


def test_kabum_installment_change_creates_new_observation(
    integration_database,
) -> None:
    """Subtask 6: mesmo preço -- só o parcelamento muda (10x sem juros ->
    6x sem juros) -- precisa gravar nova observação (KaBuM!), mesmo com
    todos os outros 9 campos idênticos."""
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, pichau_id, kabum_id = _seed_due_mission(
        integration_database.sessions, now
    )
    provider = _CommercialStateOfferProvider(
        "kabum",
        "R$ 1.999,98",
        raw_availability=None,
        installment_options=(
            RawInstallmentOption(
                installment_count=10,
                raw_amount="R$ 199,99",
                interest_kind=InstallmentInterestKind.INTEREST_FREE,
                is_highlighted=True,
            ),
        ),
    )
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((provider,)),
        ai_manager=_AlwaysMatchAIManager(),
    )
    asyncio.run(orchestrator.run_batch(now=now))

    provider.installment_options = (
        RawInstallmentOption(
            installment_count=6,
            raw_amount="R$ 333,33",
            interest_kind=InstallmentInterestKind.INTEREST_FREE,
            is_highlighted=True,
        ),
    )
    due_at = now + timedelta(minutes=30)
    _rearm_schedule(integration_database.sessions, mission_id, kabum_id, due_at)
    asyncio.run(orchestrator.run_batch(now=due_at))

    with integration_database.sessions.begin() as session:
        observations = list(
            session.scalars(
                select(PriceObservation).order_by(PriceObservation.observed_at)
            )
        )
        assert len(observations) == 2


def test_condition_change_creates_new_observation(integration_database) -> None:
    """Subtask 6: mesmo preço -- só a condição muda (Novo -> Usado) --
    precisa gravar nova observação (Amazon, evidência real de condição)."""
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, amazon_id = _seed_due_mission_for_store(
        integration_database.sessions, now, store_code="amazon"
    )
    provider = _CommercialStateOfferProvider(
        "amazon", "R$ 919,98", raw_availability="Disponível", raw_condition="Novo"
    )
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((provider,)),
        ai_manager=_AlwaysMatchAIManager(),
    )
    asyncio.run(orchestrator.run_batch(now=now))

    provider.raw_condition = "Usado"
    due_at = now + timedelta(minutes=30)
    _rearm_schedule(integration_database.sessions, mission_id, amazon_id, due_at)
    asyncio.run(orchestrator.run_batch(now=due_at))

    with integration_database.sessions.begin() as session:
        observations = list(
            session.scalars(
                select(PriceObservation).order_by(PriceObservation.observed_at)
            )
        )
        assert len(observations) == 2


def test_fulfillment_change_creates_new_observation(integration_database) -> None:
    """Subtask 6: mesmo preço -- só o texto de fulfillment muda (Prime ->
    nenhum) -- precisa gravar nova observação (Amazon)."""
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, amazon_id = _seed_due_mission_for_store(
        integration_database.sessions, now, store_code="amazon"
    )
    provider = _CommercialStateOfferProvider(
        "amazon",
        "R$ 919,98",
        raw_availability="Disponível",
        raw_fulfillment="Prime",
    )
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((provider,)),
        ai_manager=_AlwaysMatchAIManager(),
    )
    asyncio.run(orchestrator.run_batch(now=now))

    provider.raw_fulfillment = None
    due_at = now + timedelta(minutes=30)
    _rearm_schedule(integration_database.sessions, mission_id, amazon_id, due_at)
    asyncio.run(orchestrator.run_batch(now=due_at))

    with integration_database.sessions.begin() as session:
        observations = list(
            session.scalars(
                select(PriceObservation).order_by(PriceObservation.observed_at)
            )
        )
        assert len(observations) == 2


def test_seller_kind_change_creates_new_observation(integration_database) -> None:
    """Subtask 6: mesmo preço -- só seller_kind/fulfillment_kind mudam
    (platform -> marketplace_partner) -- precisa gravar nova observação
    (Amazon, marketplace real com vendedores terceiros)."""
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, amazon_id = _seed_due_mission_for_store(
        integration_database.sessions, now, store_code="amazon"
    )
    provider = _CommercialStateOfferProvider(
        "amazon",
        "R$ 919,98",
        raw_availability="Disponível",
        seller_kind=MarketplacePartyKind.PLATFORM,
        fulfillment_kind=MarketplacePartyKind.PLATFORM,
    )
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((provider,)),
        ai_manager=_AlwaysMatchAIManager(),
    )
    asyncio.run(orchestrator.run_batch(now=now))

    provider.seller_kind = MarketplacePartyKind.MARKETPLACE_PARTNER
    provider.fulfillment_kind = MarketplacePartyKind.MARKETPLACE_PARTNER
    due_at = now + timedelta(minutes=30)
    _rearm_schedule(integration_database.sessions, mission_id, amazon_id, due_at)
    asyncio.run(orchestrator.run_batch(now=due_at))

    with integration_database.sessions.begin() as session:
        observations = list(
            session.scalars(
                select(PriceObservation).order_by(PriceObservation.observed_at)
            )
        )
        assert len(observations) == 2


def test_url_change_alone_does_not_create_new_observation(integration_database) -> None:
    """Subtask 6 (item 5 da checagem): a URL real da Amazon muda a cada
    busca (token de sessão -- achado real da auditoria de PROD), sem
    representar mudança comercial nenhuma. Estado comercial canônico
    idêntico + URL diferente -> NÃO grava nova observação."""
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, amazon_id = _seed_due_mission_for_store(
        integration_database.sessions, now, store_code="amazon"
    )
    provider = _CommercialStateOfferProvider(
        "amazon",
        "R$ 919,98",
        raw_availability="Disponível",
        raw_fulfillment="Prime",
        seller_kind=MarketplacePartyKind.PLATFORM,
        fulfillment_kind=MarketplacePartyKind.PLATFORM,
        url="https://www.amazon.com.br/dp/B0FQPCRG7P?token=sessao-1",
    )
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((provider,)),
        ai_manager=_AlwaysMatchAIManager(),
    )
    asyncio.run(orchestrator.run_batch(now=now))

    # mesmo external_id (ASIN real), URL de sessao diferente -- exatamente
    # o padrao observado em PROD entre duas buscas da mesma oferta.
    provider.url = "https://www.amazon.com.br/dp/B0FQPCRG7P?token=sessao-2"
    due_at = now + timedelta(minutes=30)
    _rearm_schedule(integration_database.sessions, mission_id, amazon_id, due_at)
    asyncio.run(orchestrator.run_batch(now=due_at))

    with integration_database.sessions.begin() as session:
        observations = list(
            session.scalars(
                select(PriceObservation).order_by(PriceObservation.observed_at)
            )
        )
        assert len(observations) == 1


class _RendezvousOfferProvider:
    """Subtask 6 (prova da trava por Offer): duas missões due no MESMO
    run_batch -- a Fase A delas roda em paralelo de verdade (asyncio
    gather, só a Fase C é serializada por mission_id). Uma barreira
    asyncio força as duas coletas da mesma Offer a avançarem para a
    persistência quase ao mesmo tempo, maximizando a chance real de
    interleaving na seção crítica que a trava por Offer precisa cobrir --
    sem a trava, isto reproduziria a duplicata real de PROD."""

    source_code = "kabum"

    def __init__(self, raw_price: str, *, parties: int) -> None:
        self.raw_price = raw_price
        self._barrier = asyncio.Barrier(parties)

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        try:
            async with asyncio.timeout(5):
                await self._barrier.wait()
        except TimeoutError, asyncio.BrokenBarrierError:
            pass
        completed = request.requested_at.replace(microsecond=500000)
        return CollectionResult(
            self.source_code,
            request.requested_at,
            completed,
            (
                RawCollectedOffer(
                    source_code=self.source_code,
                    url="https://example.invalid/subtask6-race-offer",
                    title="Subtask 6 race GPU",
                    collected_at=completed,
                    external_id="subtask6-race-offer",
                    raw_price=self.raw_price,
                    raw_currency="BRL",
                    evidence={"card_text": "safe synthetic evidence"},
                ),
            ),
        )


def test_two_missions_racing_the_same_offer_never_duplicate(
    integration_database,
) -> None:
    """Subtask 6 (item 5 da checagem -- o mais importante): duas missões
    DIFERENTES, mesma loja, mesmo produto (mesmo external_id -> mesma
    Offer), ambas due no MESMO run_batch, forçadas via barreira a
    processar a MESMA Offer concorrentemente com estado comercial
    idêntico. Sem a trava SELECT FOR UPDATE por Offer, as duas podiam ler
    a mesma "última observação" antes de qualquer commit e ambas
    decidirem "não redundante" -- exatamente o mecanismo suspeito do bug
    real de PROD. Com a trava: só UMA nova PriceObservation.

    Também prova, no mesmo cenário de corrida, que a confirmação compacta
    (`SharedCollectionOffer`) não duplica nem se perde: cada Mission tem
    sua própria `CollectionRun` (`ck_collection_runs_ownership_xor`), as
    duas processam a MESMA Offer -- uma cria a observação, a outra a
    reaproveita (redundante) -- e AMBAS as runs precisam registrar sua
    própria confirmação apontando pra essa observação única, nunca zero
    (perdida) nem uma só reescrita (a segunda "engolida" pela primeira)."""
    now = datetime.now(UTC).replace(microsecond=0)
    with integration_database.sessions.begin() as session:
        kabum = session.scalar(select(Store).where(Store.code == "kabum"))
        kabum_id = kabum.id
        for label in ("A", "B"):
            user = User(
                display_name=f"Subtask 6 race mission {label}", role=UserRole.USER
            )
            session.add(user)
            session.flush()
            mission = Mission(
                user_id=user.id,
                title=f"Subtask 6 race mission {label}",
                status=MissionStatus.ACTIVE,
            )
            session.add(mission)
            session.flush()
            session.add_all(
                (
                    MissionCriteria(mission_id=mission.id, search_query="race GPU"),
                    MissionSchedule(
                        mission_id=mission.id,
                        interval_minutes=60,
                        next_run_at=now,
                        is_enabled=True,
                    ),
                    MissionSource(mission_id=mission.id, store_id=kabum_id),
                )
            )

    provider = _RendezvousOfferProvider("R$ 1.999,98", parties=2)
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((provider,)),
        ai_manager=_AlwaysMatchAIManager(),
        max_concurrency=2,
        store_min_interval_seconds=_NEUTRAL_STORE_INTERVAL_SECONDS,
        max_concurrent_user_batches=2,
    )
    result = asyncio.run(orchestrator.run_batch(now=now))

    assert result.claimed == 2
    assert result.succeeded == 2
    with integration_database.sessions.begin() as session:
        observations = list(session.scalars(select(PriceObservation)))
        assert len(observations) == 1, (
            "duas missoes concorrentes na mesma Offer duplicaram a "
            "observacao -- a trava por Offer nao esta funcionando"
        )
        confirmations = list(session.scalars(select(SharedCollectionOffer)))
        assert len(confirmations) == 2, (
            "cada uma das 2 runs concorrentes precisa da sua propria "
            "confirmacao -- nem perdida (< 2) nem duplicada por retry (> 2)"
        )
        assert {c.collection_run_id for c in confirmations} == {
            run.id
            for run in session.scalars(
                select(CollectionRun).where(CollectionRun.store_id == kabum_id)
            )
        }
        # as duas apontam pra MESMA observacao (uma criou, a outra
        # reaproveitou) -- nunca observation_id divergente/inventado.
        assert {c.observation_id for c in confirmations} == {observations[0].id}
        assert {c.offer_id for c in confirmations} == {observations[0].offer_id}


class _OppositeOrderTwoOfferProvider:
    """Subtask 6 (teste de deadlock): duas Offers reais e distintas, mas o
    PREÇO relativo entre elas se INVERTE a cada chamada de `collect` --
    exatamente o que `_limit_intermediate_candidates` usa como critério de
    ordenação (preço genuinamente volátil, como as flash sales reais de
    KaBuM! encontradas na auditoria de PROD). Isso força a ordem de
    SELEÇÃO COMERCIAL das duas Offers a ficar oposta entre as duas
    coletas concorrentes -- sem ordenação separada de travas por
    `offer.id`, isso deadlockearia."""

    source_code = "kabum"

    def __init__(self) -> None:
        self._call_count = 0
        self._barrier = asyncio.Barrier(2)

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        self._call_count += 1
        # chamada 1: X mais barato que Y / chamada 2: Y mais barato que X.
        x_price, y_price = (
            ("R$ 100,00", "R$ 200,00")
            if self._call_count == 1
            else ("R$ 200,00", "R$ 100,00")
        )
        try:
            async with asyncio.timeout(5):
                await self._barrier.wait()
        except TimeoutError, asyncio.BrokenBarrierError:
            pass
        completed = request.requested_at.replace(microsecond=500000)
        return CollectionResult(
            self.source_code,
            request.requested_at,
            completed,
            (
                RawCollectedOffer(
                    source_code=self.source_code,
                    url="https://example.invalid/subtask6-deadlock-x",
                    title="Subtask 6 deadlock GPU X",
                    collected_at=completed,
                    external_id="subtask6-deadlock-x",
                    raw_price=x_price,
                    raw_currency="BRL",
                    evidence={"card_text": "safe synthetic evidence"},
                ),
                RawCollectedOffer(
                    source_code=self.source_code,
                    url="https://example.invalid/subtask6-deadlock-y",
                    title="Subtask 6 deadlock GPU Y",
                    collected_at=completed,
                    external_id="subtask6-deadlock-y",
                    raw_price=y_price,
                    raw_currency="BRL",
                    evidence={"card_text": "safe synthetic evidence"},
                ),
            ),
        )


def test_two_missions_opposite_offer_order_never_deadlocks(
    integration_database,
) -> None:
    """Subtask 6 (item 4 da checagem -- teste de deadlock): missão A e
    missão B compartilham DUAS Offers reais, recebidas em ordem comercial
    OPOSTA (preço relativo invertido entre as duas coletas -- volatilidade
    real), executando concorrentemente no MESMO `run_batch`. Sem a
    ordenação determinística de travas por `offer.id` (separada da ordem
    de seleção comercial), isso reproduziria um deadlock real do
    PostgreSQL. Timeout curto: um deadlock/hang fica evidente como falha
    do teste, não como travamento indefinido da suíte."""
    now = datetime.now(UTC).replace(microsecond=0)
    with integration_database.sessions.begin() as session:
        kabum = session.scalar(select(Store).where(Store.code == "kabum"))
        kabum_id = kabum.id
        for label in ("A", "B"):
            user = User(
                display_name=f"Subtask 6 deadlock mission {label}",
                role=UserRole.USER,
            )
            session.add(user)
            session.flush()
            mission = Mission(
                user_id=user.id,
                title=f"Subtask 6 deadlock mission {label}",
                status=MissionStatus.ACTIVE,
            )
            session.add(mission)
            session.flush()
            session.add_all(
                (
                    MissionCriteria(mission_id=mission.id, search_query="deadlock GPU"),
                    MissionSchedule(
                        mission_id=mission.id,
                        interval_minutes=60,
                        next_run_at=now,
                        is_enabled=True,
                    ),
                    MissionSource(mission_id=mission.id, store_id=kabum_id),
                )
            )

    provider = _OppositeOrderTwoOfferProvider()
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((provider,)),
        ai_manager=_AlwaysMatchAIManager(),
        max_concurrency=2,
        store_min_interval_seconds=_NEUTRAL_STORE_INTERVAL_SECONDS,
        max_concurrent_user_batches=2,
    )

    async def _run_with_timeout():
        return await asyncio.wait_for(orchestrator.run_batch(now=now), timeout=10)

    result = asyncio.run(_run_with_timeout())

    assert result.claimed == 2
    assert result.succeeded == 2, (
        "uma das duas coletas falhou -- possivel deadlock detectado e "
        "abortado pelo PostgreSQL"
    )
    with integration_database.sessions.begin() as session:
        observations = list(session.scalars(select(PriceObservation)))
        # duas Offers reais e distintas, preco genuinamente mudou entre as
        # duas coletas para cada uma -- no maximo 2 observacoes por Offer
        # (uma por valor de preco realmente visto), nunca mais que isso.
        assert 2 <= len(observations) <= 4
        amounts = {str(o.amount) for o in observations}
        assert amounts <= {"100.0000", "200.0000"}


class _MixedExistingAndNewOfferProvider:
    """Subtask 6 (teste de deadlock -- caminho específico do
    `begin_nested()`): devolve uma Offer JÁ EXISTENTE (será mutada --
    `image_url` muda -- sem `begin_nested`, fica só "suja" na sessão) e
    uma Offer GENUINAMENTE NOVA (aciona `begin_nested`, que força flush
    incondicional de TODO o estado pendente, mesmo com `autoflush=False`
    -- é exatamente o furo apontado: se isso acontecesse ANTES da fase
    global de travas, a Offer existente seria flushada/travada na ordem
    de iteração, não na ordem global). A ordem relativa entre as duas se
    inverte a cada chamada via preço (mesmo mecanismo do teste anterior)."""

    source_code = "kabum"

    def __init__(self, existing_external_id: str, new_external_id: str) -> None:
        self.existing_external_id = existing_external_id
        self.new_external_id = new_external_id
        self._call_count = 0
        self._barrier = asyncio.Barrier(2)

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        self._call_count += 1
        existing_price, new_price = (
            ("R$ 100,00", "R$ 200,00")
            if self._call_count == 1
            else ("R$ 200,00", "R$ 100,00")
        )
        try:
            async with asyncio.timeout(5):
                await self._barrier.wait()
        except TimeoutError, asyncio.BrokenBarrierError:
            pass
        completed = request.requested_at.replace(microsecond=500000)
        return CollectionResult(
            self.source_code,
            request.requested_at,
            completed,
            (
                RawCollectedOffer(
                    source_code=self.source_code,
                    url="https://example.invalid/subtask6-existing-offer",
                    title="Subtask 6 begin_nested GPU existing",
                    collected_at=completed,
                    external_id=self.existing_external_id,
                    raw_price=existing_price,
                    raw_currency="BRL",
                    # muda a cada chamada -- forca a mutacao de
                    # `offer.image_url` no ramo "offer ja existe" de
                    # `_resolve_offer`, sem `begin_nested` proprio.
                    image_url=f"https://example.invalid/img-{self._call_count}.jpg",
                    evidence={"card_text": "safe synthetic evidence"},
                ),
                RawCollectedOffer(
                    source_code=self.source_code,
                    url="https://example.invalid/subtask6-new-offer",
                    title="Subtask 6 begin_nested GPU new",
                    collected_at=completed,
                    external_id=self.new_external_id,
                    raw_price=new_price,
                    raw_currency="BRL",
                    evidence={"card_text": "safe synthetic evidence"},
                ),
            ),
        )


def test_two_missions_mixed_existing_and_new_offer_never_deadlocks(
    integration_database,
) -> None:
    """Subtask 6 (item 5 da checagem -- furo específico do
    `begin_nested()`): força o caminho exato que você apontou -- uma
    Offer JÁ EXISTENTE (mutação de `image_url`, sem `begin_nested`
    próprio) processada ANTES de uma Offer GENUINAMENTE NOVA (aciona
    `begin_nested`, que flusha TUDO que está pendente na sessão,
    incondicionalmente) -- com a ordem relativa entre as duas invertida
    entre duas missões concorrentes. Na implementação vulnerável (mutar +
    resolver tudo ANTES da fase global de travas), isso reproduziria o
    deadlock; na implementação corrigida (identificação somente leitura
    -> trava global -> só então `_resolve_offer`), não deve nem chegar
    perto de travar."""
    now = datetime.now(UTC).replace(microsecond=0)
    existing_external_id = "subtask6-mixed-existing"
    new_external_id = "subtask6-mixed-new"

    # Semeia a Offer "existente" ANTES da corrida -- uma missao solitaria,
    # coleta unica, sem concorrencia nenhuma.
    seed_mission_id, seed_kabum_id = _seed_due_mission_for_store(
        integration_database.sessions, now, store_code="kabum"
    )
    seed_provider = _CommercialStateOfferProvider(
        "kabum", "R$ 100,00", external_id=existing_external_id
    )
    seed_orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((seed_provider,)),
        ai_manager=_AlwaysMatchAIManager(),
    )
    seed_result = asyncio.run(seed_orchestrator.run_batch(now=now))
    assert seed_result.succeeded == 1

    later = now + timedelta(minutes=30)
    with integration_database.sessions.begin() as session:
        kabum = session.scalar(select(Store).where(Store.code == "kabum"))
        kabum_id = kabum.id
        for label in ("A", "B"):
            user = User(
                display_name=f"Subtask 6 mixed mission {label}", role=UserRole.USER
            )
            session.add(user)
            session.flush()
            mission = Mission(
                user_id=user.id,
                title=f"Subtask 6 mixed mission {label}",
                status=MissionStatus.ACTIVE,
            )
            session.add(mission)
            session.flush()
            session.add_all(
                (
                    MissionCriteria(
                        mission_id=mission.id, search_query="begin_nested GPU"
                    ),
                    MissionSchedule(
                        mission_id=mission.id,
                        interval_minutes=60,
                        next_run_at=later,
                        is_enabled=True,
                    ),
                    MissionSource(mission_id=mission.id, store_id=kabum_id),
                )
            )

    provider = _MixedExistingAndNewOfferProvider(existing_external_id, new_external_id)
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((provider,)),
        ai_manager=_AlwaysMatchAIManager(),
        max_concurrency=2,
        store_min_interval_seconds=_NEUTRAL_STORE_INTERVAL_SECONDS,
        max_concurrent_user_batches=2,
    )

    async def _run_with_timeout():
        return await asyncio.wait_for(orchestrator.run_batch(now=later), timeout=10)

    result = asyncio.run(_run_with_timeout())

    assert result.claimed == 2
    assert result.succeeded == 2, (
        "uma das duas coletas falhou -- possivel deadlock detectado e "
        "abortado pelo PostgreSQL no caminho existente+mutacao / "
        "novo+begin_nested"
    )
    with integration_database.sessions.begin() as session:
        offers = list(
            session.scalars(
                select(Offer).where(
                    Offer.external_id.in_([existing_external_id, new_external_id])
                )
            )
        )
        assert len(offers) == 2


class _TwoNewSellersOppositeOrderProvider:
    """Subtask 6 (item 5 da checagem -- conflito de INSERT concorrente em
    unique constraint, `uq_sellers_store_external_id`): duas Offers de
    dois Sellers GENUINAMENTE NOVOS (nunca vistos), preço relativo
    invertido a cada chamada para forçar ordem comercial oposta entre
    duas coletas concorrentes. Nenhuma linha física existe ainda para
    nenhum dos dois Sellers -- não há o que travar com `FOR UPDATE`; só a
    trava transacional por chave lógica (`pg_advisory_xact_lock`)
    protege contra o Postgres bloquear (e potencialmente deadlockear) dois
    `INSERT` concorrentes na mesma constraint, em ordem cruzada. Amazon
    (não KaBuM!) de propósito: `Seller` só pode existir sob uma loja
    `source_type='marketplace'` (trigger real do banco), e só Amazon está
    marcada assim nesta base."""

    source_code = "amazon"

    def __init__(self) -> None:
        self._call_count = 0
        self._barrier = asyncio.Barrier(2)

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        self._call_count += 1
        p_price, q_price = (
            ("R$ 100,00", "R$ 200,00")
            if self._call_count == 1
            else ("R$ 200,00", "R$ 100,00")
        )
        try:
            async with asyncio.timeout(5):
                await self._barrier.wait()
        except TimeoutError, asyncio.BrokenBarrierError:
            pass
        completed = request.requested_at.replace(microsecond=500000)
        return CollectionResult(
            self.source_code,
            request.requested_at,
            completed,
            (
                RawCollectedOffer(
                    source_code=self.source_code,
                    url="https://example.invalid/subtask6-seller-p-offer",
                    title="Subtask 6 new seller GPU P",
                    collected_at=completed,
                    external_id="subtask6-seller-p-offer",
                    seller_external_id="subtask6-new-seller-p",
                    seller_name="Vendedor P",
                    raw_price=p_price,
                    raw_currency="BRL",
                    evidence={"card_text": "safe synthetic evidence"},
                ),
                RawCollectedOffer(
                    source_code=self.source_code,
                    url="https://example.invalid/subtask6-seller-q-offer",
                    title="Subtask 6 new seller GPU Q",
                    collected_at=completed,
                    external_id="subtask6-seller-q-offer",
                    seller_external_id="subtask6-new-seller-q",
                    seller_name="Vendedor Q",
                    raw_price=q_price,
                    raw_currency="BRL",
                    evidence={"card_text": "safe synthetic evidence"},
                ),
            ),
        )


def test_two_missions_creating_two_new_sellers_opposite_order_never_deadlocks(
    integration_database,
) -> None:
    """Subtask 6 (item 5 da checagem -- caminho específico apontado:
    conflito de `INSERT` concorrente, não `FOR UPDATE`): duas missões
    diferentes tentam criar os MESMOS dois Sellers novos (`uq_sellers_
    store_external_id`), em ordem comercial oposta entre si (preço
    relativo invertido). Sem a trava transacional por chave lógica
    (`pg_advisory_xact_lock`, ordenada globalmente), TX1 poderia inserir
    P e esperar por Q enquanto TX2 insere Q e espera por P -- ciclo real
    do Postgres, mesmo sem nenhum `SELECT ... FOR UPDATE` envolvido, já
    que nenhuma das duas linhas existia antes. Timeout curto: hang vira
    falha evidente, não travamento da suíte."""
    now = datetime.now(UTC).replace(microsecond=0)
    with integration_database.sessions.begin() as session:
        amazon = session.scalar(select(Store).where(Store.code == "amazon"))
        amazon_id = amazon.id
        for label in ("A", "B"):
            user = User(
                display_name=f"Subtask 6 new-seller mission {label}",
                role=UserRole.USER,
            )
            session.add(user)
            session.flush()
            mission = Mission(
                user_id=user.id,
                title=f"Subtask 6 new-seller mission {label}",
                status=MissionStatus.ACTIVE,
            )
            session.add(mission)
            session.flush()
            session.add_all(
                (
                    MissionCriteria(
                        mission_id=mission.id, search_query="new seller GPU"
                    ),
                    MissionSchedule(
                        mission_id=mission.id,
                        interval_minutes=60,
                        next_run_at=now,
                        is_enabled=True,
                    ),
                    MissionSource(mission_id=mission.id, store_id=amazon_id),
                )
            )

    provider = _TwoNewSellersOppositeOrderProvider()
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((provider,)),
        ai_manager=_AlwaysMatchAIManager(),
        max_concurrency=2,
        store_min_interval_seconds=_NEUTRAL_STORE_INTERVAL_SECONDS,
        max_concurrent_user_batches=2,
    )

    async def _run_with_timeout():
        return await asyncio.wait_for(orchestrator.run_batch(now=now), timeout=10)

    result = asyncio.run(_run_with_timeout())

    assert result.claimed == 2
    assert result.succeeded == 2, (
        "uma das duas coletas falhou -- possivel deadlock detectado e "
        "abortado pelo PostgreSQL entre dois INSERT concorrentes de "
        "Seller na mesma unique constraint"
    )
    with integration_database.sessions.begin() as session:
        sellers = list(
            session.scalars(
                select(Seller).where(
                    Seller.external_id.in_(
                        ["subtask6-new-seller-p", "subtask6-new-seller-q"]
                    )
                )
            )
        )
        # exatamente um Seller por chave logica -- nenhuma duplicacao
        # apesar das duas transacoes tentarem criar os dois ao mesmo tempo.
        assert len(sellers) == 2
        offers = list(
            session.scalars(
                select(Offer).where(
                    Offer.external_id.in_(
                        [
                            "subtask6-seller-p-offer",
                            "subtask6-seller-q-offer",
                        ]
                    )
                )
            )
        )
        assert len(offers) == 2


# ---------------------------------------------------------------------------
# Frente 1 (achado crítico, 2026-09-12) -- aprendizado de identidade ligado
# ao pipeline REAL do CollectionOrchestrator, não só chamadas diretas a
# `resolve_or_learn_product_variant` (essas já ficam em
# `tests/integration/test_product_identity_learning.py`). Categoria
# (placa-mãe) nunca reconhecida pelos extratores determinísticos
# (smartphone/CPU/GPU) -- só a rota assistida por IA, atrás da flag
# `product_identity_learning_enabled`, resolve.
# ---------------------------------------------------------------------------

_PIPELINE_MOBO_DDR5_EXTRACTION = (
    '{"category": "motherboard", "brand": "ASUS", "family": "TUF Gaming", '
    '"model": "B650-Plus", "variant": null, "store_sku": null, '
    '"manufacturer_part_number": null, "attributes": {"memory_type": "DDR5"}}'
)


class _IdentityLearningPipelineAIManager:
    """Distingue as 3 finalidades reais que passam pela Fase B quando o
    aprendizado de identidade está ligado: relevância, normalização de
    título e extração de identidade -- só a última é contada, para provar
    quantas vezes o pipeline REAL precisou perguntar à IA."""

    def __init__(self, identity_content: str) -> None:
        self.identity_content = identity_content
        self.identity_calls = 0

    async def generate(self, request):
        if request.purpose == "classify_offer_relevance":
            content = '{"relevance": "match"}'
        elif request.purpose == "extract_product_identity":
            self.identity_calls += 1
            content = self.identity_content
        else:
            content = '{"display_title": "ASUS TUF Gaming B650-Plus"}'
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub",
            content=content,
            finished_at=datetime.now(UTC),
        )


def test_identity_learning_is_wired_through_the_real_orchestrator_phases(
    integration_database,
) -> None:
    """Prova o requisito explícito da rodada: o aprendizado de identidade
    precisa estar ligado ao pipeline real (Fase A síncrona -> Fase B com
    IA fora da transação -> Fase C aplicando o resultado na seção
    crítica), não só passar no próprio mecanismo isolado. Com a flag
    desligada (default), este mesmo fluxo já é coberto pelos 311 testes
    de integração pré-existentes sem nenhuma chamada de identidade -- só
    quando `product_identity_learning_enabled=True` que a Fase B aciona
    `resolve_or_learn_product_variant`."""
    now = datetime.now(UTC).replace(microsecond=0)
    settings = Settings(_env_file=None, product_identity_learning_enabled=True)
    ai_manager = _IdentityLearningPipelineAIManager(_PIPELINE_MOBO_DDR5_EXTRACTION)

    mission_id, amazon_id = _seed_due_mission_for_store(
        integration_database.sessions, now, store_code="amazon"
    )
    provider = _CommercialStateOfferProvider(
        "amazon",
        "R$ 1.299,90",
        title="ASUS TUF Gaming B650-Plus WiFi DDR5 AM5",
        url="https://example.invalid/identity-pipeline-mobo",
        external_id="identity-pipeline-mobo",
    )
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((provider,)),
        ai_manager=ai_manager,
        settings=settings,
    )
    asyncio.run(orchestrator.run_batch(now=now))

    assert ai_manager.identity_calls == 1

    with integration_database.sessions.begin() as session:
        offer = session.scalar(
            select(Offer).where(Offer.external_id == "identity-pipeline-mobo")
        )
        assert offer is not None
        product = session.get(Product, offer.product_id)
        assert product is not None and product.identity_key is not None, (
            "a Fase C precisa ter aplicado o `learned_identity` da Fase B "
            "no MESMO Product da oferta -- nunca deixar identity_key nulo "
            "quando o aprendizado foi bem-sucedido"
        )
        assert product.category == "motherboard"
        candidates = list(session.scalars(select(ProductIdentityCandidate)))
        assert len(candidates) == 1
        assert candidates[0].status == "approved"

    # Segunda coleta da MESMA oferta: `identity_key` já resolvido --
    # `identity_unresolved` precisa ser False desta vez, então a Fase B
    # nunca deveria perguntar de novo à IA sobre identidade.
    due_at = now + timedelta(minutes=30)
    _rearm_schedule(integration_database.sessions, mission_id, amazon_id, due_at)
    asyncio.run(orchestrator.run_batch(now=due_at))
    assert ai_manager.identity_calls == 1, (
        "produto já com identity_key resolvido nunca deveria re-disparar "
        "a Fase B de aprendizado de identidade"
    )


def test_identity_learning_reuses_across_stores_through_the_real_orchestrator(
    integration_database,
) -> None:
    """Repete a prova anterior para o requisito de reaproveitamento entre
    lojas: duas missões de lojas DIFERENTES, tituladas de forma diferente
    mas descrevendo a MESMA placa-mãe -- a segunda, processada numa
    `run_batch` SEGUINTE (depois que a primeira já comitou seu candidato
    aprovado), nunca deveria custar uma nova chamada de IA (reconhecimento
    por tokens, `_find_reusable_candidate_by_tokens`), e ambos os
    Products convergem para o mesmo `identity_key`, tudo através do
    orquestrador real, nunca de uma chamada direta ao mecanismo isolado.

    Achado real (checkpoint 3, 2026-09-13): a Fase B processa TODAS as
    ofertas de um mesmo `run_batch` concorrentemente via `asyncio.gather`
    (`_run_phase_b`), cada uma com sua própria sessão -- se as duas lojas
    forem reivindicadas na MESMA chamada (como esta prova tentava antes,
    com `max_concurrent_user_batches=2`), nenhuma das duas ainda comitou
    quando a outra consulta candidatos reaproveitáveis, então AMBAS
    aprendem via IA (`identity_calls == 2`), não por falha de
    `_find_reusable_candidate_by_tokens`, mas porque nenhuma pode ver o
    aprendizado ainda não comitado da outra -- exatamente o comportamento
    de concorrência já documentado no docstring de módulo de
    `identity_learning.py` (duas missões concorrentes podem legitimamente
    aprender o mesmo identity_key ao mesmo tempo). Para provar reaproveitamento
    de uma ocorrência JÁ aprendida (não uma corrida entre dois aprendizados
    simultâneos), a segunda loja precisa ser processada numa `run_batch`
    posterior, depois que a primeira já terminou e comitou."""
    now = datetime.now(UTC).replace(microsecond=0)
    later = now + timedelta(minutes=1)
    settings = Settings(_env_file=None, product_identity_learning_enabled=True)
    ai_manager = _IdentityLearningPipelineAIManager(_PIPELINE_MOBO_DDR5_EXTRACTION)

    mission_amazon, amazon_id = _seed_due_mission_for_store(
        integration_database.sessions, now, store_code="amazon"
    )
    mission_kabum, kabum_id = _seed_due_mission_for_store(
        integration_database.sessions, later, store_code="kabum"
    )
    amazon_provider = _CommercialStateOfferProvider(
        "amazon",
        "R$ 1.299,90",
        title="ASUS TUF Gaming B650-Plus WiFi DDR5 AM5",
        url="https://example.invalid/identity-pipeline-mobo-amazon",
        external_id="identity-pipeline-mobo-amazon",
    )
    kabum_provider = _CommercialStateOfferProvider(
        "kabum",
        "R$ 1.279,90",
        title="ASUS TUF GAMING B650-PLUS (WI-FI) DDR5 - Chipset AMD B650, Socket AM5",
        url="https://example.invalid/identity-pipeline-mobo-kabum",
        external_id="identity-pipeline-mobo-kabum",
    )
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((amazon_provider, kabum_provider)),
        ai_manager=ai_manager,
        settings=settings,
    )
    # Duas chamadas SEQUENCIAIS de `run_batch` (nunca a mesma) -- a
    # primeira, em `now`, só a missão Amazon está due (Kabum só fica due
    # em `later`); ela aprende via IA e comita o candidato aprovado. A
    # segunda, em `later`, é quando a missão Kabum passa a estar due --
    # a Amazon já teve seu `next_run_at` avançado pelo agendamento
    # (interval_minutes=60) e não é reclaimada. Só assim a Fase B da
    # segunda chamada enxerga o candidato JÁ comitado pela primeira.
    asyncio.run(orchestrator.run_batch(now=now))
    asyncio.run(orchestrator.run_batch(now=later))

    assert ai_manager.identity_calls == 1, (
        "a segunda loja (wording diferente da MESMA placa-mãe) precisa "
        "reaproveitar o candidato já aprovado pela primeira -- nunca uma "
        "segunda chamada de IA -- tudo isso decidido dentro da Fase B do "
        "orquestrador real, não de uma chamada direta ao mecanismo"
    )

    with integration_database.sessions.begin() as session:
        amazon_offer = session.scalar(
            select(Offer).where(Offer.external_id == "identity-pipeline-mobo-amazon")
        )
        kabum_offer = session.scalar(
            select(Offer).where(Offer.external_id == "identity-pipeline-mobo-kabum")
        )
        assert amazon_offer is not None and kabum_offer is not None
        amazon_product = session.get(Product, amazon_offer.product_id)
        kabum_product = session.get(Product, kabum_offer.product_id)
        assert amazon_product is not None and kabum_product is not None
        assert amazon_product.identity_key is not None
        assert amazon_product.identity_key == kabum_product.identity_key, (
            "as duas lojas descrevem a MESMA placa-mãe -- precisam "
            "convergir para o mesmo identity_key, nunca produtos "
            "separados só por causa do wording diferente do título"
        )
        candidates = list(session.scalars(select(ProductIdentityCandidate)))
        assert len(candidates) == 2, (
            "um candidato por título normalizado distinto (hash de título "
            "diferente por loja), mesmo reaproveitando o identity_key"
        )


def test_identity_learning_reuses_across_a_brand_new_store_never_seen_before(
    integration_database,
) -> None:
    """Checkpoint 3, item de desacoplamento de loja/categoria: "magalu"
    nunca aparece em nenhum outro teste de aprendizado de identidade
    (só amazon/kabum) -- prova que o mecanismo é genérico de verdade,
    nunca dependente de nenhuma loja específica. `resolve_or_learn_
    product_variant` nem recebe o código da loja como parâmetro; o
    reaproveitamento por tokens funciona para QUALQUER loja com um
    título que descreva a MESMA família/modelo/variante, tudo através
    do orquestrador real (missões de usuários diferentes, `run_batch`
    sequenciais -- mesma disciplina de `test_identity_learning_reuses_
    across_stores_through_the_real_orchestrator`)."""
    now = datetime.now(UTC).replace(microsecond=0)
    later = now + timedelta(minutes=1)
    settings = Settings(_env_file=None, product_identity_learning_enabled=True)
    ai_manager = _IdentityLearningPipelineAIManager(_PIPELINE_MOBO_DDR5_EXTRACTION)

    mission_amazon, amazon_id = _seed_due_mission_for_store(
        integration_database.sessions, now, store_code="amazon"
    )
    mission_magalu, magalu_id = _seed_due_mission_for_store(
        integration_database.sessions, later, store_code="magalu"
    )
    amazon_provider = _CommercialStateOfferProvider(
        "amazon",
        "R$ 1.299,90",
        title="ASUS TUF Gaming B650-Plus WiFi DDR5 AM5",
        url="https://example.invalid/identity-pipeline-mobo-amazon-2",
        external_id="identity-pipeline-mobo-amazon-2",
    )
    magalu_provider = _CommercialStateOfferProvider(
        "magalu",
        "R$ 1.319,90",
        title="ASUS TUF GAMING B650-PLUS (WI-FI) DDR5 - Chipset AMD B650, Socket AM5",
        url="https://example.invalid/identity-pipeline-mobo-magalu",
        external_id="identity-pipeline-mobo-magalu",
    )
    orchestrator = CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter((amazon_provider, magalu_provider)),
        ai_manager=ai_manager,
        settings=settings,
    )
    asyncio.run(orchestrator.run_batch(now=now))
    asyncio.run(orchestrator.run_batch(now=later))

    assert ai_manager.identity_calls == 1, (
        "magalu (loja nunca usada em nenhum outro teste de identidade) "
        "precisa reaproveitar o candidato já aprovado pela amazon -- "
        "nenhum código específico de loja deveria impedir isso"
    )

    with integration_database.sessions.begin() as session:
        amazon_offer = session.scalar(
            select(Offer).where(Offer.external_id == "identity-pipeline-mobo-amazon-2")
        )
        magalu_offer = session.scalar(
            select(Offer).where(Offer.external_id == "identity-pipeline-mobo-magalu")
        )
        assert amazon_offer is not None and magalu_offer is not None
        amazon_product = session.get(Product, amazon_offer.product_id)
        magalu_product = session.get(Product, magalu_offer.product_id)
        assert amazon_product is not None and magalu_product is not None
        assert amazon_product.identity_key is not None
        assert amazon_product.identity_key == magalu_product.identity_key, (
            "amazon e magalu descrevem a MESMA placa-mãe -- precisam "
            "convergir para o mesmo identity_key, provando que o "
            "reaproveitamento nunca depende de qual loja específica "
            "coletou o título"
        )
