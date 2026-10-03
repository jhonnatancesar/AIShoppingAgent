"""TASK-137: o catálogo de identidade aceita fontes abertas copiadas (BuildCores
OpenDB, Wikidata) e guarda a referência do registro de origem.

`source` ganha `buildcores` e `wikidata`; `source_ref` guarda o id do registro
na fonte (ex.: `opendb_id` do BuildCores, Q-id do Wikidata) para atribuição e
atualização.

Revision ID: 20261004_0001
Revises: 20261003_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261004_0001"
down_revision: str | None = "20261003_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "product_identity_catalog_entries"
_CONSTRAINT = "ck_product_identity_catalog_entries_source_values"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("source_ref", sa.String(length=120), nullable=True))
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        "source IN ('seed', 'learned', 'manual', 'buildcores', 'wikidata')",
    )
    op.create_index(
        "ix_product_identity_catalog_entries_source_ref",
        _TABLE,
        ["source", "source_ref"],
    )


def downgrade() -> None:
    op.drop_index("ix_product_identity_catalog_entries_source_ref", table_name=_TABLE)
    op.execute(
        f"DELETE FROM {_TABLE} WHERE source IN ('buildcores', 'wikidata')"  # noqa: S608
    )
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT, _TABLE, "source IN ('seed', 'learned', 'manual')"
    )
    op.drop_column(_TABLE, "source_ref")
