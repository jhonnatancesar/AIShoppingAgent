"""Semeia as quatro lojas selecionáveis da V1.

Revision ID: 20260807_0002
Revises: 20260807_0001
Create Date: 2026-08-07
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260807_0002"
down_revision: str | None = "20260807_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STORES_TABLE = sa.table(
    "stores",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("code", sa.String),
    sa.column("name", sa.String),
    sa.column("base_url", sa.Text),
    sa.column("source_type", sa.String),
)

_V1_STORES = (
    {
        "code": "pichau",
        "name": "Pichau",
        "base_url": "https://www.pichau.com.br",
        "source_type": "retailer",
    },
    {
        "code": "terabyte",
        "name": "Terabyte",
        "base_url": "https://www.terabyteshop.com.br",
        "source_type": "retailer",
    },
    {
        "code": "amazon",
        "name": "Amazon",
        "base_url": "https://www.amazon.com.br",
        "source_type": "marketplace",
    },
    {
        "code": "kabum",
        "name": "Kabum",
        "base_url": "https://www.kabum.com.br",
        "source_type": "retailer",
    },
)


def upgrade() -> None:
    """Insere as quatro lojas definidas em docs/MARKETPLACE_SOURCES.md."""
    rows = [{"id": uuid4(), **store} for store in _V1_STORES]
    op.bulk_insert(_STORES_TABLE, rows)


def downgrade() -> None:
    """Remove exatamente as quatro lojas semeadas por código, não um DELETE genérico."""
    codes = tuple(store["code"] for store in _V1_STORES)
    op.execute(_STORES_TABLE.delete().where(_STORES_TABLE.c.code.in_(codes)))
