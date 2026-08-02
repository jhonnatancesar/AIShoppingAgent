"""Cria a tabela de produtos canônicos.

Revision ID: 20260802_0003
Revises: 20260802_0002
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260802_0003"
down_revision: str | None = "20260802_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Cria produtos canônicos e suas restrições de integridade."""
    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("brand", sa.String(length=160), nullable=True),
        sa.Column("model", sa.String(length=160), nullable=True),
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
            "btrim(name) <> ''",
            name="ck_products_name_not_blank",
        ),
        sa.CheckConstraint(
            "brand IS NULL OR btrim(brand) <> ''",
            name="ck_products_brand_not_blank",
        ),
        sa.CheckConstraint(
            "model IS NULL OR btrim(model) <> ''",
            name="ck_products_model_not_blank",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
    )


def downgrade() -> None:
    """Remove somente a tabela introduzida por esta revisão."""
    op.drop_table("products")
