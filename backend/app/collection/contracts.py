"""Contratos neutros entre a orquestração e os coletores de lojas."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.collection.errors import CollectionContractError
from app.core.urls import normalize_http_url


class MarketplacePartyKind(StrEnum):
    """Classificação segura de vendedor/entrega em marketplaces."""

    PLATFORM = "platform"
    MARKETPLACE_PARTNER = "marketplace_partner"
    UNKNOWN = "unknown"


class InstallmentInterestKind(StrEnum):
    """TASK-089: só marca `INTEREST_FREE`/`WITH_INTEREST` quando a própria
    loja afirma isso explicitamente no texto (ex.: "sem juros"/"com juros")
    -- `UNKNOWN` nunca é tratado como "provavelmente sem juros"; a
    investigação real confirmou lojas (KaBuM!) que não declaram nada."""

    INTEREST_FREE = "interest_free"
    WITH_INTEREST = "with_interest"
    UNKNOWN = "unknown"


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise CollectionContractError(f"{field_name} must not be blank")


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CollectionContractError(f"{field_name} must be timezone-aware")


def _require_positive_int(value: int, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise CollectionContractError(f"{field_name} must be a positive int")


@dataclass(frozen=True, slots=True)
class RawInstallmentOption:
    """TASK-089: uma condição de parcelamento efetivamente apresentada pela
    loja para uma oferta -- nunca inventada, nunca calculada.

    `raw_amount`/`raw_total_amount` continuam texto bruto (mesmo motivo de
    `RawCollectedOffer.raw_price`): a normalização monetária determinística
    já existe em `PriceNormalizer` e não deve ser duplicada aqui.
    `raw_total_amount` só é preenchido quando a própria loja mostra um
    total rotulado para exatamente esta opção (ex.: `price_total` da
    Pichau) -- na ausência, fica `None`, nunca `installment_count *
    installment_amount`. `discount_percent` só existe quando a loja anuncia
    esse percentual perto da opção (ex.: "com 15% de desconto"); nunca
    inferido a partir de outros preços."""

    installment_count: int
    raw_amount: str
    raw_total_amount: str | None = None
    discount_percent: Decimal | None = None
    interest_kind: InstallmentInterestKind = InstallmentInterestKind.UNKNOWN
    is_highlighted: bool = False
    """Extensão TASK-089 (apresentação Telegram): marca a opção exatamente
    como resumida no card da busca -- a condição que a própria loja
    decidiu destacar, nunca uma escolha do coletor. Só nasce `True` em
    `_installment_options_from_row` (o card sempre expõe no máximo uma
    linha) e é preservada por `_merge_installment_options`; jamais
    recalculada a partir de preço/quantidade."""

    def __post_init__(self) -> None:
        _require_positive_int(self.installment_count, "installment_count")
        _require_text(self.raw_amount, "raw_amount")
        if self.raw_total_amount is not None and not self.raw_total_amount.strip():
            raise CollectionContractError(
                "raw_total_amount must not be blank when informed"
            )
        if self.discount_percent is not None and self.discount_percent < 0:
            raise CollectionContractError("discount_percent must not be negative")
        if not isinstance(self.interest_kind, InstallmentInterestKind):
            raise CollectionContractError(
                "interest_kind must use InstallmentInterestKind"
            )
        if not isinstance(self.is_highlighted, bool):
            raise CollectionContractError("is_highlighted must be a bool")


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
    seller_kind: MarketplacePartyKind | None = None
    fulfillment_kind: MarketplacePartyKind | None = None
    image_url: str | None = None
    evidence: Mapping[str, object] = field(default_factory=dict)
    installment_options: tuple[RawInstallmentOption, ...] = ()
    """TASK-089: zero ou mais condições de parcelamento explicitamente
    apresentadas pela loja para esta oferta -- card e/ou página individual
    (`PlaywrightStoreProvider.enrich_installment_options`). Vazio nunca
    invalida a oferta; preço à vista continua vindo só de `raw_price`."""

    def __post_init__(self) -> None:
        _require_text(self.source_code, "source_code")
        _require_text(self.url, "url")
        _require_text(self.title, "title")
        _require_aware(self.collected_at, "collected_at")
        if self.image_url is not None and normalize_http_url(self.image_url) is None:
            raise CollectionContractError(
                "image_url must be an absolute HTTP/HTTPS URL"
            )
        if not isinstance(self.installment_options, tuple) or any(
            not isinstance(option, RawInstallmentOption)
            for option in self.installment_options
        ):
            raise CollectionContractError(
                "installment_options must be a tuple of RawInstallmentOption"
            )
        counts = [option.installment_count for option in self.installment_options]
        if len(set(counts)) != len(counts):
            raise CollectionContractError(
                "installment_options must not repeat installment_count"
            )


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
