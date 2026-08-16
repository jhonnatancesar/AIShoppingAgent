"""Integração entre migrations, metadata, seeds e PostgreSQL real."""

from uuid import uuid4

import pytest
from app.missions.models import (
    Mission,
    MissionCommand,
    MissionStatus,
    MissionTransition,
)
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration


def test_schema_head_metadata_and_store_seeds(integration_database) -> None:
    with integration_database.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == integration_database.expected_head
        )
        tables = set(inspect(connection).get_table_names())
        assert {
            "users",
            "missions",
            "collection_runs",
            "price_observations",
            "events",
            "event_consumption_attempts",
            "purchase_confirmations",
            "purchase_trail_entries",
            "telegram_update_receipts",
        } <= tables

    with integration_database.sessions() as session:
        assert set(session.scalars(select(Store.code))) == {
            "amazon",
            "kabum",
            "pichau",
            "terabyte",
        }


def test_enum_check_constraints_accept_valid_and_reject_invalid_values(
    integration_database,
) -> None:
    """As três constraints reais preservam sua semântica no PostgreSQL 18.4."""
    with integration_database.sessions.begin() as session:
        user = User(display_name="TASK-086 constraint", role=UserRole.DEV)
        session.add(user)
        session.flush()
        mission = Mission(
            user_id=user.id,
            title="TASK-086 constraint",
            status=MissionStatus.ACTIVE,
        )
        session.add(mission)
        session.flush()
        session.add(
            MissionTransition(
                mission_id=mission.id,
                from_status=MissionStatus.DRAFT,
                to_status=MissionStatus.ACTIVE,
                command=MissionCommand.ACTIVATE,
                actor_type="integration_test",
                actor_id=user.id,
            )
        )
        session.flush()

        invalid_statements = (
            (
                "user_role_values",
                text("UPDATE users SET role = 'PAID' WHERE id = :id"),
                {"id": user.id},
            ),
            (
                "store_source_type_values",
                text("UPDATE stores SET source_type = 'unknown' WHERE code = 'pichau'"),
                {},
            ),
            (
                "mission_command_values",
                text(
                    "INSERT INTO mission_transitions "
                    "(id, mission_id, from_status, to_status, command, actor_type) "
                    "VALUES (:id, :mission_id, 'draft', 'active', 'invalid', 'test')"
                ),
                {"id": uuid4(), "mission_id": mission.id},
            ),
        )
        for constraint_name, statement, parameters in invalid_statements:
            with pytest.raises(IntegrityError) as caught:
                with session.begin_nested():
                    session.execute(statement, parameters)
                    session.flush()
            assert caught.value.orig.diag.constraint_name == constraint_name
