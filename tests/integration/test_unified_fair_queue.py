"""Fila de fairness unificada entre o caminho antigo (por Mission) e o
caminho novo (por MonitoringItem) -- TASK-112, fase 3B.

Prova, contra PostgreSQL real: missão vinculada nunca entra no seletor
legado; `claim_due_collections` nunca produz efeito compartilhado;
riders nunca têm o próprio `UserCollectionQueueState` avançado; dono
reservado sem claim real não recebe cooldown; owner do caminho
orquestrado nunca é `NULL`, o standalone pode ser; ausência de deadlock
sob concorrência real cruzando os dois caminhos.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from app.collection.adapter import CollectionAdapter
from app.collection.cadence import CadenceConfig
from app.collection.contracts import (
    CollectionRequest,
    CollectionResult,
    RawCollectedOffer,
)
from app.collection.fairness import _reserve_fairness_owners
from app.collection.models import (
    CollectionRun,
    StoreActivityState,
    UserCollectionQueueState,
)
from app.collection.orchestration import (
    CollectionOrchestrator,
    _select_due_work_for_batch,
    claim_due_collections,
    claim_due_work,
)
from app.collection.shared_collection import collect_monitoring_item_store
from app.database.session import (
    create_async_session_factory,
    create_collection_async_database_engine,
)
from app.missions.models import (
    Mission,
    MissionCriteria,
    MissionMonitoringItem,
    MissionSchedule,
    MissionSource,
    MissionStatus,
)
from app.missions.service import create_mission_from_criteria_async
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy import select

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
_DETERMINISTIC_CADENCE = CadenceConfig(normal_min_minutes=60, normal_max_minutes=60)


class _StubAIManager:
    async def generate(self, request):
        from app.ai_provider import AIResponse

        content = (
            '{"relevance": "match"}'
            if request.purpose == "classify_offer_relevance"
            else '{"display_title": "Produto"}'
        )
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub",
            content=content,
            finished_at=datetime.now(UTC),
        )


class _CountingGpuProvider:
    source_code = "amazon"

    def __init__(self) -> None:
        self.calls: list[CollectionRequest] = []

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        self.calls.append(request)
        completed = request.requested_at.replace(microsecond=500000)
        return CollectionResult(
            self.source_code,
            request.requested_at,
            completed,
            (
                RawCollectedOffer(
                    source_code=self.source_code,
                    url="https://example.invalid/unified-gpu",
                    title="NVIDIA GeForce RTX 5070 Ti",
                    collected_at=completed,
                    external_id="unified-gpu-stable",
                    raw_price="3999.90",
                    raw_currency="BRL",
                    raw_availability="Em estoque",
                ),
            ),
        )


class _NoOpProvider:
    """Nunca deveria ser chamado -- provider de fonte que não participa."""

    source_code = "kabum"

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        raise AssertionError("kabum não deveria ser coletada neste teste")


def _adapter(*providers) -> CollectionAdapter:
    return CollectionAdapter(providers=providers)


def _run(coro):
    return asyncio.run(coro)


def _seed_user(sessions, label: str) -> UUID:
    with sessions.begin() as session:
        user = User(display_name=f"TASK-112-3B {label}", role=UserRole.USER)
        session.add(user)
        session.flush()
        return user.id


def _store_id(integration_database, code: str) -> UUID:
    with integration_database.sessions() as session:
        return session.scalar(select(Store.id).where(Store.code == code))


def _make_shared_mission(integration_database, user_id: UUID, *, search_query: str) -> Mission:
    async def run():
        async with integration_database.async_sessions.begin() as session:
            mission, _codes = await create_mission_from_criteria_async(
                session,
                user_id=user_id,
                search_query=search_query,
                target_amount=None,
                target_currency=None,
                source_codes=("amazon",),
                requested_at=NOW,
                actor_type="test",
            )
            return mission

    return _run(run())


def _monitoring_item_id_for(sessions, mission_id: UUID) -> UUID | None:
    with sessions() as session:
        link = session.get(MissionMonitoringItem, mission_id)
        return link.monitoring_item_id if link is not None else None


def _make_legacy_mission(
    integration_database, user_id: UUID, *, due_at: datetime, store_id: UUID, label: str
) -> Mission:
    """Missão SEM vínculo -- `monitoring_key=None` (busca deliberadamente
    não reconhecível pelo Product Identity Engine), continua no caminho
    antigo para sempre."""
    with integration_database.sessions.begin() as session:
        mission = Mission(
            user_id=user_id, title=f"legacy {label}", status=MissionStatus.ACTIVE
        )
        session.add(mission)
        session.flush()
        session.add_all(
            (
                MissionCriteria(
                    mission_id=mission.id,
                    search_query=f"item genérico sem identidade {label}",
                ),
                MissionSchedule(
                    mission_id=mission.id,
                    interval_minutes=60,
                    next_run_at=due_at,
                    is_enabled=True,
                ),
                MissionSource(mission_id=mission.id, store_id=store_id),
            )
        )
        return mission


def _queue_state(sessions, user_id: UUID) -> UserCollectionQueueState | None:
    with sessions() as session:
        return session.get(UserCollectionQueueState, user_id)


def _seed_cooldown(sessions, user_id: UUID, *, next_eligible_at: datetime) -> None:
    with sessions.begin() as session:
        session.add(
            UserCollectionQueueState(
                user_id=user_id,
                last_processed_at=NOW - timedelta(hours=1),
                next_eligible_at=next_eligible_at,
            )
        )


# ---------------------------------------------------------------------------
# 1: Mission vinculada nunca entra no seletor legado; caminho compartilhado
# a atende exatamente uma vez.
# ---------------------------------------------------------------------------


def test_linked_mission_excluded_from_legacy_selector(integration_database) -> None:
    user_id = _seed_user(integration_database.sessions, "linked")
    mission = _make_shared_mission(integration_database, user_id, search_query="RTX 5070 Ti")
    item_id = _monitoring_item_id_for(integration_database.sessions, mission.id)
    assert item_id is not None

    async def run():
        async with integration_database.async_sessions() as session:
            batch = await claim_due_work(
                session,
                now=NOW,
                limit=25,
                max_users=5,
                cadence_config=_DETERMINISTIC_CADENCE,
            )
            await session.commit()
            return batch

    batch = _run(run())
    assert batch.old_path == ()
    assert len(batch.shared) == 1
    assert batch.shared[0].monitoring_item_id == item_id

    # `claim_due_collections` (legado, intocado) nunca vê esta Mission,
    # mesmo tendo `MissionSchedule` fisicamente presente.
    async def run_legacy():
        async with integration_database.async_sessions() as session:
            claims = await claim_due_collections(session, now=NOW, limit=25, max_users=5)
            await session.commit()
            return claims

    legacy_claims = _run(run_legacy())
    assert legacy_claims == ()


# ---------------------------------------------------------------------------
# DEC-129: notificação best-effort ao Coupon Worker quando `claim_due_work`
# (scheduler REAL de produção) decide HIGH_ACTIVITY no caminho
# COMPARTILHADO (`_advance_monitoring_item_store`, `shared_claim.py`) --
# mesmo sinal do teste equivalente no caminho legado
# (`test_legacy_source_level_cadence.py`).
# ---------------------------------------------------------------------------


def test_shared_high_activity_notifies_coupon_worker_when_settings_configured(
    integration_database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import Settings

    user_id = _seed_user(integration_database.sessions, "sharednotify")
    mission = _make_shared_mission(integration_database, user_id, search_query="RTX 5070 Ti")
    item_id = _monitoring_item_id_for(integration_database.sessions, mission.id)
    assert item_id is not None
    amazon_id = _store_id(integration_database, "amazon")
    with integration_database.sessions.begin() as session:
        session.add(
            StoreActivityState(
                store_id=amazon_id,
                scope_id=item_id,
                high_activity_until=NOW + timedelta(hours=2),
            )
        )

    calls: list[datetime] = []

    async def fake_notify(settings, *, now):
        calls.append(now)

    monkeypatch.setattr(
        "app.collection.shared_claim.notify_coupon_worker_high_activity", fake_notify
    )

    async def run():
        async with integration_database.async_sessions() as session:
            batch = await claim_due_work(
                session,
                now=NOW,
                limit=25,
                max_users=5,
                cadence_config=_DETERMINISTIC_CADENCE,
                settings=Settings(
                    coupon_worker_control_url="http://127.0.0.1:8090",
                    coupon_worker_control_token_file=None,
                ),
            )
            await session.commit()
            return batch

    batch = _run(run())
    assert len(batch.shared) == 1
    assert calls == [NOW]


def test_shared_high_activity_never_notifies_without_settings(
    integration_database, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = _seed_user(integration_database.sessions, "sharednotifyoff")
    mission = _make_shared_mission(integration_database, user_id, search_query="RTX 5070 Ti")
    item_id = _monitoring_item_id_for(integration_database.sessions, mission.id)
    assert item_id is not None
    amazon_id = _store_id(integration_database, "amazon")
    with integration_database.sessions.begin() as session:
        session.add(
            StoreActivityState(
                store_id=amazon_id,
                scope_id=item_id,
                high_activity_until=NOW + timedelta(hours=2),
            )
        )

    notify = AsyncMock()
    monkeypatch.setattr(
        "app.collection.shared_claim.notify_coupon_worker_high_activity", notify
    )

    async def run():
        async with integration_database.async_sessions() as session:
            batch = await claim_due_work(
                session, now=NOW, limit=25, max_users=5, cadence_config=_DETERMINISTIC_CADENCE
            )
            await session.commit()
            return batch

    batch = _run(run())
    assert len(batch.shared) == 1
    notify.assert_not_called()


# ---------------------------------------------------------------------------
# 2: claim_due_collections nunca produz efeito shared, mesmo com trabalho
# shared devido ao mesmo tempo.
# ---------------------------------------------------------------------------


def test_claim_due_collections_never_creates_shared_side_effects(integration_database) -> None:
    linked_user = _seed_user(integration_database.sessions, "cdc-linked")
    linked_mission = _make_shared_mission(
        integration_database, linked_user, search_query="RTX 5070 Ti"
    )
    item_id = _monitoring_item_id_for(integration_database.sessions, linked_mission.id)
    assert item_id is not None

    legacy_user = _seed_user(integration_database.sessions, "cdc-legacy")
    pichau_id = _store_id(integration_database, "pichau")
    _make_legacy_mission(
        integration_database, legacy_user, due_at=NOW, store_id=pichau_id, label="cdc"
    )

    async def run():
        async with integration_database.async_sessions() as session:
            claims = await claim_due_collections(session, now=NOW, limit=25, max_users=5)
            await session.commit()
            return claims

    claims = _run(run())
    assert len(claims) == 1
    assert claims[0].mission_id != linked_mission.id  # só o legado foi claimado

    with integration_database.sessions() as session:
        shared_runs = list(
            session.scalars(
                select(CollectionRun).where(CollectionRun.monitoring_item_id == item_id)
            )
        )
        assert shared_runs == []  # zero CollectionRun compartilhada criada


# ---------------------------------------------------------------------------
# 3: dono do caminho compartilhado, rider em cooldown -- rider nunca tem o
# próprio UserCollectionQueueState avançado.
# ---------------------------------------------------------------------------


def test_rider_queue_state_never_touched_while_owner_advances(integration_database) -> None:
    owner_id = _seed_user(integration_database.sessions, "owner")
    rider_id = _seed_user(integration_database.sessions, "rider")
    owner_mission = _make_shared_mission(integration_database, owner_id, search_query="RTX 5070 Ti")
    rider_mission = _make_shared_mission(
        integration_database, rider_id, search_query="NVIDIA GeForce RTX 5070 Ti"
    )
    item_id = _monitoring_item_id_for(integration_database.sessions, owner_mission.id)
    assert item_id == _monitoring_item_id_for(integration_database.sessions, rider_mission.id)

    rider_next_eligible = NOW + timedelta(hours=3)
    _seed_cooldown(integration_database.sessions, rider_id, next_eligible_at=rider_next_eligible)

    async def run():
        async with integration_database.async_sessions() as session:
            batch = await claim_due_work(
                session, now=NOW, limit=25, max_users=5, cadence_config=_DETERMINISTIC_CADENCE
            )
            await session.commit()
            return batch

    batch = _run(run())
    assert len(batch.shared) == 1
    assert batch.shared[0].monitoring_item_id == item_id

    owner_state = _queue_state(integration_database.sessions, owner_id)
    assert owner_state is not None
    assert owner_state.last_processed_at == NOW
    assert owner_state.last_fairness_turn_id is not None

    rider_state = _queue_state(integration_database.sessions, rider_id)
    assert rider_state is not None
    assert rider_state.next_eligible_at == rider_next_eligible  # intocado


# ---------------------------------------------------------------------------
# 4: todos os vinculados em cooldown -- claim compartilhado não é
# selecionado neste ciclo.
# ---------------------------------------------------------------------------


def test_shared_claim_not_selected_when_all_linked_users_in_cooldown(integration_database) -> None:
    user_a = _seed_user(integration_database.sessions, "allcooldown-a")
    user_b = _seed_user(integration_database.sessions, "allcooldown-b")
    mission_a = _make_shared_mission(integration_database, user_a, search_query="RTX 5070 Ti")
    mission_b = _make_shared_mission(
        integration_database, user_b, search_query="NVIDIA GeForce RTX 5070 Ti"
    )
    item_id = _monitoring_item_id_for(integration_database.sessions, mission_a.id)
    assert item_id == _monitoring_item_id_for(integration_database.sessions, mission_b.id)

    _seed_cooldown(integration_database.sessions, user_a, next_eligible_at=NOW + timedelta(hours=1))
    _seed_cooldown(integration_database.sessions, user_b, next_eligible_at=NOW + timedelta(hours=1))

    async def run():
        async with integration_database.async_sessions() as session:
            batch = await claim_due_work(
                session, now=NOW, limit=25, max_users=5, cadence_config=_DETERMINISTIC_CADENCE
            )
            await session.commit()
            return batch

    batch = _run(run())
    assert batch.shared == ()
    assert batch.old_path == ()

    with integration_database.sessions() as session:
        shared_runs = list(
            session.scalars(
                select(CollectionRun).where(CollectionRun.monitoring_item_id == item_id)
            )
        )
        assert shared_runs == []


# ---------------------------------------------------------------------------
# 5: standalone (fora do orchestrator) pode ter owner NULL, nunca toca
# UserCollectionQueueState; orquestrado sempre tem owner.
# ---------------------------------------------------------------------------


def test_standalone_shared_collection_allows_null_owner_without_touching_queue_state(
    integration_database,
) -> None:
    user_id = _seed_user(integration_database.sessions, "standalone")
    mission = _make_shared_mission(integration_database, user_id, search_query="RTX 5070 Ti")
    item_id = _monitoring_item_id_for(integration_database.sessions, mission.id)
    amazon_id = _store_id(integration_database, "amazon")
    provider = _CountingGpuProvider()

    result = _run(
        collect_monitoring_item_store(
            integration_database.async_sessions,
            _adapter(provider),
            _StubAIManager(),
            monitoring_item_id=item_id,
            store_id=amazon_id,
            now=NOW,
            cadence_config=_DETERMINISTIC_CADENCE,
            # fairness_owner_user_id NÃO passado -- contrato standalone.
        )
    )
    assert result.claimed is True
    assert result.succeeded is True

    with integration_database.sessions() as session:
        run = session.scalar(
            select(CollectionRun).where(CollectionRun.monitoring_item_id == item_id)
        )
        assert run.fairness_owner_user_id is None

    assert _queue_state(integration_database.sessions, user_id) is None


def test_orchestrated_shared_claim_always_has_non_null_owner(integration_database) -> None:
    user_id = _seed_user(integration_database.sessions, "orchestrated")
    mission = _make_shared_mission(integration_database, user_id, search_query="RTX 5070 Ti")
    item_id = _monitoring_item_id_for(integration_database.sessions, mission.id)

    async def run():
        async with integration_database.async_sessions() as session:
            batch = await claim_due_work(
                session, now=NOW, limit=25, max_users=5, cadence_config=_DETERMINISTIC_CADENCE
            )
            await session.commit()
            return batch

    batch = _run(run())
    assert len(batch.shared) == 1

    with integration_database.sessions() as session:
        run = session.scalar(
            select(CollectionRun).where(CollectionRun.monitoring_item_id == item_id)
        )
        assert run.fairness_owner_user_id == user_id


# ---------------------------------------------------------------------------
# 6: dono reservado, mas a única loja disponível está em throttle global --
# zero claim real, zero avanço de cooldown.
# ---------------------------------------------------------------------------


def test_reserved_owner_with_zero_real_claims_never_advances_cooldown(integration_database) -> None:
    from app.collection.fairness import _advance_store_throttle

    user_id = _seed_user(integration_database.sessions, "zeroclaim")
    mission = _make_shared_mission(integration_database, user_id, search_query="RTX 5070 Ti")
    amazon_id = _store_id(integration_database, "amazon")

    async def seed_throttle():
        async with integration_database.async_sessions() as session:
            await _advance_store_throttle(
                session, store_id=amazon_id, claimed_at=NOW, min_interval_seconds=3600.0
            )
            await session.commit()

    _run(seed_throttle())

    async def run():
        async with integration_database.async_sessions() as session:
            batch = await claim_due_work(
                session, now=NOW, limit=25, max_users=5, cadence_config=_DETERMINISTIC_CADENCE
            )
            await session.commit()
            return batch

    batch = _run(run())
    assert batch.shared == ()
    # A linha existe (criada na Fase 1 -- `_ensure_queue_state_rows_exist`,
    # pré-requisito para travar via FOR UPDATE), mas nenhum campo é
    # populado -- `_commit_fairness_turn_for_owner` só roda para donos com
    # >= 1 claim real, que não é o caso aqui (throttle bloqueou a única
    # loja). Equivalente a "nunca processado" (`queue_sort_key` trata
    # `last_processed_at IS NULL` da mesma forma que ausência de linha).
    state = _queue_state(integration_database.sessions, user_id)
    assert state is not None
    assert state.last_processed_at is None
    assert state.next_eligible_at is None
    assert state.last_fairness_turn_id is None


# ---------------------------------------------------------------------------
# 7: CollectionBatchResult totaliza os dois caminhos.
# ---------------------------------------------------------------------------


def test_collection_batch_result_totals_include_both_paths(integration_database) -> None:
    shared_user = _seed_user(integration_database.sessions, "totals-shared")
    _make_shared_mission(integration_database, shared_user, search_query="RTX 5070 Ti")
    legacy_user = _seed_user(integration_database.sessions, "totals-legacy")
    pichau_id = _store_id(integration_database, "pichau")
    _make_legacy_mission(
        integration_database, legacy_user, due_at=NOW, store_id=pichau_id, label="totals"
    )

    engine = create_collection_async_database_engine(integration_database.settings)
    session_factory = create_async_session_factory(engine)

    async def run():
        orchestrator = CollectionOrchestrator(
            session_factory,
            _adapter(_CountingGpuProvider(), _EmptyPichauProvider()),
            ai_manager=_StubAIManager(),
            max_concurrent_user_batches=5,
            cadence_config=_DETERMINISTIC_CADENCE,
        )
        try:
            return await orchestrator.run_batch(now=NOW, limit=25)
        finally:
            await engine.dispose()

    result = _run(run())
    assert result.legacy_claimed == 1
    assert result.shared_claimed == 1
    assert result.claimed == result.legacy_claimed + result.shared_claimed
    assert result.succeeded == result.legacy_succeeded + result.shared_succeeded
    assert result.failed == result.legacy_failed + result.shared_failed


class _EmptyPichauProvider:
    source_code = "pichau"

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        return CollectionResult(self.source_code, request.requested_at, request.requested_at)


# ---------------------------------------------------------------------------
# 8: concorrência real -- dois workers processando claim_due_work ao mesmo
# tempo, trabalho cruzando os dois caminhos, nunca duplica claim nem trava.
# ---------------------------------------------------------------------------


def test_concurrent_claim_due_work_never_double_claims_and_never_deadlocks(
    integration_database,
) -> None:
    shared_user = _seed_user(integration_database.sessions, "conc-shared")
    shared_mission = _make_shared_mission(
        integration_database, shared_user, search_query="RTX 5070 Ti"
    )
    item_id = _monitoring_item_id_for(integration_database.sessions, shared_mission.id)
    legacy_user = _seed_user(integration_database.sessions, "conc-legacy")
    pichau_id = _store_id(integration_database, "pichau")
    legacy_mission = _make_legacy_mission(
        integration_database, legacy_user, due_at=NOW, store_id=pichau_id, label="conc"
    )

    barrier = Barrier(2)

    def _claim(_index: int):
        async def _claim_async():
            engine = create_collection_async_database_engine(integration_database.settings)
            sessions = create_async_session_factory(engine)
            try:
                async with sessions() as session, session.begin():
                    barrier.wait(timeout=10)
                    return await claim_due_work(
                        session, now=NOW, limit=25, max_users=5,
                        cadence_config=_DETERMINISTIC_CADENCE,
                    )
            finally:
                await engine.dispose()

        return asyncio.run(_claim_async())

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(_claim, range(2)))

    total_old_path = sum(len(batch.old_path) for batch in results)
    total_shared = sum(len(batch.shared) for batch in results)
    assert total_old_path == 1  # legacy_mission claimado exatamente 1x no total
    assert total_shared == 1  # shared item claimado exatamente 1x no total

    with integration_database.sessions() as session:
        legacy_runs = list(
            session.scalars(
                select(CollectionRun).where(CollectionRun.mission_id == legacy_mission.id)
            )
        )
        shared_runs = list(
            session.scalars(
                select(CollectionRun).where(CollectionRun.monitoring_item_id == item_id)
            )
        )
        assert len(legacy_runs) == 1
        assert len(shared_runs) == 1


# ---------------------------------------------------------------------------
# 9: revalidação de cooldown SOB O LOCK -- a seleção é somente leitura e
# stale-tolerant por desenho; "estava elegível na seleção" não pode ser
# suficiente. Confirma que `_reserve_fairness_owners` rejeita um candidato
# que deixou de ser elegível DEPOIS da seleção mas ANTES da reserva.
# ---------------------------------------------------------------------------


def test_reservation_rechecks_cooldown_under_lock_after_stale_selection(
    integration_database,
) -> None:
    owner_id = _seed_user(integration_database.sessions, "recheck")
    _make_shared_mission(integration_database, owner_id, search_query="RTX 5070 Ti")

    async def run():
        async with integration_database.async_sessions() as session, session.begin():
            # 1. Seleção somente leitura -- lê o estado ATUAL (owner
            #    elegível, sem cooldown nenhum ainda).
            selected = await _select_due_work_for_batch(
                session, due_at=NOW, limit=25, max_users=5, candidate_scan_limit=1000
            )
            assert owner_id in selected.owner_ids

            # 2. Simula "outro run_batch colocou o dono em cooldown ENTRE a
            #    seleção e a reserva" -- muda o estado real no banco, na
            #    MESMA sessão/transação (equivalente a outra transação já
            #    ter commitado isso antes desta chegar à reserva).
            await session.execute(
                UserCollectionQueueState.__table__.insert().values(
                    user_id=owner_id,
                    next_eligible_at=NOW + timedelta(hours=1),
                    updated_at=NOW,
                )
            )

            # 3. Reserva DEVE revalidar sob o lock -- nunca confiar na
            #    seleção otimista de before.
            reserved = await _reserve_fairness_owners(
                session, candidate_owners=set(selected.owner_ids), due_at=NOW
            )
            return reserved

    reserved = _run(run())
    assert owner_id not in reserved
