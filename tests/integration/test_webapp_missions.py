"""Fluxo real das consultas de missão da aplicação web (TASK-092, item 2
da V1.2) contra PostgreSQL real -- listagem por status, contagem e
detalhe agregado (`app.missions.query`, funções novas desta TASK) --
mais a auditoria de 2026-08-22 (`DEC-075`): semântica de `state_version`,
ausência de lost update, posse centralizada, transições terminais e
`actor_type` por canal."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from app.missions.models import (
    Mission,
    MissionCommand,
    MissionCriteria,
    MissionSchedule,
    MissionSource,
    MissionStatus,
    MissionTransition,
)
from app.missions.query import (
    count_missions_for_user_by_status,
    get_mission_detail_for_user,
    get_mission_for_user,
    list_missions_for_user_by_status,
)
from app.missions.service import (
    InvalidMissionTransitionError,
    MissionEditConditionError,
    MissionVersionConflictError,
    create_mission_from_criteria_async,
    edit_mission_criteria,
    transition_mission_async,
)
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy import select

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def _seed_user(sessions, *, username: str) -> User:
    with sessions.begin() as session:
        user = User(display_name=username, role=UserRole.USER, username=username)
        session.add(user)
        session.flush()
        session.expunge(user)
    return user


def _seed_mission(
    sessions,
    *,
    user_id,
    status: MissionStatus,
    title: str,
    sources=("pichau",),
    expires_at=None,
    created_at=None,
) -> Mission:
    with sessions.begin() as session:
        stores = {
            store.code: store
            for store in session.scalars(select(Store).where(Store.code.in_(sources)))
        }
        mission = Mission(
            user_id=user_id,
            title=title,
            status=status,
            state_version=0,
            expires_at=expires_at,
            **({"created_at": created_at} if created_at is not None else {}),
        )
        session.add(mission)
        session.flush()
        session.add(MissionCriteria(mission_id=mission.id, search_query=title))
        for code in sources:
            session.add(MissionSource(mission_id=mission.id, store_id=stores[code].id))
        session.add(
            MissionSchedule(
                mission_id=mission.id,
                interval_minutes=60,
                next_run_at=NOW,
            )
        )
        session.flush()
        session.expunge(mission)
    return mission


def test_list_and_count_missions_by_status_against_real_postgres(
    integration_database,
) -> None:
    user = _seed_user(integration_database.sessions, username="webmissions1")
    other_user = _seed_user(integration_database.sessions, username="webmissions1b")
    active = _seed_mission(
        integration_database.sessions,
        user_id=user.id,
        status=MissionStatus.ACTIVE,
        title="Missão ativa",
    )
    paused = _seed_mission(
        integration_database.sessions,
        user_id=user.id,
        status=MissionStatus.PAUSED,
        title="Missão pausada",
    )
    _seed_mission(
        integration_database.sessions,
        user_id=user.id,
        status=MissionStatus.COMPLETED,
        title="Missão concluída",
    )
    _seed_mission(  # de outro usuário -- nunca deve aparecer
        integration_database.sessions,
        user_id=other_user.id,
        status=MissionStatus.ACTIVE,
        title="Missão de outro usuário",
    )

    async def run():
        async with integration_database.async_sessions.begin() as session:
            default_view = await list_missions_for_user_by_status(
                session,
                user_id=user.id,
                statuses=frozenset({MissionStatus.ACTIVE, MissionStatus.PAUSED}),
                limit=20,
                offset=0,
            )
            everything = await list_missions_for_user_by_status(
                session, user_id=user.id, statuses=None, limit=20, offset=0
            )
            only_completed = await list_missions_for_user_by_status(
                session,
                user_id=user.id,
                statuses=frozenset({MissionStatus.COMPLETED}),
                limit=20,
                offset=0,
            )
            total_default = await count_missions_for_user_by_status(
                session,
                user_id=user.id,
                statuses=frozenset({MissionStatus.ACTIVE, MissionStatus.PAUSED}),
            )
            total_all = await count_missions_for_user_by_status(
                session, user_id=user.id, statuses=None
            )
            return default_view, everything, only_completed, total_default, total_all

    default_view, everything, only_completed, total_default, total_all = asyncio.run(
        run()
    )

    default_ids = {mission.id for mission in default_view}
    assert default_ids == {active.id, paused.id}
    assert len(everything) == 3
    assert [mission.id for mission in only_completed] == [
        mission.id
        for mission in everything
        if mission.status == MissionStatus.COMPLETED
    ]
    assert total_default == 2
    assert total_all == 3


def test_list_missions_by_status_paginates_with_offset(integration_database) -> None:
    user = _seed_user(integration_database.sessions, username="webmissions2")
    for index in range(3):
        _seed_mission(
            integration_database.sessions,
            user_id=user.id,
            status=MissionStatus.ACTIVE,
            title=f"Missão {index}",
        )

    async def run():
        async with integration_database.async_sessions.begin() as session:
            page1 = await list_missions_for_user_by_status(
                session, user_id=user.id, statuses=None, limit=2, offset=0
            )
            page2 = await list_missions_for_user_by_status(
                session, user_id=user.id, statuses=None, limit=2, offset=2
            )
            return page1, page2

    page1, page2 = asyncio.run(run())

    assert len(page1) == 2
    assert len(page2) == 1
    assert {mission.id for mission in page1}.isdisjoint(
        {mission.id for mission in page2}
    )


def test_get_mission_detail_for_user_composes_real_rows(integration_database) -> None:
    user = _seed_user(integration_database.sessions, username="webmissions3")
    mission = _seed_mission(
        integration_database.sessions,
        user_id=user.id,
        status=MissionStatus.ACTIVE,
        title="Missão com detalhe",
        sources=("pichau", "kabum"),
    )

    async def run():
        async with integration_database.async_sessions.begin() as session:
            await transition_mission_async(
                session,
                mission_id=mission.id,
                command=MissionCommand.PAUSE,
                expected_state_version=0,
                actor_type="web",
                actor_id=user.id,
                transitioned_at=NOW,
            )
        async with integration_database.async_sessions.begin() as session:
            return await get_mission_detail_for_user(
                session, user_id=user.id, mission_id=mission.id
            )

    detail = asyncio.run(run())

    assert detail is not None
    assert detail.mission.id == mission.id
    assert detail.mission.status == MissionStatus.PAUSED
    assert detail.criteria is not None
    assert detail.criteria.search_query == "Missão com detalhe"
    assert {store.code for _source, store in detail.sources} == {"pichau", "kabum"}
    assert detail.schedule is not None
    assert len(detail.transitions) == 1
    assert detail.transitions[0].command == MissionCommand.PAUSE
    assert detail.transitions[0].to_status == MissionStatus.PAUSED


def test_get_mission_detail_for_user_returns_none_for_another_users_mission(
    integration_database,
) -> None:
    owner = _seed_user(integration_database.sessions, username="webmissions4")
    intruder = _seed_user(integration_database.sessions, username="webmissions4b")
    mission = _seed_mission(
        integration_database.sessions,
        user_id=owner.id,
        status=MissionStatus.ACTIVE,
        title="Missão privada",
    )

    async def run():
        async with integration_database.async_sessions.begin() as session:
            return await get_mission_detail_for_user(
                session, user_id=intruder.id, mission_id=mission.id
            )

    detail = asyncio.run(run())

    assert detail is None


def test_create_mission_from_criteria_async_is_visible_through_default_list_view(
    integration_database,
) -> None:
    """Prova de ponta a ponta da camada que a rota web chama: uma missão
    criada por `create_mission_from_criteria_async` (o mesmo caminho que o
    endpoint `POST /api/v1/missions` usa) aparece imediatamente na
    listagem padrão (ativas + pausadas), sem nenhuma tradução adicional."""
    user = _seed_user(integration_database.sessions, username="webmissions5")

    async def create() -> Mission:
        async with integration_database.async_sessions.begin() as session:
            mission, _sources = await create_mission_from_criteria_async(
                session,
                user_id=user.id,
                search_query="teclado mecânico",
                model=None,
                title=None,
                target_amount=None,
                target_currency=None,
                source_codes=("pichau",),
                requested_at=NOW,
                actor_type="web",
            )
            return mission

    async def list_visible() -> list[Mission]:
        async with integration_database.async_sessions.begin() as session:
            return await list_missions_for_user_by_status(
                session,
                user_id=user.id,
                statuses=frozenset({MissionStatus.ACTIVE, MissionStatus.PAUSED}),
                limit=20,
                offset=0,
            )

    mission = asyncio.run(create())
    visible = asyncio.run(list_visible())

    assert mission.status == MissionStatus.ACTIVE
    assert [item.id for item in visible] == [mission.id]


# --- Auditoria de 2026-08-22 (`DEC-075`) ------------------------------------


def test_get_mission_detail_for_user_returns_none_for_nonexistent_mission(
    integration_database,
) -> None:
    """Par da missão de outro usuário (`test_get_mission_detail_for_user_returns_none_for_another_users_mission`
    acima) -- as duas precisam devolver `None` do mesmo jeito, prova real
    de que a política de posse não distingue os dois casos."""

    user = _seed_user(integration_database.sessions, username="webmissions6")

    async def run():
        async with integration_database.async_sessions.begin() as session:
            return await get_mission_detail_for_user(
                session, user_id=user.id, mission_id=uuid4()
            )

    assert asyncio.run(run()) is None


def test_get_mission_for_user_ownership_primitive(integration_database) -> None:
    """`get_mission_for_user` (TASK-092, auditoria) é o primitivo de posse
    compartilhado -- prova direta contra Postgres real: dono vê, outro
    usuário e ID inexistente não veem, os dois últimos indistinguíveis."""

    owner = _seed_user(integration_database.sessions, username="webmissions7")
    intruder = _seed_user(integration_database.sessions, username="webmissions7b")
    mission = _seed_mission(
        integration_database.sessions,
        user_id=owner.id,
        status=MissionStatus.ACTIVE,
        title="Missão do dono",
    )

    async def run():
        async with integration_database.async_sessions.begin() as session:
            owned = await get_mission_for_user(
                session, user_id=owner.id, mission_id=mission.id
            )
            not_owned = await get_mission_for_user(
                session, user_id=intruder.id, mission_id=mission.id
            )
            nonexistent = await get_mission_for_user(
                session, user_id=owner.id, mission_id=uuid4()
            )
            return owned, not_owned, nonexistent

    owned, not_owned, nonexistent = asyncio.run(run())

    assert owned is not None and owned.id == mission.id
    assert not_owned is None
    assert nonexistent is None


def test_lost_update_is_prevented_by_state_version(integration_database) -> None:
    """Ponto 5 da auditoria: dois clientes leem a mesma versão de uma
    missão `PAUSED`; A edita o preço-alvo, B tenta editar as lojas
    baseado na MESMA versão que A já invalidou -- B precisa ser
    rejeitado com `MissionVersionConflictError` (`409` na web), nunca
    "a última escrita silenciosamente vence"."""
    user = _seed_user(integration_database.sessions, username="webmissions8")
    mission = _seed_mission(
        integration_database.sessions,
        user_id=user.id,
        status=MissionStatus.PAUSED,
        title="Missão concorrente",
        sources=("pichau", "kabum"),
    )
    version_seen_by_both_clients = mission.state_version  # == 0

    async def client_a_edits_target():
        async with integration_database.async_sessions.begin() as session:
            await edit_mission_criteria(
                session,
                mission_id=mission.id,
                expected_state_version=version_seen_by_both_clients,
                target_update=(Decimal("999.00"), "BRL"),
                source_codes=None,
                edited_at=NOW,
            )

    async def client_b_edits_sources_with_stale_version():
        async with integration_database.async_sessions.begin() as session:
            await edit_mission_criteria(
                session,
                mission_id=mission.id,
                expected_state_version=version_seen_by_both_clients,  # obsoleta -- A já editou
                target_update=None,
                source_codes=("amazon",),
                edited_at=NOW,
            )

    asyncio.run(client_a_edits_target())  # sucesso, incrementa state_version para 1
    with pytest.raises(MissionVersionConflictError):
        asyncio.run(client_b_edits_sources_with_stale_version())

    # Confirma que a alteração de A sobreviveu intacta -- nada foi
    # silenciosamente sobrescrito por B (que nunca chegou a escrever).
    with integration_database.sessions.begin() as session:
        criteria = session.scalar(
            select(MissionCriteria).where(MissionCriteria.mission_id == mission.id)
        )
        assert criteria.target_amount == Decimal("999.0000")
        source_codes = set(
            session.scalars(
                select(Store.code)
                .join(MissionSource, MissionSource.store_id == Store.id)
                .where(MissionSource.mission_id == mission.id)
            )
        )
        assert source_codes == {"pichau", "kabum"}  # inalterado -- B nunca escreveu

        current = session.get(Mission, mission.id)
        assert current.state_version == 1  # só a edição de A contou


def test_terminal_mission_rejects_resume_and_pause_for_real(
    integration_database,
) -> None:
    """Ponto 10 da auditoria: a confirmação de cancelamento no React é só
    UX -- o domínio real precisa continuar recusando comandos de
    lifecycle numa missão `CANCELLED`, mesmo chamado direto (sem passar
    pela SPA)."""
    user = _seed_user(integration_database.sessions, username="webmissions9")
    mission = _seed_mission(
        integration_database.sessions,
        user_id=user.id,
        status=MissionStatus.CANCELLED,
        title="Missão cancelada",
    )

    async def try_resume():
        async with integration_database.async_sessions.begin() as session:
            await transition_mission_async(
                session,
                mission_id=mission.id,
                command=MissionCommand.RESUME,
                expected_state_version=0,
                actor_type="web",
                actor_id=user.id,
                transitioned_at=NOW,
            )

    async def try_pause():
        async with integration_database.async_sessions.begin() as session:
            await transition_mission_async(
                session,
                mission_id=mission.id,
                command=MissionCommand.PAUSE,
                expected_state_version=0,
                actor_type="web",
                actor_id=user.id,
                transitioned_at=NOW,
            )

    with pytest.raises(InvalidMissionTransitionError):
        asyncio.run(try_resume())
    with pytest.raises(InvalidMissionTransitionError):
        asyncio.run(try_pause())

    with integration_database.sessions.begin() as session:
        current = session.get(Mission, mission.id)
        assert current.status == MissionStatus.CANCELLED  # nunca mudou
        assert current.state_version == 0


def test_edit_rejected_for_real_when_mission_is_not_paused(
    integration_database,
) -> None:
    """A edição inline só aparece na SPA quando `status == 'paused'` --
    isso é regra visual; a proteção real precisa estar no domínio."""
    user = _seed_user(integration_database.sessions, username="webmissions10")
    mission = _seed_mission(
        integration_database.sessions,
        user_id=user.id,
        status=MissionStatus.ACTIVE,
        title="Missão ativa",
    )

    async def try_edit():
        async with integration_database.async_sessions.begin() as session:
            await edit_mission_criteria(
                session,
                mission_id=mission.id,
                expected_state_version=0,
                target_update=(Decimal("100.00"), "BRL"),
                source_codes=None,
                edited_at=NOW,
            )

    with pytest.raises(MissionEditConditionError, match="pausada"):
        asyncio.run(try_edit())


def test_actor_type_is_recorded_correctly_per_channel(integration_database) -> None:
    """Ponto 12 da auditoria: `actor_type` agora é obrigatório (sem
    padrão) em `create_mission_from_criteria_async` -- prova de que Web e
    Telegram continuam registrando o ator correto, cada um explicitamente."""
    user = _seed_user(integration_database.sessions, username="webmissions11")

    async def create(actor_type: str) -> Mission:
        async with integration_database.async_sessions.begin() as session:
            mission, _sources = await create_mission_from_criteria_async(
                session,
                user_id=user.id,
                search_query=f"produto via {actor_type}",
                model=None,
                title=None,
                target_amount=None,
                target_currency=None,
                source_codes=("pichau",),
                requested_at=NOW,
                actor_type=actor_type,
            )
            return mission

    web_mission = asyncio.run(create("web"))
    telegram_mission = asyncio.run(create("telegram"))

    with integration_database.sessions.begin() as session:
        web_transition = session.scalar(
            select(MissionTransition).where(
                MissionTransition.mission_id == web_mission.id
            )
        )
        telegram_transition = session.scalar(
            select(MissionTransition).where(
                MissionTransition.mission_id == telegram_mission.id
            )
        )
        assert web_transition.actor_type == "web"
        assert telegram_transition.actor_type == "telegram"


@pytest.mark.parametrize(
    "status_filter",
    [
        MissionStatus.ACTIVE,
        MissionStatus.PAUSED,
        MissionStatus.CANCELLED,
        MissionStatus.COMPLETED,
    ],
)
def test_every_terminal_and_operational_status_is_filterable(
    integration_database, status_filter: MissionStatus
) -> None:
    """Ponto 1/17 da auditoria: cada status documentado precisa ser
    consultável isoladamente -- missões canceladas/concluídas/expiradas
    continuam persistidas (nunca apagadas fisicamente), o filtro só
    consulta o histórico real."""
    user = _seed_user(
        integration_database.sessions, username=f"webmissions12{status_filter.value}"
    )
    matching = _seed_mission(
        integration_database.sessions,
        user_id=user.id,
        status=status_filter,
        title=f"Missão {status_filter.value}",
    )
    non_matching_status = (
        MissionStatus.ACTIVE
        if status_filter is not MissionStatus.ACTIVE
        else MissionStatus.PAUSED
    )
    _seed_mission(
        integration_database.sessions,
        user_id=user.id,
        status=non_matching_status,
        title="Não deveria aparecer neste filtro",
    )

    async def run():
        async with integration_database.async_sessions.begin() as session:
            return await list_missions_for_user_by_status(
                session,
                user_id=user.id,
                statuses=frozenset({status_filter}),
                limit=20,
                offset=0,
            )

    result = asyncio.run(run())

    assert [mission.id for mission in result] == [matching.id]


def test_expired_status_is_filterable_reached_via_the_real_domain_transition(
    integration_database,
) -> None:
    """`expired` não é semeado direto (evitaria testar um estado
    "artificialmente impossível") -- alcançado via `EXPIRE` real, exigindo
    prazo já vencido, exatamente como o domínio impõe."""
    user = _seed_user(integration_database.sessions, username="webmissions13")
    # `created_at` fixo (não o default real de agora) para que
    # `expires_at` possa ficar no passado em relação a `NOW` sem violar
    # `ck_missions_expiration_after_creation` (exige expires_at > created_at).
    mission = _seed_mission(
        integration_database.sessions,
        user_id=user.id,
        status=MissionStatus.ACTIVE,
        title="Missão com prazo vencido",
        created_at=NOW - timedelta(hours=2),
        expires_at=NOW - timedelta(hours=1),
    )

    async def expire():
        async with integration_database.async_sessions.begin() as session:
            await transition_mission_async(
                session,
                mission_id=mission.id,
                command=MissionCommand.EXPIRE,
                expected_state_version=0,
                actor_type="web",
                actor_id=user.id,
                transitioned_at=NOW,  # depois do prazo -- EXPIRE é permitido
            )

    async def list_expired():
        async with integration_database.async_sessions.begin() as session:
            return await list_missions_for_user_by_status(
                session,
                user_id=user.id,
                statuses=frozenset({MissionStatus.EXPIRED}),
                limit=20,
                offset=0,
            )

    asyncio.run(expire())
    result = asyncio.run(list_expired())

    assert [item.id for item in result] == [mission.id]
