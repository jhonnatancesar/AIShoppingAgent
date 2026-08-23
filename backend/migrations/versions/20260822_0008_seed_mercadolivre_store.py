"""Seed Mercado Livre as a selectable store.

Revision ID: 20260822_0008
Revises: 20260822_0007
Create Date: 2026-08-22
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260822_0008"
down_revision: str | None = "20260822_0007"
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
    op.bulk_insert(
        _STORES_TABLE,
        [
            {
                "id": uuid4(),
                "code": "mercadolivre",
                "name": "Mercado Livre",
                "base_url": "https://www.mercadolivre.com.br",
                "source_type": "marketplace",
                "is_active": True,
            }
        ],
    )


def downgrade() -> None:
    op.execute(
        _STORES_TABLE.delete().where(_STORES_TABLE.c.code == "mercadolivre")
    )
