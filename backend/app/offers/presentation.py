"""Título e imagem de apresentação de uma Offer concreta (TASK-114, subtask 4).

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
from app.offers.models import Offer
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


def resolve_offer_image_chain(
    offer: Offer, product: Product
) -> tuple[str | None, str | None]:
    """Devolve `(primária, alternativa)` -- nunca um valor único, porque a
    apresentação (Web/Telegram) precisa de um segundo candidato real em
    runtime quando o primeiro falhar ao carregar/enviar (revisão da
    subtask 4: a canônica é `set-once` na coleta -- ver
    `app.collection.orchestration._maybe_set_canonical_image` -- e pode
    ficar indisponível no futuro sem nenhum reparo automático; por isso a
    apresentação NUNCA depende só dela).

    Precedência: se `Product.canonical_image_url` existe, ela é a
    principal (mesmo produto/variante deve mostrar a mesma imagem em
    todas as lojas -- esse é o objetivo da canônica). A imagem própria da
    Offer é a alternativa, tentada só se a canônica falhar. Sem canônica,
    a própria Offer vira a principal, sem alternativa. Nunca devolve a
    mesma URL duas vezes (`alternativa` fica `None` quando é igual à
    principal ou inexistente)."""
    canonical = product.canonical_image_url
    own = offer.image_url
    if canonical is None:
        return own, None
    if own is None or own == canonical:
        return canonical, None
    return canonical, own
