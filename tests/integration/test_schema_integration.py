"""Integração entre migrations, metadata, seeds e PostgreSQL real."""

import pytest
from app.stores.models import Store
from sqlalchemy import inspect, select, text

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
