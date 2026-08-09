"""Adiciona replay, rate limit e retry append-only (TASK-049).

Revision ID: 20260808_0009
Revises: 20260808_0008
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260808_0009"
down_revision: str | None = "20260808_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE consumption_outcome ADD VALUE IF NOT EXISTS 'dead_lettered'"
        )

    op.add_column(
        "event_consumption_attempts",
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "ALTER TABLE event_consumption_attempts "
        "DISABLE TRIGGER trg_event_consumption_attempts_append_only"
    )
    op.execute(
        "UPDATE event_consumption_attempts "
        "SET next_retry_at = attempted_at + INTERVAL '1 microsecond' "
        "WHERE outcome = 'failed'"
    )
    op.execute(
        "ALTER TABLE event_consumption_attempts "
        "ENABLE TRIGGER trg_event_consumption_attempts_append_only"
    )
    op.drop_constraint(
        "ck_event_consumption_attempts_outcome_failure",
        "event_consumption_attempts",
        type_="check",
    )
    op.create_check_constraint(
        "ck_event_consumption_attempts_outcome_failure",
        "event_consumption_attempts",
        "(outcome IN ('succeeded', 'skipped') AND failure_code IS NULL "
        "AND next_retry_at IS NULL) OR "
        "(outcome = 'failed' AND failure_code IS NOT NULL AND "
        "failure_code ~ '^[a-z][a-z0-9]*(_[a-z0-9]+)*$' AND "
        "next_retry_at IS NOT NULL AND next_retry_at > attempted_at) OR "
        "(outcome = 'dead_lettered' AND failure_code IS NOT NULL AND "
        "failure_code ~ '^[a-z][a-z0-9]*(_[a-z0-9]+)*$' AND "
        "next_retry_at IS NULL)",
    )
    op.drop_index(
        "ix_event_consumption_attempts_terminal",
        table_name="event_consumption_attempts",
    )
    op.create_index(
        "ux_event_consumption_attempts_terminal",
        "event_consumption_attempts",
        ["consumer_name", "event_id"],
        unique=True,
        postgresql_where=sa.text(
            "outcome IN ('succeeded', 'skipped', 'dead_lettered')"
        ),
    )
    op.create_index(
        "ix_event_consumption_attempts_retry",
        "event_consumption_attempts",
        ["consumer_name", "event_id", "next_retry_at"],
        postgresql_where=sa.text("outcome = 'failed'"),
    )

    op.create_table(
        "telegram_update_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("update_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("disposition", sa.String(length=24), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "disposition IN ('accepted', 'rate_limited', 'discarded')",
            name="ck_telegram_update_receipts_disposition",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("update_id", name="uq_telegram_update_receipts_update_id"),
    )
    op.create_index(
        "ix_telegram_update_receipts_user_window",
        "telegram_update_receipts",
        ["user_id", "recorded_at"],
        postgresql_where=sa.text("disposition = 'accepted'"),
    )
    op.execute(
        """
        CREATE FUNCTION prevent_telegram_update_receipts_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'telegram_update_receipts is append-only'
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_telegram_update_receipts_append_only
        BEFORE UPDATE OR DELETE ON telegram_update_receipts
        FOR EACH ROW EXECUTE FUNCTION prevent_telegram_update_receipts_mutation()
        """
    )


def downgrade() -> None:
    dead_letters = op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM event_consumption_attempts "
            "WHERE outcome = 'dead_lettered'"
        )
    )
    if dead_letters:
        raise RuntimeError(
            "cannot downgrade TASK-049 while dead-lettered attempts exist"
        )

    op.execute(
        "DROP TRIGGER trg_telegram_update_receipts_append_only "
        "ON telegram_update_receipts"
    )
    op.drop_index(
        "ix_telegram_update_receipts_user_window",
        table_name="telegram_update_receipts",
    )
    op.drop_table("telegram_update_receipts")
    op.execute("DROP FUNCTION prevent_telegram_update_receipts_mutation()")

    op.drop_index(
        "ix_event_consumption_attempts_retry",
        table_name="event_consumption_attempts",
    )
    op.drop_index(
        "ux_event_consumption_attempts_terminal",
        table_name="event_consumption_attempts",
    )
    op.drop_constraint(
        "ck_event_consumption_attempts_outcome_failure",
        "event_consumption_attempts",
        type_="check",
    )
    op.execute(
        "ALTER TYPE consumption_outcome RENAME TO consumption_outcome_with_dead_letter"
    )
    op.execute(
        "CREATE TYPE consumption_outcome AS ENUM ('succeeded', 'failed', 'skipped')"
    )
    op.execute(
        "ALTER TABLE event_consumption_attempts ALTER COLUMN outcome "
        "TYPE consumption_outcome USING outcome::text::consumption_outcome"
    )
    op.execute("DROP TYPE consumption_outcome_with_dead_letter")
    op.create_check_constraint(
        "ck_event_consumption_attempts_outcome_failure",
        "event_consumption_attempts",
        "(outcome IN ('succeeded', 'skipped') AND failure_code IS NULL) OR "
        "(outcome = 'failed' AND failure_code IS NOT NULL AND "
        "failure_code ~ '^[a-z][a-z0-9]*(_[a-z0-9]+)*$')",
    )
    op.create_index(
        "ix_event_consumption_attempts_terminal",
        "event_consumption_attempts",
        ["consumer_name", "event_id"],
        postgresql_where=sa.text("outcome IN ('succeeded', 'skipped')"),
    )
    op.drop_column("event_consumption_attempts", "next_retry_at")
