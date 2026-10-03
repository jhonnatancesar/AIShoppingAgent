"""TASK-137 -- fontes abertas copiadas para o catálogo de identidade.

Hoje: **BuildCores OpenDB** (licença ODC-By 1.0, uso inclusive comercial com
atribuição; https://github.com/buildcores/buildcores-open-db), categorias
placa-mãe e memória RAM. Cópia da base (arquivos JSON baixados), nunca chamada
de API da fonte em produção.

Qualidade antes de quantidade: um registro só vira entrada quando tem fabricante,
nome limpo e código que o identifique de verdade; códigos ambíguos (o mesmo
código ou nome em registros diferentes) são descartados dos DOIS lados, e no
banco um código já existente em outra entrada nunca é sobrescrito. Não há IA
aqui.

Mapeamento da placa-mãe (fiel ao que a fonte chama de série e variante, mas com
nome canônico próprio):

- `brand`: fabricante; `family`: série da fonte (ou o chipset, quando a fonte não
  informa); `model`: nome da placa sem fabricante e sem os sufixos de soquete,
  memória e formato ("MSI B450M PRO-M2 MAX AM4 DDR4 Micro ATX" -> `b450m-pro-m2-max`);
- atributos: `socket`, `chipset`, `form_factor`;
- códigos: part number de verdade (sem espaço, com letra e dígito, 6+ caracteres)
  vira `part_number`; nome por extenso vira `name` (casamento estrito, ver
  `identity_catalog.match_catalog`).

Mapeamento da RAM: o kit é identificado pelo part number do fabricante
(`model`); atributos `type`, `capacity_gb`, `speed_mhz`, `kit`, `cas`. RAM sem part
number utilizável é descartada (nome de memória é ambíguo demais).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.products.identity import (
    build_resolved_variant_from_fields,
    catalog_family_key,
    normalize_for_grounding,
)
from app.products.identity_catalog import compact_code, is_usable_part_number
from app.products.identity_catalog_models import (
    ProductIdentityCatalogCode,
    ProductIdentityCatalogEntry,
)

SOURCE_BUILDCORES = "buildcores"
OPEN_SOURCES = frozenset({SOURCE_BUILDCORES, "wikidata"})

_NON_SLUG = re.compile(r"[^a-z0-9]+")
_MAX_NAME_TOKENS = 9
"""Nome de placa com mais palavras que isso é título de anúncio (marketing), não
nome de produto."""
_PRODUCT_NAME_WORDS = re.compile(
    r"GAMING|AORUS|ELITE|TOMAHAWK|WIFI|PLUS|PRIME|STRIX|TUF|MAG|MPG|MEG|PRO|EAGLE|"
    r"MORTAR|BAZOOKA|TAICHI|STEEL|LEGEND|PHANTOM|LIVEMIXER",
)
"""Código de placa que contém palavra de nome de produto é o nome colado
("TUFGAMINGB650PLUS"), não um part number: casaria por corrida compacta e furaria a regra
estrita de nome."""
_SOCKET_NOISE = re.compile(
    r"^(?:AM[1-5]\+?|FM[12]\+?|LGA-?\d{3,4}(?:-\d)?|FCLGA\d+|STRX4|STR5|SP[35]|TR4|"
    r"SOCKET)$",
    re.IGNORECASE,
)
_MEMORY_NOISE = re.compile(r"^(?:LP)?DDR[1-5]?$", re.IGNORECASE)
_FORM_NOISE = re.compile(
    r"^(?:(?:E|M|MICRO|MINI|THIN|XL|SSI)-?)?(?:ATX|ITX)$|^(?:EEB|CEB|MICRO|MINI|THIN|XL|SSI)$",
    re.IGNORECASE,
)


def _slug(value: str) -> str:
    return _NON_SLUG.sub("-", normalize_for_grounding(value).lower()).strip("-")


@dataclass(frozen=True, slots=True)
class OpenCatalogEntry:
    category: str
    brand: str
    family: str
    model: str
    attributes: Mapping[str, str]
    part_numbers: tuple[str, ...]
    names: tuple[str, ...]
    source: str
    source_ref: str
    variant: str = "base"
    required_attributes: tuple[str, ...] = ()

    @property
    def identity(self) -> tuple[str, str, str, str, str]:
        return (self.category, self.brand, self.family, self.model, self.variant)


@dataclass(slots=True)
class MappingReport:
    """Por que cada registro foi (ou não) aproveitado -- a conta fecha."""

    read: int = 0
    mapped: int = 0
    skipped: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    dropped_ambiguous_codes: int = 0

    def skip(self, reason: str) -> None:
        self.skipped[reason] += 1


def _is_noise(token: str) -> bool:
    return bool(
        _SOCKET_NOISE.match(token)
        or _MEMORY_NOISE.match(token)
        or _FORM_NOISE.match(token)
    )


def _clean_board_name(name: str, manufacturer: str) -> str:
    """Nome da placa sem fabricante e sem sufixos de soquete, memória e formato."""
    tokens = name.replace(",", " ").split()
    manufacturer_first = normalize_for_grounding(manufacturer).split()[:1]
    if (
        tokens
        and manufacturer_first
        and normalize_for_grounding(tokens[0]).split()[:1] == manufacturer_first
    ):
        tokens = tokens[1:]
    # A fonte repete o chipset no começo ("A520 A520M PRO-VDH"); as lojas não.
    if (
        len(tokens) >= 2
        and re.fullmatch(r"[A-Za-z]\d{2,3}", tokens[0])
        and tokens[1].upper().startswith(tokens[0].upper())
    ):
        tokens = tokens[1:]
    # Tira soquete e formato do final, mas MANTÉM o tipo de memória: a mesma placa
    # em DDR4 e DDR5 são produtos diferentes.
    kept_memory: list[str] = []
    while tokens and (_is_noise(tokens[-1]) or _MEMORY_NOISE.match(tokens[-1])):
        token = tokens.pop()
        if _MEMORY_NOISE.match(token):
            kept_memory.insert(0, token)
    return " ".join([*tokens, *kept_memory])


_HYPHENATED_NUMERIC = re.compile(r"^\d{2,4}-\d{4,}$")


def _is_hyphenated_numeric_code(value: str) -> bool:
    """Part number só de números com hífen (Logitech `910-005281`): específico o bastante
    (8+ dígitos em dois grupos), ao contrário de "6000" ou "8GB"."""
    return bool(_HYPHENATED_NUMERIC.match(value)) and len(compact_code(value)) >= 8


def _split_codes(
    part_numbers: Iterable[str], *, reject_name_words: bool = False
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(part numbers, nomes por extenso) -- sem espaço e específico é part number."""
    codes: list[str] = []
    names: list[str] = []
    for raw in part_numbers:
        value = (raw or "").strip()
        if not value:
            continue
        if re.search(r"\s", value):
            normalized = normalize_for_grounding(value)
            tokens = normalized.split()
            if len(tokens) >= 2 and any(
                any(char.isdigit() for char in token) for token in tokens
            ):
                names.append(normalized)
            continue
        compact = compact_code(value)
        if reject_name_words and _PRODUCT_NAME_WORDS.search(compact):
            continue
        if is_usable_part_number(compact) or _is_hyphenated_numeric_code(value):
            codes.append(compact)
    return tuple(dict.fromkeys(codes)), tuple(dict.fromkeys(names))


