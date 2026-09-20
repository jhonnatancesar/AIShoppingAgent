"""Adiciona `coupons.evidence_isolation`/`derived_reference_price` --
mecanismo de cupom sem código (2026-09-12): abrangência/preço derivados
por um cupom sozinhos não bastam para decidir apresentação/validade.

`evidence_isolation` formaliza (como coluna persistida, não só como
`source_kind` implícito de tempo de coleta) o quão isolada era a fonte
de onde a evidência veio -- "widget" (elemento estruturado, ex.:
`<input readonly>` do Magalu) e "component" (card/resultado de busca com
isolamento de DOM real, `card.inner_text()`) são confiáveis; "page"
(texto da página inteira, deepening) pode ter vazado texto de produto
relacionado. Nunca prova abrangência (`scope_kind` continua sendo o
único campo de escopo) -- só qualifica a evidência.

`derived_reference_price` guarda o preço-base usado para DERIVAR um
desconto que a própria loja não informa em R$/% (ex.: economia = "Por:
R$X" - "Você paga com o cupom R$Y" da Amazon, `coupons/evidence.py:
_amazon_economia` no Coupon Worker). Sozinho nunca garante que o
desconto continua válido -- vendedor/variante/condição podem ter mudado
desde a coleta; `app.coupons.pricing.is_derived_discount_still_valid`
faz essa checagem adicional no momento do consumo.

Revision ID: 20260912_0002
Revises: 20260912_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260912_0002"
down_revision: str | None = "20260912_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "coupons",
        sa.Column("evidence_isolation", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "coupons",
        sa.Column("derived_reference_price", sa.Numeric(19, 4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("coupons", "derived_reference_price")
    op.drop_column("coupons", "evidence_isolation")
