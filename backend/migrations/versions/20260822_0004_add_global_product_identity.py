"""Adiciona identidade global de produto/variante (TASK-097).

Revision ID: 20260822_0004
Revises: 20260822_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260822_0004"
down_revision: str | None = "20260822_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("products", sa.Column("category", sa.String(80), nullable=True))
    op.add_column("products", sa.Column("family", sa.String(160), nullable=True))
    op.add_column("products", sa.Column("variant", sa.String(160), nullable=True))
    op.add_column(
        "products",
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column("products", sa.Column("family_key", sa.String(80), nullable=True))
    op.add_column("products", sa.Column("identity_key", sa.String(80), nullable=True))
    op.add_column("products", sa.Column("identity_version", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_products_identity_complete",
        "products",
        "identity_key IS NULL OR (identity_version IS NOT NULL AND "
        "category IS NOT NULL AND brand IS NOT NULL AND family IS NOT NULL "
        "AND model IS NOT NULL AND variant IS NOT NULL AND family_key IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_products_identity_version_positive",
        "products",
        "identity_version IS NULL OR identity_version > 0",
    )
    op.create_index(
        "uq_products_identity_key",
        "products",
        ["identity_key"],
        unique=True,
        postgresql_where=sa.text("identity_key IS NOT NULL"),
    )
    op.create_index("ix_products_family_key", "products", ["family_key"])

    op.add_column(
        "mission_criteria",
        sa.Column(
            "request_kind",
            sa.String(32),
            nullable=False,
            server_default="generic_category",
        ),
    )
    op.add_column(
        "mission_criteria", sa.Column("requested_family_key", sa.String(80), nullable=True)
    )
    op.add_column(
        "mission_criteria", sa.Column("requested_identity_key", sa.String(80), nullable=True)
    )
    op.add_column(
        "mission_criteria", sa.Column("requested_variant", sa.String(160), nullable=True)
    )
    op.add_column(
        "mission_criteria",
        sa.Column(
            "variant_selection_mode",
            sa.String(32),
            nullable=False,
            server_default="not_required",
        ),
    )
    op.add_column(
        "mission_criteria",
        sa.Column("variant_prompted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_mission_criteria_request_kind_values",
        "mission_criteria",
        "request_kind IN ('specific_product', 'product_family', 'generic_category')",
    )
    op.create_check_constraint(
        "ck_mission_criteria_variant_selection_mode_values",
        "mission_criteria",
        "variant_selection_mode IN ('not_required', 'pending', 'selected', 'all')",
    )
    op.create_check_constraint(
        "ck_mission_criteria_product_request_shape",
        "mission_criteria",
        "(request_kind = 'specific_product' AND requested_identity_key IS NOT NULL "
        "AND requested_family_key IS NOT NULL AND requested_variant IS NOT NULL "
        "AND variant_selection_mode = 'not_required') "
        "OR (request_kind = 'product_family' AND requested_identity_key IS NULL "
        "AND requested_family_key IS NOT NULL AND variant_selection_mode IN "
        "('pending', 'selected', 'all')) OR (request_kind = 'generic_category' "
        "AND requested_identity_key IS NULL AND requested_family_key IS NULL "
        "AND requested_variant IS NULL "
        "AND variant_selection_mode = 'not_required')",
    )

    op.create_table(
        "mission_product_selections",
        sa.Column("mission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("mission_id", "product_id"),
    )


def downgrade() -> None:
    op.drop_table("mission_product_selections")
    op.drop_constraint(
        "ck_mission_criteria_product_request_shape", "mission_criteria", type_="check"
    )
    op.drop_constraint(
        "ck_mission_criteria_variant_selection_mode_values",
        "mission_criteria",
        type_="check",
    )
    op.drop_constraint(
        "ck_mission_criteria_request_kind_values", "mission_criteria", type_="check"
    )
    op.drop_column("mission_criteria", "variant_prompted_at")
    op.drop_column("mission_criteria", "variant_selection_mode")
    op.drop_column("mission_criteria", "requested_variant")
    op.drop_column("mission_criteria", "requested_identity_key")
    op.drop_column("mission_criteria", "requested_family_key")
    op.drop_column("mission_criteria", "request_kind")

    op.drop_index("ix_products_family_key", table_name="products")
    op.drop_index("uq_products_identity_key", table_name="products")
    op.drop_constraint("ck_products_identity_version_positive", "products", type_="check")
    op.drop_constraint("ck_products_identity_complete", "products", type_="check")
    op.drop_column("products", "identity_version")
    op.drop_column("products", "identity_key")
    op.drop_column("products", "family_key")
    op.drop_column("products", "attributes")
    op.drop_column("products", "variant")
    op.drop_column("products", "family")
    op.drop_column("products", "category")
