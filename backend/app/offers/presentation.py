"""Título de apresentação de uma Offer concreta (TASK-114).

Product é identidade global (pode ser compartilhada por Offers de
produtos fisicamente diferentes, se a identidade determinística já foi
mal resolvida em algum momento -- ver DEC-105). Offer/PriceObservation
representa o anúncio real de uma loja. Preferir sempre o texto bruto da
própria oferta (`PriceObservation.raw_evidence["title"]`, já persistido
desde TASK-097/TASK-025 para toda observação) -- nunca o nome do
Product -- para que cada oferta mostre seu próprio título real, mesmo
quando compartilha Product com outra oferta.
"""

from __future__ import annotations

from app.collection.models import PriceObservation
from app.products.models import Product


def resolve_offer_display_title(
    product: Product, observation: PriceObservation | None
) -> str:
    if observation is not None:
        raw_evidence = observation.raw_evidence
        if isinstance(raw_evidence, dict):
            raw_title = raw_evidence.get("title")
            if isinstance(raw_title, str) and raw_title.strip():
                return raw_title
    return product.display_name or product.name
