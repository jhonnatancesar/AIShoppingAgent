"""TASK-137 -- pedido do usuário -> identidade de monitoramento pelo catálogo.

Quando as regras fixas de texto (`identity.resolve_monitoring_identity`) não
reconhecem o pedido ("MSI B650M PRO", "ASUS TUF GAMING B650M-PLUS WIFI"), o
sistema procura o produto no catálogo de identidade (mesmo casamento que o GG
já usa nos títulos de loja, com a regra estrita para fontes abertas). Achando
UM produto, o pedido ganha identidade de monitoramento e a missão passa a usar
a coleta compartilhada e o funil; não achando, tudo segue como antes.

Nunca usa IA. Ambiguidade (textos diferentes do mesmo pedido apontando para
produtos diferentes) não decide.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from hashlib import sha256

from app.products.identity import (
    MonitoringIdentity,
    build_resolved_variant_from_fields,
    normalize_for_grounding,
    resolve_monitoring_identity_for_resolved_product,
)
from app.products.identity_ai import _strip_token_edges
from app.products.identity_catalog import CatalogEntrySnapshot, match_catalog_entry

_MIN_REQUEST_LENGTH = 4


def _humanize(slug: str) -> str:
    return slug.replace("-", " ").strip()


def collection_hint(
    entry: CatalogEntrySnapshot, attributes: dict[str, str]
) -> tuple[str, str | None]:
    """`(search_query, model)` que a coleta usa para este produto.

    Placa-mãe: marca + o MENOR nome do catálogo (normalmente sem o tipo de
    memória, que as lojas nem sempre escrevem) e o próprio nome como filtro de
    título. RAM e demais: marca + família + capacidade/tipo/velocidade, sem
    filtro de modelo (nome de kit é ambíguo demais nas lojas)."""
    names = sorted(
        (name for name in entry.names if len(name.split()) >= 2),
        key=lambda name: (len(name.split()), len(name), name),
    )
    if entry.category == "motherboard" and names:
        return f"{_humanize(entry.brand)} {names[0].lower()}".strip(), names[0]
    if entry.category == "ram":
        parts = [
            _humanize(entry.brand),
            _humanize(entry.family),
            f"{attributes['capacity_gb']}gb" if "capacity_gb" in attributes else "",
            attributes.get("type", ""),
            attributes.get("speed_mhz", ""),
        ]
        return " ".join(part for part in parts if part), None
    return f"{_humanize(entry.brand)} {_humanize(entry.model)}".strip(), None


def monitoring_identity_from_catalog(
    catalog: Sequence[CatalogEntrySnapshot], request_texts: Sequence[str]
) -> MonitoringIdentity | None:
    """Identidade (escopo `SPECIFIC`) do produto único que o catálogo reconhece
    nos textos do pedido, ou `None`."""
    found: dict[tuple[str, ...], tuple[CatalogEntrySnapshot, MonitoringIdentity]] = {}
    for text in dict.fromkeys(request_texts):
        if not text or len(text.strip()) < _MIN_REQUEST_LENGTH:
            continue
        match = match_catalog_entry(tuple(catalog), text)
        if match is None:
            continue
        entry, resolved = match
        if entry.required_attributes:
            continue  # celular: a capacidade decide o produto; fora deste passo
        # O catálogo grava atributos com hífen (`capacity-gb`); o registro de
        # categorias usa sublinhado (`capacity_gb`).
        attributes = {
            name.replace("-", "_"): value for name, value in resolved.attributes
        }
        identity = resolve_monitoring_identity_for_resolved_product(
            category=resolved.category,
            brand=resolved.brand,
            family=resolved.family,
            model=resolved.model,
            variant=resolved.variant,
            attributes=attributes,
        )
        if identity is None:
            continue
        readable = {name: str(value).lower() for name, value in attributes.items()}
        identity = replace(identity, collection_search=collection_hint(entry, readable))
        found[(resolved.category, resolved.brand, resolved.family, resolved.model)] = (
            entry,
            identity,
        )
    if len(found) != 1:
        return None
    return next(iter(found.values()))[1]


def identity_from_entry(entry: CatalogEntrySnapshot) -> MonitoringIdentity | None:
    """Identidade de monitoramento de uma entrada JÁ escolhida (decisão da IA
    guardada). Mesmo caminho do casamento direto."""
    if entry.required_attributes:
        return None
    resolved = build_resolved_variant_from_fields(
        category=entry.category,
        brand=entry.brand,
        family=entry.family,
        model=entry.model,
        variant=entry.variant,
        attributes=entry.attributes,
    )
    if resolved is None:
        return None
    attributes = {name.replace("-", "_"): value for name, value in resolved.attributes}
    identity = resolve_monitoring_identity_for_resolved_product(
        category=resolved.category,
        brand=resolved.brand,
        family=resolved.family,
        model=resolved.model,
        variant=resolved.variant,
        attributes=attributes,
    )
    if identity is None:
        return None
    readable = {name: str(value).lower() for name, value in attributes.items()}
    return replace(identity, collection_search=collection_hint(entry, readable))


# --- dúvida: candidatos do banco para a IA escolher -------------------------

_FILLER = frozenset(
    {
        "PLACA", "MAE", "MOTHERBOARD", "MB", "MEMORIA", "RAM", "QUERO", "UM", "UMA",
        "DE", "DA", "DO", "PARA", "COM", "SOCKET", "ATX", "MATX", "MICRO", "MINI", "ITX",
        "AMD", "INTEL",
    }
)  # fmt: skip
_NOT_A_MODEL_CODE = re.compile(
    r"^(?:DDR\d?|LPDDR\d|AM\d\+?|LGA\d{3,4}|\d+(?:GB|TB|MHZ|MT/?S)|CL\d+)$"
)
MAX_CANDIDATES = 5
KNOWN_BRANDS = frozenset(
    {
        "ASUS", "MSI", "GIGABYTE", "AORUS", "ASROCK", "BIOSTAR", "EVGA", "NZXT",
        "COLORFUL", "MAXSUN", "ZOTAC", "KINGSTON", "CORSAIR", "CRUCIAL", "ADATA",
        "XPG", "PATRIOT", "PNY", "TEAMGROUP", "GSKILL", "SAMSUNG", "HYPERX",
    }
)  # fmt: skip
"""Marcas reconhecidas no TEXTO do pedido, além das do catálogo carregado: um pedido
que cita uma marca fora dos candidatos nunca vira dúvida."""
_MIN_SCORE = 0.5


def _tokens(text: str) -> list[str]:
    return [
        token
        for token in (
            _strip_token_edges(t) for t in normalize_for_grounding(text).split()
        )
        if token
    ]


def _is_model_code(token: str) -> bool:
    return any(char.isdigit() for char in token) and not _NOT_A_MODEL_CODE.match(token)


def request_key(text: str) -> str:
    """Chave estável do pedido: mesmas palavras (sem acento, caixa ou pontuação)
    dão a mesma chave."""
    return sha256(" ".join(_tokens(text)).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Candidate:
    entry: CatalogEntrySnapshot
    name: str
    score: float


def find_candidates(
    catalog: Sequence[CatalogEntrySnapshot],
    request_texts: Sequence[str],
    *,
    limit: int = MAX_CANDIDATES,
) -> list[Candidate]:
    """Produtos do catálogo PARECIDOS com o pedido (quando não há um único exato).

    Exige o código de modelo do pedido (palavra com número, ex.: B650M) dentro do
    nome do candidato, a mesma marca quando o pedido cita uma, e parecença de
    palavras (Jaccard) de pelo menos 0,5. Devolve até `limit`, do mais para o menos
    parecido. Sem código de modelo no pedido ("cadeira gamer"), não há candidato."""
    request = {
        token
        for text in request_texts
        for token in _tokens(text)
        if token not in _FILLER
    }
    anchors = {token for token in request if _is_model_code(token)}
    if not anchors:
        return []
    brand_tokens = {entry.brand.upper() for entry in catalog} | KNOWN_BRANDS
    request_brands = request & brand_tokens
    comparable = request - brand_tokens
    found: list[Candidate] = []
    for entry in catalog:
        if entry.required_attributes or not entry.names:
            continue
        if request_brands and entry.brand.upper() not in request_brands:
            continue
        best: Candidate | None = None
        for name in entry.names:
            name_tokens = {token for token in _tokens(name) if token not in _FILLER}
            if not anchors & name_tokens:
                continue
            union = name_tokens | comparable
            score = len(name_tokens & comparable) / len(union) if union else 0.0
            if score >= _MIN_SCORE and (best is None or score > best.score):
                best = Candidate(entry, name, score)
        if best is not None:
            found.append(best)
    found.sort(key=lambda c: (-c.score, len(c.name), c.entry.model))
    return found[:limit]


def choice_is_coherent(
    request_texts: Sequence[str], entry: CatalogEntrySnapshot, name: str
) -> bool:
    """Conferência determinística da escolha da IA: todo código de modelo do pedido
    precisa estar no nome escolhido, e a marca do pedido (se houver) precisa ser a
    da entrada. A IA nunca sozinha decide."""
    request = {
        token
        for text in request_texts
        for token in _tokens(text)
        if token not in _FILLER
    }
    anchors = {token for token in request if _is_model_code(token)}
    if not anchors or not anchors <= set(_tokens(name)):
        return False
    request_brands = {token for token in request if token == entry.brand.upper()}
    other_brand = {
        token
        for token in request
        if token in KNOWN_BRANDS and token != entry.brand.upper()
    }
    return not other_brand or bool(request_brands)
