"""TASK-132 (Parte A): vigilância do título de anúncios já cadastrados -- detecta
que o anúncio passou a descrever OUTRO produto (versão padrão mudou, link
reaproveitado) e acumula as coletas seguidas antes de trocar o cadastro.
Nasce vazia.

Revision ID: 20260928_0002
Revises: 20260928_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260928_0002"
down_revision: str | None = "20260928_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "offer_identity_watch"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("offer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pending_title", sa.Text(), nullable=False),
        sa.Column("pending_title_hash", sa.String(length=80), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sightings", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("needs_ai", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("ai_attempts", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("sightings >= 1", name="ck_offer_identity_watch_sightings"),
        sa.CheckConstraint(
            "ai_attempts >= 0", name="ck_offer_identity_watch_ai_attempts"
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["offers.id"],
            name=op.f("fk_offer_identity_watch_offer_id_offers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("offer_id", name=op.f("pk_offer_identity_watch")),
    )
    op.create_index(
        "ix_offer_identity_watch_needs_ai",
        _TABLE,
        ["first_seen_at"],
        postgresql_where=sa.text("needs_ai"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_offer_identity_watch_needs_ai",
        table_name=_TABLE,
        postgresql_where=sa.text("needs_ai"),
    )
    op.drop_table(_TABLE)
