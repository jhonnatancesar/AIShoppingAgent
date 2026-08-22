"""Modelo persistente de ofertas."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
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
        Index("ix_offers_product_id", "product_id"),
        Index("ix_offers_store_id", "store_id"),
        Index("ix_offers_seller_id", "seller_id"),
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
