"""Detalhe de oferta da área USER (TASK-095)."""

import logging
from datetime import UTC, date, datetime
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
from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.coupons.pricing import AppliedCoupon, best_applicable_coupon
from app.coupons.service import get_candidate_coupons_for_offer
from app.database.dependency import get_web_async_session
from app.offers.presentation import (
    resolve_offer_display_title,
    resolve_offer_image_chain,
)
from app.offers.query import (
    OfferPriceHistory,
    PriceHistoryPeriod,
    UserOfferComparison,
    UserOfferDetail,
    UserOfferSummary,
    get_offer_comparison_for_user,
    get_offer_detail_for_user,
    get_offer_price_history_for_user,
    list_user_offers,
)
from app.users.models import User
from app.webapp.dependency import require_web_session

router = APIRouter(prefix="/api/v1/offers", tags=["offers"])
logger = logging.getLogger("app.webapp.offers_router")


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


class AppliedCouponOut(BaseModel):
    """Só presente quando um cupom REALMENTE se aplica (nunca porque
    existe no banco) -- `app.coupons.pricing.best_applicable_coupon`."""

    code: str | None
    discount_kind: str
    original_amount: Decimal
    discount_amount: Decimal
    final_amount: Decimal
    currency: str


class OfferDetailResponse(BaseModel):
    id: UUID
    title: str
    image_url: str | None
    image_fallback_url: str | None
    original_url: str
    last_seen_at: str
    store: StoreOut
    seller: SellerOut | None
    rating: OfferRatingOut | None
    latest_observation: LatestOfferObservationOut | None
    applied_coupon: AppliedCouponOut | None = None


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
    image_fallback_url: str | None
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


class ComparisonOfferOut(BaseModel):
    id: UUID
    original_url: str
    image_url: str | None
    image_fallback_url: str | None
    store: StoreOut
    seller: SellerOut | None
    rating: OfferRatingOut | None
    latest_observation: LatestOfferObservationOut | None


class OfferComparisonResponse(BaseModel):
    product_id: UUID
    title: str
    variant: str | None
    attributes: dict[str, str]
    comparable: bool
    offers: list[ComparisonOfferOut]


class PriceHistoryPointOut(BaseModel):
    date: date
    amount: Decimal


class PriceHistorySeriesOut(BaseModel):
    store_id: UUID
    store_code: str
    store_name: str
    points: list[PriceHistoryPointOut]


class PriceHistoryMetricsOut(BaseModel):
    current_amount: Decimal | None
    min_amount: Decimal | None
    max_amount: Decimal | None
    average_amount: Decimal | None
    variation_percent: Decimal | None


class PriceHistoryResponse(BaseModel):
    product_id: UUID
    comparable: bool
    reason: str | None
    period: PriceHistoryPeriod
    currency: str | None
    period_from: datetime | None
    period_to: datetime
    series: list[PriceHistorySeriesOut]
    metrics: PriceHistoryMetricsOut | None


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


def _as_response(
    detail: UserOfferDetail, applied_coupon: AppliedCoupon | None = None
) -> OfferDetailResponse:
    observation = detail.observation
    image_url, image_fallback_url = resolve_offer_image_chain(
        detail.offer, detail.product
    )
    return OfferDetailResponse(
        id=detail.offer.id,
        title=resolve_offer_display_title(detail.product, detail.observation),
        image_url=image_url,
        image_fallback_url=image_fallback_url,
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
        applied_coupon=(
            AppliedCouponOut(
                code=applied_coupon.code or None,
                discount_kind=applied_coupon.discount_kind,
                original_amount=applied_coupon.original_amount,
                discount_amount=applied_coupon.discount_amount,
                final_amount=applied_coupon.final_amount,
                currency=applied_coupon.currency,
            )
            if applied_coupon is not None
            else None
        ),
    )


