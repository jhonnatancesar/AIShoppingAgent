"""Detalhe de oferta da área USER (TASK-095)."""

from decimal import Decimal
from typing import Annotated, Literal, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.authorization import (
    AuthorizationDenied,
    Permission,
    authorize,
    deny_resource_unavailable,
)
from app.collection.contracts import (
    InstallmentInterestKind,
    MarketplacePartyKind,
    OfferCondition,
)
from app.collection.normalization import Availability
from app.core.errors import ApiError
from app.database.dependency import get_web_async_session
from app.offers.query import (
    UserOfferDetail,
    UserOfferSummary,
    get_offer_detail_for_user,
    list_user_offers,
)
from app.users.models import User
from app.webapp.dependency import require_web_session

router = APIRouter(prefix="/api/v1/offers", tags=["offers"])


class StoreOut(BaseModel):
    code: str
    name: str


class SellerOut(BaseModel):
    name: str


class OfferRatingOut(BaseModel):
    average: Decimal
    review_count: int
    observed_at: str


class InstallmentOut(BaseModel):
    installment_count: int
    installment_amount: Decimal
    installment_total_amount: Decimal | None
    discount_percent: Decimal | None
    interest_kind: InstallmentInterestKind
    is_highlighted: bool


class LatestOfferObservationOut(BaseModel):
    amount: Decimal
    currency: str
    shipping_amount: Decimal | None
    total_amount: Decimal
    fulfillment: str | None
    seller_kind: MarketplacePartyKind | None
    fulfillment_kind: MarketplacePartyKind | None
    condition: OfferCondition
    availability: Availability
    observed_at: str
    installments: list[InstallmentOut]


class OfferDetailResponse(BaseModel):
    id: UUID
    title: str
    image_url: str | None
    original_url: str
    last_seen_at: str
    store: StoreOut
    seller: SellerOut | None
    rating: OfferRatingOut | None
    latest_observation: LatestOfferObservationOut | None


class OfferSummaryObservationOut(BaseModel):
    amount: Decimal
    total_amount: Decimal
    currency: str
    condition: OfferCondition
    availability: Availability
    observed_at: str


class OfferSummaryOut(BaseModel):
    id: UUID
    title: str
    image_url: str | None
    last_seen_at: str
    store: StoreOut
    seller: SellerOut | None
    rating: OfferRatingOut | None
    latest_observation: OfferSummaryObservationOut | None


class OfferListResponse(BaseModel):
    items: list[OfferSummaryOut]
    limit: int
    offset: int
    total: int


async def _deny_offer_unavailable(
    session: AsyncSession, *, user: User, offer_id: UUID
) -> NoReturn:
    try:
        deny_resource_unavailable(
            session,
            user,
            Permission.MISSION_READ,
            resource_type="offer",
            resource_id=offer_id,
        )
    except AuthorizationDenied as error:
        await session.commit()
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="offer_access_denied",
            message="Você não tem acesso a esta oferta.",
        ) from error
    raise AssertionError("unreachable")


def _as_response(detail: UserOfferDetail) -> OfferDetailResponse:
    observation = detail.observation
    return OfferDetailResponse(
        id=detail.offer.id,
        title=detail.product.display_name or detail.product.name,
        image_url=detail.offer.image_url,
        original_url=detail.offer.url,
        last_seen_at=detail.offer.last_seen_at.isoformat(),
        store=StoreOut(code=detail.store.code, name=detail.store.name),
        seller=SellerOut(name=detail.seller.name) if detail.seller else None,
        rating=(
            OfferRatingOut(
                average=detail.offer.rating_average,
                review_count=detail.offer.review_count,
                observed_at=detail.offer.rating_observed_at.isoformat(),
            )
            if detail.offer.rating_average is not None
            and detail.offer.review_count is not None
            and detail.offer.rating_observed_at is not None
            else None
        ),
        latest_observation=(
            LatestOfferObservationOut(
                amount=observation.amount,
                currency=observation.currency,
                shipping_amount=observation.shipping_amount,
                total_amount=observation.total_amount,
                fulfillment=observation.fulfillment,
                seller_kind=observation.seller_kind,
                fulfillment_kind=observation.fulfillment_kind,
                condition=observation.condition,
                availability=observation.availability,
                observed_at=observation.observed_at.isoformat(),
                installments=[
                    InstallmentOut(
                        installment_count=item.installment_count,
                        installment_amount=item.installment_amount,
                        installment_total_amount=item.installment_total_amount,
                        discount_percent=item.discount_percent,
                        interest_kind=item.interest_kind,
                        is_highlighted=item.is_highlighted,
                    )
                    for item in detail.installments
                ],
            )
            if observation is not None
            else None
        ),
    )


