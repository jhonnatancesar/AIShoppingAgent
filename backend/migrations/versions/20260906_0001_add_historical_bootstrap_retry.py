"""Adiciona retry/backoff e lease ao bootstrap histórico (FASE F1),
reaproveitando o mesmo padrão já aprovado de `MarketPriceAssessment`
(TASK-113): status `FAILED` dedicado, `retry_after`/`failure_count`/
`last_error` para backoff de erro, `lease_until` para recuperar um
`PROCESSING` abandonado -- nenhum desses conceitos é confundido com a
revalidação de 90 dias (`completed_at`), que continua exclusiva do
caminho de sucesso.

Revision ID: 20260906_0001
Revises: 20260905_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260906_0001"
down_revision: str | None = "20260905_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE historical_bootstrap_status ADD VALUE IF NOT EXISTS 'failed'"
        )

    op.add_column(
        "historical_bootstraps",
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "historical_bootstraps",
        sa.Column("retry_after", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "historical_bootstraps",
        sa.Column(
            "failure_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "historical_bootstraps",
        sa.Column("last_error", sa.String(2000), nullable=True),
    )
    op.create_check_constraint(
        "ck_historical_bootstraps_failure_count_non_negative",
        "historical_bootstraps",
        "failure_count >= 0",
    )
    op.create_index(
        "ix_historical_bootstraps_processing_lease",
        "historical_bootstraps",
        ["lease_until"],
        postgresql_where=sa.text("status = 'processing'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_historical_bootstraps_processing_lease",
        table_name="historical_bootstraps",
    )
    op.drop_constraint(
        "ck_historical_bootstraps_failure_count_non_negative",
        "historical_bootstraps",
        type_="check",
    )
    op.drop_column("historical_bootstraps", "last_error")
    op.drop_column("historical_bootstraps", "failure_count")
    op.drop_column("historical_bootstraps", "retry_after")
    op.drop_column("historical_bootstraps", "lease_until")

    # Postgres não permite remover um valor de enum diretamente -- mesmo
    # padrão já usado em `20260808_0009_add_limits_and_resilience.py`
    # (rename -> recria sem o valor -> troca a coluna -> descarta o tipo
    # antigo).
    op.execute(
        "ALTER TYPE historical_bootstrap_status "
        "RENAME TO historical_bootstrap_status_with_failed"
    )
    op.execute(
        "CREATE TYPE historical_bootstrap_status AS ENUM "
        "('processing', 'completed_with_references', "
        "'completed_without_references')"
    )
    op.execute(
        "ALTER TABLE historical_bootstraps ALTER COLUMN status "
        "TYPE historical_bootstrap_status "
        "USING status::text::historical_bootstrap_status"
    )
    op.execute("DROP TYPE historical_bootstrap_status_with_failed")
