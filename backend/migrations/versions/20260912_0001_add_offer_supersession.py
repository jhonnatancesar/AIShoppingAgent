"""Adiciona `offers.superseded_by_id`/`superseded_at` -- rodada de
frescor (2026-09-11), correção sobre a duplicação do vendedor Amazon:
excluir a Offer antiga (sem vendedor) do preço "atual" só por
envelhecimento (`resolve_offer_freshness`) não bastava -- existe uma
janela real de coexistência com a Offer nova (vendedor identificado)
antes de qualquer confirmação envelhecer, e listagem/comparação podiam
mostrar as duas como alternativas distintas nesse meio-tempo.

Quando um vendedor real é identificado pela primeira vez para um
anúncio (`store_id`+`external_id`) que antes só existia sem vendedor,
a Offer antiga é marcada como substituída pela nova IMEDIATAMENTE
(`app.collection.orchestration._supersede_old_unattributed_offer`),
saindo de listagem/comparação sem esperar frescor -- seu histórico de
`PriceObservation` permanece intocado e continua acessível por ID
direto, nunca fundido nem reatribuído ao vendedor novo.

Revision ID: 20260912_0001
Revises: 20260906_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260912_0001"
down_revision: str | None = "20260906_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "offers",
        sa.Column("superseded_by_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "offers",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_offers_superseded_by_id_offers",
        "offers",
        "offers",
        ["superseded_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_offers_superseded_by_id",
        "offers",
        ["superseded_by_id"],
        postgresql_where=sa.text("superseded_by_id IS NOT NULL"),
    )
    op.create_check_constraint(
        "ck_offers_superseded_by_not_self",
        "offers",
        sa.text("superseded_by_id IS NULL OR superseded_by_id <> id"),
    )
    op.create_check_constraint(
        "ck_offers_superseded_pair_complete",
        "offers",
        sa.text("(superseded_by_id IS NULL) = (superseded_at IS NULL)"),
    )


def downgrade() -> None:
    op.drop_constraint("ck_offers_superseded_pair_complete", "offers", type_="check")
    op.drop_constraint("ck_offers_superseded_by_not_self", "offers", type_="check")
    op.drop_index("ix_offers_superseded_by_id", table_name="offers")
    op.drop_constraint(
        "fk_offers_superseded_by_id_offers", "offers", type_="foreignkey"
    )
    op.drop_column("offers", "superseded_at")
    op.drop_column("offers", "superseded_by_id")
