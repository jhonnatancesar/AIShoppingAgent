"""Cria user_feedback (bug/suporte/sugestão de loja, subtask 7 da auditoria GG Oferta).

Revision ID: 20260901_0001
Revises: 20260830_0001

Registro único e compartilhado entre Web e Telegram para três tipos de
envio (`kind`): bug, suporte e sugestão de loja. `message` é opcional
porque uma sugestão de loja pode não trazer comentário; `store_name`/
`store_url` só se aplicam a sugestões de loja. Nenhuma regra de "campo
obrigatório para este kind" vira CHECK constraint aqui -- fica na camada
de aplicação (`app.feedback.service`, Telegram e Web), como já é o
padrão para regras de fluxo neste projeto.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260901_0001"
down_revision: str | None = "20260830_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("store_name", sa.String(length=160), nullable=True),
        sa.Column("store_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="new"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('bug', 'support', 'store_suggestion')",
            name="ck_user_feedback_kind_values",
        ),
        sa.CheckConstraint(
            "channel IN ('web', 'telegram')",
            name="ck_user_feedback_channel_values",
        ),
        sa.CheckConstraint(
            "status IN ('new', 'reviewed', 'closed')",
            name="ck_user_feedback_status_values",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_feedback_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_feedback")),
    )
    op.create_index(
        "ix_user_feedback_status_created_at",
        "user_feedback",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_feedback_status_created_at", table_name="user_feedback")
    op.drop_table("user_feedback")
