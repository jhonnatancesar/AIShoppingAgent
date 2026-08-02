"""Suporta fontes selecionadas e vendedores de marketplace.

Revision ID: 20260802_0009
Revises: 20260802_0008
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260802_0009"
down_revision: str | None = "20260802_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Adiciona tipo de fonte, vendedores e seleção por missão."""
    op.add_column(
        "stores",
        sa.Column(
            "source_type",
            sa.String(length=16),
            server_default=sa.text("'retailer'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "store_source_type_values",
        "stores",
        "source_type IN ('retailer', 'marketplace')",
    )
    op.execute("UPDATE stores SET source_type = 'marketplace' WHERE code = 'amazon'")
    op.create_table(
        "sellers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
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
        sa.CheckConstraint("btrim(name) <> ''", name="ck_sellers_name_not_blank"),
        sa.CheckConstraint(
            "external_id IS NULL OR btrim(external_id) <> ''",
            name="ck_sellers_external_id_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name=op.f("fk_sellers_store_id_stores"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sellers")),
        sa.UniqueConstraint("id", "store_id", name="uq_sellers_id_store_id"),
    )
    op.create_index("ix_sellers_store_id", "sellers", ["store_id"])
    op.create_index(
        "uq_sellers_store_external_id",
        "sellers",
        ["store_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    op.execute(
        """
        CREATE FUNCTION enforce_seller_marketplace_store()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM stores
                WHERE id = NEW.store_id AND source_type = 'marketplace'
            ) THEN
                RAISE EXCEPTION 'sellers require a marketplace store'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_sellers_marketplace_store
        BEFORE INSERT OR UPDATE OF store_id ON sellers
        FOR EACH ROW EXECUTE FUNCTION enforce_seller_marketplace_store()
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_marketplace_demotion_with_sellers()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.source_type = 'retailer' AND EXISTS (
                SELECT 1 FROM sellers WHERE store_id = NEW.id
            ) THEN
                RAISE EXCEPTION 'marketplace with sellers cannot become retailer'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_stores_prevent_seller_orphans
        BEFORE UPDATE OF source_type ON stores
        FOR EACH ROW EXECUTE FUNCTION prevent_marketplace_demotion_with_sellers()
        """
    )

    op.add_column(
        "offers",
        sa.Column("seller_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.drop_index("uq_offers_store_url", table_name="offers")
    op.drop_index("uq_offers_store_external_id", table_name="offers")
    op.create_foreign_key(
        "fk_offers_seller_store_sellers",
        "offers",
        "sellers",
        ["seller_id", "store_id"],
        ["id", "store_id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_offers_seller_id", "offers", ["seller_id"])
    op.create_index(
        "uq_offers_retailer_external_id",
        "offers",
        ["store_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("seller_id IS NULL AND external_id IS NOT NULL"),
    )
    op.create_index(
        "uq_offers_marketplace_external_id",
        "offers",
        ["store_id", "seller_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("seller_id IS NOT NULL AND external_id IS NOT NULL"),
    )
    op.create_index(
        "uq_offers_retailer_url",
        "offers",
        ["store_id", "url"],
        unique=True,
        postgresql_where=sa.text("seller_id IS NULL"),
    )
    op.create_index(
        "uq_offers_marketplace_url",
        "offers",
        ["store_id", "seller_id", "url"],
        unique=True,
        postgresql_where=sa.text("seller_id IS NOT NULL"),
    )

    op.create_table(
        "mission_sources",
        sa.Column("mission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["missions.id"],
            name=op.f("fk_mission_sources_mission_id_missions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name=op.f("fk_mission_sources_store_id_stores"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "mission_id", "store_id", name=op.f("pk_mission_sources")
        ),
    )
    op.create_index("ix_mission_sources_store_id", "mission_sources", ["store_id"])


def downgrade() -> None:
    """Restaura o contrato anterior sem remover ofertas existentes."""
    op.drop_index("ix_mission_sources_store_id", table_name="mission_sources")
    op.drop_table("mission_sources")

    op.drop_index("uq_offers_marketplace_url", table_name="offers")
    op.drop_index("uq_offers_retailer_url", table_name="offers")
    op.drop_index("uq_offers_marketplace_external_id", table_name="offers")
    op.drop_index("uq_offers_retailer_external_id", table_name="offers")
    op.drop_index("ix_offers_seller_id", table_name="offers")
    op.drop_constraint("fk_offers_seller_store_sellers", "offers", type_="foreignkey")
    op.drop_column("offers", "seller_id")
    op.create_index(
        "uq_offers_store_external_id",
        "offers",
        ["store_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    op.create_index("uq_offers_store_url", "offers", ["store_id", "url"], unique=True)

    op.execute("DROP TRIGGER trg_stores_prevent_seller_orphans ON stores")
    op.execute("DROP FUNCTION prevent_marketplace_demotion_with_sellers()")
    op.execute("DROP TRIGGER trg_sellers_marketplace_store ON sellers")
    op.execute("DROP FUNCTION enforce_seller_marketplace_store()")
    op.drop_index("uq_sellers_store_external_id", table_name="sellers")
    op.drop_index("ix_sellers_store_id", table_name="sellers")
    op.drop_table("sellers")
    op.drop_constraint("store_source_type_values", "stores", type_="check")
    op.drop_column("stores", "source_type")
