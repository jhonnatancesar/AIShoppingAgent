"""Adiciona pending_intent a users (TASK-058).

Revision ID: 20260808_0002
Revises: 20260808_0001
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20260808_0002"
down_revision: str | None = "20260808_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Adiciona `pending_intent`, opcional, para o fluxo de confirmação.

    Guarda os dados mínimos para executar uma `create_mission` ou
    `mission_command` já validada, aguardando confirmação explícita do
    usuário (TASK-058). Nunca guarda um `Intent` bruto nem texto livre.
    """
    op.add_column("users", sa.Column("pending_intent", JSONB(), nullable=True))


def downgrade() -> None:
    """Remove somente o que esta revisão introduziu."""
    op.drop_column("users", "pending_intent")
