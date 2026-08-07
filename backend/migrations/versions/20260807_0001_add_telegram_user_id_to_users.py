"""Adiciona o identificador de usuário do Telegram.

Revision ID: 20260807_0001
Revises: 20260802_0012
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0001"
down_revision: str | None = "20260802_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Adiciona `telegram_user_id`, opcional e único, a `users`.

    Representa exclusivamente o `user.id` do Telegram (a pessoa), nunca o
    `chat.id` (a conversa); este último não é persistido nesta revisão.
    """
    op.add_column(
        "users",
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_users_telegram_user_id", "users", ["telegram_user_id"]
    )


def downgrade() -> None:
    """Remove somente o que esta revisão introduziu."""
    op.drop_constraint("uq_users_telegram_user_id", "users", type_="unique")
    op.drop_column("users", "telegram_user_id")
