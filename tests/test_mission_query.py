"""Testes das consultas somente leitura de missões por proprietário."""

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.missions.models import MissionStatus
from app.missions.query import (
    MissionReferenceError,
    find_missions_by_reference,
    list_missions_for_user,
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
