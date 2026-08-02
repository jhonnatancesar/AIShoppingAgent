"""Cria transições imutáveis de missão.

Revision ID: 20260802_0008
Revises: 20260802_0007
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260802_0008"
down_revision: str | None = "20260802_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MISSION_STATUS_VALUES = (
    "draft",
    "active",
    "paused",
    "completed",
    "cancelled",
    "expired",
)
mission_status = postgresql.ENUM(
    *MISSION_STATUS_VALUES, name="mission_status", create_type=False
)


def upgrade() -> None:
    """Cria o histórico append-only das mudanças de estado."""
    op.create_table(
        "mission_transitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_status", mission_status, nullable=False),
        sa.Column("to_status", mission_status, nullable=False),
        sa.Column("command", sa.String(length=32), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "transitioned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "from_status <> to_status",
            name="ck_mission_transitions_status_changed",
        ),
        sa.CheckConstraint(
            "btrim(actor_type) <> ''",
            name="ck_mission_transitions_actor_type_not_blank",
        ),
        sa.CheckConstraint(
            "reason IS NULL OR btrim(reason) <> ''",
            name="ck_mission_transitions_reason_not_blank",
        ),
        sa.CheckConstraint(
            "command IN ('activate', 'pause', 'resume', 'complete', 'cancel', 'expire')",
            name="mission_command_values",
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["missions.id"],
            name=op.f("fk_mission_transitions_mission_id_missions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_mission_transitions_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mission_transitions")),
    )
    op.create_index(
        "ix_mission_transitions_history",
        "mission_transitions",
        ["mission_id", "transitioned_at", "id"],
    )
    op.execute(
        """
        CREATE FUNCTION prevent_mission_transitions_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'mission_transitions is append-only'
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_mission_transitions_append_only
        BEFORE UPDATE OR DELETE ON mission_transitions
        FOR EACH ROW EXECUTE FUNCTION prevent_mission_transitions_mutation()
        """
    )


def downgrade() -> None:
    """Remove somente a estrutura introduzida nesta revisão."""
    op.execute(
        "DROP TRIGGER trg_mission_transitions_append_only ON mission_transitions"
    )
    op.drop_index("ix_mission_transitions_history", table_name="mission_transitions")
    op.drop_table("mission_transitions")
    op.execute("DROP FUNCTION prevent_mission_transitions_mutation()")
