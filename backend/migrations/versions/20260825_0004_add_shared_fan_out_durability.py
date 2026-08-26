"""Add durable shared-collection fan-out tracking (TASK-112 fase 3A, correção "fan-out durável").

Revision ID: 20260825_0004
Revises: 20260825_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260825_0004"
down_revision: str | None = "20260825_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shared_collection_offers",
        sa.Column("collection_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("offer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("observation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["collection_run_id"],
            ["collection_runs.id"],
            name=op.f("fk_shared_collection_offers_collection_run_id_collection_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["offers.id"],
            name=op.f("fk_shared_collection_offers_offer_id_offers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["price_observations.id"],
            name=op.f("fk_shared_collection_offers_observation_id_price_observations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "collection_run_id", "offer_id", name=op.f("pk_shared_collection_offers")
        ),
    )

    # Migration ainda não publicada/deployada (TASK-112 fase 3A inteira
    # segue sem commit) -- editada in-place para nascer já com o schema
    # final de retry/concorrência/lease, em vez de uma segunda migration
    # imediatamente corrigindo o que acabou de ser criado.
    # Rodada 4 de correções da fase 3A (ainda sem commit -- editada
    # in-place, mesmo motivo do comentário acima): `skipped` (Mission não
    # elegível mais na hora do fan-out) e `attention_required` (falha
    # retryable esgotou as tentativas automáticas, mas não é definitiva)
    # entram desde a criação da tabela, sem migration de correção depois.
    shared_fan_out_status = postgresql.ENUM(
        "pending",
        "processing",
        "done",
        "skipped",
        "attention_required",
        "terminal_failed",
        name="shared_fan_out_status",
        create_type=False,
    )
    shared_fan_out_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "shared_fan_out_tasks",
        sa.Column("collection_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            shared_fan_out_status,
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "attempt_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("last_error", sa.String(length=2000), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_shared_fan_out_tasks_attempt_count_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["collection_run_id"],
            ["collection_runs.id"],
            name=op.f("fk_shared_fan_out_tasks_collection_run_id_collection_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["missions.id"],
            name=op.f("fk_shared_fan_out_tasks_mission_id_missions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "collection_run_id", "mission_id", name=op.f("pk_shared_fan_out_tasks")
        ),
    )
    op.create_index(
        "ix_shared_fan_out_tasks_pending",
        "shared_fan_out_tasks",
        ["collection_run_id"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_shared_fan_out_tasks_processing_claimed_at",
        "shared_fan_out_tasks",
        ["claimed_at"],
        postgresql_where=sa.text("status = 'processing'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_shared_fan_out_tasks_processing_claimed_at",
        table_name="shared_fan_out_tasks",
    )
    op.drop_index("ix_shared_fan_out_tasks_pending", table_name="shared_fan_out_tasks")
    op.drop_table("shared_fan_out_tasks")
    op.execute("DROP TYPE shared_fan_out_status")
    op.drop_table("shared_collection_offers")
