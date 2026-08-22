"""Normalização monetária exata dos resultados brutos de coleta."""

import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from app.collection.contracts import (
    CollectionResult,
    InstallmentInterestKind,
    MarketplacePartyKind,
    OfferCondition,
    RawCollectedOffer,
    RawInstallmentOption,
)
from app.collection.errors import CollectionNormalizationError

logger = logging.getLogger("app.collection.normalization")

_CURRENCY_SYMBOLS = {"R$": "BRL", "$": "USD", "€": "EUR", "£": "GBP"}
_FREE_SHIPPING = re.compile(r"\b(frete\s+gr[aá]tis|gr[aá]tis|free\s+shipping)\b", re.I)
_UNAVAILABLE = re.compile(
    r"\b(indispon[ií]vel|fora\s+de\s+estoque|esgotado|unavailable)\b", re.I
)
_AVAILABLE = re.compile(r"\b(em\s+estoque|dispon[ií]vel|restam\s+\d+)\b", re.I)


class Availability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class NormalizedInstallmentOption:
    """TASK-089: uma opção de parcelamento já com valores monetários
    convertidos para `Decimal` -- `installment_total_amount` continua
    `None` sempre que a loja não rotular um total para esta opção
    específica (nunca `installment_count * installment_amount`)."""

    installment_count: int
    installment_amount: Decimal
    installment_total_amount: Decimal | None
    discount_percent: Decimal | None
    interest_kind: InstallmentInterestKind
    is_highlighted: bool


@dataclass(frozen=True, slots=True)
class NormalizedCollectedOffer:
    """Oferta pronta para persistência posterior, sem perder sua evidência bruta."""

    raw_offer: RawCollectedOffer
    amount: Decimal
    currency: str
    shipping_amount: Decimal | None
    total_amount: Decimal
    availability: Availability
    condition: OfferCondition

    @property
    def seller_external_id(self) -> str | None:
        return self.raw_offer.seller_external_id

    @property
    def seller_name(self) -> str | None:
        return self.raw_offer.seller_name

    @property
    def fulfillment(self) -> str | None:
        return self.raw_offer.raw_fulfillment

    @property
    def seller_kind(self) -> MarketplacePartyKind | None:
        return self.raw_offer.seller_kind

    @property
    def fulfillment_kind(self) -> MarketplacePartyKind | None:
        return self.raw_offer.fulfillment_kind

    @property
    def rating_average(self) -> Decimal | None:
        return PriceNormalizer._rating_snapshot(
            self.raw_offer.raw_rating_average,
            self.raw_offer.raw_review_count,
        )[0]

    @property
    def review_count(self) -> int | None:
        return PriceNormalizer._rating_snapshot(
            self.raw_offer.raw_rating_average,
            self.raw_offer.raw_review_count,
        )[1]

    @property
    def installment_options(self) -> tuple[NormalizedInstallmentOption, ...]:
        """TASK-089: recalculado a partir de `raw_offer` a cada acesso --
        nunca armazenado -- pelo mesmo motivo de `seller_kind`/
        `fulfillment_kind`: `enrich_installment_options` (Pichau/Terabyte)
        só atualiza `raw_offer` depois que a oferta já foi normalizada uma
        vez; um campo armazenado ficaria com os dados de antes do
        enriquecimento."""
        return PriceNormalizer._installment_options(
            self.raw_offer.installment_options, self.currency
        )


@dataclass(frozen=True, slots=True)
class NormalizedCollectionResult:
    raw_result: CollectionResult
    offers: tuple[NormalizedCollectedOffer, ...]


