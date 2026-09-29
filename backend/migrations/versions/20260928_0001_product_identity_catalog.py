"""TASK-132 (Parte B): catálogo de nomenclaturas de produto -- entradas
(categoria, marca, família, modelo, variante) e os códigos (part number, nome)
que as identificam num título. Nasce vazio; é alimentado por
`scripts/seed_identity_catalog.py` (pré-lista + identidades já aprovadas com
part number) e, em regime, pelo aprendizado automático da resolução.

Revision ID: 20260928_0001
Revises: 20260927_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260928_0001"
down_revision: str | None = "20260927_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENTRIES = "product_identity_catalog_entries"
_CODES = "product_identity_catalog_codes"


def upgrade() -> None:
    op.create_table(
        _ENTRIES,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("brand", sa.String(length=160), nullable=False),
        sa.Column("family", sa.String(length=160), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column(
            "variant", sa.String(length=160), server_default="base", nullable=False
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "required_attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("family_key", sa.String(length=80), nullable=False),
        sa.Column("identity_key", sa.String(length=80), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default="active", nullable=False
        ),
        sa.Column(
            "source", sa.String(length=16), server_default="learned", nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'rejected')",
            name=op.f("ck_product_identity_catalog_entries_status_values"),
        ),
        sa.CheckConstraint(
            "source IN ('seed', 'learned', 'manual')",
            name=op.f("ck_product_identity_catalog_entries_source_values"),
        ),
        sa.CheckConstraint(
            "btrim(category) <> '' AND btrim(brand) <> '' AND btrim(family) <> '' "
            "AND btrim(model) <> '' AND btrim(variant) <> ''",
            name=op.f("ck_product_identity_catalog_entries_fields_not_blank"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_identity_catalog_entries")),
        sa.UniqueConstraint(
            "category",
            "brand",
            "family",
            "model",
            "variant",
            name="uq_product_identity_catalog_entries_identity",
        ),
    )
    op.create_index("ix_product_identity_catalog_entries_status", _ENTRIES, ["status"])
    op.create_table(
        _CODES,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("value_normalized", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('part_number', 'name')",
            name=op.f("ck_product_identity_catalog_codes_kind_values"),
        ),
        sa.CheckConstraint(
            "btrim(value_normalized) <> ''",
            name=op.f("ck_product_identity_catalog_codes_value_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            [f"{_ENTRIES}.id"],
            name=op.f("fk_product_identity_catalog_codes_entry_id_entries"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_identity_catalog_codes")),
        sa.UniqueConstraint(
            "kind", "value_normalized", name="uq_product_identity_catalog_codes_value"
        ),
    )
    op.create_index("ix_product_identity_catalog_codes_entry_id", _CODES, ["entry_id"])


def downgrade() -> None:
    op.drop_index("ix_product_identity_catalog_codes_entry_id", table_name=_CODES)
    op.drop_table(_CODES)
    op.drop_index("ix_product_identity_catalog_entries_status", table_name=_ENTRIES)
    op.drop_table(_ENTRIES)
