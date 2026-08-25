"""Vínculo Mission -> MonitoringItem e necessidade real de coleta por
(item, loja) -- TASK-112, fase 2, contra PostgreSQL real.

Sem coleta real, sem fairness/TASK-108 -- só modelo, vínculo na criação
de missão e lifecycle (pause/resume/cancel) derivando corretamente
`MonitoringItemStore.is_enabled`.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import UUID

import pytest
from app.database.session import (
    create_async_session_factory,
    create_collection_async_database_engine,
)
from app.missions.models import (
    Mission,
    MissionCommand,
    MissionCriteria,
    MissionMonitoringItem,
    MissionStatus,
    MonitoringItem,
    MonitoringItemStore,
)
from app.missions.service import (
    create_mission_from_criteria_async,
    promote_confirmed_product_identity_async,
    set_mission_product_selection_async,
    transition_mission_async,
)
from app.offers.models import Offer
from app.products.identity import resolve_product_variant
from app.products.models import Product
from app.users.models import User, UserRole
from sqlalchemy import func, select

pytestmark = pytest.mark.integration


def _seed_user(sessions, label: str) -> UUID:
    with sessions.begin() as session:
        user = User(display_name=f"TASK-112 {label}", role=UserRole.USER)
        session.add(user)
        session.flush()
        return user.id


def _create_mission(integration_database, **kwargs):
    async def run():
        async with integration_database.async_sessions.begin() as session:
            return await create_mission_from_criteria_async(session, **kwargs)

    return asyncio.run(run())


def _transition(integration_database, **kwargs):
    async def run():
        async with integration_database.async_sessions.begin() as session:
            return await transition_mission_async(session, **kwargs)

    return asyncio.run(run())


def _monitoring_item_id_for(sessions, mission_id: UUID) -> UUID | None:
    with sessions() as session:
        link = session.get(MissionMonitoringItem, mission_id)
        return link.monitoring_item_id if link is not None else None


def _store_state(sessions, monitoring_item_id: UUID, store_id: UUID) -> MonitoringItemStore | None:
    with sessions() as session:
        return session.get(MonitoringItemStore, (monitoring_item_id, store_id))


def _monitoring_item_count(sessions) -> int:
    with sessions() as session:
        return session.scalar(select(func.count(MonitoringItem.id)))


NOW = datetime.now(UTC)


def _make_mission(integration_database, user_id: UUID, *, search_query: str, sources: tuple[str, ...]):
    mission, _codes = _create_mission(
        integration_database,
        user_id=user_id,
        search_query=search_query,
        target_amount=None,
        target_currency=None,
        source_codes=sources,
        requested_at=NOW,
        actor_type="test",
    )
    return mission


# ---------------------------------------------------------------------------
# Vínculo Mission -> MonitoringItem
# ---------------------------------------------------------------------------


def test_two_missions_same_monitoring_key_share_one_monitoring_item(
    integration_database,
) -> None:
    user_a = _seed_user(integration_database.sessions, "A")
    user_b = _seed_user(integration_database.sessions, "B")

    mission_a = _make_mission(integration_database, user_a, search_query="RTX 5070 Ti", sources=("amazon",))
    mission_b = _make_mission(
        integration_database, user_b, search_query="NVIDIA GeForce RTX 5070 Ti", sources=("amazon",)
    )

    item_a = _monitoring_item_id_for(integration_database.sessions, mission_a.id)
    item_b = _monitoring_item_id_for(integration_database.sessions, mission_b.id)
    assert item_a is not None
    assert item_a == item_b
    assert _monitoring_item_count(integration_database.sessions) == 1


def test_two_concurrent_creations_same_key_never_duplicate_monitoring_item(
    integration_database,
) -> None:
    user_a = _seed_user(integration_database.sessions, "concurrent-A")
    user_b = _seed_user(integration_database.sessions, "concurrent-B")
    barrier = Barrier(2)

    def _run_creation(user_id: UUID):
        async def run():
            engine = create_collection_async_database_engine(integration_database.settings)
            sessions = create_async_session_factory(engine)
            try:
                barrier.wait(timeout=10)
                async with sessions.begin() as session:
                    mission, _codes = await create_mission_from_criteria_async(
                        session,
                        user_id=user_id,
                        search_query="Ryzen 9950X3D",
                        target_amount=None,
                        target_currency=None,
                        source_codes=("kabum",),
                        requested_at=NOW,
                        actor_type="test",
                    )
                    return mission.id
            finally:
                await engine.dispose()

        return asyncio.run(run())

    with ThreadPoolExecutor(max_workers=2) as executor:
        mission_ids = list(executor.map(_run_creation, (user_a, user_b)))

    assert _monitoring_item_count(integration_database.sessions) == 1
    items = {
        _monitoring_item_id_for(integration_database.sessions, mission_id)
        for mission_id in mission_ids
    }
    assert len(items) == 1
    assert None not in items


def test_different_monitoring_keys_never_share_item(integration_database) -> None:
    user_a = _seed_user(integration_database.sessions, "diff-A")
    user_b = _seed_user(integration_database.sessions, "diff-B")

    mission_gpu = _make_mission(integration_database, user_a, search_query="RTX 5070 Ti", sources=("amazon",))
    mission_cpu = _make_mission(integration_database, user_b, search_query="Ryzen 9950X3D", sources=("amazon",))

    item_gpu = _monitoring_item_id_for(integration_database.sessions, mission_gpu.id)
    item_cpu = _monitoring_item_id_for(integration_database.sessions, mission_cpu.id)
    assert item_gpu is not None
    assert item_cpu is not None
    assert item_gpu != item_cpu


def test_unresolved_identity_never_links_and_never_creates_item(integration_database) -> None:
    user = _seed_user(integration_database.sessions, "unresolved")

    mission = _make_mission(
        integration_database, user, search_query="cadeira gamer reclinável azul", sources=("amazon",)
    )

    assert _monitoring_item_id_for(integration_database.sessions, mission.id) is None
    assert _monitoring_item_count(integration_database.sessions) == 0


# ---------------------------------------------------------------------------
# Compartilhamento por store
# ---------------------------------------------------------------------------


def test_shared_stores_get_one_monitoring_item_store_row_enabled(integration_database) -> None:
    user_a = _seed_user(integration_database.sessions, "shared-A")
    user_b = _seed_user(integration_database.sessions, "shared-B")

    mission_a = _make_mission(
        integration_database, user_a, search_query="RTX 5070 Ti", sources=("amazon", "kabum")
    )
    mission_b = _make_mission(
        integration_database, user_b, search_query="RTX 5070 Ti", sources=("amazon", "kabum")
    )

    item_id = _monitoring_item_id_for(integration_database.sessions, mission_a.id)
    assert item_id == _monitoring_item_id_for(integration_database.sessions, mission_b.id)

    with integration_database.sessions() as session:
        rows = list(
            session.scalars(
                select(MonitoringItemStore).where(MonitoringItemStore.monitoring_item_id == item_id)
            )
        )
    assert len(rows) == 2  # amazon + kabum, nunca duplicado por missão
    assert all(row.is_enabled for row in rows)


def test_partially_different_stores_matches_the_worked_example(integration_database) -> None:
    """Exemplo do pedido: A = 9950X3D + Amazon/Kabum/Pichau, B = 9950X3D +
    Amazon/Kabum -> 1 MonitoringItem, 3 MonitoringItemStore (amazon/kabum
    compartilhados por A+B, pichau só por A), todos habilitados."""
    user_a = _seed_user(integration_database.sessions, "partial-A")
    user_b = _seed_user(integration_database.sessions, "partial-B")

    mission_a = _make_mission(
        integration_database,
        user_a,
        search_query="Ryzen 9950X3D",
        sources=("amazon", "kabum", "pichau"),
    )
    mission_b = _make_mission(
        integration_database, user_b, search_query="Ryzen 9950X3D", sources=("amazon", "kabum")
    )

    item_id = _monitoring_item_id_for(integration_database.sessions, mission_a.id)
    assert item_id == _monitoring_item_id_for(integration_database.sessions, mission_b.id)

    with integration_database.sessions() as session:
        rows = {
            row.store_id: row
            for row in session.scalars(
                select(MonitoringItemStore).where(MonitoringItemStore.monitoring_item_id == item_id)
            )
        }
    assert len(rows) == 3
    assert all(row.is_enabled for row in rows.values())


# ---------------------------------------------------------------------------
# Lifecycle: pause / cancel / resume
# ---------------------------------------------------------------------------


def _seed_two_missions_sharing_amazon(integration_database, *, label: str):
    user_a = _seed_user(integration_database.sessions, f"{label}-A")
    user_b = _seed_user(integration_database.sessions, f"{label}-B")
    mission_a = _make_mission(integration_database, user_a, search_query="RTX 5070 Ti", sources=("amazon",))
    mission_b = _make_mission(integration_database, user_b, search_query="RTX 5070 Ti", sources=("amazon",))
    item_id = _monitoring_item_id_for(integration_database.sessions, mission_a.id)
    with integration_database.sessions() as session:
        store_id = session.scalar(
            select(MonitoringItemStore.store_id).where(
                MonitoringItemStore.monitoring_item_id == item_id
            )
        )
    return mission_a, mission_b, item_id, store_id


def test_pausing_one_mission_never_affects_the_other(integration_database) -> None:
    mission_a, mission_b, item_id, store_id = _seed_two_missions_sharing_amazon(
        integration_database, label="pause"
    )

    _transition(
        integration_database,
        mission_id=mission_a.id,
        command=MissionCommand.PAUSE,
        expected_state_version=mission_a.state_version,
        actor_type="test",
        transitioned_at=NOW,
    )

    with integration_database.sessions() as session:
        assert session.get(Mission, mission_a.id).status is MissionStatus.PAUSED
        assert session.get(Mission, mission_b.id).status is MissionStatus.ACTIVE
    # B ainda ativa e ainda precisa da amazon -- continua habilitado.
    row = _store_state(integration_database.sessions, item_id, store_id)
    assert row.is_enabled is True


def test_cancelling_one_mission_never_affects_the_other(integration_database) -> None:
    mission_a, mission_b, item_id, store_id = _seed_two_missions_sharing_amazon(
        integration_database, label="cancel"
    )

    _transition(
        integration_database,
        mission_id=mission_a.id,
        command=MissionCommand.CANCEL,
        expected_state_version=mission_a.state_version,
        actor_type="test",
        transitioned_at=NOW,
    )

    with integration_database.sessions() as session:
        assert session.get(Mission, mission_a.id).status is MissionStatus.CANCELLED
        assert session.get(Mission, mission_b.id).status is MissionStatus.ACTIVE
    row = _store_state(integration_database.sessions, item_id, store_id)
    assert row.is_enabled is True
    # histórico nunca apagado -- linha continua existindo mesmo pausada/cancelada.
    with integration_database.sessions() as session:
        assert session.get(MonitoringItem, item_id) is not None


def test_last_active_mission_leaving_disables_item_store_without_deleting_history(
    integration_database,
) -> None:
    mission_a, mission_b, item_id, store_id = _seed_two_missions_sharing_amazon(
        integration_database, label="last"
    )

    _transition(
        integration_database,
        mission_id=mission_a.id,
        command=MissionCommand.PAUSE,
        expected_state_version=mission_a.state_version,
        actor_type="test",
        transitioned_at=NOW,
    )
    row = _store_state(integration_database.sessions, item_id, store_id)
    assert row.is_enabled is True  # B ainda ativa

    _transition(
        integration_database,
        mission_id=mission_b.id,
        command=MissionCommand.CANCEL,
        expected_state_version=mission_b.state_version,
        actor_type="test",
        transitioned_at=NOW,
    )
    row = _store_state(integration_database.sessions, item_id, store_id)
    assert row.is_enabled is False  # ninguem mais ativo precisa da amazon

    # nada foi apagado -- a linha continua existindo, só desabilitada.
    with integration_database.sessions() as session:
        assert session.get(MonitoringItemStore, (item_id, store_id)) is not None
        assert session.get(MonitoringItem, item_id) is not None


def test_resume_reuses_the_existing_monitoring_item_and_store(integration_database) -> None:
    user = _seed_user(integration_database.sessions, "resume")
    mission = _make_mission(integration_database, user, search_query="RTX 5070 Ti", sources=("amazon",))
    item_id = _monitoring_item_id_for(integration_database.sessions, mission.id)
    with integration_database.sessions() as session:
        store_id = session.scalar(
            select(MonitoringItemStore.store_id).where(
                MonitoringItemStore.monitoring_item_id == item_id
            )
        )

    _transition(
        integration_database,
        mission_id=mission.id,
        command=MissionCommand.PAUSE,
        expected_state_version=mission.state_version,
        actor_type="test",
        transitioned_at=NOW,
    )
    assert _store_state(integration_database.sessions, item_id, store_id).is_enabled is False
    assert _monitoring_item_count(integration_database.sessions) == 1  # nenhum item novo

    with integration_database.sessions() as session:
        paused_version = session.get(Mission, mission.id).state_version

    _transition(
        integration_database,
        mission_id=mission.id,
        command=MissionCommand.RESUME,
        expected_state_version=paused_version,
        actor_type="test",
        transitioned_at=NOW,
    )

    assert _monitoring_item_id_for(integration_database.sessions, mission.id) == item_id
    assert _monitoring_item_count(integration_database.sessions) == 1  # continua o mesmo item
    assert _store_state(integration_database.sessions, item_id, store_id).is_enabled is True


# ---------------------------------------------------------------------------
# Corrida real em pause/cancel/resume (FOR UPDATE + reconsulta pós-lock) --
# duas conexões/transações genuinamente concorrentes, mesmo padrão de
# `test_two_concurrent_creations_same_key_never_duplicate_monitoring_item`
# e de `test_two_worker_processes_do_not_corrupt_or_duplicate_mission_state`
# (tests/integration/test_collection_orchestration.py).
# ---------------------------------------------------------------------------


def _run_transition_concurrently(
    integration_database, *, mission_id: UUID, command: MissionCommand, expected_state_version: int, barrier: Barrier
) -> None:
    async def run():
        engine = create_collection_async_database_engine(integration_database.settings)
        sessions = create_async_session_factory(engine)
        try:
            barrier.wait(timeout=10)
            async with sessions.begin() as session:
                await transition_mission_async(
                    session,
                    mission_id=mission_id,
                    command=command,
                    expected_state_version=expected_state_version,
                    actor_type="test",
                    transitioned_at=NOW,
                )
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_concurrent_pause_of_two_missions_correctly_disables_the_last_dependency(
    integration_database,
) -> None:
    """A corrida que o desenho anterior tinha: A e B, únicas ACTIVE do
    mesmo item+loja, pausando em transações concorrentes distintas. Sem o
    FOR UPDATE + reconsulta pós-lock, as duas poderiam se enxergar
    mutuamente como "ainda ativa" e nenhuma desligar. Com a correção, o
    resultado é sempre determinístico: desabilitado."""
    mission_a, mission_b, item_id, store_id = _seed_two_missions_sharing_amazon(
        integration_database, label="race-pause"
    )
    barrier = Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                _run_transition_concurrently,
                integration_database,
                mission_id=mission_a.id,
                command=MissionCommand.PAUSE,
                expected_state_version=mission_a.state_version,
                barrier=barrier,
            ),
            executor.submit(
                _run_transition_concurrently,
                integration_database,
                mission_id=mission_b.id,
                command=MissionCommand.PAUSE,
                expected_state_version=mission_b.state_version,
                barrier=barrier,
            ),
        ]
        for future in futures:
            future.result(timeout=15)

    with integration_database.sessions() as session:
        assert session.get(Mission, mission_a.id).status is MissionStatus.PAUSED
        assert session.get(Mission, mission_b.id).status is MissionStatus.PAUSED
    row = _store_state(integration_database.sessions, item_id, store_id)
    assert row.is_enabled is False


def test_concurrent_pause_and_resume_produce_correct_final_state(integration_database) -> None:
    """A entra em pause enquanto B (já pausada antes, vinculada ao mesmo
    item+loja) entra em resume, ao mesmo tempo. B precisa da loja agora
    -- resultado final tem que ser habilitado, não importa a ordem real
    de execução."""
    mission_a, mission_b, item_id, store_id = _seed_two_missions_sharing_amazon(
        integration_database, label="race-pause-resume"
    )
    _transition(
        integration_database,
        mission_id=mission_b.id,
        command=MissionCommand.PAUSE,
        expected_state_version=mission_b.state_version,
        actor_type="test",
        transitioned_at=NOW,
    )
    with integration_database.sessions() as session:
        b_paused_version = session.get(Mission, mission_b.id).state_version
    barrier = Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                _run_transition_concurrently,
                integration_database,
                mission_id=mission_a.id,
                command=MissionCommand.PAUSE,
                expected_state_version=mission_a.state_version,
                barrier=barrier,
            ),
            executor.submit(
                _run_transition_concurrently,
                integration_database,
                mission_id=mission_b.id,
                command=MissionCommand.RESUME,
                expected_state_version=b_paused_version,
                barrier=barrier,
            ),
        ]
        for future in futures:
            future.result(timeout=15)

    with integration_database.sessions() as session:
        assert session.get(Mission, mission_a.id).status is MissionStatus.PAUSED
        assert session.get(Mission, mission_b.id).status is MissionStatus.ACTIVE
    row = _store_state(integration_database.sessions, item_id, store_id)
    assert row.is_enabled is True


# ---------------------------------------------------------------------------
# reconcile_mission_monitoring_item -- identidade mudando depois da criação
# ---------------------------------------------------------------------------


def test_promote_confirmed_identity_moves_unresolved_mission_to_a_monitoring_item(
    integration_database,
) -> None:
    user = _seed_user(integration_database.sessions, "unresolved-to-resolved")
    mission = _make_mission(
        integration_database, user, search_query="acessório qualquer sem categoria", sources=("amazon",)
    )
    assert _monitoring_item_id_for(integration_database.sessions, mission.id) is None
    assert _monitoring_item_count(integration_database.sessions) == 0

    async def run():
        async with integration_database.async_sessions.begin() as session:
            return await promote_confirmed_product_identity_async(
                session, mission_id=mission.id, confirmed_search_query="RTX 5070 Ti"
            )

    assert asyncio.run(run()) is True

    item_id = _monitoring_item_id_for(integration_database.sessions, mission.id)
    assert item_id is not None
    assert _monitoring_item_count(integration_database.sessions) == 1
    row = _store_state(integration_database.sessions, item_id, _amazon_store_id(integration_database))
    assert row is not None and row.is_enabled is True  # missão ACTIVE, já reflete na store


def test_identity_change_moves_link_and_preserves_old_item_for_other_mission(
    integration_database,
) -> None:
    """Mission A (X) confirmada para outro produto (Y): vínculo muda para
    Y; X continua existindo; Mission B (que também usava X) continua
    vinculada a X, então X.amazon continua habilitado; só quando B também
    sair de X é que X.amazon desativa."""
    user_x = _seed_user(integration_database.sessions, "relink-X")
    user_b = _seed_user(integration_database.sessions, "relink-B")
    mission_x = _make_mission(integration_database, user_x, search_query="RTX 5070 Ti", sources=("amazon",))
    mission_b = _make_mission(integration_database, user_b, search_query="RTX 5070 Ti", sources=("amazon",))
    item_x = _monitoring_item_id_for(integration_database.sessions, mission_x.id)
    assert item_x == _monitoring_item_id_for(integration_database.sessions, mission_b.id)

    async def run():
        async with integration_database.async_sessions.begin() as session:
            return await promote_confirmed_product_identity_async(
                session, mission_id=mission_x.id, confirmed_search_query="Ryzen 9950X3D"
            )

    assert asyncio.run(run()) is True

    item_y = _monitoring_item_id_for(integration_database.sessions, mission_x.id)
    assert item_y is not None
    assert item_y != item_x
    # B nunca foi tocada -- continua vinculada a X.
    assert _monitoring_item_id_for(integration_database.sessions, mission_b.id) == item_x
    assert _monitoring_item_count(integration_database.sessions) == 2

    amazon_id = _amazon_store_id(integration_database)
    # X continua existindo e sua store continua habilitada -- B ainda ativa e precisa.
    row_x = _store_state(integration_database.sessions, item_x, amazon_id)
    assert row_x is not None and row_x.is_enabled is True
    # Y (novo item de X) já reflete a necessidade de X.
    row_y = _store_state(integration_database.sessions, item_y, amazon_id)
    assert row_y is not None and row_y.is_enabled is True

    # B também sai -- agora sim X.amazon desativa; nada é apagado.
    with integration_database.sessions() as session:
        b_version = session.get(Mission, mission_b.id).state_version
    _transition(
        integration_database,
        mission_id=mission_b.id,
        command=MissionCommand.CANCEL,
        expected_state_version=b_version,
        actor_type="test",
        transitioned_at=NOW,
    )
    row_x = _store_state(integration_database.sessions, item_x, amazon_id)
    assert row_x is not None and row_x.is_enabled is False
    with integration_database.sessions() as session:
        assert session.get(MonitoringItem, item_x) is not None  # histórico preservado


def test_reconciling_the_same_identity_again_is_idempotent(integration_database) -> None:
    user = _seed_user(integration_database.sessions, "idempotent")
    mission = _make_mission(integration_database, user, search_query="RTX 5070 Ti", sources=("amazon",))
    item_id = _monitoring_item_id_for(integration_database.sessions, mission.id)
    assert item_id is not None

    async def run():
        async with integration_database.async_sessions.begin() as session:
            return await promote_confirmed_product_identity_async(
                session, mission_id=mission.id, confirmed_search_query="RTX 5070 Ti"
            )

    assert asyncio.run(run()) is True

    assert _monitoring_item_id_for(integration_database.sessions, mission.id) == item_id
    assert _monitoring_item_count(integration_database.sessions) == 1
    row = _store_state(integration_database.sessions, item_id, _amazon_store_id(integration_database))
    assert row is not None and row.is_enabled is True


def _select_all_variants(integration_database, *, user_id: UUID, mission_id: UUID, version: int):
    async def run():
        async with integration_database.async_sessions.begin() as session:
            return await set_mission_product_selection_async(
                session,
                user_id=user_id,
                mission_id=mission_id,
                expected_state_version=version,
                select_all=True,
                selected_at=NOW,
                allow_uncollected_family_products=True,
            )

    return asyncio.run(run())


def _amazon_store_id(integration_database) -> UUID:
    from app.stores.models import Store

    with integration_database.sessions() as session:
        return session.scalar(select(Store.id).where(Store.code == "amazon"))


# ---------------------------------------------------------------------------
# Precedência da identidade efetiva (correção): variante de Product
# SELECIONADA (TASK-097) prevalece sobre o texto original quando presente
# -- nunca um segundo algoritmo de monitoring_key, mesmo núcleo
# (`app.products.identity._build_monitoring_identity`) para as duas
# entradas (texto via `_parse`, estrutura já resolvida via `Product`).
# ---------------------------------------------------------------------------


def _seed_resolved_product(sessions, *, text: str, store_id: UUID) -> Product:
    """Product+Offer com identidade REAL (mesmo `resolve_product_variant`
    que a coleta de verdade usaria, TASK-097) -- não um fake solto."""
    resolved = resolve_product_variant(text)
    assert resolved is not None, f"texto de teste não resolveu identidade: {text!r}"
    with sessions.begin() as session:
        product = Product(
            name=resolved.label,
            brand=resolved.brand,
            model=resolved.model,
            display_name=resolved.label,
            category=resolved.category,
            family=resolved.family,
            variant=resolved.variant,
            attributes=dict(resolved.attributes),
            family_key=resolved.family_key,
            identity_key=resolved.identity_key,
            identity_version=1,
        )
        session.add(product)
        session.flush()
        session.add(
            Offer(
                product_id=product.id,
                store_id=store_id,
                url=f"https://example.invalid/{product.id}",
            )
        )
        return product


def _select_variant(integration_database, *, user_id: UUID, mission_id: UUID, product_id: UUID, version: int):
    async def run():
        async with integration_database.async_sessions.begin() as session:
            return await set_mission_product_selection_async(
                session,
                user_id=user_id,
                mission_id=mission_id,
                expected_state_version=version,
                product_ids=(product_id,),
                selected_at=NOW,
                allow_uncollected_family_products=True,
            )

    return asyncio.run(run())


def test_family_mission_has_no_link_until_a_variant_is_selected(integration_database) -> None:
    user = _seed_user(integration_database.sessions, "family-pending")
    mission = _make_mission(integration_database, user, search_query="iPhone 17", sources=("amazon",))
    with integration_database.sessions() as session:
        criteria = session.scalar(
            select(MissionCriteria).where(MissionCriteria.mission_id == mission.id)
        )
        assert criteria.variant_selection_mode.value == "pending"
    assert _monitoring_item_id_for(integration_database.sessions, mission.id) is None
    assert _monitoring_item_count(integration_database.sessions) == 0


def test_selecting_a_specific_variant_creates_monitoring_item_immediately(
    integration_database,
) -> None:
    user = _seed_user(integration_database.sessions, "select-variant")
    mission = _make_mission(integration_database, user, search_query="iPhone 17", sources=("amazon",))
    amazon_id = _amazon_store_id(integration_database)
    product_256 = _seed_resolved_product(
        integration_database.sessions, text="iPhone 17 Pro 256GB", store_id=amazon_id
    )
    with integration_database.sessions() as session:
        version = session.get(Mission, mission.id).state_version

    _select_variant(
        integration_database, user_id=user, mission_id=mission.id, product_id=product_256.id, version=version
    )

    item_id = _monitoring_item_id_for(integration_database.sessions, mission.id)
    assert item_id is not None
    assert _monitoring_item_count(integration_database.sessions) == 1
    row = _store_state(integration_database.sessions, item_id, amazon_id)
    assert row is not None and row.is_enabled is True  # missão ACTIVE, já reflete


def test_changing_selected_variant_moves_the_link_from_a_to_b(integration_database) -> None:
    user = _seed_user(integration_database.sessions, "select-a-then-b")
    mission = _make_mission(integration_database, user, search_query="iPhone 17", sources=("amazon",))
    amazon_id = _amazon_store_id(integration_database)
    product_256 = _seed_resolved_product(
        integration_database.sessions, text="iPhone 17 Pro 256GB", store_id=amazon_id
    )
    product_512 = _seed_resolved_product(
        integration_database.sessions, text="iPhone 17 Pro 512GB", store_id=amazon_id
    )
    with integration_database.sessions() as session:
        version = session.get(Mission, mission.id).state_version
    _select_variant(
        integration_database, user_id=user, mission_id=mission.id, product_id=product_256.id, version=version
    )
    item_a = _monitoring_item_id_for(integration_database.sessions, mission.id)
    assert item_a is not None

    with integration_database.sessions() as session:
        version = session.get(Mission, mission.id).state_version
    _select_variant(
        integration_database, user_id=user, mission_id=mission.id, product_id=product_512.id, version=version
    )
    item_b = _monitoring_item_id_for(integration_database.sessions, mission.id)

    assert item_b is not None
    assert item_b != item_a
    assert _monitoring_item_count(integration_database.sessions) == 2
    # item A continua existindo (histórico preservado); sua store desativa
    # -- ninguém mais (a própria missão migrou) precisa dela.
    row_a = _store_state(integration_database.sessions, item_a, amazon_id)
    assert row_a is not None and row_a.is_enabled is False
    with integration_database.sessions() as session:
        assert session.get(MonitoringItem, item_a) is not None
    row_b = _store_state(integration_database.sessions, item_b, amazon_id)
    assert row_b is not None and row_b.is_enabled is True


def test_text_and_selected_product_for_the_same_variant_converge_to_the_same_key(
    integration_database,
) -> None:
    """Caso 5 do pedido: o texto original ("iPhone 17 Pro 256GB", já
    específico o bastante para SPECIFIC_PRODUCT direto) e uma seleção
    explícita de Product para a MESMA variante (partindo de "iPhone 17",
    família ambígua) têm que produzir a mesma monitoring_key -- mesmo
    algoritmo, fontes diferentes."""
    user_text = _seed_user(integration_database.sessions, "converge-text")
    user_product = _seed_user(integration_database.sessions, "converge-product")

    mission_text = _make_mission(
        integration_database, user_text, search_query="iPhone 17 Pro 256GB", sources=("amazon",)
    )
    item_from_text = _monitoring_item_id_for(integration_database.sessions, mission_text.id)
    assert item_from_text is not None  # SPECIFIC_PRODUCT direto por texto

    mission_family = _make_mission(
        integration_database, user_product, search_query="iPhone 17", sources=("amazon",)
    )
    amazon_id = _amazon_store_id(integration_database)
    product_256 = _seed_resolved_product(
        integration_database.sessions, text="iPhone 17 Pro 256GB", store_id=amazon_id
    )
    with integration_database.sessions() as session:
        version = session.get(Mission, mission_family.id).state_version
    _select_variant(
        integration_database,
        user_id=user_product,
        mission_id=mission_family.id,
        product_id=product_256.id,
        version=version,
    )
    item_from_product = _monitoring_item_id_for(integration_database.sessions, mission_family.id)

    assert item_from_product is not None
    assert item_from_product == item_from_text
    assert _monitoring_item_count(integration_database.sessions) == 1


def test_selected_product_prevails_over_less_specific_original_text(integration_database) -> None:
    """Caso 6 do pedido: o texto original da missão era só "iPhone 17"
    (família, sem variante) -- depois de selecionar 256GB, a identidade
    efetiva tem que ser a do Product selecionado, nunca continuar
    derivada do texto original (que sozinho nem resolveria)."""
    user = _seed_user(integration_database.sessions, "product-prevails")
    mission = _make_mission(integration_database, user, search_query="iPhone 17", sources=("amazon",))
    amazon_id = _amazon_store_id(integration_database)
    product_256 = _seed_resolved_product(
        integration_database.sessions, text="iPhone 17 Pro 256GB", store_id=amazon_id
    )
    with integration_database.sessions() as session:
        version = session.get(Mission, mission.id).state_version
    _select_variant(
        integration_database, user_id=user, mission_id=mission.id, product_id=product_256.id, version=version
    )

    item_id = _monitoring_item_id_for(integration_database.sessions, mission.id)
    assert item_id is not None
    with integration_database.sessions() as session:
        item = session.get(MonitoringItem, item_id)
    # a chave reflete a variante 256GB (Product selecionado), não "iPhone 17"
    # sozinho -- que nem geraria monitoring_key nenhuma (storage bloqueante).
    assert item.canonical_identity["attributes"]["storage_gb"] == "256"


# ---------------------------------------------------------------------------
# scope=FAMILY -- VariantSelectionMode.ALL ("qualquer variante da
# família"), fechamento do caso PRODUCT_FAMILY sem Shared Monitoring.
# ---------------------------------------------------------------------------


def test_two_all_mode_missions_for_the_same_family_share_one_monitoring_item(
    integration_database,
) -> None:
    """Caso 1+3 do pedido: USER A e USER B pedem 'iPhone 17' e escolhem
    'qualquer variante' (ALL) -- mesma intenção, tem que compartilhar a
    mesma necessidade de coleta (1 MonitoringItem, escopo family)."""
    user_a = _seed_user(integration_database.sessions, "family-all-A")
    user_b = _seed_user(integration_database.sessions, "family-all-B")
    amazon_id = _amazon_store_id(integration_database)
    # select_all exige ao menos uma variante já conhecida da família
    # (mesmo com allow_uncollected_family_products=True) -- não gera
    # identidade do nada, só dispensa a exigência de variante específica.
    _seed_resolved_product(integration_database.sessions, text="iPhone 17 Pro 256GB", store_id=amazon_id)

    mission_a = _make_mission(integration_database, user_a, search_query="iPhone 17", sources=("amazon",))
    mission_b = _make_mission(integration_database, user_b, search_query="iPhone 17", sources=("amazon",))
    assert _monitoring_item_id_for(integration_database.sessions, mission_a.id) is None  # PENDING

    with integration_database.sessions() as session:
        version_a = session.get(Mission, mission_a.id).state_version
        version_b = session.get(Mission, mission_b.id).state_version
    _select_all_variants(integration_database, user_id=user_a, mission_id=mission_a.id, version=version_a)
    _select_all_variants(integration_database, user_id=user_b, mission_id=mission_b.id, version=version_b)

    item_a = _monitoring_item_id_for(integration_database.sessions, mission_a.id)
    item_b = _monitoring_item_id_for(integration_database.sessions, mission_b.id)
    assert item_a is not None
    assert item_a == item_b
    assert _monitoring_item_count(integration_database.sessions) == 1

    with integration_database.sessions() as session:
        item = session.get(MonitoringItem, item_a)
    assert item.canonical_identity["scope"] == "family"


def test_family_all_mode_defaults_only_unspecified_attributes_to_any_in_the_stored_identity(
    integration_database,
) -> None:
    """Caso 4 do pedido: escopo family nunca herda o valor real de um
    Product específico que por acaso já exista na família só porque ele
    existe -- a missão em si pediu só 'iPhone 17' (sem Pro, sem
    storage), então sua identidade fica exatamente com o que ELA
    especificou: nada de variante, storage ANY. Ver
    `test_family_scope_preserves_explicit_blocking_attribute_when_present`
    (nível de motor) para a prova de que um atributo que a PRÓPRIA missão
    especificasse seria preservado, não zerado."""
    user = _seed_user(integration_database.sessions, "family-all-any")
    amazon_id = _amazon_store_id(integration_database)
    _seed_resolved_product(integration_database.sessions, text="iPhone 17 Pro 256GB", store_id=amazon_id)
    mission = _make_mission(integration_database, user, search_query="iPhone 17", sources=("amazon",))
    with integration_database.sessions() as session:
        version = session.get(Mission, mission.id).state_version

    _select_all_variants(integration_database, user_id=user, mission_id=mission.id, version=version)

    item_id = _monitoring_item_id_for(integration_database.sessions, mission.id)
    assert item_id is not None
    with integration_database.sessions() as session:
        item = session.get(MonitoringItem, item_id)
    # "iPhone 17" não especificou Pro/Plus/... -- ANY explícito, nunca
    # None/ausência no payload canônico (não pode haver ambiguidade).
    assert item.canonical_identity["variant"] == "ANY"
    assert item.canonical_identity["attributes"]["storage_gb"] == "ANY"


def test_all_mode_and_selected_specific_variant_never_share_the_same_item(
    integration_database,
) -> None:
    """Caso 2 do pedido: 'qualquer variante' (ALL) e '256GB' (SELECTED)
    são intenções de coleta diferentes -- nunca podem colidir, mesmo
    partindo da mesma família 'iPhone 17'."""
    user_all = _seed_user(integration_database.sessions, "all-vs-selected-A")
    user_selected = _seed_user(integration_database.sessions, "all-vs-selected-B")
    amazon_id = _amazon_store_id(integration_database)
    product_256 = _seed_resolved_product(
        integration_database.sessions, text="iPhone 17 Pro 256GB", store_id=amazon_id
    )

    mission_all = _make_mission(
        integration_database, user_all, search_query="iPhone 17", sources=("amazon",)
    )
    with integration_database.sessions() as session:
        version = session.get(Mission, mission_all.id).state_version
    _select_all_variants(integration_database, user_id=user_all, mission_id=mission_all.id, version=version)

    mission_selected = _make_mission(
        integration_database, user_selected, search_query="iPhone 17", sources=("amazon",)
    )
    with integration_database.sessions() as session:
        version = session.get(Mission, mission_selected.id).state_version
    _select_variant(
        integration_database,
        user_id=user_selected,
        mission_id=mission_selected.id,
        product_id=product_256.id,
        version=version,
    )

    item_all = _monitoring_item_id_for(integration_database.sessions, mission_all.id)
    item_selected = _monitoring_item_id_for(integration_database.sessions, mission_selected.id)
    assert item_all is not None
    assert item_selected is not None
    assert item_all != item_selected
    assert _monitoring_item_count(integration_database.sessions) == 2


def test_switching_from_all_to_a_specific_selection_moves_the_link(integration_database) -> None:
    """Caso 7 do pedido: missão começa em ALL (escopo family) e depois o
    usuário restringe para uma variante específica (SELECTED) -- o
    vínculo tem que migrar de family para specific, histórico preservado,
    nunca dois algoritmos/duas chaves paralelas."""
    user = _seed_user(integration_database.sessions, "all-then-selected")
    amazon_id = _amazon_store_id(integration_database)
    product_256 = _seed_resolved_product(
        integration_database.sessions, text="iPhone 17 Pro 256GB", store_id=amazon_id
    )
    mission = _make_mission(integration_database, user, search_query="iPhone 17", sources=("amazon",))
    with integration_database.sessions() as session:
        version = session.get(Mission, mission.id).state_version
    _select_all_variants(integration_database, user_id=user, mission_id=mission.id, version=version)
    item_family = _monitoring_item_id_for(integration_database.sessions, mission.id)
    assert item_family is not None
    with integration_database.sessions() as session:
        assert session.get(MonitoringItem, item_family).canonical_identity["scope"] == "family"

    with integration_database.sessions() as session:
        version = session.get(Mission, mission.id).state_version
    _select_variant(
        integration_database, user_id=user, mission_id=mission.id, product_id=product_256.id, version=version
    )

    item_specific = _monitoring_item_id_for(integration_database.sessions, mission.id)
    assert item_specific is not None
    assert item_specific != item_family
    with integration_database.sessions() as session:
        item = session.get(MonitoringItem, item_specific)
    assert item.canonical_identity["scope"] == "specific"
    assert item.canonical_identity["attributes"]["storage_gb"] == "256"
    # item family preservado (histórico), mas sem ninguém mais precisando
    # dele agora -- store desativa, nada é apagado.
    row_family = _store_state(integration_database.sessions, item_family, amazon_id)
    assert row_family is not None and row_family.is_enabled is False
    with integration_database.sessions() as session:
        assert session.get(MonitoringItem, item_family) is not None
