"""TASK-132 (Parte A): o anúncio passou a descrever OUTRO produto.

Um anúncio (mesma loja + mesmo `external_id`) pode mudar de produto sem mudar de
link: a Amazon troca a versão padrão de um ASIN (GIGABYTE B550 AORUS Elite AX V3
virou outra placa), a loja reaproveita a URL. Antes desta task a Offer ficava
presa ao Product antigo para sempre e o histórico do produto novo era gravado no
cadastro do antigo.

Fluxo (todo dentro da transação de coleta, só banco -- nenhuma IA na coleta):

1. `track_listing_identity`, chamado a cada coleta de uma Offer existente, pergunta
   a `lookup_title_identity` quem é o título coletado (extrator, catálogo, cache,
   reuso por palavras -- tudo local). Igual ao Product da Offer: a vigilância
   some. Diferente: acumula avistamentos do MESMO título; ao chegar em
   `LISTING_CHANGE_CONFIRMATIONS` coletas seguidas, aplica a troca. Título ainda
   sem identidade conhecida: marca `needs_ai` e a varredura
   `app.products.listing_title_sweep` resolve com orçamento.
2. `apply_listing_change` preserva o histórico: o que foi observado ANTES do
   primeiro avistamento do título novo fica numa Offer arquivada (`superseded_by_id`
   = a Offer viva, some de listagem/comparação como a troca de vendedor) ligada ao
   Product antigo; a Offer viva passa para o Product novo e recomeça o histórico.

Nunca derruba a coleta: qualquer falha vira log e a coleta segue.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.collection.models import (
    MissionOfferRelevance,
    PriceObservation,
    SharedCollectionOffer,
)
from app.coupons.models import OfferCouponPriceDay
from app.database.time import utc_now
from app.offers.models import Offer, OfferIdentityWatch
from app.products.identity import IDENTITY_VERSION, ResolvedProductVariant
from app.products.identity_ai import normalized_title_hash
from app.products.identity_learning import lookup_title_identity
from app.products.models import Product
from app.purchase.models import PurchaseConfirmation

logger = logging.getLogger(__name__)

LISTING_CHANGE_CONFIRMATIONS = 2
"""Coletas seguidas com o mesmo título novo antes de trocar o cadastro."""

MAX_AI_ATTEMPTS = 3
"""Tentativas de IA da varredura por título pendente (depois disso, só o
extrator/catálogo/cache voltam a resolver -- nunca laço de IA)."""


def _same_product(product: Product, identity: ResolvedProductVariant) -> bool:
    return product.identity_key == identity.identity_key


async def track_listing_identity(
    session: AsyncSession,
    offer: Offer,
    *,
    raw_title: str,
    collected_at: datetime,
) -> bool:
    """Vigia o título coletado de uma Offer existente. Devolve `True` quando
    trocou o Product da Offer nesta chamada (o chamador recalcula o alvo)."""
    try:
        async with session.begin_nested():
            return await _track(session, offer, raw_title, collected_at)
    except SQLAlchemyError:
        logger.warning(
            "listing_identity_tracking_failed",
            extra={"offer_id": str(offer.id)},
            exc_info=True,
        )
        return False


async def _track(
    session: AsyncSession, offer: Offer, raw_title: str, collected_at: datetime
) -> bool:
    if offer.superseded_by_id is not None:
        return False  # arquivada: nunca vigia
    product = await session.get(Product, offer.product_id)
    if product is None or product.identity_key is None:
        return False  # ad-hoc: o caminho normal de identidade cuida dela
    watch = await session.get(OfferIdentityWatch, offer.id)
    title_hash = normalized_title_hash(raw_title)
    lookup = await lookup_title_identity(session, raw_title)

    if lookup.status == "resolved" and lookup.resolved is not None:
        if _same_product(product, lookup.resolved):
            if watch is not None:
                await session.delete(watch)
            return False
        return await _register_sighting(
            session, offer, watch, raw_title, title_hash, collected_at, lookup.resolved
        )
    if lookup.status == "unknown":
        _mark_needs_ai(session, offer, watch, raw_title, title_hash, collected_at)
    # `cached_unresolved`: o título já foi avaliado e não fechou identidade --
    # não é evidência de troca, nada a fazer.
    return False


def _mark_needs_ai(
    session: AsyncSession,
    offer: Offer,
    watch: OfferIdentityWatch | None,
    raw_title: str,
    title_hash: str,
    collected_at: datetime,
) -> None:
    if watch is not None and watch.pending_title_hash == title_hash:
        watch.needs_ai = True
        watch.sightings += 1
        return
    session.add(
        _replace_watch(
            session,
            offer,
            watch,
            raw_title,
            title_hash,
            collected_at,
            needs_ai=True,
        )
    )


def _replace_watch(
    session: AsyncSession,
    offer: Offer,
    watch: OfferIdentityWatch | None,
    raw_title: str,
    title_hash: str,
    collected_at: datetime,
    *,
    needs_ai: bool,
) -> OfferIdentityWatch:
    if watch is None:
        watch = OfferIdentityWatch(offer_id=offer.id)
        session.add(watch)
    watch.pending_title = raw_title
    watch.pending_title_hash = title_hash
    watch.first_seen_at = collected_at
    watch.sightings = 1
    watch.needs_ai = needs_ai
    watch.ai_attempts = 0
    return watch


async def _register_sighting(
    session: AsyncSession,
    offer: Offer,
    watch: OfferIdentityWatch | None,
    raw_title: str,
    title_hash: str,
    collected_at: datetime,
    identity: ResolvedProductVariant,
) -> bool:
    if watch is None or watch.pending_title_hash != title_hash:
        _replace_watch(
            session, offer, watch, raw_title, title_hash, collected_at, needs_ai=False
        )
        return False
    watch.needs_ai = False
    watch.sightings += 1
    if watch.sightings < LISTING_CHANGE_CONFIRMATIONS:
        return False
    return await apply_listing_change(
        session, offer, identity, first_seen_at=watch.first_seen_at, watch=watch
    )


async def _product_for_identity(
    session: AsyncSession, identity: ResolvedProductVariant
) -> Product:
    existing = await session.scalar(
        select(Product).where(Product.identity_key == identity.identity_key)
    )
    if existing is not None:
        return existing
    product = Product(
        id=uuid4(),
        name=identity.label[:300],
        brand=identity.brand,
        model=identity.model,
        display_name=identity.label[:300],
        category=identity.category,
        family=identity.family,
        variant=identity.variant,
        attributes=dict(identity.attributes),
        family_key=identity.family_key,
        identity_key=identity.identity_key,
        identity_version=IDENTITY_VERSION,
    )
    try:
        async with session.begin_nested():
            session.add(product)
            await session.flush()
    except IntegrityError:
        winner = await session.scalar(
            select(Product).where(Product.identity_key == identity.identity_key)
        )
        if winner is None:
            raise
        return winner
    return product


async def apply_listing_change(
    session: AsyncSession,
    offer: Offer,
    identity: ResolvedProductVariant,
    *,
    first_seen_at: datetime,
    watch: OfferIdentityWatch | None = None,
) -> bool:
    """Troca o Product da Offer viva e arquiva o histórico antigo. `False` sem
    trocar quando a Offer tem confirmação de compra (evidência imutável que
    aponta para ela) -- nesse caso a vigilância é descartada e o caso vai a log."""
    has_purchase = await session.scalar(
        select(func.count())
        .select_from(PurchaseConfirmation)
        .where(PurchaseConfirmation.offer_id == offer.id)
    )
    if has_purchase:
        logger.warning(
            "listing_change_skipped_purchase_confirmation",
            extra={"offer_id": str(offer.id)},
        )
        if watch is not None:
            await session.delete(watch)
        return False

    old_product_id = offer.product_id
    new_product = await _product_for_identity(session, identity)
    if new_product.id == old_product_id:
        if watch is not None:
            await session.delete(watch)
        return False

    now = utc_now()
    tag = uuid4().hex[:8]
    archived = Offer(
        id=uuid4(),
        product_id=old_product_id,
        store_id=offer.store_id,
        seller_id=offer.seller_id,
        external_id=f"{offer.external_id}~arquivada-{tag}"
        if offer.external_id
        else None,
        url=f"{offer.url}#arquivada-{tag}",
        image_url=offer.image_url,
        rating_average=offer.rating_average,
        review_count=offer.review_count,
        rating_observed_at=offer.rating_observed_at,
        created_at=offer.created_at,
        last_seen_at=offer.last_seen_at,
    )
    session.add(archived)
    await session.flush()
    archived.superseded_by_id = offer.id
    archived.superseded_at = now

    old_ids = list(
        (
            await session.scalars(
                select(PriceObservation.id)
                .where(
                    PriceObservation.offer_id == offer.id,
                    PriceObservation.observed_at < first_seen_at,
                )
                .order_by(PriceObservation.observed_at.desc(), PriceObservation.id)
            )
        ).all()
    )
    has_new = await session.scalar(
        select(func.count())
        .select_from(PriceObservation)
        .where(
            PriceObservation.offer_id == offer.id,
            PriceObservation.observed_at >= first_seen_at,
        )
    )
    if old_ids and not has_new:
        # A coleta com o título novo foi redundante (mesmo estado comercial) e
        # não gravou observação própria: a mais recente do período antigo é o
        # que representa o estado atual e fica com a Offer viva.
        old_ids = old_ids[1:]
    if old_ids:
        await session.execute(
            update(PriceObservation)
            .where(PriceObservation.id.in_(old_ids))
            .values(offer_id=archived.id)
        )
        await session.execute(
            update(SharedCollectionOffer)
            .where(
                SharedCollectionOffer.offer_id == offer.id,
                SharedCollectionOffer.observation_id.in_(old_ids),
            )
            .values(offer_id=archived.id)
        )
        await session.execute(
            update(OfferCouponPriceDay)
            .where(
                OfferCouponPriceDay.offer_id == offer.id,
                OfferCouponPriceDay.observation_id.in_(old_ids),
            )
            .values(offer_id=archived.id)
        )
    # A relevância (missão x anúncio) dependia do produto antigo: a próxima
    # coleta reclassifica com o produto novo.
    await session.execute(
        delete(MissionOfferRelevance).where(MissionOfferRelevance.offer_id == offer.id)
    )
    offer.product_id = new_product.id
    if watch is not None:
        await session.delete(watch)
    logger.info(
        "listing_product_changed",
        extra={
            "offer_id": str(offer.id),
            "archived_offer_id": str(archived.id),
            "old_product_id": str(old_product_id),
            "new_product_id": str(new_product.id),
            "moved_observations": len(old_ids),
        },
    )
    return True


async def apply_pending_identity(
    session: AsyncSession, watch: OfferIdentityWatch, identity: ResolvedProductVariant
) -> bool:
    """Chamado pela varredura de IA: o título pendente ganhou identidade. Diferente
    do Product da Offer: conta como avistamento já confirmado se o título se
    repetiu; igual: vigilância some."""
    offer = await session.get(Offer, watch.offer_id)
    if offer is None or offer.superseded_by_id is not None:
        await session.delete(watch)
        return False
    product = await session.get(Product, offer.product_id)
    if product is None or product.identity_key is None:
        await session.delete(watch)
        return False
    if _same_product(product, identity):
        await session.delete(watch)
        return False
    watch.needs_ai = False
    if watch.sightings < LISTING_CHANGE_CONFIRMATIONS:
        return False
    return await apply_listing_change(
        session, offer, identity, first_seen_at=watch.first_seen_at, watch=watch
    )


__all__ = [
    "LISTING_CHANGE_CONFIRMATIONS",
    "MAX_AI_ATTEMPTS",
    "apply_listing_change",
    "apply_pending_identity",
    "track_listing_identity",
]