def _chipset(value: Any) -> str:
    text = normalize_for_grounding(str(value or ""))
    tokens = [
        token for token in text.split() if token not in {"AMD", "INTEL", "NVIDIA"}
    ]
    return _slug(" ".join(tokens))


def map_buildcores_motherboard(
    record: Mapping[str, Any], report: MappingReport
) -> OpenCatalogEntry | None:
    report.read += 1
    meta = record.get("metadata") or {}
    manufacturer = (meta.get("manufacturer") or "").strip()
    name = (meta.get("name") or "").strip()
    opendb_id = str(record.get("opendb_id") or "").strip()
    if not manufacturer or not name or not opendb_id:
        report.skip("sem_fabricante_nome_ou_id")
        return None
    cleaned = _clean_board_name(name, manufacturer)
    part_numbers, extra_names = _split_codes(
        meta.get("part_numbers") or [], reject_name_words=True
    )
    if len(cleaned.split()) > _MAX_NAME_TOKENS:
        short = sorted(
            (
                candidate
                for candidate in extra_names
                if len(candidate.split()) <= _MAX_NAME_TOKENS
            ),
            key=lambda candidate: (len(candidate.split()), candidate),
        )
        if not short:
            report.skip("nome_longo_demais")
            return None
        cleaned = short[0]
    model = _slug(cleaned)
    chipset = _chipset(record.get("chipset"))
    if not model or not any(char.isdigit() for char in model):
        report.skip("nome_sem_modelo_identificavel")
        return None
    brand = _slug(manufacturer)
    family = _slug(meta.get("series") or "") or chipset
    if not family:
        report.skip("sem_serie_nem_chipset")
        return None
    own_name = normalize_for_grounding(cleaned)
    without_memory = " ".join(
        token for token in own_name.split() if not _MEMORY_NOISE.match(token)
    )
    names = tuple(
        dict.fromkeys(
            candidate
            for candidate in (own_name, without_memory)
            if 2 <= len(candidate.split()) <= _MAX_NAME_TOKENS
        )
    )
    if not part_numbers and not names:
        report.skip("sem_codigo_utilizavel")
        return None
    attributes = {
        key: value
        for key, value in {
            "socket": _slug(str(record.get("socket") or "")),
            "chipset": chipset,
            "form_factor": _slug(str(record.get("form_factor") or "")),
            "memory_type": _slug(
                str((record.get("memory") or {}).get("ram_type") or "")
            ),
        }.items()
        if value
    }
    report.mapped += 1
    return OpenCatalogEntry(
        category="motherboard",
        brand=brand,
        family=family,
        model=model,
        attributes=attributes,
        part_numbers=part_numbers,
        names=names,
        source=SOURCE_BUILDCORES,
        source_ref=opendb_id,
    )


