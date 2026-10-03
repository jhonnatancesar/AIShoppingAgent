"""TASK-132 (Parte B) -- catálogo de nomenclaturas de produto.

Uma linha por (categoria, marca, família, modelo, variante) conhecida, com os
códigos que a identificam num título de loja: part number do fabricante e
nomes. É consultado ANTES do reuso por palavras do título e da IA
(`app.products.identity_catalog`), para o GG não depender de a IA "descobrir"
de novo o que já se sabe. Alimentado sozinho pelas identidades já aprovadas que
têm part number (entrada `learned`), por uma pré-lista de produtos que mais
saem (`seed`) e à mão (`manual`).

`identity_key` só existe para entrada de identidade completa; entrada com
`required_attributes` (ex.: celular, onde a capacidade decide o produto) guarda
só a `family_key` e monta a identidade a partir do título, pelo mesmo caminho
do extrator determinístico (atributo `storage_gb`).
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.time import utc_now


class ProductIdentityCatalogEntry(Base):
    __tablename__ = "product_identity_catalog_entries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'rejected')",
            name="ck_product_identity_catalog_entries_status_values",
        ),
        CheckConstraint(
            "source IN ('seed', 'learned', 'manual', 'buildcores', 'wikidata')",
            name="ck_product_identity_catalog_entries_source_values",
        ),
        CheckConstraint(
            "btrim(category) <> '' AND btrim(brand) <> '' AND btrim(family) <> '' "
            "AND btrim(model) <> '' AND btrim(variant) <> ''",
            name="ck_product_identity_catalog_entries_fields_not_blank",
        ),
        UniqueConstraint(
            "category",
            "brand",
            "family",
            "model",
            "variant",
            name="uq_product_identity_catalog_entries_identity",
        ),
        Index("ix_product_identity_catalog_entries_status", "status"),
        Index("ix_product_identity_catalog_entries_source_ref", "source", "source_ref"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    brand: Mapped[str] = mapped_column(String(160), nullable=False)
    family: Mapped[str] = mapped_column(String(160), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    variant: Mapped[str] = mapped_column(
        String(160), nullable=False, default="base", server_default="base"
    )
    attributes: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    required_attributes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    family_key: Mapped[str] = mapped_column(String(80), nullable=False)
    identity_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="learned", server_default="learned"
    )
    source_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    """TASK-137: id do registro na fonte aberta (`opendb_id` do BuildCores, Q-id do
    Wikidata); `NULL` nas entradas `seed`/`learned`/`manual`."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )


class ProductIdentityCatalogCode(Base):
    """Código que aponta para uma entrada: `part_number` (compactado, só
    A-Z/0-9) ou `name` (tokens normalizados do nome). Único no catálogo todo,
    inclusive contra entrada `rejected` -- recusa fica guardada e o mesmo
    código nunca é reaprendido."""

    __tablename__ = "product_identity_catalog_codes"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('part_number', 'name')",
            name="ck_product_identity_catalog_codes_kind_values",
        ),
        CheckConstraint(
            "btrim(value_normalized) <> ''",
            name="ck_product_identity_catalog_codes_value_not_blank",
        ),
        UniqueConstraint(
            "kind", "value_normalized", name="uq_product_identity_catalog_codes_value"
        ),
        Index("ix_product_identity_catalog_codes_entry_id", "entry_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    entry_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("product_identity_catalog_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    value_normalized: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )


class CatalogRequestResolution(Base):
    """TASK-137 (passo C): decisão da IA entre candidatos do catálogo para UM pedido
    de missão em dúvida (vários parecidos ou nenhum exato). Uma linha por pedido
    normalizado: o mesmo pedido nunca gasta IA duas vezes.

    `pending`: registrada na criação da missão, esperando o worker. `resolved`: a IA
    escolheu um candidato coerente com o pedido (`chosen_entry_id`). `none`: a IA
    (ou a conferência) disse que nenhum serve -- a missão fica como está. `ai_failed`:
    a IA não deu resposta utilizável; `next_retry_at` controla a nova tentativa."""

    __tablename__ = "catalog_request_resolutions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'resolved', 'none', 'ai_failed')",
            name="ck_catalog_request_resolutions_status_values",
        ),
        CheckConstraint(
            "attempts >= 0", name="ck_catalog_request_resolutions_attempts_non_negative"
        ),
        UniqueConstraint("request_key", name="uq_catalog_request_resolutions_key"),
        Index("ix_catalog_request_resolutions_due", "status", "next_retry_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    request_key: Mapped[str] = mapped_column(String(64), nullable=False)
    request_text: Mapped[str] = mapped_column(String(500), nullable=False)
    understood: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    candidates: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    chosen_entry_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("product_identity_catalog_entries.id", ondelete="SET NULL"),
        nullable=True,
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    ai_error_kind: Mapped[str | None] = mapped_column(String(24), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ai_provider: Mapped[str | None] = mapped_column(String(80), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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
