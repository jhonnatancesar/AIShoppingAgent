"""Cria lojas e ofertas.

Revision ID: 20260802_0004
Revises: 20260802_0003
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260802_0004"
down_revision: str | None = "20260802_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Cria a origem normalizada e as ofertas com identidade estável."""
    op.create_table(
        "stores",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
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
            "code ~ '^[a-z][a-z0-9]*(_[a-z0-9]+)*$'",
            name="ck_stores_code_snake_case",
        ),
        sa.CheckConstraint("btrim(name) <> ''", name="ck_stores_name_not_blank"),
        sa.CheckConstraint(
            "btrim(base_url) <> ''",
            name="ck_stores_base_url_not_blank",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stores")),
        sa.UniqueConstraint("code", name=op.f("uq_stores_code")),
    )
    op.create_table(
        "offers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
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
            "external_id IS NULL OR btrim(external_id) <> ''",
            name="ck_offers_external_id_not_blank",
        ),
        sa.CheckConstraint("btrim(url) <> ''", name="ck_offers_url_not_blank"),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_offers_product_id_products"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name=op.f("fk_offers_store_id_stores"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_offers")),
    )
    op.create_index("ix_offers_product_id", "offers", ["product_id"])
    op.create_index("ix_offers_store_id", "offers", ["store_id"])
    op.create_index(
        "uq_offers_store_external_id",
        "offers",
        ["store_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    op.create_index(
        "uq_offers_store_url",
        "offers",
        ["store_id", "url"],
        unique=True,
    )


def downgrade() -> None:
    """Remove ofertas antes da entidade de apoio referenciada."""
    op.drop_index("uq_offers_store_url", table_name="offers")
    op.drop_index("uq_offers_store_external_id", table_name="offers")
    op.drop_index("ix_offers_store_id", table_name="offers")
    op.drop_index("ix_offers_product_id", table_name="offers")
    op.drop_table("offers")
    op.drop_table("stores")