class PriceNormalizer:
    """Converte apenas formatos monetários determinísticos, sem usar float."""

    def normalize_result(self, result: CollectionResult) -> NormalizedCollectionResult:
        return NormalizedCollectionResult(
            raw_result=result,
            offers=tuple(self.normalize_offer(offer) for offer in result.offers),
        )

    def normalize_offer(self, offer: RawCollectedOffer) -> NormalizedCollectedOffer:
        currency = self._currency(offer.raw_currency, offer.raw_price)
        amount = self._amount(offer.raw_price, currency, field="price")
        shipping = self._shipping(offer.raw_shipping, currency)
        availability = self._availability(offer.raw_availability)
        condition = self._condition(offer.raw_condition)
        total = amount + (shipping if shipping is not None else Decimal(0))
        _require_numeric_19_4(total, "total")
        return NormalizedCollectedOffer(
            raw_offer=offer,
            amount=amount,
            currency=currency,
            shipping_amount=shipping,
            total_amount=total,
            availability=availability,
            condition=condition,
        )

    @staticmethod
    def _rating_snapshot(
        raw_average: str | None, raw_count: str | None
    ) -> tuple[Decimal | None, int | None]:
        """Normaliza somente o par explicitamente declarado pela loja.

        Avaliação é opcional e nunca invalida uma oferta comercial válida.
        Contagens visuais abreviadas não são expandidas; o provider deve
        fornecer a evidência exata (por exemplo, o ``aria-label`` da Amazon).
        """
        if raw_average is None and raw_count is None:
            return (None, None)
        if raw_average is None or raw_count is None:
            logger.warning("offer_rating_snapshot_incomplete")
            return (None, None)
        clean_average = raw_average.replace("\xa0", " ").strip()
        clean_count = raw_count.replace("\xa0", " ").strip()
        average_match = re.fullmatch(r"([0-5](?:[.,]\d{1,2})?)", clean_average)
        if average_match is None:
            average_match = re.search(
                r"(?<!\d)([0-5](?:[.,]\d{1,2})?)\s+de\s+5\s+estrelas?",
                clean_average,
                re.I,
            )
        count_match = re.fullmatch(r"(\d+)", clean_count)
        if count_match is None:
            count_match = re.search(
                r"(?<!\d)(\d{1,3}(?:[.\s]\d{3})*|\d+)\s+"
                r"(?:classificaç(?:ão|ões)|avaliaç(?:ão|ões))",
                clean_count,
                re.I,
            )
        if average_match is None or count_match is None:
            logger.warning("offer_rating_snapshot_normalization_failed")
            return (None, None)
        try:
            average = Decimal(average_match.group(1).replace(",", "."))
            count = int(re.sub(r"[.\s]", "", count_match.group(1)))
        except (InvalidOperation, ValueError):
            logger.warning("offer_rating_snapshot_normalization_failed")
            return (None, None)
        if not average.is_finite() or not Decimal(0) <= average <= Decimal(5):
            logger.warning("offer_rating_snapshot_normalization_failed")
            return (None, None)
        return (average, count)

    @staticmethod
    def _installment_options(
        raw_options: tuple[RawInstallmentOption, ...], currency: str
    ) -> tuple[NormalizedInstallmentOption, ...]:
        """TASK-089: uma opção malformada nunca derruba a oferta inteira --
        só aquela opção é descartada (logada), as demais e o preço à vista
        seguem normalmente. `@staticmethod` de propósito: `installment_options`
        em `NormalizedCollectedOffer` chama isto sem instanciar
        `PriceNormalizer` (mesma razão de `_amount` já ser estático)."""
        normalized: list[NormalizedInstallmentOption] = []
        for raw_option in raw_options:
            try:
                installment_amount = PriceNormalizer._amount(
                    raw_option.raw_amount, currency, field="installment_amount"
                )
                installment_total_amount = (
                    PriceNormalizer._amount(
                        raw_option.raw_total_amount,
                        currency,
                        field="installment_total_amount",
                    )
                    if raw_option.raw_total_amount is not None
                    else None
                )
            except CollectionNormalizationError:
                logger.warning(
                    "installment_option_normalization_failed",
                    extra={"installment_count": raw_option.installment_count},
                )
                continue
            normalized.append(
                NormalizedInstallmentOption(
                    installment_count=raw_option.installment_count,
                    installment_amount=installment_amount,
                    installment_total_amount=installment_total_amount,
                    discount_percent=raw_option.discount_percent,
                    interest_kind=raw_option.interest_kind,
                    is_highlighted=raw_option.is_highlighted,
                )
            )
        return tuple(normalized)

    def _currency(self, raw_currency: str | None, raw_amount: str | None) -> str:
        declared = raw_currency.strip().upper() if raw_currency else None
        if declared is not None and not re.fullmatch(r"[A-Z]{3}", declared):
            raise CollectionNormalizationError("currency must be an ISO 4217 code")
        detected = self._detected_currency(raw_amount)
        if declared and detected and declared != detected:
            raise CollectionNormalizationError(
                "declared and detected currencies differ"
            )
        currency = declared or detected
        if currency is None:
            raise CollectionNormalizationError("currency is missing")
        return currency

    def _shipping(self, raw_shipping: str | None, currency: str) -> Decimal | None:
        if raw_shipping is None or not raw_shipping.strip():
            return None
        if _FREE_SHIPPING.search(raw_shipping):
            return Decimal(0)
        if not re.search(r"\d", raw_shipping):
            return None
        detected = self._detected_currency(raw_shipping)
        if detected and detected != currency:
            raise CollectionNormalizationError("shipping currency differs from price")
        return self._amount(raw_shipping, currency, field="shipping")

    @staticmethod
    def _availability(raw_availability: str | None) -> Availability:
        if raw_availability:
            if _UNAVAILABLE.search(raw_availability):
                return Availability.UNAVAILABLE
            if _AVAILABLE.search(raw_availability):
                return Availability.AVAILABLE
        return Availability.UNKNOWN

    @staticmethod
    def _condition(raw_condition: str | None) -> OfferCondition:
        if raw_condition is None:
            return OfferCondition.UNKNOWN
        normalized = re.sub(r"\s+", " ", raw_condition.strip().casefold())
        if re.fullmatch(r"(?:recondicionado|refurbished|renewed)", normalized):
            return OfferCondition.REFURBISHED
        if re.fullmatch(
            r"(?:usado|seminovo|used|pre-owned)"
            r"(?:\s*-\s*(?:excelente|bom|aceitável|excellent|good|acceptable))?",
            normalized,
        ):
            return OfferCondition.USED
        if re.fullmatch(r"(?:novo|new)", normalized):
            return OfferCondition.NEW
        return OfferCondition.UNKNOWN

    @staticmethod
    def _detected_currency(raw: str | None) -> str | None:
        if not raw:
            return None
        remaining = raw
        detected = set()
        for symbol in sorted(_CURRENCY_SYMBOLS, key=len, reverse=True):
            if symbol in remaining:
                detected.add(_CURRENCY_SYMBOLS[symbol])
                remaining = remaining.replace(symbol, "")
        if len(detected) > 1:
            raise CollectionNormalizationError("multiple currencies detected")
        return next(iter(detected), None)

    @staticmethod
    def _amount(raw: str | None, currency: str, *, field: str) -> Decimal:
        if raw is None or not raw.strip():
            raise CollectionNormalizationError(f"{field} is missing")
        cleaned = raw.replace("\xa0", " ")
        for symbol, code in _CURRENCY_SYMBOLS.items():
            if code == currency:
                cleaned = cleaned.replace(symbol, "")
        cleaned = re.sub(r"\s+", "", cleaned)
        if not re.fullmatch(r"\d[\d.,]*", cleaned):
            raise CollectionNormalizationError(
                f"{field} has an invalid monetary format"
            )
        canonical = _canonical_decimal(cleaned)
        try:
            amount = Decimal(canonical)
        except InvalidOperation as error:
            raise CollectionNormalizationError(f"{field} is not decimal") from error
        _require_numeric_19_4(amount, field)
        return amount