def map_buildcores_ram(
    record: Mapping[str, Any], report: MappingReport
) -> OpenCatalogEntry | None:
    report.read += 1
    meta = record.get("metadata") or {}
    manufacturer = (meta.get("manufacturer") or "").strip()
    opendb_id = str(record.get("opendb_id") or "").strip()
    if not manufacturer or not opendb_id:
        report.skip("sem_fabricante_ou_id")
        return None
    part_numbers, _names = _split_codes(meta.get("part_numbers") or [])
    if not part_numbers:
        report.skip("sem_part_number_utilizavel")
        return None
    ram_type = str(record.get("ram_type") or "").strip().lower()
    capacity = record.get("capacity")
    speed = record.get("speed")
    if not ram_type or not isinstance(capacity, int) or not isinstance(speed, int):
        report.skip("sem_tipo_capacidade_ou_velocidade")
        return None
    modules = record.get("modules") or {}
    quantity = modules.get("quantity")
    module_capacity = modules.get("capacity_gb")
    attributes = {
        "type": ram_type,
        "capacity_gb": str(capacity),
        "speed_mhz": str(speed),
    }
    if isinstance(quantity, int) and isinstance(module_capacity, int):
        attributes["kit"] = f"{quantity}x{module_capacity}"
    if isinstance(record.get("cas_latency"), int):
        attributes["cas"] = str(record["cas_latency"])
    family = _slug(meta.get("series") or "") or _slug(manufacturer)
    report.mapped += 1
    return OpenCatalogEntry(
        category="ram",
        brand=_slug(manufacturer),
        family=family,
        model=_slug(part_numbers[0]),
        attributes=attributes,
        part_numbers=part_numbers,
        names=(),
        source=SOURCE_BUILDCORES,
        source_ref=opendb_id,
    )


