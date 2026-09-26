"""TASK-128 etapa 2: quando nem a categoria sai do título
(`awaiting_page`), o worker abre a página do produto e a IA tenta de
novo com os dados da própria página -- decisão do usuário (2026-09-26):
"retornar ao GG que não entendeu e abrir o worker e ler a página e
passar mais detalhes para a IA".

- `source_product_id`: Product que originou o "não entendi" (a página é
  aberta por uma Offer dele); `SET NULL` se o Product for fundido.
- `page_context`: dados da página usados pela IA e pelo grounding.
- `page_attempts`/`page_read_at`: tentativas e reserva curta da leitura.
- status `unrecognized`: TERMINAL -- nem com a página deu para
  categorizar (ou a loja não abre página); nunca é tentado de novo.

Revision ID: 20260926_0002
Revises: 20260926_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260926_0002"
down_revision: str | None = "20260926_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "product_identity_candidates"
_STATUS_CHECK = "ck_product_identity_candidates_status_values"
_PREVIOUS_STATUSES = (
    "'approved', 'pending_review', 'rejected', 'partial', 'awaiting_page'"
)
_NEW_STATUSES = f"{_PREVIOUS_STATUSES}, 'unrecognized'"
_SOURCE_PRODUCT_FK = "fk_product_identity_candidates_source_product_id_products"
_AWAITING_PAGE_INDEX = "ix_product_identity_candidates_awaiting_page"


def upgrade() -> None:
    op.drop_constraint(_STATUS_CHECK, _TABLE, type_="check")
    op.create_check_constraint(_STATUS_CHECK, _TABLE, f"status IN ({_NEW_STATUSES})")
    op.create_check_constraint(
        "ck_product_identity_candidates_unrecognized_shape",
        _TABLE,
        "status <> 'unrecognized' OR (category IS NULL AND identity_key IS NULL)",
    )
    op.add_column(
        _TABLE,
        sa.Column("source_product_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        _SOURCE_PRODUCT_FK,
        _TABLE,
        "products",
        ["source_product_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(_TABLE, sa.Column("page_context", sa.Text(), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column(
            "page_attempts", sa.SmallInteger(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        _TABLE,
        sa.Column("page_read_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_product_identity_candidates_page_attempts_non_negative",
        _TABLE,
        "page_attempts >= 0",
    )
    op.create_index(
        _AWAITING_PAGE_INDEX,
        _TABLE,
        ["page_attempts", "created_at"],
        postgresql_where=sa.text("status = 'awaiting_page'"),
    )


def downgrade() -> None:
    # `unrecognized` não cabe no CHECK antigo; volta a ser "aguardando
    # página" (sem vínculo, só cache) -- nada de identidade se perde.
    op.execute(
        f"UPDATE {_TABLE} SET status = 'awaiting_page' WHERE status = 'unrecognized'"
    )
    op.drop_index(_AWAITING_PAGE_INDEX, table_name=_TABLE)
    op.drop_constraint(
        "ck_product_identity_candidates_page_attempts_non_negative",
        _TABLE,
        type_="check",
    )
    op.drop_column(_TABLE, "page_read_at")
    op.drop_column(_TABLE, "page_attempts")
    op.drop_column(_TABLE, "page_context")
    op.drop_constraint(_SOURCE_PRODUCT_FK, _TABLE, type_="foreignkey")
    op.drop_column(_TABLE, "source_product_id")
    op.drop_constraint(
        "ck_product_identity_candidates_unrecognized_shape", _TABLE, type_="check"
    )
    op.drop_constraint(_STATUS_CHECK, _TABLE, type_="check")
    op.create_check_constraint(
        _STATUS_CHECK, _TABLE, f"status IN ({_PREVIOUS_STATUSES})"
    )
