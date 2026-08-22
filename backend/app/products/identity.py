"""Identidade determinística e fail-closed de produto/variante (TASK-097).

O módulo é deliberadamente independente de loja, missão e IA. Categorias novas
entram como extratores registrados; consumidores trabalham apenas com o mesmo
contrato e com chaves canônicas versionadas.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType


class ProductRequestKind(StrEnum):
    SPECIFIC_PRODUCT = "specific_product"
    PRODUCT_FAMILY = "product_family"
    GENERIC_CATEGORY = "generic_category"


IDENTITY_VERSION = 1


@dataclass(frozen=True, slots=True)
class ResolvedProductVariant:
    category: str
    brand: str
    family: str
    model: str
    variant: str
    attributes: tuple[tuple[str, str], ...]
    family_key: str
    identity_key: str
    label: str


@dataclass(frozen=True, slots=True)
class ProductRequestIdentity:
    kind: ProductRequestKind
    family_key: str | None = None
    identity_key: str | None = None
    variant: str | None = None


@dataclass(frozen=True, slots=True)
class _ParsedFamily:
    category: str
    brand: str
    family: str
    model: str
    variant: str
    variant_explicit: bool
    attributes: tuple[tuple[str, str], ...]
    required_attributes: frozenset[str]


_Extractor = Callable[[str], _ParsedFamily | None]
_SPACE = re.compile(r"\s+")
_STORAGE = re.compile(r"(?<!\d)(\d{2,4})\s*(GB|TB)(?![A-Z])")


def _normalized(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _SPACE.sub(" ", plain.upper().replace("_", " ").replace("-", " ")).strip()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _normalized(value).lower()).strip("-")


def _storage_attribute(text: str) -> tuple[tuple[str, str], ...]:
    """Aceita uma única capacidade plausível; múltiplas são ambíguas.

    Valores abaixo de 64 GB tendem a ser RAM nos títulos atuais e não podem ser
    promovidos a armazenamento por aproximação. 1/2 TB são convertidos para GB.
    """
    values: set[int] = set()
    for raw_value, unit in _STORAGE.findall(text):
        value = int(raw_value)
        if unit == "TB":
            value *= 1024
        if value >= 64:
            values.add(value)
    if len(values) != 1:
        return ()
    return (("storage_gb", str(values.pop())),)


def _iphone(text: str) -> _ParsedFamily | None:
    match = re.search(
        r"\b(?:APPLE\s+)?IPHONE\s*(\d{1,2})(?:\s+(PRO\s+MAX|PRO|AIR|PLUS|MINI))?\b",
        text,
    )
    if match is None:
        return None
    generation, variant = match.groups()
    return _ParsedFamily(
        category="smartphone",
        brand="apple",
        family="iphone",
        model=generation,
        variant=_slug(variant or "base"),
        variant_explicit=variant is not None,
        attributes=_storage_attribute(text),
        required_attributes=frozenset({"storage_gb"}),
    )


def _galaxy_s(text: str) -> _ParsedFamily | None:
    match = re.search(
        r"\b(?:SAMSUNG\s+)?(?:GALAXY\s+)?S\s*(\d{2})(?:\s+(ULTRA|PLUS|FE))?\b",
        text,
    )
    if match is None:
        return None
    generation, variant = match.groups()
    return _ParsedFamily(
        category="smartphone",
        brand="samsung",
        family="galaxy-s",
        model=f"s{generation}",
        variant=_slug(variant or "base"),
        variant_explicit=variant is not None,
        attributes=_storage_attribute(text),
        required_attributes=frozenset({"storage_gb"}),
    )


_EXTRACTORS: tuple[_Extractor, ...] = (_iphone, _galaxy_s)


def _key(parts: tuple[str, ...]) -> str:
    canonical = "|".join(parts)
    digest = sha256(canonical.encode("utf-8")).hexdigest()
    return f"v{IDENTITY_VERSION}:{digest}"


def _family_key(parsed: _ParsedFamily) -> str:
    return _key((parsed.category, parsed.brand, parsed.family, parsed.model))


def _identity_key(parsed: _ParsedFamily) -> str | None:
    attributes = dict(parsed.attributes)
    if not parsed.required_attributes.issubset(attributes):
        return None
    attribute_parts = tuple(f"{name}={value}" for name, value in parsed.attributes)
    return _key(
        (
            parsed.category,
            parsed.brand,
            parsed.family,
            parsed.model,
            parsed.variant,
            *attribute_parts,
        )
    )


def _label(parsed: _ParsedFamily) -> str:
    brands = {"apple": "Apple", "samsung": "Samsung"}
    families = {"iphone": "iPhone", "galaxy-s": "Galaxy"}
    variants = {
        "base": "",
        "pro": "Pro",
        "pro-max": "Pro Max",
        "air": "Air",
        "plus": "Plus",
        "mini": "Mini",
        "ultra": "Ultra",
        "fe": "FE",
    }
    model = parsed.model.upper() if parsed.family == "galaxy-s" else parsed.model
    pieces = [brands.get(parsed.brand, parsed.brand.title())]
    pieces.append(families.get(parsed.family, parsed.family.title()))
    pieces.append(model)
    variant = variants.get(parsed.variant, parsed.variant.replace("-", " ").title())
    if variant:
        pieces.append(variant)
    attributes = MappingProxyType(dict(parsed.attributes))
    if "storage_gb" in attributes:
        storage = int(attributes["storage_gb"])
        pieces.append(f"{storage // 1024} TB" if storage >= 1024 else f"{storage} GB")
    return " ".join(pieces)


def _parse(text: str) -> _ParsedFamily | None:
    normalized = _normalized(text)
    for extractor in _EXTRACTORS:
        parsed = extractor(normalized)
        if parsed is not None:
            return parsed
    return None


def resolve_product_variant(text: str) -> ResolvedProductVariant | None:
    """Resolve somente uma variante completa; ausência obrigatória falha fechada."""
    parsed = _parse(text)
    if parsed is None:
        return None
    identity_key = _identity_key(parsed)
    if identity_key is None:
        return None
    return ResolvedProductVariant(
        category=parsed.category,
        brand=parsed.brand,
        family=parsed.family,
        model=parsed.model,
        variant=parsed.variant,
        attributes=parsed.attributes,
        family_key=_family_key(parsed),
        identity_key=identity_key,
        label=_label(parsed),
    )


def classify_product_request(text: str) -> ProductRequestIdentity:
    """Distingue produto específico, família e categoria sem usar IA."""
    parsed = _parse(text)
    if parsed is None:
        return ProductRequestIdentity(ProductRequestKind.GENERIC_CATEGORY)
    family_key = _family_key(parsed)
    identity_key = _identity_key(parsed)
    if identity_key is None:
        return ProductRequestIdentity(
            ProductRequestKind.PRODUCT_FAMILY,
            family_key=family_key,
            variant=parsed.variant if parsed.variant_explicit else None,
        )
    return ProductRequestIdentity(
        ProductRequestKind.SPECIFIC_PRODUCT,
        family_key=family_key,
        identity_key=identity_key,
        variant=parsed.variant,
    )
