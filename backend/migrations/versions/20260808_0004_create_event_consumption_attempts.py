"""Cria tentativas append-only de consumo de eventos (TASK-044).

Revision ID: 20260808_0004
Revises: 20260808_0003
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260808_0004"
down_revision: str | None = "20260808_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Cria o histórico de resultados e protege suas linhas contra mutação."""
    outcome = postgresql.ENUM(
        "succeeded",
        "failed",
        name="consumption_outcome",
        create_type=False,
    )
    outcome.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "event_consumption_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("consumer_name", sa.String(length=64), nullable=False),
        sa.Column("outcome", outcome, nullable=False),
        sa.Column("failure_code", sa.String(length=120), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "btrim(consumer_name) <> ''",
            name="ck_event_consumption_attempts_consumer_not_blank",
        ),
        sa.CheckConstraint(
            "(outcome = 'succeeded' AND failure_code IS NULL) OR "
            "(outcome = 'failed' AND failure_code IS NOT NULL AND "
            "failure_code ~ '^[a-z][a-z0-9]*(_[a-z0-9]+)*$')",
            name="ck_event_consumption_attempts_outcome_failure",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name=op.f("fk_event_consumption_attempts_event_id_events"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event_consumption_attempts")),
    )
    op.create_index(
        "ix_event_consumption_attempts_success",
        "event_consumption_attempts",
        ["consumer_name", "event_id"],
        postgresql_where=sa.text("outcome = 'succeeded'"),
    )
    op.execute(
        """
        CREATE FUNCTION prevent_event_consumption_attempts_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'event_consumption_attempts is append-only'
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_event_consumption_attempts_append_only
        BEFORE UPDATE OR DELETE ON event_consumption_attempts
        FOR EACH ROW EXECUTE FUNCTION prevent_event_consumption_attempts_mutation()
        """
    )


def downgrade() -> None:
    """Remove a estrutura e o enum introduzidos por esta revisão."""
    op.execute(
        "DROP TRIGGER trg_event_consumption_attempts_append_only "
        "ON event_consumption_attempts"
    )
    op.drop_index(
        "ix_event_consumption_attempts_success",
        table_name="event_consumption_attempts",
    )
    op.drop_table("event_consumption_attempts")
    op.execute("DROP FUNCTION prevent_event_consumption_attempts_mutation()")
    postgresql.ENUM(name="consumption_outcome").drop(op.get_bind(), checkfirst=True)
