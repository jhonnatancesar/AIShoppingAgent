"""Pesquisa read-only no catálogo persistido para a área USER (TASK-099)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collection.contracts import OfferCondition
from app.collection.models import PriceObservation
from app.collection.normalization import Availability
from app.offers.models import Offer
from app.products.identity import ProductRequestIdentity, ProductRequestKind
from app.products.models import Product
from app.stores.models import Store

_MAX_QUERY_ROWS_PER_STORE = 50
_MAX_OFFERS_PER_STORE = 5


@dataclass(frozen=True, slots=True)
class ProductSearchHit:
    offer: Offer
    product: Product
    store: Store
    observation: PriceObservation


def _tokens(text: str) -> tuple[str, ...]:
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return tuple(dict.fromkeys(re.findall(r"[a-z0-9]+", plain.lower())))


def _commercial_key(hit: ProductSearchHit) -> tuple:
    observation = hit.observation
    availability = 0 if observation.availability is Availability.AVAILABLE else 1
    condition = {
        OfferCondition.NEW: 0,
        OfferCondition.UNKNOWN: 1,
        OfferCondition.REFURBISHED: 2,
        OfferCondition.USED: 3,
    }[observation.condition]
    return (
        hit.store.code,
        availability,
        condition,
        observation.total_amount,
        observation.amount,
        str(hit.offer.id),
    )


async def search_persisted_products(
    session: AsyncSession,
    *,
    query: str,
    source_codes: tuple[str, ...],
    request_identity: ProductRequestIdentity,
) -> tuple[ProductSearchHit, ...]:
    """Retorna ofertas conhecidas sem criar missão, run ou observação."""
    latest_observation_id = (
        select(PriceObservation.id)
        .where(PriceObservation.offer_id == Offer.id)
        .order_by(PriceObservation.observed_at.desc(), PriceObservation.id.desc())
        .limit(1)
        .correlate(Offer)
        .scalar_subquery()
    )
    statement = (
        select(Offer, Product, Store, PriceObservation)
        .join(Product, Product.id == Offer.product_id)
        .join(Store, Store.id == Offer.store_id)
        .join(PriceObservation, PriceObservation.id == latest_observation_id)
        .where(Store.is_active.is_(True))
    )
    if request_identity.kind is ProductRequestKind.SPECIFIC_PRODUCT:
        statement = statement.where(
            Product.identity_key == request_identity.identity_key
        )
    elif request_identity.kind is ProductRequestKind.PRODUCT_FAMILY:
        statement = statement.where(Product.family_key == request_identity.family_key)
        if request_identity.variant is not None:
            statement = statement.where(Product.variant == request_identity.variant)
    else:
        searchable = func.lower(func.coalesce(Product.display_name, Product.name))
        for token in _tokens(query):
            statement = statement.where(searchable.contains(token))

    rows = []
    for source_code in sorted(source_codes):
        rows.extend(
            (
                await session.execute(
                    statement.where(Store.code == source_code)
                    .order_by(Offer.last_seen_at.desc(), Offer.id)
                    .limit(_MAX_QUERY_ROWS_PER_STORE)
                )
            ).all()
        )
    ordered = sorted(
        (
            ProductSearchHit(
                offer=offer,
                product=product,
                store=store,
                observation=observation,
            )
            for offer, product, store, observation in rows
        ),
        key=_commercial_key,
    )
    counts: dict[str, int] = {}
    selected: list[ProductSearchHit] = []
    for hit in ordered:
        count = counts.get(hit.store.code, 0)
        if count >= _MAX_OFFERS_PER_STORE:
            continue
        counts[hit.store.code] = count + 1
        selected.append(hit)
    return tuple(selected)