def drop_ambiguous_codes(
    entries: list[OpenCatalogEntry], report: MappingReport
) -> list[OpenCatalogEntry]:
    """Código (part number ou nome) usado por mais de uma entrada é ambíguo:
    sai dos DOIS lados. Entrada que fica sem nenhum código é descartada."""
    owners: dict[tuple[str, str], set[tuple[str, ...]]] = defaultdict(set)
    for entry in entries:
        for code in entry.part_numbers:
            owners[("part_number", code)].add(entry.identity)
        for name in entry.names:
            owners[("name", name)].add(entry.identity)
    ambiguous = {key for key, identities in owners.items() if len(identities) > 1}
    report.dropped_ambiguous_codes = len(ambiguous)
    kept: list[OpenCatalogEntry] = []
    for entry in entries:
        part_numbers = tuple(
            code
            for code in entry.part_numbers
            if ("part_number", code) not in ambiguous
        )
        names = tuple(name for name in entry.names if ("name", name) not in ambiguous)
        if not part_numbers and not names:
            report.mapped -= 1
            report.skip("so_tinha_codigo_ambiguo")
            continue
        kept.append(
            OpenCatalogEntry(
                category=entry.category,
                brand=entry.brand,
                family=entry.family,
                model=entry.model,
                attributes=entry.attributes,
                part_numbers=part_numbers,
                names=names,
                source=entry.source,
                source_ref=entry.source_ref,
                variant=entry.variant,
                required_attributes=entry.required_attributes,
            )
        )
    return kept


def merge_same_identity(entries: list[OpenCatalogEntry]) -> list[OpenCatalogEntry]:
    """Dois registros da fonte com a mesma identidade (marca/família/modelo)
    viram UMA entrada com os códigos juntos."""
    merged: dict[tuple[str, ...], OpenCatalogEntry] = {}
    for entry in entries:
        current = merged.get(entry.identity)
        if current is None:
            merged[entry.identity] = entry
            continue
        merged[entry.identity] = OpenCatalogEntry(
            category=current.category,
            brand=current.brand,
            family=current.family,
            model=current.model,
            attributes=current.attributes,
            part_numbers=tuple(
                dict.fromkeys((*current.part_numbers, *entry.part_numbers))
            ),
            names=tuple(dict.fromkeys((*current.names, *entry.names))),
            source=current.source,
            source_ref=current.source_ref,
            variant=current.variant,
            required_attributes=current.required_attributes,
        )
    return list(merged.values())


def merge_shared_part_numbers(
    entries: list[OpenCatalogEntry],
) -> list[OpenCatalogEntry]:
    """Registros da MESMA marca que dividem um part number são o mesmo produto escrito
    de jeitos diferentes: viram uma entrada (a de nome mais curto), com os códigos
    juntos."""
    parent: dict[tuple[str, ...], tuple[str, ...]] = {
        e.identity: e.identity for e in entries
    }

    def find(node: tuple[str, ...]) -> tuple[str, ...]:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    first_by_code: dict[tuple[str, str, str], tuple[str, ...]] = {}
    for entry in entries:
        for code in entry.part_numbers:
            key = (entry.category, entry.brand, code)
            if key in first_by_code:
                parent[find(entry.identity)] = find(first_by_code[key])
            else:
                first_by_code[key] = entry.identity
    groups: dict[tuple[str, ...], list[OpenCatalogEntry]] = defaultdict(list)
    for entry in entries:
        groups[find(entry.identity)].append(entry)
    merged: list[OpenCatalogEntry] = []
    for members in groups.values():
        lead = min(members, key=lambda e: (len(e.model), e.model, e.source_ref))
        merged.append(
            OpenCatalogEntry(
                category=lead.category,
                brand=lead.brand,
                family=lead.family,
                model=lead.model,
                attributes=lead.attributes,
                part_numbers=tuple(
                    dict.fromkeys(c for m in members for c in m.part_numbers)
                ),
                # Só os nomes do registro principal: o nome dos outros pode ser de
                # outra placa ("TOMAHAWK" e "TOMAHAWK MAX" dividindo um código).
                names=lead.names,
                source=lead.source,
                source_ref=lead.source_ref,
                variant=lead.variant,
                required_attributes=lead.required_attributes,
            )
        )
    return merged


