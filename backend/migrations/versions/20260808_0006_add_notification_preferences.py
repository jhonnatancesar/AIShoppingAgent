"""Adiciona preferências e resultado skipped para notificações (TASK-037).

Revision ID: 20260808_0006
Revises: 20260808_0005
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260808_0006"
down_revision: str | None = "20260808_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Habilita preferências por alerta e consumo terminal sem envio."""
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE consumption_outcome ADD VALUE IF NOT EXISTS 'skipped'")
    op.add_column(
        "users",
        sa.Column(
            "notify_price_decreases",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "notify_target_reached",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.drop_constraint(
        "ck_event_consumption_attempts_outcome_failure",
        "event_consumption_attempts",
        type_="check",
    )
    op.create_check_constraint(
        "ck_event_consumption_attempts_outcome_failure",
        "event_consumption_attempts",
        "(outcome IN ('succeeded', 'skipped') AND failure_code IS NULL) OR "
        "(outcome = 'failed' AND failure_code IS NOT NULL AND "
        "failure_code ~ '^[a-z][a-z0-9]*(_[a-z0-9]+)*$')",
    )
    op.drop_index(
        "ix_event_consumption_attempts_success",
        table_name="event_consumption_attempts",
    )
    op.create_index(
        "ix_event_consumption_attempts_terminal",
        "event_consumption_attempts",
        ["consumer_name", "event_id"],
        postgresql_where=sa.text("outcome IN ('succeeded', 'skipped')"),
    )


def downgrade() -> None:
    """Remove a extensão se nenhum resultado skipped tiver sido persistido."""
    skipped = op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM event_consumption_attempts WHERE outcome = 'skipped'"
        )
    )
    if skipped:
        raise RuntimeError(
            "cannot downgrade TASK-037 while skipped consumption attempts exist"
        )

    op.drop_index(
        "ix_event_consumption_attempts_terminal",
        table_name="event_consumption_attempts",
    )
    op.drop_constraint(
        "ck_event_consumption_attempts_outcome_failure",
        "event_consumption_attempts",
        type_="check",
    )
    op.execute(
        "ALTER TYPE consumption_outcome RENAME TO consumption_outcome_with_skipped"
    )
    op.execute("CREATE TYPE consumption_outcome AS ENUM ('succeeded', 'failed')")
    op.execute(
        "ALTER TABLE event_consumption_attempts ALTER COLUMN outcome "
        "TYPE consumption_outcome USING outcome::text::consumption_outcome"
    )
    op.execute("DROP TYPE consumption_outcome_with_skipped")
    op.create_check_constraint(
        "ck_event_consumption_attempts_outcome_failure",
        "event_consumption_attempts",
        "(outcome = 'succeeded' AND failure_code IS NULL) OR "
        "(outcome = 'failed' AND failure_code IS NOT NULL AND "
        "failure_code ~ '^[a-z][a-z0-9]*(_[a-z0-9]+)*$')",
    )
    op.create_index(
        "ix_event_consumption_attempts_success",
        "event_consumption_attempts",
        ["consumer_name", "event_id"],
        postgresql_where=sa.text("outcome = 'succeeded'"),
    )
    op.drop_column("users", "notify_target_reached")
    op.drop_column("users", "notify_price_decreases")
