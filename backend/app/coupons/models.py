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

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
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
    evidence_isolation: Mapped[str | None] = mapped_column(String(20))
    """`"widget"` | `"component"` | `"page"` | `None` -- qualifica quão
    isolada era a fonte da evidência (worker), nunca prova abrangência
    (`scope_kind` continua sendo o único campo de escopo). `"page"`
    (texto de página inteira, deepening) pode ter vazado texto de
    produto relacionado -- `"widget"`/`"component"` têm isolamento de DOM
    real. `None` = worker anterior a este campo, sem essa qualificação."""
    derived_reference_price: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))
    """Preço-base usado para DERIVAR um desconto que a loja não informa
    literalmente em R$/% (ex.: economia = "Por: R$X" - "Você paga com o
    cupom" da Amazon). Sozinho nunca garante que o desconto continua
    válido -- ver `app.coupons.pricing.is_derived_discount_still_valid`,
    chamada obrigatoriamente no consumo quando este campo não é `None`."""
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


class OfferCouponPriceDay(Base):
    """TASK-125: o preço com cupom que a COLETA calculou, guardado por
    (Offer, observação de preço, dia comercial) -- antes esse cálculo
    (`best_applicable_coupon`, Fase B) só alimentava o alerta e era
    descartado, então o gráfico de histórico nunca mostrava o preço com
    desconto. Decisão do usuário (2026-09-26): registrar só daqui pra
    frente (dado exato, nada reconstruído); o gráfico usa este valor no
    ponto do dia e mostra preço normal + cupom no tooltip.

    Uma linha por (Offer, observação, dia): a MESMA observação pode ser
    reaproveitada em dias diferentes (preço de tabela igual, TASK-093), e
    o cupom de cada dia pode mudar -- por isso o dia entra na chave. Várias
    coletas no mesmo dia guardam o MENOR preço com cupom visto nele."""

    __tablename__ = "offer_coupon_price_days"
    __table_args__ = (
        UniqueConstraint(
            "offer_id",
            "observation_id",
            "commercial_day",
            name="uq_offer_coupon_price_days_confirmation",
        ),
        CheckConstraint(
            "discount_amount > 0", name="ck_offer_coupon_price_days_discount_positive"
        ),
        CheckConstraint(
            "final_amount >= 0", name="ck_offer_coupon_price_days_final_non_negative"
        ),
        CheckConstraint(
            "final_amount = original_amount - discount_amount",
            name="ck_offer_coupon_price_days_final_consistent",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    offer_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("offers.id", ondelete="CASCADE"),
        nullable=False,
    )
    observation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("price_observations.id", ondelete="CASCADE"),
        nullable=False,
    )
    commercial_day: Mapped[date] = mapped_column(Date, nullable=False)
    """Dia comercial de `America/Sao_Paulo` -- mesmo critério do gráfico."""
    coupon_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("coupons.id", ondelete="SET NULL"),
        nullable=True,
    )
    coupon_code: Mapped[str] = mapped_column(Text, nullable=False)
    """Cópia do código (`''` = cupom automático, sem código) -- o tooltip
    continua mostrando qual cupom foi usado mesmo se o `Coupon` sumir."""
    original_amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    final_amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
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
