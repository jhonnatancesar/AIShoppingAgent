"""Fluxo real de edição de critérios de missão já criada (TASK-069)."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from app.missions.models import (
    Mission,
    MissionCriteria,
    MissionSource,
    MissionStatus,
)
from app.missions.service import (
    MissionEditConditionError,
    MissionVersionConflictError,
    edit_mission_criteria,
)
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy import select

pytestmark = pytest.mark.integration


def _edit(integration_database, **kwargs):
    async def run():
        async with integration_database.async_sessions.begin() as session:
            return await edit_mission_criteria(session, **kwargs)

    return asyncio.run(run())


def _seed_paused_mission(sessions, *, sources: tuple[str, ...] = ("kabum", "pichau")):
    with sessions.begin() as session:
        stores = {
            store.code: store
            for store in session.scalars(select(Store).where(Store.code.in_(sources)))
        }
        user = User(display_name="TASK-069 synthetic", role=UserRole.USER)
        session.add(user)
        session.flush()
        mission = Mission(
            user_id=user.id,
            title="TASK-069 edit target",
            status=MissionStatus.PAUSED,
            state_version=1,
        )
        session.add(mission)
        session.flush()
        session.add(
            MissionCriteria(
                mission_id=mission.id,
                search_query="synthetic keyboard",
                target_amount=Decimal("500.00"),
                target_currency="BRL",
            )
        )
        session.add_all(
            MissionSource(mission_id=mission.id, store_id=stores[code].id)
            for code in sources
        )
        return mission.id, mission.state_version


def test_edit_updates_target_and_sources_together_and_preserves_history(
    integration_database,
) -> None:
    mission_id, version = _seed_paused_mission(integration_database.sessions)

    mission, effective_codes = _edit(
        integration_database,
        mission_id=mission_id,
        expected_state_version=version,
        target_update=(Decimal("300.00"), "BRL"),
        source_codes=("pichau", "terabyte"),
        edited_at=datetime.now(UTC),
    )
    assert mission.status is MissionStatus.PAUSED  # nunca muda status
    assert mission.state_version == version  # edição não é transição

    assert set(effective_codes) == {"pichau", "terabyte"}
    with integration_database.sessions.begin() as session:
        criteria = session.scalar(
            select(MissionCriteria).where(MissionCriteria.mission_id == mission_id)
        )
        assert criteria.target_amount == Decimal("300.0000")
        assert criteria.target_currency == "BRL"

        source_codes = set(
            session.scalars(
                select(Store.code)
                .join(MissionSource, MissionSource.store_id == Store.id)
                .where(MissionSource.mission_id == mission_id)
            )
        )
        assert source_codes == {"pichau", "terabyte"}  # kabum saiu, terabyte entrou

        mission = session.get(Mission, mission_id)
        assert mission.status is MissionStatus.PAUSED
        assert mission.state_version == version


def test_edit_clears_target_price(integration_database) -> None:
    mission_id, version = _seed_paused_mission(integration_database.sessions)

    _edit(
        integration_database,
        mission_id=mission_id,
        expected_state_version=version,
        target_update=(None, None),
        source_codes=None,
    )

    with integration_database.sessions.begin() as session:
        criteria = session.scalar(
            select(MissionCriteria).where(MissionCriteria.mission_id == mission_id)
        )
        assert criteria.target_amount is None
        assert criteria.target_currency is None


def test_edit_rejects_active_mission_and_stale_version(integration_database) -> None:
    mission_id, version = _seed_paused_mission(integration_database.sessions)
    with integration_database.sessions.begin() as session:
        mission = session.get(Mission, mission_id)
        mission.status = MissionStatus.ACTIVE

    with pytest.raises(MissionEditConditionError, match="pausada"):
        _edit(
            integration_database,
            mission_id=mission_id,
            expected_state_version=version,
            target_update=None,
            source_codes=("kabum",),
        )

    with integration_database.sessions.begin() as session:
        mission = session.get(Mission, mission_id)
        mission.status = MissionStatus.PAUSED

    with pytest.raises(MissionVersionConflictError):
        _edit(
            integration_database,
            mission_id=mission_id,
            expected_state_version=version + 1,
            target_update=None,
            source_codes=("kabum",),
        )


def test_edit_rejects_zeroing_out_every_selected_store(integration_database) -> None:
    mission_id, version = _seed_paused_mission(
        integration_database.sessions, sources=("kabum",)
    )

    with pytest.raises(MissionEditConditionError, match="ao menos uma loja"):
        _edit(
            integration_database,
            mission_id=mission_id,
            expected_state_version=version,
            target_update=None,
            source_codes=(),
        )


def test_removing_a_store_never_touches_its_price_history(integration_database) -> None:
    """TASK-069: histórico de `PriceObservation`/`CollectionRun` de uma loja
    removida nunca é apagado -- só a linha de `MissionSource` some."""
    from app.collection.models import CollectionRun, CollectionRunStatus

    mission_id, version = _seed_paused_mission(
        integration_database.sessions, sources=("kabum", "pichau")
    )
    with integration_database.sessions.begin() as session:
        kabum_id = session.scalar(select(Store.id).where(Store.code == "kabum"))
        run = CollectionRun(
            id=uuid4(),
            mission_id=mission_id,
            store_id=kabum_id,
            status=CollectionRunStatus.SUCCEEDED,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(run)
        run_id = run.id

    # remove kabum da missão, mantém só pichau.
    _edit(
        integration_database,
        mission_id=mission_id,
        expected_state_version=version,
        target_update=None,
        source_codes=("pichau",),
    )

    with integration_database.sessions.begin() as session:
        remaining_source = session.scalar(
            select(MissionSource.store_id).where(
                MissionSource.mission_id == mission_id,
                MissionSource.store_id == kabum_id,
            )
        )
        assert remaining_source is None  # a seleção da loja sumiu

        preserved_run = session.get(CollectionRun, run_id)
        assert preserved_run is not None  # o histórico da coleta não
        assert preserved_run.store_id == kabum_id