def _as_summary(detail: UserOfferSummary) -> OfferSummaryOut:
    observation = detail.observation
    return OfferSummaryOut(
        id=detail.offer.id,
        title=detail.product.display_name or detail.product.name,
        image_url=detail.offer.image_url,
        last_seen_at=detail.offer.last_seen_at.isoformat(),
        store=StoreOut(code=detail.store.code, name=detail.store.name),
        seller=SellerOut(name=detail.seller.name) if detail.seller else None,
        rating=(
            OfferRatingOut(
                average=detail.offer.rating_average,
                review_count=detail.offer.review_count,
                observed_at=detail.offer.rating_observed_at.isoformat(),
            )
            if detail.offer.rating_average is not None
            and detail.offer.review_count is not None
            and detail.offer.rating_observed_at is not None
            else None
        ),
        latest_observation=(
            OfferSummaryObservationOut(
                amount=observation.amount,
                total_amount=observation.total_amount,
                currency=observation.currency,
                condition=observation.condition,
                availability=observation.availability,
                observed_at=observation.observed_at.isoformat(),
            )
            if observation is not None
            else None
        ),
    )


@router.get(
    "",
    operation_id="list_user_offers",
    summary="Listar ofertas relevantes acessíveis ao usuário",
)
async def list_offers(
    q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    store: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    condition: OfferCondition | None = None,
    availability: Availability | None = None,
    sort: Literal["recent", "price_asc", "price_desc"] = "recent",
    limit: Annotated[int, Query(ge=1, le=100)] = 24,
    offset: Annotated[int, Query(ge=0)] = 0,
    user: User = Depends(require_web_session),
    session: AsyncSession = Depends(get_web_async_session),
) -> OfferListResponse:
    try:
        authorize(session, user, Permission.MISSION_READ)
    except AuthorizationDenied as error:
        await session.commit()
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="offer_list_access_denied",
            message="Você não tem acesso às ofertas.",
        ) from error
    items, total = await list_user_offers(
        session,
        user_id=user.id,
        search=q,
        store_code=store,
        condition=condition.value if condition else None,
        availability=availability.value if availability else None,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return OfferListResponse(
        items=[_as_summary(item) for item in items],
        limit=limit,
        offset=offset,
        total=total,
    )


@router.get(
    "/{offer_id}",
    operation_id="get_user_offer",
    summary="Consultar detalhe de uma oferta acessível ao usuário",
    response_description="Oferta com o estado comercial mais recente.",
)
async def get_user_offer(
    offer_id: UUID,
    user: User = Depends(require_web_session),
    session: AsyncSession = Depends(get_web_async_session),
) -> OfferDetailResponse:
    try:
        authorize(
            session,
            user,
            Permission.MISSION_READ,
            resource_type="offer",
            resource_id=offer_id,
        )
    except AuthorizationDenied as error:
        await session.commit()
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="offer_access_denied",
            message="Você não tem acesso a esta oferta.",
        ) from error
    detail = await get_offer_detail_for_user(
        session, offer_id=offer_id, user_id=user.id
    )
    if detail is None:
        await _deny_offer_unavailable(session, user=user, offer_id=offer_id)
    return _as_response(detail)