@dataclass(slots=True)
class ImportStats:
    entries_new: int = 0
    entries_existing: int = 0
    entries_invalid: int = 0
    codes_new: int = 0
    codes_conflict: int = 0


async def import_open_entries(
    session: AsyncSession, entries: Iterable[OpenCatalogEntry]
) -> ImportStats:
    """Grava (idempotente) as entradas e seus códigos. Nunca sobrescreve nada que
    já exista: entrada com a mesma identidade é reaproveitada (só ganha códigos
    novos) e código já cadastrado em OUTRA entrada fica com a dona original e é
    contado como conflito."""
    stats = ImportStats()
    for item in entries:
        built = build_resolved_variant_from_fields(
            category=item.category,
            brand=item.brand,
            family=item.family,
            model=item.model,
            variant=item.variant,
            attributes=item.attributes,
        )
        if built is None:
            stats.entries_invalid += 1
            continue
        # Celular: a capacidade decide o produto -> sem `identity_key` na entrada;
        # a identidade nasce do título (`resolve_catalog_family`).
        identity_key = None if item.required_attributes else built.identity_key
        inserted = await session.scalar(
            insert(ProductIdentityCatalogEntry)
            .values(
                category=item.category,
                brand=item.brand,
                family=item.family,
                model=item.model,
                variant=item.variant,
                attributes=dict(item.attributes),
                required_attributes=list(item.required_attributes),
                family_key=catalog_family_key(
                    category=item.category,
                    brand=item.brand,
                    family=item.family,
                    model=item.model,
                ),
                identity_key=identity_key,
                status="active",
                source=item.source,
                source_ref=item.source_ref,
            )
            .on_conflict_do_nothing(
                constraint="uq_product_identity_catalog_entries_identity"
            )
            .returning(ProductIdentityCatalogEntry.id)
        )
        entry = await session.scalar(
            select(ProductIdentityCatalogEntry).where(
                ProductIdentityCatalogEntry.category == item.category,
                ProductIdentityCatalogEntry.brand == item.brand,
                ProductIdentityCatalogEntry.family == item.family,
                ProductIdentityCatalogEntry.model == item.model,
                ProductIdentityCatalogEntry.variant == item.variant,
            )
        )
        if inserted is not None:
            stats.entries_new += 1
        else:
            stats.entries_existing += 1
        if entry is None or entry.status != "active":
            continue
        for kind, values in (("part_number", item.part_numbers), ("name", item.names)):
            for value in values:
                code_id = await session.scalar(
                    insert(ProductIdentityCatalogCode)
                    .values(entry_id=entry.id, kind=kind, value_normalized=value)
                    .on_conflict_do_nothing(
                        constraint="uq_product_identity_catalog_codes_value"
                    )
                    .returning(ProductIdentityCatalogCode.id)
                )
                if code_id is not None:
                    stats.codes_new += 1
                    continue
                owner = await session.scalar(
                    select(ProductIdentityCatalogCode.entry_id).where(
                        ProductIdentityCatalogCode.kind == kind,
                        ProductIdentityCatalogCode.value_normalized == value,
                    )
                )
                if owner != entry.id:
                    stats.codes_conflict += 1
    return stats
