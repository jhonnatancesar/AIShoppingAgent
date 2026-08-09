"""Fluxo real de agenda, coleta, persistência e eventos da TASK-062."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from app.collection.adapter import CollectionAdapter
from app.collection.contracts import (
    CollectionRequest,
    CollectionResult,
    RawCollectedOffer,
)
from app.collection.errors import ProviderBlockedError
from app.collection.models import CollectionRun, CollectionRunStatus, PriceObservation
from app.collection.orchestration import CollectionOrchestrator, claim_due_collections
from app.events import Event, EventType
from app.missions.models import (
    Mission,
    MissionCriteria,
    MissionSchedule,
    MissionSource,
    MissionStatus,
)
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy import func, select

pytestmark = pytest.mark.integration


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


def test_orchestrator_isolates_source_failure_and_publishes_real_events(
    integration_database,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, pichau_id, kabum_id = _seed_due_mission(
        integration_database.sessions, now
    )
    orchestrator = CollectionOrchestrator(
        integration_database.sessions,
        CollectionAdapter((_SuccessfulProvider(), _FailingProvider())),
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
        }
        failed = session.scalar(
            select(Event).where(
                Event.mission_id == mission_id,
                Event.event_type == EventType.COLLECTION_FAILED_V1.value,
            )
        )
        assert failed is not None
        assert failed.payload["failure_code"] == "provider_blocked"

    assert asyncio.run(orchestrator.run_batch(now=now)).claimed == 0


def test_concurrent_claimers_never_duplicate_a_source(integration_database) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    mission_id, _, _ = _seed_due_mission(integration_database.sessions, now)
    barrier = Barrier(2)

    def claim() -> tuple:
        with integration_database.sessions.begin() as session:
            barrier.wait(timeout=10)
            return claim_due_collections(session, now=now)

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
        with integration_database.sessions.begin() as session:
            schedule = session.scalar(
                select(MissionSchedule).where(MissionSchedule.mission_id == mission_id)
            )
            schedule.next_run_at = due_at

    def _expire_backoff(store_id, due_at):
        with integration_database.sessions.begin() as session:
            source = session.get(MissionSource, (mission_id, store_id))
            source.next_eligible_at = due_at - timedelta(seconds=1)

    orchestrator = CollectionOrchestrator(
        integration_database.sessions,
        CollectionAdapter((_SuccessfulProvider(), _FailingProvider())),
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
        integration_database.sessions,
        CollectionAdapter((_SuccessfulProvider(), _FailingProvider())),
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
        integration_database.sessions,
        CollectionAdapter((_EmptyProvider(),)),
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
