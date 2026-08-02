"""Cria o marco inicial da infraestrutura de migrações.

Revision ID: 20260802_0001
Revises:
Create Date: 2026-08-02
"""

from collections.abc import Sequence

revision: str = "20260802_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Registra a baseline sem antecipar tabelas de domínio."""


def downgrade() -> None:
    """Remove a marca da baseline sem alterar tabelas de domínio."""
