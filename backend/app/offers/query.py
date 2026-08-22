"""Consultas USER de ofertas, sempre escopadas por missão do proprietário."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collection.models import (
    MissionOfferRelevance,
    OfferInstallmentOption,
    PriceObservation,
)
from app.collection.relevance import OfferRelevance
from app.missions.models import (
    Mission,
    MissionCriteria,
    MissionProductSelection,
    VariantSelectionMode,
)
from app.offers.models import Offer
from app.products.identity import ProductRequestKind
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


@dataclass(frozen=True, slots=True)
class UserOfferSummary:
    offer: Offer
    product: Product
    store: Store
    seller: Seller | None
    observation: PriceObservation | None


@dataclass(frozen=True, slots=True)
class UserComparisonOffer:
    offer: Offer
    store: Store
    seller: Seller | None
    observation: PriceObservation | None
    installments: tuple[OfferInstallmentOption, ...]


@dataclass(frozen=True, slots=True)
class UserOfferComparison:
    product: Product
    offers: tuple[UserComparisonOffer, ...]


def _accessible_offer_exists(*, user_id: UUID):
    return exists(
        select(MissionOfferRelevance.offer_id)
        .join(Mission, Mission.id == MissionOfferRelevance.mission_id)
        .where(
            MissionOfferRelevance.offer_id == Offer.id,
            Mission.user_id == user_id,
            MissionOfferRelevance.classification.in_(_ACCESSIBLE_RELEVANCE),
        )
    )


def user_offers_statement(
    *,
    user_id: UUID,
    search: str | None = None,
    store_code: str | None = None,
    condition: str | None = None,
    availability: str | None = None,
    sort: str = "recent",
):
    """Lista Offers únicas acessíveis; NO_MATCH/missão alheia falham fechados."""
    latest_observation_id = (
        select(PriceObservation.id)
        .where(PriceObservation.offer_id == Offer.id)
        .order_by(PriceObservation.observed_at.desc(), PriceObservation.id.desc())
        .limit(1)
        .correlate(Offer)
        .scalar_subquery()
    )
    statement = (
        select(Offer, Product, Store, Seller, PriceObservation)
        .join(Product, Product.id == Offer.product_id)
        .join(Store, Store.id == Offer.store_id)
        .outerjoin(Seller, Seller.id == Offer.seller_id)
        .outerjoin(PriceObservation, PriceObservation.id == latest_observation_id)
        .where(_accessible_offer_exists(user_id=user_id))
    )
    if search:
        statement = statement.where(
            func.lower(func.coalesce(Product.display_name, Product.name)).contains(
                search.strip().lower()
            )
        )
    if store_code:
        statement = statement.where(Store.code == store_code)
    if condition:
        statement = statement.where(PriceObservation.condition == condition)
    if availability:
        statement = statement.where(PriceObservation.availability == availability)
    if sort == "price_asc":
        statement = statement.order_by(
            PriceObservation.total_amount.asc().nulls_last(), Offer.id
        )
    elif sort == "price_desc":
        statement = statement.order_by(
            PriceObservation.total_amount.desc().nulls_last(), Offer.id
        )
    else:
        statement = statement.order_by(Offer.last_seen_at.desc(), Offer.id)
    return statement


async def list_user_offers(
    session: AsyncSession,
    *,
    user_id: UUID,
    search: str | None,
    store_code: str | None,
    condition: str | None,
    availability: str | None,
    sort: str,
    limit: int,
    offset: int,
) -> tuple[tuple[UserOfferSummary, ...], int]:
    base = user_offers_statement(
        user_id=user_id,
        search=search,
        store_code=store_code,
        condition=condition,
        availability=availability,
        sort=sort,
    )
    total = await session.scalar(
        select(func.count()).select_from(base.order_by(None).subquery())
    )
    rows = (await session.execute(base.limit(limit).offset(offset))).all()
    return (
        tuple(
            UserOfferSummary(
                offer=offer,
                product=product,
                store=store,
                seller=seller,
                observation=observation,
            )
            for offer, product, store, seller, observation in rows
        ),
        int(total or 0),
    )


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
    row = (
        await session.execute(
            offer_for_user_statement(offer_id=offer_id, user_id=user_id)
        )
    ).first()
    if row is None:
        return None
    offer, product, store, seller = row
    observation = await session.scalar(
        select(PriceObservation)
        .where(PriceObservation.offer_id == offer.id)
        .order_by(PriceObservation.observed_at.desc(), PriceObservation.id.desc())
        .limit(1)
    )
    installments: tuple[OfferInstallmentOption, ...] = ()
    if observation is not None:
        installments = tuple(
            await session.scalars(
                select(OfferInstallmentOption)
                .where(OfferInstallmentOption.price_observation_id == observation.id)
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


_COMPARISON_CONDITION_RANK = {"new": 0, "unknown": 1, "refurbished": 2, "used": 3}
_COMPARISON_PARTY_RANK = {"platform": 0, "marketplace_partner": 1, "unknown": 2}
_COMPARISON_AVAILABILITY_RANK = {"available": 0, "unknown": 1, "unavailable": 2}
_COMPARISON_PER_STORE_LIMIT = 5


def comparison_offers_statement(*, product_id: UUID, user_id: UUID):
    """Mesmo Product global e ownership por Offer; identidade aproximada é proibida."""
    latest_observation_id = (
        select(PriceObservation.id)
        .where(PriceObservation.offer_id == Offer.id)
        .order_by(PriceObservation.observed_at.desc(), PriceObservation.id.desc())
        .limit(1)
        .correlate(Offer)
        .scalar_subquery()
    )
    return (
        select(Offer, Store, Seller, PriceObservation)
        .join(Store, Store.id == Offer.store_id)
        .outerjoin(Seller, Seller.id == Offer.seller_id)
        .outerjoin(PriceObservation, PriceObservation.id == latest_observation_id)
        .where(
            Offer.product_id == product_id,
            _accessible_offer_exists(user_id=user_id),
        )
        .order_by(Store.code, Offer.id)
    )


def _comparison_key(row: tuple[Offer, Store, Seller | None, PriceObservation | None]):
    offer, store, _seller, observation = row
    if observation is None:
        return (store.code, 9, 9, 9, float("inf"), float("inf"), str(offer.id))
    condition = getattr(observation.condition, "value", observation.condition)
    seller_kind = getattr(observation.seller_kind, "value", observation.seller_kind)
    availability = getattr(observation.availability, "value", observation.availability)
    return (
        store.code,
        _COMPARISON_CONDITION_RANK.get(condition, 9),
        _COMPARISON_PARTY_RANK.get(seller_kind, 9),
        _COMPARISON_AVAILABILITY_RANK.get(availability, 9),
        observation.total_amount,
        observation.amount,
        str(offer.id),
    )


async def get_offer_comparison_for_user(
    session: AsyncSession, *, offer_id: UUID, user_id: UUID
) -> UserOfferComparison | None:
    anchor = await get_offer_detail_for_user(
        session, offer_id=offer_id, user_id=user_id
    )
    if anchor is None:
        return None
    if anchor.product.identity_key is None:
        return UserOfferComparison(product=anchor.product, offers=())
    rows = list(
        (
            await session.execute(
                comparison_offers_statement(
                    product_id=anchor.product.id, user_id=user_id
                )
            )
        ).all()
    )
    counts: dict[UUID, int] = {}
    selected = []
    for row in sorted(rows, key=_comparison_key):
        store_id = row[1].id
        if counts.get(store_id, 0) >= _COMPARISON_PER_STORE_LIMIT:
            continue
        counts[store_id] = counts.get(store_id, 0) + 1
        selected.append(row)
    result = []
    for offer, store, seller, observation in selected:
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
                        OfferInstallmentOption.installment_count.desc(),
                        OfferInstallmentOption.id,
                    )
                )
            )
        result.append(
            UserComparisonOffer(offer, store, seller, observation, installments)
        )
    return UserOfferComparison(product=anchor.product, offers=tuple(result))


async def list_current_offer_links_for_mission(
    session: AsyncSession, *, mission_id: UUID, user_id: UUID
) -> tuple[MissionOfferLink, ...]:
    """Ofertas relevantes da coleta mais recente de cada loja da missão."""
    criteria = await session.scalar(
        select(MissionCriteria).where(MissionCriteria.mission_id == mission_id)
    )
    if (
        criteria is not None
        and criteria.request_kind == ProductRequestKind.PRODUCT_FAMILY.value
        and criteria.variant_selection_mode is VariantSelectionMode.PENDING
    ):
        return ()
    selected_ids: set[UUID] | None = None
    if (
        criteria is not None
        and criteria.request_kind == ProductRequestKind.PRODUCT_FAMILY.value
        and criteria.variant_selection_mode is VariantSelectionMode.SELECTED
    ):
        selected_ids = set(
            await session.scalars(
                select(MissionProductSelection.product_id).where(
                    MissionProductSelection.mission_id == mission_id
                )
            )
        )
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
    eligible_rows = tuple(
        (offer, product, store)
        for offer, product, store in rows
        if criteria is None
        or criteria.request_kind != ProductRequestKind.PRODUCT_FAMILY.value
        or (
            product.identity_key is not None
            and product.family_key == criteria.requested_family_key
            and (
                criteria.requested_variant is None
                or product.variant == criteria.requested_variant
            )
            and (selected_ids is None or product.id in selected_ids)
        )
    )
    latest_seen_by_store: dict[UUID, datetime] = {}
    for offer, _product, store in eligible_rows:
        latest_seen_by_store.setdefault(store.id, offer.last_seen_at)
    return tuple(
        MissionOfferLink(offer=offer, product=product, store=store)
        for offer, product, store in eligible_rows
        if offer.last_seen_at == latest_seen_by_store[store.id]
    )
