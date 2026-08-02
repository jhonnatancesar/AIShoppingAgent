"""Testes do histórico e da execução de transições de missão."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.database.base import Base
from app.database.model_registry import REGISTERED_MODELS
from app.missions.models import (
    Mission,
    MissionCommand,
    MissionStatus,
    MissionTransition,
)
from app.missions.service import (
    TRANSITIONS,
    InvalidMissionTransitionError,
    MissionNotFoundError,
    MissionTransitionConditionError,
    MissionVersionConflictError,
    transition_mission,
)
from sqlalchemy import CheckConstraint, Enum, ForeignKeyConstraint, Index

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
    session.scalar.side_effect = scalar_results
    return session


def test_transition_contract_contains_exact_lifecycle() -> None:
    assert set(TRANSITIONS.items()) == {
        ((MissionStatus.DRAFT, MissionCommand.ACTIVATE), MissionStatus.ACTIVE),
        ((MissionStatus.DRAFT, MissionCommand.CANCEL), MissionStatus.CANCELLED),
        ((MissionStatus.DRAFT, MissionCommand.EXPIRE), MissionStatus.EXPIRED),
        ((MissionStatus.ACTIVE, MissionCommand.PAUSE), MissionStatus.PAUSED),
        ((MissionStatus.ACTIVE, MissionCommand.COMPLETE), MissionStatus.COMPLETED),
        ((MissionStatus.ACTIVE, MissionCommand.CANCEL), MissionStatus.CANCELLED),
        ((MissionStatus.ACTIVE, MissionCommand.EXPIRE), MissionStatus.EXPIRED),
        ((MissionStatus.PAUSED, MissionCommand.RESUME), MissionStatus.ACTIVE),
        ((MissionStatus.PAUSED, MissionCommand.COMPLETE), MissionStatus.COMPLETED),
        ((MissionStatus.PAUSED, MissionCommand.CANCEL), MissionStatus.CANCELLED),
        ((MissionStatus.PAUSED, MissionCommand.EXPIRE), MissionStatus.EXPIRED),
    }


def test_transition_updates_state_version_and_appends_history() -> None:
    mission = _mission(MissionStatus.DRAFT)
    session = _session(mission, uuid4(), uuid4())

    transition = transition_mission(
        session,
        mission_id=mission.id,
        command=MissionCommand.ACTIVATE,
        expected_state_version=0,
        actor_type="user",
        actor_id=mission.user_id,
        transitioned_at=NOW,
    )

    assert mission.status is MissionStatus.ACTIVE
    assert mission.state_version == 1
    assert mission.updated_at == NOW
    assert transition.from_status is MissionStatus.DRAFT
    assert transition.to_status is MissionStatus.ACTIVE
    assert transition.command is MissionCommand.ACTIVATE
    session.add.assert_called_once_with(transition)
    session.flush.assert_called_once_with()


@pytest.mark.parametrize(
    ("actor_type", "reason", "version", "transitioned_at"),
    [
        (" ", None, 0, NOW),
        ("x" * 33, None, 0, NOW),
        ("user", " ", 0, NOW),
        ("user", None, -1, NOW),
        ("user", None, 0, datetime(2026, 8, 2, 15, 0)),
    ],
)
def test_transition_rejects_invalid_input(
    actor_type: str,
    reason: str | None,
    version: int,
    transitioned_at: datetime,
) -> None:
    with pytest.raises(ValueError):
        transition_mission(
            _session(),
            mission_id=uuid4(),
            command=MissionCommand.CANCEL,
            expected_state_version=version,
            actor_type=actor_type,
            reason=reason,
            transitioned_at=transitioned_at,
        )


def test_transition_rejects_missing_mission() -> None:
    with pytest.raises(MissionNotFoundError):
        transition_mission(
            _session(None),
            mission_id=uuid4(),
            command=MissionCommand.CANCEL,
            expected_state_version=0,
            actor_type="user",
            transitioned_at=NOW,
        )


def test_transition_rejects_stale_version() -> None:
    mission = _mission()
    mission.state_version = 2

    with pytest.raises(MissionVersionConflictError):
        transition_mission(
            _session(mission),
            mission_id=mission.id,
            command=MissionCommand.CANCEL,
            expected_state_version=1,
            actor_type="user",
            transitioned_at=NOW,
        )


def test_terminal_state_rejects_every_command() -> None:
    mission = _mission(MissionStatus.COMPLETED)

    with pytest.raises(InvalidMissionTransitionError):
        transition_mission(
            _session(mission),
            mission_id=mission.id,
            command=MissionCommand.CANCEL,
            expected_state_version=0,
            actor_type="system",
            transitioned_at=NOW,
        )


def test_activation_requires_criteria() -> None:
    mission = _mission()

    with pytest.raises(MissionTransitionConditionError):
        transition_mission(
            _session(mission, None),
            mission_id=mission.id,
            command=MissionCommand.ACTIVATE,
            expected_state_version=0,
            actor_type="user",
            transitioned_at=NOW,
        )


def test_activation_requires_selected_source() -> None:
    mission = _mission()

    with pytest.raises(MissionTransitionConditionError):
        transition_mission(
            _session(mission, uuid4(), None),
            mission_id=mission.id,
            command=MissionCommand.ACTIVATE,
            expected_state_version=0,
            actor_type="user",
            transitioned_at=NOW,
        )


def test_resume_rejects_reached_deadline() -> None:
    mission = _mission(MissionStatus.PAUSED, expires_at=NOW)

    with pytest.raises(MissionTransitionConditionError):
        transition_mission(
            _session(mission, uuid4(), uuid4()),
            mission_id=mission.id,
            command=MissionCommand.RESUME,
            expected_state_version=0,
            actor_type="user",
            transitioned_at=NOW,
        )


@pytest.mark.parametrize("expires_at", [None, NOW + timedelta(seconds=1)])
def test_expiration_requires_reached_deadline(expires_at: datetime | None) -> None:
    mission = _mission(MissionStatus.ACTIVE, expires_at=expires_at)

    with pytest.raises(MissionTransitionConditionError):
        transition_mission(
            _session(mission),
            mission_id=mission.id,
            command=MissionCommand.EXPIRE,
            expected_state_version=0,
            actor_type="system",
            transitioned_at=NOW,
        )


def test_expiration_succeeds_at_deadline() -> None:
    mission = _mission(MissionStatus.ACTIVE, expires_at=NOW)

    transition = transition_mission(
        _session(mission),
        mission_id=mission.id,
        command=MissionCommand.EXPIRE,
        expected_state_version=0,
        actor_type="system",
        transitioned_at=NOW,
    )

    assert transition.to_status is MissionStatus.EXPIRED


def test_transition_table_matches_history_contract() -> None:
    table = MissionTransition.__table__
    assert [column.name for column in table.columns] == [
        "id",
        "mission_id",
        "from_status",
        "to_status",
        "command",
        "actor_type",
        "actor_id",
        "reason",
        "transitioned_at",
    ]
    assert {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    } == {
        "ck_mission_transitions_status_changed",
        "ck_mission_transitions_actor_type_not_blank",
        "ck_mission_transitions_reason_not_blank",
        "mission_command_values",
    }
    assert isinstance(table.c.command.type, Enum)
    assert table.c.command.type.native_enum is False
    assert table.c.command.type.length == 32
    foreign_keys = {
        tuple(constraint.columns)[0].name: tuple(constraint.elements)[0]
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert foreign_keys["mission_id"].target_fullname == "missions.id"
    assert foreign_keys["actor_id"].target_fullname == "users.id"
    assert all(element.ondelete == "RESTRICT" for element in foreign_keys.values())
    index = next(index for index in table.indexes if isinstance(index, Index))
    assert index.name == "ix_mission_transitions_history"
    assert tuple(column.name for column in index.columns) == (
        "mission_id",
        "transitioned_at",
        "id",
    )


def test_transition_model_is_registered_in_shared_metadata() -> None:
    assert MissionTransition in REGISTERED_MODELS
    assert Base.metadata.tables["mission_transitions"] is MissionTransition.__table__
