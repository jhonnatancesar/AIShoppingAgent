"""Equivalentes assíncronos do serviço de missões (extensão da TASK-079,
usados pelo webhook Telegram) -- mesmas regras de
`tests/test_mission_transitions.py`/`tests/test_mission_creation.py`,
cobrindo `transition_mission_async`/`create_mission_from_criteria_async`."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.missions.models import (
    Mission,
    MissionCommand,
    MissionSchedule,
    MissionStatus,
)
from app.missions.service import (
    MissionNotFoundError,
    MissionVersionConflictError,
    create_mission_from_criteria_async,
    transition_mission_async,
)

NOW = datetime(2026, 8, 2, 15, 0, tzinfo=UTC)


def _mission(
    status: MissionStatus = MissionStatus.DRAFT,
    *,
    expires_at: datetime | None = None,
) -> Mission:
    return Mission(
        id=uuid4(),
        user_id=uuid4(),
        title="Comprar notebook",
        status=status,
        state_version=0,
        expires_at=expires_at,
        created_at=NOW - timedelta(days=1),
        updated_at=NOW - timedelta(days=1),
    )


def _session(*scalar_results: object) -> MagicMock:
    session = MagicMock()
    session.scalar = AsyncMock(side_effect=scalar_results)
    session.flush = AsyncMock()
    return session


def test_transition_mission_async_updates_state_version_and_appends_history() -> None:
    mission = _mission(MissionStatus.DRAFT)
    session = _session(mission, uuid4(), uuid4())

    transition = asyncio.run(
        transition_mission_async(
            session,
            mission_id=mission.id,
            command=MissionCommand.ACTIVATE,
            expected_state_version=0,
            actor_type="user",
            actor_id=mission.user_id,
            transitioned_at=NOW,
        )
    )

    assert mission.status is MissionStatus.ACTIVE
    assert mission.state_version == 1
    assert transition.from_status is MissionStatus.DRAFT
    assert transition.to_status is MissionStatus.ACTIVE
    session.add.assert_called_once_with(transition)
    session.flush.assert_called_once_with()


def test_transition_mission_async_rejects_missing_mission() -> None:
    with pytest.raises(MissionNotFoundError):
        asyncio.run(
            transition_mission_async(
                _session(None),
                mission_id=uuid4(),
                command=MissionCommand.CANCEL,
                expected_state_version=0,
                actor_type="user",
                transitioned_at=NOW,
            )
        )


def test_transition_mission_async_rejects_stale_version() -> None:
    mission = _mission()
    mission.state_version = 2

    with pytest.raises(MissionVersionConflictError):
        asyncio.run(
            transition_mission_async(
                _session(mission),
                mission_id=mission.id,
                command=MissionCommand.CANCEL,
                expected_state_version=1,
                actor_type="user",
                transitioned_at=NOW,
            )
        )


class _FakeStore:
    def __init__(self, code: str) -> None:
        self.code = code
        self.id = uuid4()


def _creation_session(stores: list[_FakeStore]) -> MagicMock:
    """Sessão mockada onde `scalar` "encontra de volta" a missão recém
    adicionada, simulando o que uma transação real faria dentro de
    `transition_mission_async` logo após `create_mission_from_criteria_async`
    inseri-la."""
    session = MagicMock()
    session.scalars = AsyncMock(return_value=stores)
    session.flush = AsyncMock()
    added_missions: list[Mission] = []

    def _capture_add(obj: object) -> None:
        if isinstance(obj, Mission):
            added_missions.append(obj)

    session.add.side_effect = _capture_add

    scalar_calls = {"count": 0}

    async def _scalar_side_effect(*_args: object, **_kwargs: object) -> object:
        scalar_calls["count"] += 1
        if scalar_calls["count"] == 1:
            return added_missions[-1]
        return uuid4()  # critério e fonte existentes, ambos apenas precisam ser truthy

    session.scalar = AsyncMock(side_effect=_scalar_side_effect)
    return session


def test_create_mission_from_criteria_async_activates_with_explicit_sources() -> None:
    stores = [_FakeStore("pichau"), _FakeStore("kabum")]
    session = _creation_session(stores)

    mission, sources = asyncio.run(
        create_mission_from_criteria_async(
            session,
            user_id=uuid4(),
            search_query="notebook gamer",
            target_amount=Decimal("5000.00"),
            target_currency="BRL",
            source_codes=("pichau", "kabum"),
            requested_at=NOW,
        )
    )

    assert sources == ("pichau", "kabum")
    assert mission.status is MissionStatus.ACTIVE
    assert mission.title == "notebook gamer"
    schedule = next(
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], MissionSchedule)
    )
    assert schedule.mission_id == mission.id
    assert schedule.next_run_at == NOW
    assert schedule.interval_minutes == 60
