"""TASK-132 (Parte B) -- catálogo de nomenclaturas: consulta e aprendizado.

Consulta (`lookup_catalog_identity`): roda ANTES do cache por título, do reuso
por palavras e da IA em `identity_learning._prepare_resolution`. Ordem:

1. **part number** do fabricante no título (compactado, só A-Z/0-9) -- a
   evidência mais forte; mais de uma entrada casando = ambíguo, não resolve;
2. **nome** (tokens do nome presentes no título), recusando entrada cuja
   identidade não conhece uma palavra de edição do título (`identity_edition`:
   "Pro Max" nunca resolve para "Pro") e ficando com o nome mais específico.

Entrada com `required_attributes` (celular: a capacidade decide o produto) monta
a identidade pelo mesmo caminho dos extratores determinísticos
(`identity.resolve_catalog_family`, atributo `storage_gb`); sem a capacidade no
título não resolve (cai na IA/vínculo parcial, como o extrator).

Aprendizado (`learn_catalog_entry`): toda identidade aprovada pela resolução
que traz part number vira (ou reforça) uma entrada `learned`. Nunca sobrescreve
o que já existe: código já cadastrado, inclusive de entrada `rejected`, é
ignorado -- recusa fica guardada e o mesmo código não é reaprendido.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.products.identity import (
    ResolvedProductVariant,
    build_resolved_variant_from_fields,
    catalog_family_key,
    normalize_for_grounding,
    resolve_catalog_family,
)
from app.products.identity_ai import _strip_token_edges, tokens_present
from app.products.identity_catalog_models import (
    ProductIdentityCatalogCode,
    ProductIdentityCatalogEntry,
)
from app.products.identity_catalog_seed import SeedEntry
from app.products.identity_edition import has_unknown_edition_word

logger = logging.getLogger(__name__)

_NON_ALNUM = re.compile(r"[^A-Z0-9]")
_MIN_PART_NUMBER_LENGTH = 6


def compact_code(value: str) -> str:
    """Forma compacta (A-Z/0-9) de um código -- a chave do part number no
    catálogo, igual para "KF560C36BBE-8" e "KF560C36BBE8"."""
    return _NON_ALNUM.sub("", normalize_for_grounding(value))


def is_usable_part_number(compact: str) -> bool:
    """Part number tem de ser específico: pelo menos 6 caracteres, com letra E
    dígito. Código curto ou só numérico ("8GB", "6000") casaria com título
    qualquer."""
    return (
        len(compact) >= _MIN_PART_NUMBER_LENGTH
        and any(char.isdigit() for char in compact)
        and any(char.isalpha() for char in compact)
    )


@dataclass(frozen=True, slots=True)
class CatalogEntrySnapshot:
    category: str
    brand: str
    family: str
    model: str
    variant: str
    attributes: dict[str, str]
    required_attributes: tuple[str, ...]
    family_key: str
    identity_key: str | None
    part_numbers: tuple[str, ...]
    names: tuple[str, ...]
    source: str = "learned"
    entry_id: UUID | None = None


async def load_catalog(session: AsyncSession) -> tuple[CatalogEntrySnapshot, ...]:
    """Entradas ATIVAS com seus códigos (duas consultas curtas, só leitura)."""
    entries = (
        await session.scalars(
            select(ProductIdentityCatalogEntry).where(
                ProductIdentityCatalogEntry.status == "active"
            )
        )
    ).all()
    if not entries:
        return ()
    codes = (
        await session.execute(
            select(
                ProductIdentityCatalogCode.entry_id,
                ProductIdentityCatalogCode.kind,
                ProductIdentityCatalogCode.value_normalized,
            ).where(
                ProductIdentityCatalogCode.entry_id.in_([entry.id for entry in entries])
            )
        )
    ).all()
    return _snapshots(entries, codes)


def load_catalog_sync(session: Session) -> tuple[CatalogEntrySnapshot, ...]:
    """Mesma leitura de `load_catalog`, para o caminho síncrono da criação de missão."""
    entries = session.scalars(
        select(ProductIdentityCatalogEntry).where(
            ProductIdentityCatalogEntry.status == "active"
        )
    ).all()
    if not entries:
        return ()
    codes = session.execute(
        select(
            ProductIdentityCatalogCode.entry_id,
            ProductIdentityCatalogCode.kind,
            ProductIdentityCatalogCode.value_normalized,
        ).where(
            ProductIdentityCatalogCode.entry_id.in_([entry.id for entry in entries])
        )
    ).all()
    return _snapshots(entries, codes)


def _snapshots(entries, codes) -> tuple[CatalogEntrySnapshot, ...]:
    part_numbers: dict[object, list[str]] = {}
    names: dict[object, list[str]] = {}
    for entry_id, kind, value in codes:
        (part_numbers if kind == "part_number" else names).setdefault(
            entry_id, []
        ).append(value)
    return tuple(
        CatalogEntrySnapshot(
            category=entry.category,
            brand=entry.brand,
            family=entry.family,
            model=entry.model,
            variant=entry.variant,
            attributes=dict(entry.attributes),
            required_attributes=tuple(entry.required_attributes),
            family_key=entry.family_key,
            identity_key=entry.identity_key,
            part_numbers=tuple(part_numbers.get(entry.id, ())),
            names=tuple(names.get(entry.id, ())),
            source=entry.source,
            entry_id=entry.id,
        )
        for entry in entries
    )


def _resolve_entry(
    entry: CatalogEntrySnapshot, raw_title: str
) -> ResolvedProductVariant | None:
    if entry.required_attributes:
        return resolve_catalog_family(
            category=entry.category,
            brand=entry.brand,
            family=entry.family,
            model=entry.model,
            variant=entry.variant,
            attributes=entry.attributes,
            required_attributes=entry.required_attributes,
            title=raw_title,
        )
    built = build_resolved_variant_from_fields(
        category=entry.category,
        brand=entry.brand,
        family=entry.family,
        model=entry.model,
        variant=entry.variant,
        attributes=entry.attributes,
    )
    if built is None or (
        entry.identity_key is not None and built.identity_key != entry.identity_key
    ):
        # Entrada cuja chave gravada não bate com a fórmula atual: nunca usa
        # (fail-closed) -- só a revisão conserta.
        logger.warning(
            "product_identity_catalog_entry_key_mismatch",
            extra={"family": entry.family, "model": entry.model},
        )
        return None
    return built


_OPEN_SOURCES = frozenset({"buildcores", "wikidata"})
_NAME_TRAILING_OK = re.compile(
    r"^(?:AM[1-5]\+?|LGA\d{3,4}|DDR[1-5]|SOCKET|CHIPSET|ATX|MATX|MICRO|MINI|ITX|EATX|"
    r"AMD|INTEL|PLACA|MAE|MOTHERBOARD|MAINBOARD|SUPORTA|SUPORTE|SUPPORTS|"
    r"COMPATIVEL|BOX|OEM|"
    r"\d{1,4}(?:GB|TB)|5G|4G|LTE|NFC|DUAL|SIM|ESIM|CHIP|SMARTPHONE|CELULAR|TELEFONE|GLOBAL|"
    r"DESBLOQUEADO|LACRADO|NOVO|PRETO|BRANCO|AZUL|VERDE|VERMELHO|ROSA|ROXO|CINZA|PRATA|"
    r"DOURADO|AMARELO|LARANJA|BLACK|WHITE|BLUE|GREEN|RED|PINK|GRAY|GREY|SILVER|GOLD|VIOLET)$"
)


def _name_run_is_complete(raw_tokens: list[str], name_tokens: list[str]) -> bool:
    """TASK-137: nome de fonte aberta só casa como corrida CONTÍGUA e COMPLETA no
    título: logo depois dela só pode vir o fim do título, uma pontuação de lista ou
    um termo neutro (soquete, DDR, formato, marca do chip). Qualquer outra palavra
    ("A", "WIFI", "PLUS") pode ser outra placa: "B650M PRO" nunca casa com
    "B650M PRO-A". Sem certeza, não casa (cai na IA, como antes)."""
    stripped = [_strip_token_edges(token) for token in raw_tokens]
    size = len(name_tokens)
    for start in range(len(stripped) - size + 1):
        if stripped[start : start + size] != name_tokens:
            continue
        last_raw = raw_tokens[start + size - 1]
        if last_raw != stripped[start + size - 1] and last_raw[-1:] in ",;)":
            return True
        following = stripped[start + size] if start + size < len(stripped) else None
        if following is None or _NAME_TRAILING_OK.match(following):
            return True
    return False


def match_catalog(
    catalog: tuple[CatalogEntrySnapshot, ...], raw_title: str
) -> ResolvedProductVariant | None:
    found = match_catalog_entry(catalog, raw_title)
    return found[1] if found is not None else None


def match_catalog_entry(
    catalog: tuple[CatalogEntrySnapshot, ...], raw_title: str
) -> tuple[CatalogEntrySnapshot, ResolvedProductVariant] | None:
    """Igual a `match_catalog`, devolvendo também a entrada que casou (TASK-137: o
    pedido do usuário usa o nome da entrada para montar a busca nas lojas)."""
    title_normalized = normalize_for_grounding(raw_title)
    by_part_number = [
        entry
        for entry in catalog
        if any(tokens_present(title_normalized, code) for code in entry.part_numbers)
    ]
    if by_part_number:
        if len(by_part_number) != 1:
            return None  # o mesmo código em duas entradas: ambíguo, não decide
        resolved_by_code = _resolve_entry(by_part_number[0], raw_title)
        return (
            (by_part_number[0], resolved_by_code)
            if resolved_by_code is not None
            else None
        )

    raw_title_tokens = title_normalized.split()
    title_tokens = {_strip_token_edges(token) for token in raw_title_tokens}
    best: CatalogEntrySnapshot | None = None
    best_size = (0, 0)
    tied = False
    for entry in catalog:
        for name in entry.names:
            # Nome casa por token EXATO, sem o atalho de "corrida compacta" de
            # `tokens_present`: ele apaga a pontuação e faria "PRO" casar com
            # "PRO+" (Redmi Note 13 Pro x Pro+).
            name_tokens = [_strip_token_edges(token) for token in name.split()]
            if not name_tokens or not all(
                token in title_tokens for token in name_tokens
            ):
                continue
            if entry.source in _OPEN_SOURCES and not _name_run_is_complete(
                raw_title_tokens, name_tokens
            ):
                continue
            view = SimpleNamespace(
                brand=entry.brand,
                family=entry.family,
                model=entry.model,
                variant=entry.variant,
                attributes={**entry.attributes, "_nome": name},
            )
            if has_unknown_edition_word(title_normalized, view):
                continue
            size = (len(name_tokens), len(name))
            if size > best_size:
                best, best_size, tied = entry, size, False
            elif size == best_size and entry is not best:
                tied = True
    if best is None or tied:
        return None
    resolved_by_name = _resolve_entry(best, raw_title)
    return (best, resolved_by_name) if resolved_by_name is not None else None


async def lookup_catalog_identity(
    session: AsyncSession, raw_title: str
) -> ResolvedProductVariant | None:
    return match_catalog(await load_catalog(session), raw_title)


async def upsert_seed_entry(session: AsyncSession, seed: SeedEntry) -> tuple[bool, int]:
    """Grava (idempotente) uma entrada da pré-lista e os nomes dela. Devolve
    `(entrada_nova, codigos_novos)`. Nome já cadastrado em outra entrada, ou
    entrada `rejected`, é respeitado e ignorado."""
    family_key = catalog_family_key(
        category=seed.category, brand=seed.brand, family=seed.family, model=seed.model
    )
    identity_key: str | None = None
    if not seed.required_attributes:
        built = build_resolved_variant_from_fields(
            category=seed.category,
            brand=seed.brand,
            family=seed.family,
            model=seed.model,
            variant=seed.variant,
            attributes={},
        )
        identity_key = built.identity_key if built is not None else None
    values = {
        "category": seed.category,
        "brand": seed.brand,
        "family": seed.family,
        "model": seed.model,
        "variant": seed.variant,
        "attributes": {},
        "required_attributes": list(seed.required_attributes),
        "family_key": family_key,
        "identity_key": identity_key,
        "status": "active",
        "source": "seed",
    }
    inserted = await session.scalar(
        insert(ProductIdentityCatalogEntry)
        .values(**values)
        .on_conflict_do_nothing(
            constraint="uq_product_identity_catalog_entries_identity"
        )
        .returning(ProductIdentityCatalogEntry.id)
    )
    entry = await session.scalar(
        select(ProductIdentityCatalogEntry).where(
            ProductIdentityCatalogEntry.category == seed.category,
            ProductIdentityCatalogEntry.brand == seed.brand,
            ProductIdentityCatalogEntry.family == seed.family,
            ProductIdentityCatalogEntry.model == seed.model,
            ProductIdentityCatalogEntry.variant == seed.variant,
        )
    )
    if entry is None or entry.status != "active":
        return inserted is not None, 0
    added = 0
    for name in seed.names:
        code = await session.scalar(
            insert(ProductIdentityCatalogCode)
            .values(
                entry_id=entry.id,
                kind="name",
                value_normalized=normalize_for_grounding(name),
            )
            .on_conflict_do_nothing(
                constraint="uq_product_identity_catalog_codes_value"
            )
            .returning(ProductIdentityCatalogCode.id)
        )
        added += 1 if code is not None else 0
    return inserted is not None, added


async def learn_catalog_entry(
    session: AsyncSession,
    resolved: ResolvedProductVariant,
    manufacturer_part_number: str | None,
) -> bool:
    """Registra (ou reforça) a entrada `learned` de uma identidade aprovada que
    traz part number. Devolve `True` quando gravou algo novo. Nunca derruba a
    resolução: falha vira log e a resolução segue."""
    compact = compact_code(manufacturer_part_number or "")
    if not is_usable_part_number(compact):
        return False
    try:
        async with session.begin_nested():
            existing_code = await session.scalar(
                select(ProductIdentityCatalogCode.id).where(
                    ProductIdentityCatalogCode.kind == "part_number",
                    ProductIdentityCatalogCode.value_normalized == compact,
                )
            )
            if existing_code is not None:
                return False
            await session.execute(
                insert(ProductIdentityCatalogEntry)
                .values(
                    category=resolved.category,
                    brand=resolved.brand,
                    family=resolved.family,
                    model=resolved.model,
                    variant=resolved.variant,
                    attributes=dict(resolved.attributes),
                    required_attributes=[],
                    family_key=resolved.family_key,
                    identity_key=resolved.identity_key,
                    status="active",
                    source="learned",
                )
                .on_conflict_do_nothing(
                    constraint="uq_product_identity_catalog_entries_identity"
                )
            )
            entry = await session.scalar(
                select(ProductIdentityCatalogEntry).where(
                    ProductIdentityCatalogEntry.category == resolved.category,
                    ProductIdentityCatalogEntry.brand == resolved.brand,
                    ProductIdentityCatalogEntry.family == resolved.family,
                    ProductIdentityCatalogEntry.model == resolved.model,
                    ProductIdentityCatalogEntry.variant == resolved.variant,
                )
            )
            if (
                entry is None
                or entry.status != "active"
                or entry.identity_key not in (None, resolved.identity_key)
            ):
                return False
            await session.execute(
                insert(ProductIdentityCatalogCode)
                .values(entry_id=entry.id, kind="part_number", value_normalized=compact)
                .on_conflict_do_nothing(
                    constraint="uq_product_identity_catalog_codes_value"
                )
            )
            return True
    except SQLAlchemyError:
        logger.warning(
            "product_identity_catalog_learning_failed",
            extra={"family": resolved.family, "model": resolved.model},
            exc_info=True,
        )
        return False
