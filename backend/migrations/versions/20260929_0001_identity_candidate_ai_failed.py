"""TASK-133 (etapa 1): toda falha de IA de identidade deixa um candidato.

Antes, quando a chamada de IA falhava (cota, provedor fora, tempo esgotado,
resposta fora do contrato), o GG não gravava nada e chamava a IA de novo pelo
mesmo título a cada coleta. Agora a falha vira um candidato `ai_failed`:

- `ai_error_kind`: motivo (`quota`, `provider_unavailable`, `timeout`,
  `provider_error`, `circuit_open`, `invalid_response`, `request_rejected`);
- `ai_attempts`: chamadas de IA já feitas para o título;
- `next_retry_at`: antes desse prazo a IA não é chamada de novo.

Também cria `identity_ai_breaker` (uma linha): o disjuntor COMPARTILHADO da
IA de identidade, para worker, API e scripts pararem juntos e sobreviverem a
restart. Nasce sem nenhuma linha `ai_failed` e com o disjuntor vazio.

Revision ID: 20260929_0001
Revises: 20260928_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260929_0001"
down_revision: str | None = "20260928_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "product_identity_candidates"
_STATUS_CHECK = "ck_product_identity_candidates_status_values"
_PREVIOUS_STATUSES = (
    "'approved', 'pending_review', 'rejected', 'partial', "
    "'awaiting_page', 'unrecognized'"
)
_NEW_STATUSES = f"{_PREVIOUS_STATUSES}, 'ai_failed'"
_SHAPE_CHECK = "ck_product_identity_candidates_ai_failed_shape"
_ATTEMPTS_CHECK = "ck_product_identity_candidates_ai_attempts_non_negative"
_INDEX = "ix_product_identity_candidates_ai_failed"


def upgrade() -> None:
    op.create_table(
        "identity_ai_breaker",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("open_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_identity_ai_breaker_single_row"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_identity_ai_breaker")),
    )
    op.add_column(
        _TABLE,
        sa.Column("ai_attempts", sa.SmallInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        _TABLE, sa.Column("ai_error_kind", sa.String(length=40), nullable=True)
    )
    op.add_column(
        _TABLE, sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.drop_constraint(_STATUS_CHECK, _TABLE, type_="check")
    op.create_check_constraint(_STATUS_CHECK, _TABLE, f"status IN ({_NEW_STATUSES})")
    op.create_check_constraint(
        _SHAPE_CHECK,
        _TABLE,
        "status <> 'ai_failed' OR (category IS NULL AND identity_key IS NULL "
        "AND next_retry_at IS NOT NULL AND ai_error_kind IS NOT NULL)",
    )
    op.create_check_constraint(_ATTEMPTS_CHECK, _TABLE, "ai_attempts >= 0")
    op.create_index(
        _INDEX,
        _TABLE,
        ["next_retry_at"],
        postgresql_where=sa.text("status = 'ai_failed'"),
    )


def downgrade() -> None:
    op.drop_table("identity_ai_breaker")
    # `ai_failed` não cabe no CHECK antigo: some (era só registro de falha; a
    # próxima coleta chama a IA de novo, como antes desta migration).
    op.execute(f"DELETE FROM {_TABLE} WHERE status = 'ai_failed'")
    op.drop_index(
        _INDEX, table_name=_TABLE, postgresql_where=sa.text("status = 'ai_failed'")
    )
    op.drop_constraint(_ATTEMPTS_CHECK, _TABLE, type_="check")
    op.drop_constraint(_SHAPE_CHECK, _TABLE, type_="check")
    op.drop_constraint(_STATUS_CHECK, _TABLE, type_="check")
    op.create_check_constraint(
        _STATUS_CHECK, _TABLE, f"status IN ({_PREVIOUS_STATUSES})"
    )
    op.drop_column(_TABLE, "next_retry_at")
    op.drop_column(_TABLE, "ai_error_kind")
    op.drop_column(_TABLE, "ai_attempts")
