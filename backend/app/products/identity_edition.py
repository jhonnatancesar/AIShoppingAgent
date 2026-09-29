"""TASK-131/132 -- guarda de palavras de edição, compartilhada pelo reuso por
palavras do título (`identity_learning`) e pelo catálogo (`identity_catalog`).

Backfill de PROD (2026-09-28): o reuso devolvia o PRIMEIRO cadastro aprovado
cujos tokens de marca/família/modelo aparecem no título, e palavras de edição
extras ("MAX", "PZ", o "Max" de "Pro Max") não impediam o reuso -- a
"MSI MAG X870E Tomahawk MAX" caía no cadastro da Tomahawk sem MAX, o iPhone 17
Pro Max no do Pro. Lista fechada e curta, de propósito: fora dela ficam "AIR"
("Air Cooler"), "MINI" ("Mini-ITX"), "SUPER" ("Super Retina") e "SE", que
aparecem em título de produto sem mudar o modelo. Custo assumido: título com
uma dessas palavras que o cadastro não conhece vai para a IA (uma chamada a
mais) em vez de ser fundido no produto errado.
"""

import re
from typing import Protocol

from app.products.identity import normalize_for_grounding

EDITION_WORDS = frozenset(
    {"MAX", "PLUS", "ULTRA", "PRO", "LITE", "FE", "PZ", "TI", "XT", "XTX"}
)

_WORD = re.compile(r"[A-Z0-9]+")


class _HasIdentityFields(Protocol):
    brand: str | None
    family: str | None
    model: str | None
    variant: str | None
    attributes: dict[str, str]


def _words(text: str) -> set[str]:
    # Só letras e dígitos: "PRO+" (Redmi Note 13 Pro+) e "(MAX)," contam como
    # PRO e MAX, sem depender de a pontuação estar solta do token.
    return set(_WORD.findall(normalize_for_grounding(text)))


def has_unknown_edition_word(
    raw_title_normalized: str, candidate: _HasIdentityFields
) -> bool:
    """`True` quando o título traz uma palavra de edição (`EDITION_WORDS`) que o
    candidato não registra em marca/família/modelo/variante/atributos."""
    known: set[str] = set()
    for field in (
        candidate.brand,
        candidate.family,
        candidate.model,
        candidate.variant,
    ):
        known |= _words(field or "")
    for value in candidate.attributes.values():
        known |= _words(value)
    title_words = _words(raw_title_normalized)
    return any(word in title_words and word not in known for word in EDITION_WORDS)
