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

from collections.abc import Sequence
from dataclasses import replace

from app.products.identity import (
    MonitoringIdentity,
    resolve_monitoring_identity_for_resolved_product,
)
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
