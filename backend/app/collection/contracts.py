"""Contratos neutros entre a orquestração e os coletores de lojas."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.collection.errors import CollectionContractError


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise CollectionContractError(f"{field_name} must not be blank")


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CollectionContractError(f"{field_name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class CollectionRequest:
    """Solicitação de coleta já restrita a uma fonte selecionada."""

    mission_id: UUID
    source_code: str
    search_query: str
    requested_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.source_code, "source_code")
        _require_text(self.search_query, "search_query")
        _require_aware(self.requested_at, "requested_at")


@dataclass(frozen=True, slots=True)
class RawCollectedOffer:
    """Oferta bruta; preço e demais campos serão normalizados na TASK-025."""

    source_code: str
    url: str
    title: str
    collected_at: datetime
    external_id: str | None = None
    seller_external_id: str | None = None
    seller_name: str | None = None
    raw_price: str | None = None
    raw_currency: str | None = None
    raw_shipping: str | None = None
    raw_availability: str | None = None
    raw_fulfillment: str | None = None
    evidence: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.source_code, "source_code")
        _require_text(self.url, "url")
        _require_text(self.title, "title")
        _require_aware(self.collected_at, "collected_at")


@dataclass(frozen=True, slots=True)
class CollectionResult:
    """Lote bruto devolvido por exatamente um provider."""

    source_code: str
    started_at: datetime
    completed_at: datetime
    offers: tuple[RawCollectedOffer, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.source_code, "source_code")
        _require_aware(self.started_at, "started_at")
        _require_aware(self.completed_at, "completed_at")
        if self.completed_at < self.started_at:
            raise CollectionContractError("completed_at must not precede started_at")
        if any(offer.source_code != self.source_code for offer in self.offers):
            raise CollectionContractError("all offers must belong to result source")


@runtime_checkable
class CollectionProvider(Protocol):
    """Porta assíncrona implementada por cada fonte na TASK-055."""

    source_code: str

    async def collect(self, request: CollectionRequest) -> CollectionResult: ...


@dataclass(frozen=True, slots=True)
class ResolvedProductIdentity:
    """TASK-083: identidade de um `model` cru confirmada por um provider de
    loja dedicado à resolução -- `search_query` vem sempre de um título de
    candidato real que bateu com o modelo, nunca de invenção/canonicalização
    nova."""

    model: str
    search_query: str
    source: str

    def __post_init__(self) -> None:
        _require_text(self.model, "model")
        _require_text(self.search_query, "search_query")
        _require_text(self.source, "source")


@runtime_checkable
class ProductIdentityResolver(Protocol):
    """TASK-083: porta para resolver um `model` cru contra fontes de
    produto reais. A implementação concreta (Playwright, providers de
    loja dedicados) pertence à camada `collection` -- nunca exposta ao
    `IntentInterpreter`/webhook."""

    async def resolve(self, model: str) -> ResolvedProductIdentity | None: ...
