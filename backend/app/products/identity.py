"""Identidade determinística e fail-closed de produto/variante (TASK-097).

O módulo é deliberadamente independente de loja, missão e IA. Categorias novas
entram como extratores registrados; consumidores trabalham apenas com o mesmo
contrato e com chaves canônicas versionadas.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping
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


# ---------------------------------------------------------------------------
# Product Identity Engine (TASK-112): monitoring_key genérica e versionada,
# construída inteiramente SOBRE o motor acima (_parse) -- nunca reescreve
# identity_key/family_key (intocados, usados em produção por
# resolve_product_variant/classify_product_request; mudar a fórmula deles
# invalidaria Product.identity_key já persistido). A IA nunca decide
# equivalência aqui: ela só interpreta/estrutura o pedido do usuário ANTES
# deste módulo (fora dele); tudo daqui pra baixo é puro, síncrono,
# determinístico e sem sessão de banco -- mesmo espírito do resto do
# arquivo (ver docstring do módulo).
# ---------------------------------------------------------------------------

MONITORING_KEY_VERSION = 1


@dataclass(frozen=True, slots=True)
class AttributeDefinition:
    """Um atributo conhecido de uma categoria, para fins de monitoring_key.

    `blocking=True` tem o mesmo efeito que `_ParsedFamily.required_attributes`
    já tem hoje para `identity_key` (ex.: `storage_gb` do iPhone): ausente,
    o pedido nunca gera `monitoring_key` -- fica em família/genérico, a UX
    de seleção de variante já existente (TASK-097) decide, sem mudança
    nenhuma aqui. `blocking=False` (default) nunca bloqueia: ausente vira o
    literal `"ANY"` na chave, sempre explícito, nunca omitido em silêncio.
    """

    name: str
    blocking: bool = False


@dataclass(frozen=True, slots=True)
class CategoryDefinition:
    """Ontologia de uma categoria -- quais atributos existem e o
    comportamento de cada um quando o usuário não especifica. Não carrega
    extractor: a categoria já é resolvida por `_parse` (mesmo parser da
    TASK-097, registrado em `_EXTRACTORS`); este registry só declara os
    atributos usados pela camada de `monitoring_key`. Uma categoria sem
    extractor correspondente em `_EXTRACTORS` fica inerte (fail-closed --
    `_parse` nunca devolve essa categoria) até um extractor real ser
    adicionado; adicionar um não exige tocar neste registry nem no
    algoritmo de `resolve_monitoring_identity`.
    """

    category: str
    attributes: tuple[AttributeDefinition, ...] = ()

    def blocking_attribute_names(self) -> frozenset[str]:
        return frozenset(attr.name for attr in self.attributes if attr.blocking)


_CATEGORY_REGISTRY: tuple[CategoryDefinition, ...] = (
    # -- Categorias com extractor de texto funcionando hoje (_EXTRACTORS
    # abaixo) -- únicas com monitoring_key testada nesta fase.
    CategoryDefinition("smartphone", (AttributeDefinition("storage_gb", blocking=True),)),
    CategoryDefinition("cpu"),
    CategoryDefinition("gpu", (AttributeDefinition("board_brand"), AttributeDefinition("vram"))),
    # -- Ontologia registrada para o restante das categorias previstas no
    # desenho (docs/tasks/TASK-112.md §1.3), sem extractor de texto ainda.
    # Inertes por enquanto (_parse nunca devolve essas categorias); existem
    # para documentar os atributos esperados e não exigir redesenho do
    # algoritmo quando um extractor real for adicionado.
    CategoryDefinition("tablet", (AttributeDefinition("storage_gb", blocking=True),)),
    CategoryDefinition(
        "notebook",
        (
            AttributeDefinition("cpu_model"),
            AttributeDefinition("gpu_model"),
            AttributeDefinition("ram_gb"),
            AttributeDefinition("storage_gb"),
            AttributeDefinition("screen_size"),
        ),
    ),
    CategoryDefinition(
        "desktop",
        (
            AttributeDefinition("cpu_model"),
            AttributeDefinition("gpu_model"),
            AttributeDefinition("ram_gb"),
            AttributeDefinition("storage_gb"),
        ),
    ),
    CategoryDefinition(
        "monitor",
        (
            AttributeDefinition("size", blocking=True),
            AttributeDefinition("resolution"),
            AttributeDefinition("refresh_rate"),
            AttributeDefinition("panel"),
        ),
    ),
    CategoryDefinition(
        "tv",
        (
            AttributeDefinition("size", blocking=True),
            AttributeDefinition("resolution"),
            AttributeDefinition("panel"),
            AttributeDefinition("smart_platform"),
        ),
    ),
    CategoryDefinition(
        "ram",
        (
            AttributeDefinition("capacity_gb", blocking=True),
            AttributeDefinition("type", blocking=True),
            AttributeDefinition("speed_mhz"),
        ),
    ),
    CategoryDefinition(
        "ssd",
        (
            AttributeDefinition("capacity_gb", blocking=True),
            AttributeDefinition("interface", blocking=True),
            AttributeDefinition("form_factor"),
        ),
    ),
    CategoryDefinition(
        "hdd", (AttributeDefinition("capacity_gb", blocking=True), AttributeDefinition("interface"))
    ),
    CategoryDefinition(
        "motherboard",
        (
            AttributeDefinition("socket", blocking=True),
            AttributeDefinition("chipset"),
            AttributeDefinition("form_factor"),
        ),
    ),
    CategoryDefinition(
        "psu", (AttributeDefinition("wattage", blocking=True), AttributeDefinition("certification"))
    ),
    CategoryDefinition("case", (AttributeDefinition("form_factor"), AttributeDefinition("color"))),
    CategoryDefinition(
        "cooler", (AttributeDefinition("type", blocking=True), AttributeDefinition("size_mm"))
    ),
    CategoryDefinition(
        "keyboard",
        (
            AttributeDefinition("layout"),
            AttributeDefinition("switch_type"),
            AttributeDefinition("connectivity"),
        ),
    ),
    CategoryDefinition("mouse", (AttributeDefinition("connectivity"), AttributeDefinition("dpi"))),
    CategoryDefinition("headset", (AttributeDefinition("connectivity"),)),
    CategoryDefinition("console", (AttributeDefinition("storage_gb"),)),
    CategoryDefinition(
        "controller", (AttributeDefinition("color"), AttributeDefinition("connectivity"))
    ),
    CategoryDefinition(
        "camera", (AttributeDefinition("resolution_mp"), AttributeDefinition("sensor_type"))
    ),
    CategoryDefinition("router", (AttributeDefinition("wifi_standard"), AttributeDefinition("bands"))),
    CategoryDefinition(
        "printer", (AttributeDefinition("type", blocking=True), AttributeDefinition("connectivity"))
    ),
)
_CATEGORY_BY_NAME: dict[str, CategoryDefinition] = {
    definition.category: definition for definition in _CATEGORY_REGISTRY
}


# -- Extractores novos desta fase (CPU/GPU) -- mesmo contrato de _iphone/
# _galaxy_s acima (texto -> _ParsedFamily | None), registrados no MESMO
# _EXTRACTORS ao final do módulo. Isso significa que resolve_product_variant/
# classify_product_request (TASK-097, produção real) passam a reconhecer
# CPU/GPU também, de graça -- mesmo mecanismo, sem duplicar lógica de parsing.

_RYZEN_KEYWORD = re.compile(r"\bAMD\b|\bRYZEN\b")
_RYZEN_CODE = re.compile(r"\b(\d{4,5})([A-Z0-9]{0,3})\b")
# TASK-112 (correção): o tier ("Ryzen 9"/"Ryzen 7"/...) não é uma restrição
# à parte do texto -- é informação IMPLÍCITA no próprio código do modelo,
# conhecimento público do esquema de nomenclatura da AMD, verificável em
# qualquer lista de SKUs: o SEGUNDO dígito do código de 4 dígitos indica o
# tier, estável através de gerações (9950X3D=Ryzen 9, 9800X3D=Ryzen 7 --
# mesmo começando com "9" -- 7950X3D=Ryzen 9, 7800X3D=Ryzen 7, 7600X=Ryzen
# 5, 5950X=Ryzen 9, 5800X=Ryzen 7, 5600X=Ryzen 5, 3950X=Ryzen 9, 3700X=
# Ryzen 7, 3600X=Ryzen 5 -- confirmado através de Zen2/3/4/5 desktop). Por
# isso o `family` é SEMPRE derivado do código, nunca do texto ao redor --
# "9950x3d", "ryzen 9950x3d" e "amd ryzen 9 9950x3d" têm que convergir para
# a mesma identidade, já que os três descrevem o mesmo produto; a menção
# explícita do tier no texto é reconfirmação redundante, não uma segunda
# restrição. APU/mobile/Threadripper (esquema de numeração diferente)
# ficam fora desta fase -- caem em GENERIC_CATEGORY, fail-closed, nunca um
# family errado por extrapolação.
_RYZEN_TIER_BY_SECOND_DIGIT = {
    "9": "9",
    "8": "7",
    "7": "7",
    "6": "5",
    "5": "5",
    "4": "3",
    "3": "3",
}


def _ryzen_family(model_digits: str) -> str:
    tier = _RYZEN_TIER_BY_SECOND_DIGIT.get(model_digits[1])
    return f"ryzen-{tier}" if tier else "ryzen"


def _cpu(text: str) -> _ParsedFamily | None:
    """AMD Ryzen desktop -- único fabricante coberto nesta fase; Intel (ou
    qualquer outro) entra pelo mesmo mecanismo, como outro extractor
    registrado em `_EXTRACTORS`, sem tocar neste. `family` nunca vem do
    texto (ver `_RYZEN_TIER_BY_SECOND_DIGIT` acima) -- só do código."""
    has_keyword = _RYZEN_KEYWORD.search(text) is not None
    for match in _RYZEN_CODE.finditer(text):
        digits, suffix = match.groups()
        if not has_keyword and not suffix.endswith("X3D"):
            # TASK-093/TASK-075: X3D (3D V-Cache) é branding exclusivo da
            # AMD, nunca usado pela Intel -- só por isso um código bare
            # pode ser aceito sem a palavra "AMD"/"RYZEN" no texto, sem
            # risco de inferir fabricante errado.
            continue
        code = digits + suffix
        return _ParsedFamily(
            category="cpu",
            brand="amd",
            family=_ryzen_family(digits),
            model=_slug(code),
            variant="",
            variant_explicit=False,
            attributes=(),
            required_attributes=frozenset(),
        )
    return None


_GPU_KEYWORD = re.compile(r"\bRTX\b|\bGEFORCE\b|\bNVIDIA\b")
_GPU_MODEL = re.compile(r"\b(\d{4})\s*(TI|SUPER)?\b")
# TASK-112: parceiros de placa (AIB) mais comuns no varejo BR -- lista
# fechada de propósito (fail-closed: parceiro fora da lista vira ausente/
# ANY, nunca um valor incorreto). Crescer esta lista não exige mudar o
# algoritmo, só adicionar o nome aqui.
_GPU_BOARD_BRANDS = (
    "ASUS",
    "MSI",
    "GIGABYTE",
    "ZOTAC",
    "PNY",
    "GALAX",
    "PALIT",
    "COLORFUL",
    "INNO3D",
    "EVGA",
)


def _gpu_board_brand(text: str) -> str | None:
    for brand in _GPU_BOARD_BRANDS:
        if re.search(rf"\b{brand}\b", text):
            return brand
    return None


def _gpu(text: str) -> _ParsedFamily | None:
    """NVIDIA GeForce RTX -- único fabricante coberto nesta fase; AMD
    Radeon/Intel Arc entram pelo mesmo mecanismo depois. Aceita o número
    bare (sem "RTX"/"GEFORCE"/"NVIDIA") só quando acompanhado de sufixo
    Ti/Super -- um número de 4 dígitos sozinho é ambíguo demais para
    inferir fabricante sem nenhum outro sinal no texto."""
    has_keyword = _GPU_KEYWORD.search(text) is not None
    match = _GPU_MODEL.search(text)
    if match is None:
        return None
    number, suffix = match.groups()
    if not has_keyword and suffix is None:
        return None
    model = f"{number}-{suffix}" if suffix else number
    board_brand = _gpu_board_brand(text)
    attributes = (("board_brand", board_brand.lower()),) if board_brand else ()
    return _ParsedFamily(
        category="gpu",
        brand="nvidia",
        family="geforce-rtx",
        model=_slug(model),
        variant="",
        variant_explicit=False,
        attributes=attributes,
        required_attributes=frozenset(),
    )


# Registro aditivo -- não toca a tupla original definida acima (_iphone,
# _galaxy_s intocados); só estende. `_parse`/resolve_product_variant/
# classify_product_request passam a enxergar CPU/GPU a partir daqui.
_EXTRACTORS = (*_EXTRACTORS, _cpu, _gpu)


@dataclass(frozen=True, slots=True)
class MonitoringIdentity:
    """Estrutura canônica + `monitoring_key` (TASK-112) de um pedido de
    monitoramento. Construída inteiramente sobre `_parse` (mesmo motor da
    TASK-097): nunca chama IA, nunca decide "parecido o suficiente" --
    duas chamadas com textos que normalizam para a mesma estrutura sempre
    devolvem a mesma `monitoring_key`; qualquer diferença estrutural real
    (inclusive `ANY` vs. valor restrito) sempre devolve chaves diferentes.
    """

    category: str
    brand: str
    family: str
    model: str
    variant: str | None
    attributes: tuple[tuple[str, str], ...]
    monitoring_key: str


def _canonical_attribute(
    category: str,
    attribute: str,
    raw_value: str,
    aliases: Mapping[tuple[str, str, str], str],
) -> str:
    """Aliases (TASK-112, `ProductIdentityAlias`) são sempre `dado`, nunca
    decisão da IA em tempo de comparação -- só o chamador (fora deste
    módulo, sem sessão de banco aqui) carrega o mapa persistido e passa
    pronto, mesmo padrão de `app.collection.queue_config.resolve_queue_
    config` (função pura recebendo o dado já resolvido, não uma sessão)."""
    normalized = _normalized(raw_value)
    canonical = aliases.get((category, attribute, normalized), normalized)
    return _slug(canonical)


def resolve_monitoring_identity(
    text: str,
    *,
    aliases: Mapping[tuple[str, str, str], str] = MappingProxyType({}),
) -> MonitoringIdentity | None:
    """Resolve a identidade canônica + `monitoring_key` de um texto já
    interpretado/estruturado pela IA (a IA nunca é chamada aqui, e nunca
    decide equivalência -- só este motor determinístico decide).

    Fail-closed: devolve `None` quando a categoria não é reconhecida por
    nenhum extractor (`_parse`), quando a categoria reconhecida não está
    registrada em `_CATEGORY_REGISTRY`, ou quando falta um atributo
    bloqueante -- seja o `_ParsedFamily.required_attributes` já existente
    da TASK-097 (ex.: `storage_gb` do iPhone), seja um
    `AttributeDefinition.blocking` desta camada. Nesses casos a missão
    correspondente nunca compartilha coleta com nenhuma outra (TASK-112)
    -- continua com a sua própria, exatamente como hoje.
    """
    parsed = _parse(text)
    if parsed is None:
        return None
    category = _CATEGORY_BY_NAME.get(parsed.category)
    if category is None:
        return None
    found = dict(parsed.attributes)
    if not parsed.required_attributes.issubset(found):
        return None
    if not category.blocking_attribute_names().issubset(found):
        return None

    brand = _canonical_attribute(parsed.category, "brand", parsed.brand, aliases)
    family = _slug(parsed.family)
    model = _canonical_attribute(parsed.category, "model", parsed.model, aliases)
    variant = _slug(parsed.variant) if parsed.variant else None

    attributes = tuple(
        (
            attr.name,
            _canonical_attribute(parsed.category, attr.name, found[attr.name], aliases)
            if attr.name in found
            else "ANY",
        )
        for attr in category.attributes
    )

    parts = [f"v{MONITORING_KEY_VERSION}", parsed.category, brand, family, model]
    if variant:
        parts.append(variant)
    parts.extend(f"{name}={value}" for name, value in attributes)
    digest = sha256("|".join(parts).encode("utf-8")).hexdigest()
    return MonitoringIdentity(
        category=parsed.category,
        brand=brand,
        family=family,
        model=model,
        variant=variant,
        attributes=attributes,
        monitoring_key=f"v{MONITORING_KEY_VERSION}:{digest}",
    )
