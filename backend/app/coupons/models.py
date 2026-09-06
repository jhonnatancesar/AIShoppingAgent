"""Cupons coletados pelo Coupon Worker (repositório separado
`AIShoppingAgent-cupom`), persistidos diretamente no mesmo PostgreSQL do
GG Oferta -- decisão de arquitetura de 2026-09-06: sem sync de SQLite,
sem API intermediária, sem segundo banco para integração.

`Coupon` espelha EXATAMENTE os campos que o worker produz hoje (ver
`coupons/persistence.py` daquele repositório, dataclass `Coupon`) --
`scope_kind`/`scope_reference`/`valid_until` são preservados CRUS
(texto), sem nenhum parsing ou validação: a regra de aplicabilidade
ainda não foi definida.

O vínculo com `Offer` é uma tabela de associação separada
(`CouponOfferLink`), nunca uma coluna em `coupons` -- um mesmo cupom
pode se aplicar a mais de uma oferta, e um cupom sem nenhum vínculo é
simplesmente genérico (ainda não avaliado). O worker NUNCA cria essas
associações (não conhece a regra de aplicabilidade); só o GG Oferta as
cria, numa fase futura.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.time import utc_now


class Coupon(Base):
    """Uma observação de cupom com evidência real -- nunca fabricada.

    Dedup real (mesma semântica já usada pelo worker no SQLite):
    `(store_id, code, evidence)`, com `code` NUNCA `NULL` (`''` quando o
    cupom não tem código próprio, ex.: clip automático) -- evita a
    diferença de semântica de `NULL` em `UNIQUE` entre SQLite e
    PostgreSQL.
    """

    __tablename__ = "coupons"
    __table_args__ = (
        CheckConstraint("btrim(evidence) <> ''", name="ck_coupons_evidence_not_blank"),
        UniqueConstraint("store_id", "code", "evidence", name="uq_coupons_evidence"),
        Index("ix_coupons_store_status", "store_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    store_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="RESTRICT"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(Text, nullable=False, default="")
    discount_kind: Mapped[str | None] = mapped_column(String(20))
    """`"fixed_amount"` | `"percentage"` | `None` -- cru, do worker."""
    discount_value: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))
    minimum_purchase_amount: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))
    maximum_discount_amount: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))
    scope_kind: Mapped[str | None] = mapped_column(String(20))
    """`"product"` | `"store_wide"` | `"category"` | `None` -- preservado
    cru; nenhuma regra de aplicabilidade decidida ainda."""
    scope_reference: Mapped[str | None] = mapped_column(Text)
    """Texto livre do worker -- preservado cru, nunca resolvido contra
    `Product`/`Offer` nesta fase."""
    valid_until: Mapped[str | None] = mapped_column(Text)
    """Texto, nunca data estruturada -- o worker não garante formato
    confiável (deliberadamente nunca infere data relativa). Não inventar
    parsing aqui."""
    raw_rule_text: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    """`"active"` | `"expired"` -- vocabulário do worker, sem CHECK
    constraint de propósito (preservação crua, mesmo espírito de
    `scope_kind`)."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )


class CouponOfferLink(Base):
    """Associação N:N entre `Coupon` e `Offer` -- criada só pelo GG Oferta,
    nunca pelo worker. Apagar uma `Offer` remove só o VÍNCULO
    (`ondelete="CASCADE"` em `offer_id`) -- o `Coupon` nunca é afetado,
    porque ele não tem nenhuma FK direta pra `Offer`. Uma `Offer` nunca
    fica impossível de excluir só por ter um cupom associado (correção
    explícita: a primeira versão usava `RESTRICT` nas duas FKs, o que
    bloquearia a exclusão -- vínculo nunca deve travar exclusão de
    Offer)."""

    __tablename__ = "coupon_offer_links"
    __table_args__ = (
        UniqueConstraint("coupon_id", "offer_id", name="uq_coupon_offer_links_pair"),
        Index("ix_coupon_offer_links_offer", "offer_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    coupon_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("coupons.id", ondelete="RESTRICT"),
        nullable=False,
    )
    offer_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("offers.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
