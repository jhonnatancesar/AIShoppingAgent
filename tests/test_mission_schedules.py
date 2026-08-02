"""Testes do modelo e das regras de agenda de missões."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.database.base import Base
from app.database.model_registry import REGISTERED_MODELS
from app.missions.models import MissionSchedule
from app.missions.schedule import advance_schedule, find_due_schedules
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint

NOW = datetime(2026, 8, 2, 16, 0, tzinfo=UTC)


def _schedule(**overrides: object) -> MissionSchedule:
    values: dict[str, object] = {
        "mission_id": uuid4(),
        "interval_minutes": 60,
        "next_run_at": NOW,
        "is_enabled": True,
        "created_at": NOW - timedelta(days=1),
        "updated_at": NOW - timedelta(days=1),
    }
    values.update(overrides)
    return MissionSchedule(**values)


def test_schedule_table_matches_contract() -> None:
    table = MissionSchedule.__table__
    assert [column.name for column in table.columns] == [
        "id",
        "mission_id",
        "interval_minutes",
        "next_run_at",
        "last_run_at",
        "is_enabled",
        "created_at",
        "updated_at",
    ]
    assert table.c.interval_minutes.nullable is False
    assert table.c.next_run_at.type.timezone is True
    assert table.c.last_run_at.type.timezone is True
    assert table.c.is_enabled.default.arg is True
    assert any(
        isinstance(constraint, UniqueConstraint)
        and tuple(constraint.columns) == (table.c.mission_id,)
        for constraint in table.constraints
    )


def test_schedule_constraints_and_reference_protect_integrity() -> None:
    table = MissionSchedule.__table__
    assert {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    } == {
        "ck_mission_schedules_interval_positive",
        "ck_mission_schedules_run_order",
    }
    foreign_key = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    )
    element = tuple(foreign_key.elements)[0]
    assert element.target_fullname == "missions.id"
    assert element.ondelete == "RESTRICT"


def test_schedule_due_index_is_partial_and_deterministic() -> None:
    index = next(
        index for index in MissionSchedule.__table__.indexes if isinstance(index, Index)
    )
    assert index.name == "ix_mission_schedules_due"
    assert tuple(column.name for column in index.columns) == (
        "next_run_at",
        "mission_id",
    )
    assert str(index.dialect_options["postgresql"]["where"]) == "is_enabled"


def test_schedule_is_registered_in_shared_metadata() -> None:
    assert MissionSchedule in REGISTERED_MODELS
    assert Base.metadata.tables["mission_schedules"] is MissionSchedule.__table__


def test_find_due_schedules_returns_locked_query_result() -> None:
    schedule = _schedule()
    session = MagicMock()
    session.scalars.return_value = [schedule]

    result = find_due_schedules(session, due_at=NOW, limit=25)

    assert result == [schedule]
    statement = session.scalars.call_args.args[0]
    rendered = str(statement)
    assert "missions.status" in rendered
    assert "mission_schedules.next_run_at" in rendered
    assert "LIMIT" in rendered
    assert statement._for_update_arg.skip_locked is True
    assert statement._for_update_arg.of == [MissionSchedule.__table__]


@pytest.mark.parametrize("limit", [0, 1001])
def test_find_due_schedules_rejects_invalid_limit(limit: int) -> None:
    with pytest.raises(ValueError):
        find_due_schedules(MagicMock(), due_at=NOW, limit=limit)


def test_find_due_schedules_rejects_naive_time() -> None:
    with pytest.raises(ValueError):
        find_due_schedules(MagicMock(), due_at=datetime(2026, 8, 2, 16, 0))


def test_advance_schedule_keeps_cadence_without_backlog() -> None:
    schedule = _schedule(interval_minutes=30)
    started_at = NOW + timedelta(minutes=95)

    advance_schedule(schedule, started_at=started_at)

    assert schedule.last_run_at == started_at
    assert schedule.next_run_at == NOW + timedelta(minutes=120)
    assert schedule.updated_at == started_at


def test_advance_schedule_moves_exact_due_time_one_interval() -> None:
    schedule = _schedule(interval_minutes=15)

    advance_schedule(schedule, started_at=NOW)

    assert schedule.next_run_at == NOW + timedelta(minutes=15)


@pytest.mark.parametrize(
    ("schedule", "started_at"),
    [
        (_schedule(is_enabled=False), NOW),
        (_schedule(interval_minutes=0), NOW),
        (_schedule(next_run_at=NOW + timedelta(minutes=1)), NOW),
        (_schedule(), datetime(2026, 8, 2, 16, 0)),
    ],
)
def test_advance_schedule_rejects_invalid_execution(
    schedule: MissionSchedule, started_at: datetime
) -> None:
    with pytest.raises(ValueError):
        advance_schedule(schedule, started_at=started_at)