def _require_numeric_19_4(amount: Decimal, field: str) -> None:
    if amount < 0 or amount.as_tuple().exponent < -4:
        raise CollectionNormalizationError(f"{field} is outside numeric(19,4)")
    if len(amount.as_tuple().digits) + max(amount.as_tuple().exponent, 0) > 19:
        raise CollectionNormalizationError(f"{field} is outside numeric(19,4)")


def _canonical_decimal(value: str) -> str:
    separators = [(index, char) for index, char in enumerate(value) if char in ".,"]
    if not separators:
        return value
    last_index, decimal_mark = separators[-1]
    fraction_size = len(value) - last_index - 1
    marks = {char for _, char in separators}
    if len(marks) == 1:
        groups = value.split(decimal_mark)
        if len(groups) == 2 and fraction_size in {1, 2, 4}:
            return f"{groups[0]}.{groups[1]}"
        if 1 <= len(groups[0]) <= 3 and all(len(group) == 3 for group in groups[1:]):
            return "".join(groups)
        raise CollectionNormalizationError("ambiguous monetary separators")
    thousands_mark = next(mark for mark in marks if mark != decimal_mark)
    integer_groups = value[:last_index].split(thousands_mark)
    if (
        value.count(decimal_mark) == 1
        and 1 <= len(integer_groups[0]) <= 3
        and all(len(group) == 3 for group in integer_groups[1:])
        and fraction_size in {1, 2, 3, 4}
    ):
        return f"{''.join(integer_groups)}.{value[last_index + 1 :]}"
    raise CollectionNormalizationError("ambiguous monetary separators")
