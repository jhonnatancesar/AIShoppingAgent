"""Adiciona o destino privado de notificações Telegram (TASK-036).

Revision ID: 20260808_0005
Revises: 20260808_0004
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260808_0005"
down_revision: str | None = "20260808_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Armazena somente o chat privado correspondente ao usuário Telegram."""
    op.add_column(
        "users",
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
    )
    op.create_check_constraint(
        "ck_users_telegram_private_chat",
        "users",
        "telegram_chat_id IS NULL OR "
        "(telegram_user_id IS NOT NULL AND telegram_chat_id = telegram_user_id)",
    )
    op.create_unique_constraint(
        op.f("uq_users_telegram_chat_id"),
        "users",
        ["telegram_chat_id"],
    )


def downgrade() -> None:
    """Remove o destino sem alterar a identidade Telegram da pessoa."""
    op.drop_constraint(
        op.f("uq_users_telegram_chat_id"),
        "users",
        type_="unique",
    )
    op.drop_constraint(
        "ck_users_telegram_private_chat",
        "users",
        type_="check",
    )
    op.drop_column("users", "telegram_chat_id")
