"""Aprendizado de identidade de produto assistido por IA (rodada de
2026-09-12): quando nenhum extrator determinístico de
`app.products.identity` reconhece um título, `app.products.identity_ai`
tenta uma extração assistida por IA (via `AIProviderManager` -> César
Core, nunca provider direto), validada por grounding determinístico
(marca/família/modelo precisam aparecer literalmente no título -- a IA
nunca decide sozinha, só estrutura o que já está no texto).

`ProductIdentityCandidate` é conceitualmente SEPARADO de
`ProductIdentityAlias` (`app.products.models`): aquele corrige a
ESCRITA de um valor de atributo já conhecido dentro de uma categoria
existente (ex.: "ASUSTeK" -> "asus"); este aprende uma FAMÍLIA/MODELO
inteira nova a partir de texto livre, com sua própria fila de revisão.
Nenhum dos dois sobrecarrega o outro.

Chave de reuso é `normalized_title_hash` (SHA-256 do título já
normalizado por `app.products.identity._normalized`) -- o MESMO título
(de qualquer loja) nunca dispara uma segunda chamada de IA depois que
já existe uma linha `approved` para esse hash; sobrevive a restart
porque é Postgres, não cache em processo."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.time import utc_now


class ProductIdentityCandidate(Base):
    """Uma proposta de identidade estruturada para UM título bruto
    específico -- nunca uma regra genérica/regex gerada por IA (risco
    de segurança/corretude que este desenho evita deliberadamente: a
    IA só preenche campos, a fórmula de `family_key`/`identity_key`
    continua sendo código puro determinístico, ver
    `app.products.identity.build_resolved_variant_from_fields`).

    `status`: `"approved"` (grounding OK + campos obrigatórios completos
    -- participa de resolução automática de identidade, mesmo nível de
    confiança que os extratores regex); `"pending_review"` (grounding
    parcial ou incompleto -- NUNCA usado para resolver identidade
    automaticamente até um humano promover); `"rejected"` (revisado e
    recusado -- preservado para nunca tentar de novo o MESMO título
    sem uma decisão explícita nova).

    TASK-128 (decisão do usuário: "ela não pode não resolver"): dois
    estados de CACHE sem identidade exata -- `"partial"` (vínculo
    parcial: categoria + marca/família quando aparecem no título, nunca
    `identity_key`/`family_key`, então nunca funde produtos) e
    `"awaiting_page"` (nem a categoria saiu do título; o worker lê a
    página e a IA tenta de novo). Nos dois, o mesmo título nunca chama a
    IA outra vez só pelo título."""

    __tablename__ = "product_identity_candidates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('approved', 'pending_review', 'rejected', 'partial', "
            "'awaiting_page')",
            name="ck_product_identity_candidates_status_values",
        ),
        CheckConstraint(
            "status NOT IN ('approved', 'pending_review', 'rejected') OR ("
            "category IS NOT NULL AND brand IS NOT NULL AND family IS NOT NULL "
            "AND model IS NOT NULL AND variant IS NOT NULL "
            "AND family_key IS NOT NULL AND identity_key IS NOT NULL)",
            name="ck_product_identity_candidates_complete_identity",
        ),
        CheckConstraint(
            "status <> 'partial' OR (category IS NOT NULL AND model IS NULL "
            "AND family_key IS NULL AND identity_key IS NULL)",
            name="ck_product_identity_candidates_partial_shape",
        ),
        CheckConstraint(
            "status <> 'awaiting_page' OR (category IS NULL AND identity_key IS NULL)",
            name="ck_product_identity_candidates_awaiting_page_shape",
        ),
        CheckConstraint(
            "btrim(raw_title) <> ''",
            name="ck_product_identity_candidates_raw_title_not_blank",
        ),
        CheckConstraint(
            "btrim(category) <> ''",
            name="ck_product_identity_candidates_category_not_blank",
        ),
        CheckConstraint(
            "btrim(brand) <> ''", name="ck_product_identity_candidates_brand_not_blank"
        ),
        CheckConstraint(
            "btrim(family) <> ''",
            name="ck_product_identity_candidates_family_not_blank",
        ),
        CheckConstraint(
            "btrim(model) <> ''", name="ck_product_identity_candidates_model_not_blank"
        ),
        Index(
            "uq_product_identity_candidates_title_hash",
            "normalized_title_hash",
            unique=True,
        ),
        Index("ix_product_identity_candidates_status", "status"),
        Index("ix_product_identity_candidates_identity_key", "identity_key"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    raw_title: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_title_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(160), nullable=True)
    family: Mapped[str | None] = mapped_column(String(160), nullable=True)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    variant: Mapped[str | None] = mapped_column(String(160), nullable=True)
    attributes: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    store_sku: Mapped[str | None] = mapped_column(String(160), nullable=True)
    """Código/SKU que a PRÓPRIA loja atribui ao anúncio (checkpoint 3,
    2026-09-13) -- específico da origem, nunca um identificador
    confiável entre lojas diferentes; nunca participa da fórmula de
    `identity_key` nem é exigido por `_find_reusable_candidate_by_
    tokens` (ausência numa loja não prova produto diferente)."""
    manufacturer_part_number: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    """Part number/código de peça atribuído pelo FABRICANTE (checkpoint
    3, 2026-09-13) -- quando duas lojas exibem o MESMO valor, é
    evidência forte de mesma variante comercial (ver
    `app.products.identity_arbiter`); também nunca participa da
    fórmula de `identity_key` (algumas lojas simplesmente não expõem
    esse campo, e isso não pode virar contradição)."""
    family_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    identity_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending_review",
        server_default="pending_review",
    )
    grounded: Mapped[bool] = mapped_column(nullable=False, default=False)
    """`True` só quando marca/família/modelo devolvidos pela IA foram
    verificados como presentes literalmente no título normalizado
    (`app.products.identity_ai._is_grounded`) -- condição necessária
    (não suficiente sozinha) para `status="approved"`."""
    ai_provider: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ai_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reviewer_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
