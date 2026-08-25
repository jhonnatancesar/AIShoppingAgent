"""Add product identity aliases for the Product Identity Engine (TASK-112).

Revision ID: 20260824_0002
Revises: 20260824_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_0002"
down_revision: str | None = "20260824_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_identity_aliases",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("attribute_name", sa.String(length=80), nullable=False),
        sa.Column("raw_value_normalized", sa.String(length=160), nullable=False),
        sa.Column("canonical_value", sa.String(length=160), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'candidate')",
            name="ck_product_identity_aliases_status_values",
        ),
        sa.CheckConstraint(
            "btrim(category) <> ''",
            name="ck_product_identity_aliases_category_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(attribute_name) <> ''",
            name="ck_product_identity_aliases_attribute_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(raw_value_normalized) <> ''",
            name="ck_product_identity_aliases_raw_value_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(canonical_value) <> ''",
            name="ck_product_identity_aliases_canonical_value_not_blank",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_identity_aliases")),
    )
    op.create_index(
        "uq_product_identity_aliases_scope",
        "product_identity_aliases",
        ["category", "attribute_name", "raw_value_normalized"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_product_identity_aliases_scope", table_name="product_identity_aliases"
    )
    op.drop_table("product_identity_aliases")
