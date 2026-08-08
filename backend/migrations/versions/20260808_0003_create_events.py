"""Cria o registro durável e append-only de eventos de domínio (TASK-043).

Revision ID: 20260808_0003
Revises: 20260808_0002
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260808_0003"
down_revision: str | None = "20260808_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Cria `events` e impede alteração ou remoção de linhas já gravadas."""
    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mission_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(event_type) <> ''",
            name="ck_events_event_type_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(aggregate_type) <> ''",
            name="ck_events_aggregate_type_not_blank",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_events_payload_object",
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["missions.id"],
            name=op.f("fk_events_mission_id_missions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
    )
    op.create_index(
        "ix_events_aggregate_history",
        "events",
        ["aggregate_type", "aggregate_id", "occurred_at", "id"],
    )
    op.create_index(
        "ix_events_mission_history",
        "events",
        ["mission_id", "occurred_at", "id"],
    )
    op.execute(
        """
        CREATE FUNCTION prevent_events_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'events is append-only'
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_events_append_only
        BEFORE UPDATE OR DELETE ON events
        FOR EACH ROW EXECUTE FUNCTION prevent_events_mutation()
        """
    )


def downgrade() -> None:
    """Remove somente a estrutura introduzida nesta revisão."""
    op.execute("DROP TRIGGER trg_events_append_only ON events")
    op.drop_index("ix_events_mission_history", table_name="events")
    op.drop_index("ix_events_aggregate_history", table_name="events")
    op.drop_table("events")
    op.execute("DROP FUNCTION prevent_events_mutation()")
