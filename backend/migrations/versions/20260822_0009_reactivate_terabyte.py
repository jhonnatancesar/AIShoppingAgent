"""Reativa a Terabyte -- bloqueio Cloudflare superado por transporte Edge/CDP.

Revision ID: 20260822_0009
Revises: 20260822_0008
Create Date: 2026-08-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260822_0009"
down_revision: str | None = "20260822_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STORES_TABLE = sa.table(
    "stores",
    sa.column("code", sa.String),
    sa.column("is_active", sa.Boolean),
)


def upgrade() -> None:
    """TASK-105: reverte a desativação de dados do `DEC-070` (Cloudflare
    Bot Management bloqueava o Chromium gerenciado pelo Playwright).
    `TerabyteProvider` passou a usar Edge/CDP como transporte primário e
    único (mesma infraestrutura já supervisionada para a Magalu), sem
    fallback para o Playwright comprovadamente bloqueado -- a Terabyte
    sobe ativa a partir desta revisão, sem `UPDATE` manual em produção."""
    op.execute(
        _STORES_TABLE.update()
        .where(_STORES_TABLE.c.code == "terabyte")
        .values(is_active=True)
    )


def downgrade() -> None:
    """Restaura exatamente o estado do `DEC-070` (Terabyte desativada)."""
    op.execute(
        _STORES_TABLE.update()
        .where(_STORES_TABLE.c.code == "terabyte")
        .values(is_active=False)
    )
