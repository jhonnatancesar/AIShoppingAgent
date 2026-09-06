"""Adiciona referências históricas externas one-shot (FASE F1).

Revision ID: 20260905_0001
Revises: 20260901_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260905_0001"
down_revision: str | None = "20260901_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    status = postgresql.ENUM(
        "processing",
        "completed_with_references",
        "completed_without_references",
        name="historical_bootstrap_status",
        create_type=False,
    )
    status.create(op.get_bind())
    op.create_table(
        "historical_bootstraps",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("condition", sa.String(16), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("status", status, nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name="ck_historical_bootstraps_currency"
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "product_id", "condition", "currency", name="uq_historical_bootstraps_scope"
        ),
    )
    op.create_table(
        "external_price_references",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bootstrap_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(120), nullable=False),
        sa.Column("reference_type", sa.String(40), nullable=False),
        sa.Column("amount", sa.Numeric(19, 4), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("historical_date", sa.Date(), nullable=True),
        sa.Column("store_name", sa.String(160), nullable=True),
        sa.Column("safe_url", sa.Text(), nullable=False),
        sa.Column("condition", sa.String(16), nullable=False),
        sa.Column("matched_identity_key", sa.String(80), nullable=False),
        sa.Column(
            "match_evidence",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("quality", sa.String(16), nullable=False),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount >= 0", name="ck_external_price_references_amount"),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name="ck_external_price_references_currency"
        ),
        sa.CheckConstraint(
            "btrim(source) <> ''", name="ck_external_price_references_source"
        ),
        sa.CheckConstraint(
            "btrim(safe_url) <> ''", name="ck_external_price_references_url"
        ),
        sa.ForeignKeyConstraint(
            ["bootstrap_id"], ["historical_bootstraps.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "bootstrap_id",
            "source",
            "safe_url",
            "amount",
            "historical_date",
            name="uq_external_price_references_evidence",
        ),
    )
    op.create_index(
        "ix_external_price_references_product",
        "external_price_references",
        ["product_id", "collected_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_external_price_references_product", table_name="external_price_references"
    )
    op.drop_table("external_price_references")
    op.drop_table("historical_bootstraps")
    postgresql.ENUM(name="historical_bootstrap_status").drop(op.get_bind())
