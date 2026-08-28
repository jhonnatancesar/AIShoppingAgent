"""Cadência individual por `(Mission, Store)` no caminho legado -- TASK-112,
fase 3B, correção estrutural pós-auditoria.

Achado corrigido: o desenho anterior desta fase reagendava
`MissionSchedule` (agenda por MISSÃO INTEIRA) pela decisão de cadência
MAIS CEDO entre as lojas recém-claimadas -- uma Mission com KaBuM em
NORMAL (45-75min) e Amazon em HIGH_ACTIVITY (30-45min) fazia KaBuM ser
recoletada bem antes da sua própria cadência só porque a Mission
"acordava" para atender Amazon. Corrigido: `MissionSource` (não
`MissionSchedule`) é a unidade autoritativa de cadência do caminho
legado, exatamente como `MonitoringItemStore` já é para o caminho
compartilhado -- cada `(mission, store)` tem sua PRÓPRIA `next_run_at`.

Estes testes provam PROVIDER CALL real (via `CollectionOrchestrator.
run_batch`, que executa claim + rede + persistência de verdade), não só
`next_run_at` calculado -- por pedido explícito: "quero provar que a
store NORMAL realmente não foi acessada".
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID

import pytest
from app.collection.adapter import CollectionAdapter
from app.collection.cadence import CadenceConfig
from app.collection.contracts import CollectionRequest, CollectionResult, RawCollectedOffer
from app.collection.models import CollectionRun, StoreActivityState, UserCollectionQueueState
from app.collection.orchestration import CollectionOrchestrator, claim_due_collections
from app.database.session import (
    create_async_session_factory,
    create_collection_async_database_engine,
)
from app.missions.models import (
    Mission,
    MissionCriteria,
    MissionSchedule,
    MissionSource,
    MissionStatus,
)
from app.missions.service import transition_mission_async
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy import select

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
# Determinístico: NORMAL sempre 60min, PROMO/HIGH_ACTIVITY sempre 30min --
# elimina o jitter (`random.uniform`) real da política para asserts exatos.
_DETERMINISTIC_CADENCE = CadenceConfig(
    normal_min_minutes=60,
    normal_max_minutes=60,
    promo_min_minutes=30,
    promo_max_minutes=30,
    high_activity_window_minutes=30,
    high_activity_change_threshold=3,
    high_activity_duration_minutes=180,
)


class _CountingProvider:
    def __init__(self, source_code: str) -> None:
        self.source_code = source_code
        self.calls = 0

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        self.calls += 1
        completed = request.requested_at.replace(microsecond=500000)
        return CollectionResult(
            self.source_code,
            request.requested_at,
            completed,
            (
                RawCollectedOffer(
                    source_code=self.source_code,
                    url=f"https://example.invalid/{self.source_code}-{self.calls}",
                    title="Synthetic GPU",
                    collected_at=completed,
                    external_id=f"{self.source_code}-stable",
                    raw_price="1999.90",
                    raw_currency="BRL",
                    raw_availability="Em estoque",
                ),
            ),
        )


class _BlockedProvider:
    """Bloqueio externo confirmado (DEC-046) -- sempre 403."""

    def __init__(self, source_code: str) -> None:
        self.source_code = source_code
        self.calls = 0

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        self.calls += 1
        from app.collection.errors import ProviderBlockedError

        raise ProviderBlockedError(self.source_code, 403)


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


def _run(coro):
    return asyncio.run(coro)


def _store_id(integration_database, code: str) -> UUID:
    with integration_database.sessions() as session:
        return session.scalar(select(Store.id).where(Store.code == code))


def _seed_user(sessions, label: str) -> UUID:
    with sessions.begin() as session:
        user = User(display_name=f"TASK-112-legacy-cadence {label}", role=UserRole.USER)
        session.add(user)
        session.flush()
        return user.id


def _seed_mission(
    integration_database,
    user_id: UUID,
    *,
    label: str,
    sources: dict[UUID, datetime | None],
) -> UUID:
    """`sources`: `{store_id: next_run_at}` -- `None` = elegível desde já
    (primeira coleta), qualquer outro valor simula uma cadência já
    estabelecida por um ciclo anterior."""
    with integration_database.sessions.begin() as session:
        mission = Mission(user_id=user_id, title=f"legacy cadence {label}", status=MissionStatus.ACTIVE)
        session.add(mission)
        session.flush()
        session.add(
            MissionCriteria(mission_id=mission.id, search_query=f"item genérico sem identidade {label}")
        )
        session.add(
            MissionSchedule(mission_id=mission.id, interval_minutes=60, next_run_at=NOW, is_enabled=True)
        )
        for store_id, next_run_at in sources.items():
            session.add(MissionSource(mission_id=mission.id, store_id=store_id, next_run_at=next_run_at))
        return mission.id


def _mission_source(sessions, mission_id: UUID, store_id: UUID) -> MissionSource:
    with sessions() as session:
        return session.get(MissionSource, (mission_id, store_id))


def _make_orchestrator(integration_database, *providers, cadence_config=_DETERMINISTIC_CADENCE) -> CollectionOrchestrator:
    return CollectionOrchestrator(
        integration_database.async_sessions,
        CollectionAdapter(providers=providers),
        ai_manager=_StubAIManager(),
        cadence_config=cadence_config,
        store_min_interval_seconds=0.0,
        user_cooldown_min_seconds=0.0,
        user_cooldown_max_seconds=0.0,
        max_concurrent_user_batches=5,
    )


# ---------------------------------------------------------------------------
# Caso 1: duas stores, cadências diferentes -- a mais rápida (HIGH_ACTIVITY)
# nunca acelera a mais lenta (NORMAL) da MESMA Mission.
# ---------------------------------------------------------------------------


def test_faster_store_never_accelerates_slower_store_same_mission(integration_database) -> None:
    amazon_id = _store_id(integration_database, "amazon")
    kabum_id = _store_id(integration_database, "kabum")
    user_id = _seed_user(integration_database.sessions, "caso1")
    mission_id = _seed_mission(
        integration_database,
        user_id,
        label="caso1",
        sources={amazon_id: NOW, kabum_id: NOW + timedelta(minutes=25)},
    )
    # Amazon em HIGH_ACTIVITY (30min determinístico); KaBuM permanece NORMAL
    # (60min determinístico, sem PromotionalWindow/StoreActivityState).
    with integration_database.sessions.begin() as session:
        session.add(
            StoreActivityState(
                store_id=amazon_id,
                scope_id=mission_id,
                high_activity_until=NOW + timedelta(hours=2),
            )
        )

    amazon_provider = _CountingProvider("amazon")
    kabum_provider = _CountingProvider("kabum")
    orchestrator = _make_orchestrator(integration_database, amazon_provider, kabum_provider)

    # Ciclo 1 @ NOW: só Amazon due.
    result = _run(orchestrator.run_batch(now=NOW))
    assert result.legacy_claimed == 1
    assert amazon_provider.calls == 1
    assert kabum_provider.calls == 0

    amazon_source = _mission_source(integration_database.sessions, mission_id, amazon_id)
    assert amazon_source.next_run_at == NOW + timedelta(minutes=30)  # HIGH_ACTIVITY

    # Ciclo 2 @ NOW+25min: KaBuM fica due (era NOW+25); Amazon (due em
    # NOW+30) continua fora -- exatamente o cenário do achado da auditoria.
    result = _run(orchestrator.run_batch(now=NOW + timedelta(minutes=25)))
    assert result.legacy_claimed == 1
    assert amazon_provider.calls == 1  # NÃO recoletada
    assert kabum_provider.calls == 1  # coletada no SEU próprio horário

    kabum_source = _mission_source(integration_database.sessions, mission_id, kabum_id)
    assert kabum_source.next_run_at == NOW + timedelta(minutes=25 + 60)  # NORMAL


# ---------------------------------------------------------------------------
# Caso 2: três stores -- NORMAL, PROMO_CALENDAR e HIGH_ACTIVITY simultâneas
# na MESMA Mission, cada uma no seu próprio relógio.
# ---------------------------------------------------------------------------


def test_high_activity_store_alone_never_drags_others(integration_database) -> None:
    """Versão sem PROMO (que é intencionalmente global) -- isola só
    HIGH_ACTIVITY (POR LOJA, `_is_high_activity` filtra por `store_id`)
    contra duas stores NORMAL da MESMA Mission."""
    amazon_id = _store_id(integration_database, "amazon")
    kabum_id = _store_id(integration_database, "kabum")
    pichau_id = _store_id(integration_database, "pichau")
    user_id = _seed_user(integration_database.sessions, "caso2b")
    mission_id = _seed_mission(
        integration_database,
        user_id,
        label="caso2b",
        sources={amazon_id: NOW, kabum_id: NOW, pichau_id: NOW},
    )
    with integration_database.sessions.begin() as session:
        session.add(
            StoreActivityState(
                store_id=amazon_id,
                scope_id=mission_id,
                high_activity_until=NOW + timedelta(hours=2),
            )
        )

    amazon_provider = _CountingProvider("amazon")
    kabum_provider = _CountingProvider("kabum")
    pichau_provider = _CountingProvider("pichau")
    orchestrator = _make_orchestrator(integration_database, amazon_provider, kabum_provider, pichau_provider)

    _run(orchestrator.run_batch(now=NOW))
    assert (amazon_provider.calls, kabum_provider.calls, pichau_provider.calls) == (1, 1, 1)
    assert _mission_source(integration_database.sessions, mission_id, amazon_id).next_run_at == NOW + timedelta(minutes=30)
    assert _mission_source(integration_database.sessions, mission_id, kabum_id).next_run_at == NOW + timedelta(minutes=60)
    assert _mission_source(integration_database.sessions, mission_id, pichau_id).next_run_at == NOW + timedelta(minutes=60)

    # @ NOW+30min: só Amazon due (HIGH_ACTIVITY expirado desta rodada por
    # falta de mudanças novas -- volta a NORMAL, mas o teste só precisa
    # provar que KaBuM/Pichau não são tocadas antes do seu próprio horário).
    _run(orchestrator.run_batch(now=NOW + timedelta(minutes=30)))
    assert amazon_provider.calls == 2
    assert kabum_provider.calls == 1  # intocada
    assert pichau_provider.calls == 1  # intocada


# ---------------------------------------------------------------------------
# Caso 3: backoff (DEC-046) isolado por store -- não afeta outra store da
# mesma Mission.
# ---------------------------------------------------------------------------


def test_backoff_on_one_store_never_blocks_another_same_mission(integration_database) -> None:
    amazon_id = _store_id(integration_database, "amazon")
    kabum_id = _store_id(integration_database, "kabum")
    user_id = _seed_user(integration_database.sessions, "caso3")
    mission_id = _seed_mission(
        integration_database, user_id, label="caso3", sources={amazon_id: NOW, kabum_id: NOW}
    )
    with integration_database.sessions.begin() as session:
        source = session.get(MissionSource, (mission_id, amazon_id))
        source.next_eligible_at = NOW + timedelta(hours=1)  # backoff futuro

    amazon_provider = _BlockedProvider("amazon")
    kabum_provider = _CountingProvider("kabum")
    orchestrator = _make_orchestrator(integration_database, amazon_provider, kabum_provider)

    result = _run(orchestrator.run_batch(now=NOW))
    assert amazon_provider.calls == 0  # backoff impede o claim -- nunca chega ao provider
    assert kabum_provider.calls == 1
    assert result.legacy_claimed == 1


# ---------------------------------------------------------------------------
# Caso 5: adicionar uma nova store à Mission não reseta a agenda das
# stores já existentes.
# ---------------------------------------------------------------------------


def test_new_source_never_resets_existing_sources_schedule(integration_database) -> None:
    amazon_id = _store_id(integration_database, "amazon")
    kabum_id = _store_id(integration_database, "kabum")
    user_id = _seed_user(integration_database.sessions, "caso5")
    mission_id = _seed_mission(
        integration_database,
        user_id,
        label="caso5",
        sources={amazon_id: NOW + timedelta(minutes=40)},  # já estabelecida, não due ainda
    )
    with integration_database.sessions.begin() as session:
        session.add(MissionSource(mission_id=mission_id, store_id=kabum_id))  # nova, next_run_at=NULL

    amazon_provider = _CountingProvider("amazon")
    kabum_provider = _CountingProvider("kabum")
    orchestrator = _make_orchestrator(integration_database, amazon_provider, kabum_provider)

    _run(orchestrator.run_batch(now=NOW))
    assert kabum_provider.calls == 1  # primeira coleta da nova source
    assert amazon_provider.calls == 0  # agenda antiga preservada, ainda não due

    amazon_source = _mission_source(integration_database.sessions, mission_id, amazon_id)
    assert amazon_source.next_run_at == NOW + timedelta(minutes=40)  # intocada


# ---------------------------------------------------------------------------
# Caso 6: pause impede claims; resume respeita a agenda individual (sem
# rajada) e quotas continuam validadas por `transition_mission_async`.
# ---------------------------------------------------------------------------


def test_pause_blocks_and_resume_respects_existing_schedule(integration_database) -> None:
    amazon_id = _store_id(integration_database, "amazon")
    user_id = _seed_user(integration_database.sessions, "caso6")
    mission_id = _seed_mission(integration_database, user_id, label="caso6", sources={amazon_id: NOW})

    from app.missions.models import MissionCommand

    async def pause():
        async with integration_database.async_sessions() as session, session.begin():
            await transition_mission_async(
                session, mission_id=mission_id, command=MissionCommand.PAUSE,
                expected_state_version=0, actor_type="test",
            )

    _run(pause())

    provider = _CountingProvider("amazon")
    orchestrator = _make_orchestrator(integration_database, provider)
    result = _run(orchestrator.run_batch(now=NOW))
    assert provider.calls == 0
    assert result.legacy_claimed == 0

    async def resume():
        async with integration_database.async_sessions() as session, session.begin():
            await transition_mission_async(
                session, mission_id=mission_id, command=MissionCommand.RESUME,
                expected_state_version=1, actor_type="test",
            )

    _run(resume())
    amazon_source = _mission_source(integration_database.sessions, mission_id, amazon_id)
    assert amazon_source.next_run_at == NOW  # nunca resetado por pause/resume

    result = _run(orchestrator.run_batch(now=NOW))
    assert provider.calls == 1
    assert result.legacy_claimed == 1


# ---------------------------------------------------------------------------
# Caso 8: usuário com várias MissionSource due continua sendo UM candidato
# de fairness -- todas as sources dele são processadas no MESMO ciclo, e
# UserCollectionQueueState avança uma única vez (não "N turnos").
# ---------------------------------------------------------------------------


def test_user_with_several_due_sources_gets_one_fairness_turn(integration_database) -> None:
    amazon_id = _store_id(integration_database, "amazon")
    kabum_id = _store_id(integration_database, "kabum")
    pichau_id = _store_id(integration_database, "pichau")
    user_id = _seed_user(integration_database.sessions, "caso8")
    _seed_mission(
        integration_database,
        user_id,
        label="caso8",
        sources={amazon_id: NOW, kabum_id: NOW, pichau_id: NOW},
    )

    amazon_provider = _CountingProvider("amazon")
    kabum_provider = _CountingProvider("kabum")
    pichau_provider = _CountingProvider("pichau")
    orchestrator = _make_orchestrator(
        integration_database, amazon_provider, kabum_provider, pichau_provider,
    )

    result = _run(orchestrator.run_batch(now=NOW))
    assert (amazon_provider.calls, kabum_provider.calls, pichau_provider.calls) == (1, 1, 1)
    assert result.legacy_claimed == 3

    with integration_database.sessions() as session:
        state = session.get(UserCollectionQueueState, user_id)
        assert state is not None
        assert state.last_processed_at == NOW  # um único turno, não três


# ---------------------------------------------------------------------------
# Caso 7: concorrência real -- duas transações disputando a MESMA
# MissionSource nunca a claimam duas vezes (lock novo, `_claim_legacy_
# source_attempt`).
# ---------------------------------------------------------------------------


def test_concurrent_claims_never_double_claim_same_mission_source(integration_database) -> None:
    amazon_id = _store_id(integration_database, "amazon")
    user_id = _seed_user(integration_database.sessions, "caso7")
    mission_id = _seed_mission(integration_database, user_id, label="caso7", sources={amazon_id: NOW})

    barrier = Barrier(2)

    def _claim(_index: int):
        from app.collection.orchestration import claim_due_work

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

    total_claims = sum(len(batch.old_path) for batch in results)
    assert total_claims == 1

    with integration_database.sessions() as session:
        runs = list(
            session.scalars(
                select(CollectionRun).where(
                    CollectionRun.mission_id == mission_id, CollectionRun.store_id == amazon_id
                )
            )
        )
        assert len(runs) == 1


# ---------------------------------------------------------------------------
# Caso 10: `claim_due_collections` (compatibility/legacy-only) respeita a
# MESMA due-ness por source -- não é só `claim_due_work`/`CollectionOrchestrator`.
# ---------------------------------------------------------------------------


def test_claim_due_collections_respects_per_source_cadence(integration_database) -> None:
    amazon_id = _store_id(integration_database, "amazon")
    kabum_id = _store_id(integration_database, "kabum")
    user_id = _seed_user(integration_database.sessions, "caso10")
    mission_id = _seed_mission(
        integration_database,
        user_id,
        label="caso10",
        sources={amazon_id: NOW, kabum_id: NOW + timedelta(minutes=90)},
    )

    async def run(now):
        async with integration_database.async_sessions() as session, session.begin():
            return await claim_due_collections(
                session, now=now, limit=25, max_users=5, cadence_config=_DETERMINISTIC_CADENCE,
            )

    claims = _run(run(NOW))
    assert len(claims) == 1
    assert claims[0].store_id == amazon_id  # só a source due -- KaBuM (due em +90min) fica de fora
