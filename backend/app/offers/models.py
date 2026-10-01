"""Modelo persistente de ofertas."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.time import utc_now


class Offer(Base):
    """Anúncio estável de um produto em uma loja, sem preço corrente."""

    __tablename__ = "offers"
    __table_args__ = (
        CheckConstraint(
            "external_id IS NULL OR btrim(external_id) <> ''",
            name="ck_offers_external_id_not_blank",
        ),
        CheckConstraint("btrim(url) <> ''", name="ck_offers_url_not_blank"),
        CheckConstraint(
            "image_url IS NULL OR image_url ~* '^https?://[^/@?#[:space:]]+([/?#]|$)'",
            name="ck_offers_image_url_http",
        ),
        CheckConstraint(
            "rating_average IS NULL OR (rating_average >= 0 AND rating_average <= 5)",
            name="ck_offers_rating_average_range",
        ),
        CheckConstraint(
            "review_count IS NULL OR review_count >= 0",
            name="ck_offers_review_count_non_negative",
        ),
        CheckConstraint(
            "(rating_average IS NULL AND review_count IS NULL AND "
            "rating_observed_at IS NULL) OR "
            "(rating_average IS NOT NULL AND review_count IS NOT NULL AND "
            "rating_observed_at IS NOT NULL)",
            name="ck_offers_rating_snapshot_complete",
        ),
        Index("ix_offers_product_id", "product_id"),
        Index("ix_offers_store_id", "store_id"),
        Index("ix_offers_seller_id", "seller_id"),
        Index(
            "ix_offers_superseded_by_id",
            "superseded_by_id",
            postgresql_where="superseded_by_id IS NOT NULL",
        ),
        CheckConstraint(
            "sales_count IS NULL OR sales_count >= 0",
            name="ck_offers_sales_count_non_negative",
        ),
        CheckConstraint(
            "sales_scope IS NULL OR sales_scope IN ('last_month', 'total')",
            name="ck_offers_sales_scope_values",
        ),
        CheckConstraint(
            "(sales_count IS NULL AND sales_scope IS NULL AND "
            "sales_observed_at IS NULL) OR "
            "(sales_count IS NOT NULL AND sales_scope IS NOT NULL AND "
            "sales_observed_at IS NOT NULL)",
            name="ck_offers_sales_snapshot_complete",
        ),
        CheckConstraint(
            "superseded_by_id IS NULL OR superseded_by_id <> id",
            name="ck_offers_superseded_by_not_self",
        ),
        CheckConstraint(
            "(superseded_by_id IS NULL) = (superseded_at IS NULL)",
            name="ck_offers_superseded_pair_complete",
        ),
        Index(
            "uq_offers_retailer_external_id",
            "store_id",
            "external_id",
            unique=True,
            postgresql_where="seller_id IS NULL AND external_id IS NOT NULL",
        ),
        Index(
            "uq_offers_marketplace_external_id",
            "store_id",
            "seller_id",
            "external_id",
            unique=True,
            postgresql_where="seller_id IS NOT NULL AND external_id IS NOT NULL",
        ),
        Index(
            "uq_offers_retailer_url",
            "store_id",
            "url",
            unique=True,
            postgresql_where="seller_id IS NULL",
        ),
        Index(
            "uq_offers_marketplace_url",
            "store_id",
            "seller_id",
            "url",
            unique=True,
            postgresql_where="seller_id IS NOT NULL",
        ),
        ForeignKeyConstraint(
            ["seller_id", "store_id"],
            ["sellers.id", "sellers.store_id"],
            name="fk_offers_seller_store_sellers",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    product_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
    )
    store_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="RESTRICT"),
        nullable=False,
    )
    seller_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    rating_average: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    review_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    rating_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sales_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sales_scope: Mapped[str | None] = mapped_column(String(16), nullable=True)
    sales_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """TASK-136: quantidade de vendas que a LOJA publica no card (Amazon, mensal:
    `last_month`; Mercado Livre, acumulada: `total`), sinal de popularidade lido
    antes da IA. Só existe quando a loja informa; ausência nunca apaga o último
    valor. O par/trio é todo preenchido ou todo nulo, como a nota."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    """TASK-093: quando a oferta foi confirmada como ainda existente pela
    última vez numa coleta -- distinto de `updated_at` (que reflete
    qualquer alteração de metadado, ex. `image_url`, sem relação com
    continuidade comercial). Atualizado a cada coleta bem-sucedida dessa
    oferta, mesmo quando nenhuma `PriceObservation` nova é criada por
    redundância semântica (`app.collection.orchestration._persist_phase_a`)
    -- é o único lugar que preserva "a oferta continuou sendo vista" sem
    tocar no histórico append-only de `PriceObservation`."""
    superseded_by_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("offers.id", ondelete="SET NULL"),
        nullable=True,
    )
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """Rodada de frescor (2026-09-11, correção sobre a exclusão só por
    idade): quando um vendedor real é identificado pela primeira vez
    para um anúncio (`store_id`+`external_id`) que antes só existia sem
    vendedor (`seller_id IS NULL`), a Offer ANTIGA é marcada como
    substituída pela NOVA (`_supersede_old_unattributed_offer`,
    `orchestration.py`) -- some de listagem/comparação IMEDIATAMENTE
    (não espera a oferta antiga envelhecer via `resolve_offer_
    freshness`), mas seu histórico (`PriceObservation`) permanece
    intocado e acessível por ID direto -- nunca fundido nem reatribuído
    ao vendedor novo. `ondelete=SET NULL` (nunca CASCADE/RESTRICT): a
    Offer nova pode ser removida sem impedir a remoção da antiga nem
    reviver a supersessão como se apontasse pra outra linha."""


class OfferShortLink(Base):
    """Token público opaco que resolve sempre a URL corrente da Offer."""

    __tablename__ = "offer_short_links"
    __table_args__ = (
        CheckConstraint(
            "btrim(token) <> ''", name="ck_offer_short_links_token_not_blank"
        ),
        UniqueConstraint("offer_id", name="uq_offer_short_links_offer_id"),
    )

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    offer_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("offers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )


class OfferIdentityWatch(Base):
    """TASK-132 (Parte A): vigilância do título de um anúncio já cadastrado.

    Quando o título coletado deixa de descrever o produto ao qual a Offer está
    ligada (a versão padrão de um anúncio da Amazon mudou, a loja reaproveitou o
    link), esta linha acumula as coletas seguidas com o MESMO título novo
    (`sightings`). Só depois de `LISTING_CHANGE_CONFIRMATIONS` avistamentos com
    identidade conhecida e diferente a troca é aplicada (`app.offers.
    listing_change`). Some quando o título volta a bater com o produto.
    `needs_ai`: o título novo ainda não tem identidade sem IA; a varredura
    `app.products.listing_title_sweep` resolve (com orçamento por ciclo)."""

    __tablename__ = "offer_identity_watch"
    __table_args__ = (
        CheckConstraint("sightings >= 1", name="ck_offer_identity_watch_sightings"),
        CheckConstraint("ai_attempts >= 0", name="ck_offer_identity_watch_ai_attempts"),
        Index(
            "ix_offer_identity_watch_needs_ai",
            "first_seen_at",
            postgresql_where="needs_ai",
        ),
    )

    offer_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("offers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    pending_title: Mapped[str] = mapped_column(Text, nullable=False)
    pending_title_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    sightings: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )
    needs_ai: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false"
    )
    ai_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )
