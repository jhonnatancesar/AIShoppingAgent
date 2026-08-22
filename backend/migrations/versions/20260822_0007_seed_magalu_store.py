"""Adiciona a Magalu ao catálogo de Stores da V1.2.

Revision ID: 20260822_0007
Revises: 20260822_0006
Create Date: 2026-08-22
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260822_0007"
down_revision: str | None = "20260822_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STORES_TABLE = sa.table(
    "stores",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("code", sa.String),
    sa.column("name", sa.String),
    sa.column("base_url", sa.Text),
    sa.column("source_type", sa.String),
    sa.column("is_active", sa.Boolean),
)


def upgrade() -> None:
    """Semeia somente a Store Magalu, ativa e tratada como marketplace."""
    op.bulk_insert(
        _STORES_TABLE,
        [
            {
                "id": uuid4(),
                "code": "magalu",
                "name": "Magalu",
                "base_url": "https://www.magazineluiza.com.br",
                "source_type": "marketplace",
                "is_active": True,
            }
        ],
    )


def downgrade() -> None:
    """Remove apenas a Store introduzida por esta revisão."""
    op.execute(_STORES_TABLE.delete().where(_STORES_TABLE.c.code == "magalu"))
