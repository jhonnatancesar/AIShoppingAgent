"""TASK-137 (passo C): decisão da IA entre candidatos do catálogo para um pedido
de missão em dúvida.

Uma linha por pedido normalizado (`request_key`): guarda os candidatos, o que a
IA entendeu do pedido, a escolha e o estado das tentativas. O mesmo pedido nunca
gasta IA duas vezes.

Revision ID: 20261005_0001
Revises: 20261004_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261005_0001"
down_revision: str | None = "20261004_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "catalog_request_resolutions"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_key", sa.String(length=64), nullable=False),
        sa.Column("request_text", sa.String(length=500), nullable=False),
        sa.Column(
            "understood",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "candidates",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column(
            "chosen_entry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product_identity_catalog_entries.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ai_error_kind", sa.String(length=24), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ai_provider", sa.String(length=80), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'resolved', 'none', 'ai_failed')",
            name="ck_catalog_request_resolutions_status_values",
        ),
        sa.CheckConstraint(
            "attempts >= 0", name="ck_catalog_request_resolutions_attempts_non_negative"
        ),
        sa.UniqueConstraint("request_key", name="uq_catalog_request_resolutions_key"),
    )
    op.create_index(
        "ix_catalog_request_resolutions_due",
        _TABLE,
        ["status", "next_retry_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_catalog_request_resolutions_due", table_name=_TABLE)
    op.drop_table(_TABLE)
