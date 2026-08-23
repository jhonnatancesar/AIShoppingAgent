"""Integração entre migrations, metadata, seeds e PostgreSQL real."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from app.collection.contracts import MarketplacePartyKind
from app.collection.models import CollectionRun, CollectionRunStatus, PriceObservation
from app.collection.normalization import Availability
from app.missions.models import (
    Mission,
    MissionCommand,
    MissionStatus,
    MissionTransition,
)
from app.offers.models import Offer
from app.products.models import Product
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
            "magalu",
            "mercadolivre",
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


def test_marketplace_party_constraints_preserve_null_and_reject_invalid(
    integration_database,
) -> None:
    now = datetime.now(UTC)
    with integration_database.sessions.begin() as session:
        store = session.scalar(select(Store).where(Store.code == "amazon"))
        user = User(display_name="TASK-077 constraint", role=UserRole.USER)
        session.add(user)
        session.flush()
        mission = Mission(
            user_id=user.id,
            title="TASK-077 constraint",
            status=MissionStatus.ACTIVE,
        )
        product = Product(name="TASK-077 product")
        session.add_all((mission, product))
        session.flush()
        offer = Offer(
            product_id=product.id,
            store_id=store.id,
            external_id="task077-constraint",
            url="https://www.amazon.com.br/dp/task077-constraint",
        )
        run = CollectionRun(
            mission_id=mission.id,
            store_id=store.id,
            status=CollectionRunStatus.SUCCEEDED,
            started_at=now,
            finished_at=now,
        )
        session.add_all((offer, run))
        session.flush()
        observation = PriceObservation(
            offer_id=offer.id,
            collection_run_id=run.id,
            amount=Decimal("10.00"),
            currency="BRL",
            total_amount=Decimal("10.00"),
            availability=Availability.AVAILABLE,
            observed_at=now,
            seller_kind=None,
            fulfillment_kind=None,
        )
        session.add(observation)
        session.flush()

        for value in MarketplacePartyKind:
            session.execute(
                text(
                    "UPDATE price_observations "
                    "SET seller_kind = :value, fulfillment_kind = :value "
                    "WHERE id = :id"
                ),
                {"id": observation.id, "value": value.value},
            )
            session.flush()

        for column, constraint in (
            ("seller_kind", "ck_price_observations_seller_kind_values"),
            ("fulfillment_kind", "ck_price_observations_fulfillment_kind_values"),
        ):
            with pytest.raises(IntegrityError) as caught:
                with session.begin_nested():
                    session.execute(
                        text(
                            f"UPDATE price_observations SET {column} = 'invalid' "
                            "WHERE id = :id"
                        ),
                        {"id": observation.id},
                    )
                    session.flush()
            assert caught.value.orig.diag.constraint_name == constraint
