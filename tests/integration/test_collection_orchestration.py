"""Fluxo real de agenda, coleta, persistência e eventos da TASK-062."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
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
