"""Consultas USER de ofertas, sempre escopadas por missão do proprietário."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collection.models import (
    MissionOfferRelevance,
    OfferInstallmentOption,
    PriceObservation,
)
from app.collection.relevance import OfferRelevance
from app.missions.models import Mission
from app.offers.models import Offer
from app.products.models import Product
from app.stores.models import Seller, Store

_ACCESSIBLE_RELEVANCE = (
    OfferRelevance.MATCH,
    OfferRelevance.POSSIBLE_MATCH,
)


@dataclass(frozen=True, slots=True)
class UserOfferDetail:
    offer: Offer
    product: Product
    store: Store
    seller: Seller | None
    observation: PriceObservation | None
    installments: tuple[OfferInstallmentOption, ...]


@dataclass(frozen=True, slots=True)
class MissionOfferLink:
    offer: Offer
    product: Product
    store: Store


def offer_for_user_statement(*, offer_id: UUID, user_id: UUID):
    """Statement fail-closed: NO_MATCH e missões alheias nunca autorizam."""
    return (
        select(Offer, Product, Store, Seller)
        .join(Product, Product.id == Offer.product_id)
        .join(Store, Store.id == Offer.store_id)
        .outerjoin(Seller, Seller.id == Offer.seller_id)
        .join(
            MissionOfferRelevance,
            MissionOfferRelevance.offer_id == Offer.id,
        )
        .join(Mission, Mission.id == MissionOfferRelevance.mission_id)
        .where(
            Offer.id == offer_id,
            Mission.user_id == user_id,
            MissionOfferRelevance.classification.in_(_ACCESSIBLE_RELEVANCE),
        )
        .order_by(Mission.id)
        .limit(1)
    )


async def get_offer_detail_for_user(
    session: AsyncSession, *, offer_id: UUID, user_id: UUID
) -> UserOfferDetail | None:
    row = (await session.execute(
        offer_for_user_statement(offer_id=offer_id, user_id=user_id)
    )).first()
    if row is None:
        return None
    offer, product, store, seller = row
    observation = await session.scalar(
        select(PriceObservation)
        .where(PriceObservation.offer_id == offer.id)
        .order_by(
            PriceObservation.observed_at.desc(), PriceObservation.id.desc()
        )
        .limit(1)
    )
    installments: tuple[OfferInstallmentOption, ...] = ()
    if observation is not None:
        installments = tuple(
            await session.scalars(
                select(OfferInstallmentOption)
                .where(
                    OfferInstallmentOption.price_observation_id == observation.id
                )
                .order_by(
                    OfferInstallmentOption.is_highlighted.desc(),
                    OfferInstallmentOption.installment_count.asc(),
                    OfferInstallmentOption.id.asc(),
                )
            )
        )
    return UserOfferDetail(
        offer=offer,
        product=product,
        store=store,
        seller=seller,
        observation=observation,
        installments=installments,
    )


async def list_current_offer_links_for_mission(
    session: AsyncSession, *, mission_id: UUID, user_id: UUID
) -> tuple[MissionOfferLink, ...]:
    """Ofertas relevantes da coleta mais recente de cada loja da missão."""
    rows = (
        await session.execute(
            select(Offer, Product, Store)
            .join(Product, Product.id == Offer.product_id)
            .join(Store, Store.id == Offer.store_id)
            .join(
                MissionOfferRelevance,
                MissionOfferRelevance.offer_id == Offer.id,
            )
            .join(Mission, Mission.id == MissionOfferRelevance.mission_id)
            .where(
                Mission.id == mission_id,
                Mission.user_id == user_id,
                MissionOfferRelevance.classification.in_(_ACCESSIBLE_RELEVANCE),
            )
            .order_by(Store.code, Offer.last_seen_at.desc(), Offer.id)
        )
    ).all()
    latest_seen_by_store: dict[UUID, datetime] = {}
    for offer, _product, store in rows:
        latest_seen_by_store.setdefault(store.id, offer.last_seen_at)
    return tuple(
        MissionOfferLink(offer=offer, product=product, store=store)
        for offer, product, store in rows
        if offer.last_seen_at == latest_seen_by_store[store.id]
    )
