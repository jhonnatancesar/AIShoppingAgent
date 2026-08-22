"""Testes das consultas somente leitura de missões por proprietário."""

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.missions.models import MissionStatus
from app.missions.query import (
    MissionReferenceError,
    count_missions_for_user_by_status,
    find_missions_by_reference,
    get_mission_detail_for_user,
    list_missions_for_user,
    list_missions_for_user_by_status,
    list_visible_missions_for_user,
    resolve_mission_for_command,
)


class _FakeMission:
    def __init__(self, status: MissionStatus = MissionStatus.ACTIVE) -> None:
        self.id = uuid4()
        self.status = status


def _session(missions: list[_FakeMission]) -> MagicMock:
    session = MagicMock()
    session.scalars = AsyncMock(return_value=missions)
    return session


def test_list_missions_for_user_returns_scalars_result() -> None:
    missions = [_FakeMission(), _FakeMission()]
    session = _session(missions)

    result = asyncio.run(list_missions_for_user(session, user_id=uuid4()))

    assert result == missions


def test_list_visible_missions_for_user_filters_owner_and_allowed_statuses() -> None:
    missions = [_FakeMission(MissionStatus.ACTIVE)]
    session = _session(missions)
    user_id = uuid4()

    result = asyncio.run(
        list_visible_missions_for_user(session, user_id=user_id, limit=16)
    )

    assert result == missions
    statement = session.scalars.await_args.args[0]
    compiled = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert user_id.hex in compiled
    assert "missions.status IN ('active', 'paused', 'cancelled')" in compiled
    active_position = compiled.index("missions.status = 'active'")
    paused_position = compiled.index("missions.status = 'paused'")
    cancelled_position = compiled.index("missions.status = 'cancelled'")
    assert active_position < paused_position < cancelled_position
    assert "LIMIT 16" in compiled


def test_find_missions_by_reference_returns_scalars_result() -> None:
    missions = [_FakeMission()]
    session = _session(missions)

    result = asyncio.run(
        find_missions_by_reference(session, user_id=uuid4(), reference="notebook")
    )

    assert result == missions


def test_resolve_mission_for_command_with_reference_returns_single_match() -> None:
    mission = _FakeMission()
    session = _session([mission])

    resolved = asyncio.run(
        resolve_mission_for_command(session, user_id=uuid4(), reference="notebook")
    )

    assert resolved is mission


def test_resolve_mission_for_command_with_reference_rejects_zero_matches() -> None:
    session = _session([])

    with pytest.raises(MissionReferenceError, match="Não encontrei"):
        asyncio.run(
            resolve_mission_for_command(session, user_id=uuid4(), reference="notebook")
        )


def test_resolve_mission_for_command_with_reference_rejects_multiple_matches() -> None:
    session = _session([_FakeMission(), _FakeMission()])

    with pytest.raises(MissionReferenceError, match="mais de uma"):
        asyncio.run(
            resolve_mission_for_command(session, user_id=uuid4(), reference="notebook")
        )


def test_resolve_mission_for_command_without_reference_uses_only_non_terminal() -> None:
    active = _FakeMission(MissionStatus.ACTIVE)
    completed = _FakeMission(MissionStatus.COMPLETED)
    session = _session([active, completed])

    resolved = asyncio.run(
        resolve_mission_for_command(session, user_id=uuid4(), reference=None)
    )

    assert resolved is active


def test_resolve_mission_for_command_without_reference_rejects_multiple_non_terminal() -> (
    None
):
    session = _session(
        [_FakeMission(MissionStatus.ACTIVE), _FakeMission(MissionStatus.PAUSED)]
    )

    with pytest.raises(MissionReferenceError, match="mais de uma"):
        asyncio.run(
            resolve_mission_for_command(session, user_id=uuid4(), reference=None)
        )


def test_resolve_mission_for_command_without_reference_rejects_no_non_terminal() -> (
    None
):
    session = _session([_FakeMission(MissionStatus.COMPLETED)])

    with pytest.raises(MissionReferenceError, match="Não encontrei"):
        asyncio.run(
            resolve_mission_for_command(session, user_id=uuid4(), reference=None)
        )


def test_list_missions_for_user_by_status_without_filter_lists_everything() -> None:
    missions = [_FakeMission(MissionStatus.ACTIVE), _FakeMission(MissionStatus.EXPIRED)]
    session = _session(missions)
    user_id = uuid4()

    result = asyncio.run(
        list_missions_for_user_by_status(
            session, user_id=user_id, statuses=None, limit=20, offset=0
        )
    )

    assert result == missions
    statement = session.scalars.await_args.args[0]
    compiled = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert user_id.hex in compiled
    assert "status IN" not in compiled
    assert "LIMIT 20" in compiled
    # DEC-075 (correção de 2026-08-22): updated_at decrescente -- pausar/
    # retomar/editar/cancelar sobem a missão na lista, não só criar -- com
    # id decrescente como desempate determinístico sob paginação.
    assert "ORDER BY missions.updated_at DESC, missions.id DESC" in compiled


def test_list_missions_for_user_by_status_filters_and_paginates() -> None:
    missions = [_FakeMission(MissionStatus.ACTIVE)]
    session = _session(missions)
    user_id = uuid4()

    result = asyncio.run(
        list_missions_for_user_by_status(
            session,
            user_id=user_id,
            statuses=frozenset({MissionStatus.ACTIVE, MissionStatus.PAUSED}),
            limit=20,
            offset=40,
        )
    )

    assert result == missions
    statement = session.scalars.await_args.args[0]
    compiled = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "missions.status IN" in compiled
    assert "'active'" in compiled
    assert "'paused'" in compiled
    assert "LIMIT 20" in compiled
    assert "OFFSET 40" in compiled


def test_count_missions_for_user_by_status_returns_scalar() -> None:
    session = MagicMock()
    session.scalar = AsyncMock(return_value=7)

    total = asyncio.run(
        count_missions_for_user_by_status(session, user_id=uuid4(), statuses=None)
    )

    assert total == 7


def test_count_missions_for_user_by_status_defaults_to_zero_for_none() -> None:
    session = MagicMock()
    session.scalar = AsyncMock(return_value=None)

    total = asyncio.run(
        count_missions_for_user_by_status(session, user_id=uuid4(), statuses=None)
    )

    assert total == 0


def test_get_mission_detail_for_user_returns_none_when_mission_not_found() -> None:
    session = MagicMock()
    session.scalar = AsyncMock(return_value=None)

    detail = asyncio.run(
        get_mission_detail_for_user(session, user_id=uuid4(), mission_id=uuid4())
    )

    assert detail is None
    session.execute.assert_not_called()


def test_get_mission_detail_for_user_composes_all_parts() -> None:
    mission = _FakeMission(MissionStatus.PAUSED)
    criteria = MagicMock()
    schedule = MagicMock()
    source_row = (MagicMock(), MagicMock())
    transition = MagicMock()

    session = MagicMock()
    session.scalar = AsyncMock(side_effect=[mission, criteria, schedule])
    execute_result = MagicMock()
    execute_result.all.return_value = [source_row]
    session.execute = AsyncMock(return_value=execute_result)
    session.scalars = AsyncMock(return_value=[transition])

    detail = asyncio.run(
        get_mission_detail_for_user(session, user_id=uuid4(), mission_id=mission.id)
    )

    assert detail is not None
    assert detail.mission is mission
    assert detail.criteria is criteria
    assert detail.schedule is schedule
    assert detail.sources == [source_row]
    assert detail.transitions == [transition]