def _as_summary(detail: UserOfferSummary) -> OfferSummaryOut:
    observation = detail.observation
    image_url, image_fallback_url = resolve_offer_image_chain(
        detail.offer, detail.product
    )
    return OfferSummaryOut(
        id=detail.offer.id,
        title=resolve_offer_display_title(detail.product, detail.observation),
        image_url=image_url,
        image_fallback_url=image_fallback_url,
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


def _as_comparison(comparison: UserOfferComparison) -> OfferComparisonResponse:
    offers = []
    for item in comparison.offers:
        observation = item.observation
        rating = (
            OfferRatingOut(
                average=item.offer.rating_average,
                review_count=item.offer.review_count,
                observed_at=item.offer.rating_observed_at.isoformat(),
            )
            if item.offer.rating_average is not None
            and item.offer.review_count is not None
            and item.offer.rating_observed_at is not None
            else None
        )
        item_image_url, item_image_fallback_url = resolve_offer_image_chain(
            item.offer, comparison.product
        )
        offers.append(
            ComparisonOfferOut(
                id=item.offer.id,
                original_url=item.offer.url,
                image_url=item_image_url,
                image_fallback_url=item_image_fallback_url,
                store=StoreOut(code=item.store.code, name=item.store.name),
                seller=SellerOut(name=item.seller.name) if item.seller else None,
                rating=rating,
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
                                installment_count=option.installment_count,
                                installment_amount=option.installment_amount,
                                installment_total_amount=option.installment_total_amount,
                                discount_percent=option.discount_percent,
                                interest_kind=option.interest_kind,
                                is_highlighted=option.is_highlighted,
                            )
                            for option in item.installments
                        ],
                    )
                    if observation is not None
                    else None
                ),
            )
        )
    product = comparison.product
    return OfferComparisonResponse(
        product_id=product.id,
        title=product.display_name or product.name,
        variant=product.variant,
        attributes=product.attributes or {},
        comparable=product.identity_key is not None,
        offers=offers,
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
    "/{offer_id}/comparison",
    operation_id="compare_user_offer",
    summary="Comparar a mesma variante entre lojas",
)
async def compare_offer(
    offer_id: UUID,
    user: User = Depends(require_web_session),
    session: AsyncSession = Depends(get_web_async_session),
) -> OfferComparisonResponse:
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
            code="offer_comparison_access_denied",
            message="Você não tem acesso a esta comparação.",
        ) from error
    comparison = await get_offer_comparison_for_user(
        session, offer_id=offer_id, user_id=user.id
    )
    if comparison is None:
        await _deny_offer_unavailable(session, user=user, offer_id=offer_id)
    return _as_comparison(comparison)


def _as_price_history(history: OfferPriceHistory) -> PriceHistoryResponse:
    return PriceHistoryResponse(
        product_id=history.product_id,
        comparable=history.comparable,
        reason=history.reason,
        period=history.period,
        currency=history.currency,
        period_from=history.period_from,
        period_to=history.period_to,
        series=[
            PriceHistorySeriesOut(
                store_id=series.store_id,
                store_code=series.store_code,
                store_name=series.store_name,
                points=[
                    PriceHistoryPointOut(date=point.day, amount=point.amount)
                    for point in series.points
                ],
            )
            for series in history.series
        ],
        metrics=(
            PriceHistoryMetricsOut(
                current_amount=history.metrics.current_amount,
                min_amount=history.metrics.min_amount,
                max_amount=history.metrics.max_amount,
                average_amount=history.metrics.average_amount,
                variation_percent=history.metrics.variation_percent,
            )
            if history.metrics is not None
            else None
        ),
    )


@router.get(
    "/{offer_id}/price-history",
    operation_id="get_user_offer_price_history",
    summary="Histórico de preço do produto ancorado nesta oferta",
    response_description=(
        "Série diária por loja e métricas de mercado do Product associado a"
        " esta Offer, autorizado pela mesma Offer. `period` inválido produz"
        " 422 nativo do FastAPI (validação estrutural de query param), não"
        ' o envelope `{"error": ...}` desta API.'
    ),
)
async def get_user_offer_price_history(
    offer_id: UUID,
    period: PriceHistoryPeriod = "1m",
    user: User = Depends(require_web_session),
    session: AsyncSession = Depends(get_web_async_session),
) -> PriceHistoryResponse:
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
    history = await get_offer_price_history_for_user(
        session,
        offer_id=offer_id,
        user_id=user.id,
        period=period,
        now=datetime.now(UTC),
    )
    if history is None:
        await _deny_offer_unavailable(session, user=user, offer_id=offer_id)
    return _as_price_history(history)


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
    settings: Settings = Depends(get_settings),
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
    applied_coupon = None
    if settings.coupons_enabled and detail.observation is not None:
        try:
            candidates = await get_candidate_coupons_for_offer(
                session, offer_id=detail.offer.id, store_id=detail.offer.store_id
            )
            applied_coupon = best_applicable_coupon(
                detail.offer,
                candidates,
                detail.observation.amount,
                detail.observation.currency,
            )
        except Exception:
            # Consumo de cupons é uma etapa derivada da exibição, nunca
            # parte crítica dela -- falha aqui nunca impede a Offer de
            # aparecer normalmente.
            logger.warning(
                "coupon_lookup_failed", extra={"offer_id": str(offer_id)}, exc_info=True
            )
    return _as_response(detail, applied_coupon)
