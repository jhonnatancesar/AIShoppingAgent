"""Detalhe de oferta da área USER (TASK-095)."""

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal, NoReturn
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai_provider.contracts import AIProviderManager
from app.ai_provider.manager import (
    build_admin_dev_ai_provider_manager,
    build_user_ai_provider_manager,
)
from app.alerts.internal_history import get_internal_historical_best
from app.authorization import (
    AuthorizationDenied,
    Permission,
    ai_profile_for_user,
    authorize,
    deny_resource_unavailable,
    permissions_for_role,
)
from app.collection.contracts import (
    InstallmentInterestKind,
    MarketplacePartyKind,
    OfferCondition,
)
from app.collection.normalization import Availability
from app.collection.relevance import OfferRelevance
from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.coupons.pricing import AppliedCoupon, best_applicable_coupon
from app.coupons.service import (
    get_active_coupons_by_store,
    get_candidate_coupons_for_offer,
)
from app.database.dependency import (
    get_web_async_session,
    get_web_async_session_factory,
)
from app.historical_bootstrap.models import HistoricalBootstrapStatus
from app.historical_bootstrap.service import (
    ClaimedHistoricalBootstrap,
    ManualSearchAvailability,
    claim_manual_historical_bootstrap,
    get_external_price_reference_evidence,
    get_historical_bootstrap_state,
    manual_search_availability,
    run_claimed_historical_bootstrap,
)
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
    get_offer_comparison_for_dev,
    get_offer_comparison_for_user,
    get_offer_detail_for_dev,
    get_offer_detail_for_user,
    get_offer_price_history_for_dev,
    get_offer_price_history_for_user,
    list_all_offers_dev,
    list_user_offers,
)
from app.products.models import Product
from app.search.cesar_core_fetch import CesarCoreFetchProvider
from app.search.manager import build_web_search_manager
from app.users.models import User, UserRole
from app.webapp.dependency import require_web_session

router = APIRouter(prefix="/api/v1/offers", tags=["offers"])
logger = logging.getLogger("app.webapp.offers_router")


class StoreOut(BaseModel):
    id: UUID
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
    payment_method: str | None = None


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
    applied_coupon: AppliedCouponOut | None = None
    latest_observation: OfferSummaryObservationOut | None
    classification: OfferRelevance | None = None
    """TASK-126: só populado em `all_users=true` (DEV) -- `None` no modo
    normal, nunca um valor inventado. Relevância bruta mais recente da
    oferta, incluindo `NO_MATCH` (nunca aparece fora do modo DEV)."""


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


class HistoricalPriceInternalOut(BaseModel):
    """Menor preço já registrado pelo próprio GG (só novo/disponível)."""

    amount: Decimal
    currency: str


class HistoricalPriceReferenceOut(BaseModel):
    """Menor preço histórico externo já coletado (hardwarebarato.com e
    busca genérica, via César Core) -- `historical_date` é `None` quando a
    fonte não trouxe data, nunca inventada."""

    amount: Decimal
    currency: str
    source: str
    source_url: str
    store_name: str | None
    historical_date: date | None
    collected_at: str


class HistoricalPriceSearchOut(BaseModel):
    availability: ManualSearchAvailability
    last_status: HistoricalBootstrapStatus | None
    last_completed_at: str | None
    next_allowed_at: str | None
    """Só preenchido quando a busca está bloqueada pela janela de
    revalidação (`blocked_recent`/`requires_force`)."""


class HistoricalPriceResponse(BaseModel):
    product_id: UUID
    internal: HistoricalPriceInternalOut | None
    reference: HistoricalPriceReferenceOut | None
    search: HistoricalPriceSearchOut


class HistoricalPriceSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = False
    """Só tem efeito para DEV (depois da confirmação na tela); ignorado
    para qualquer outro papel -- nunca eleva o acesso de ninguém."""


_HISTORICAL_SEARCH_REFUSALS: dict[ManualSearchAvailability, tuple[str, str]] = {
    ManualSearchAvailability.REQUIRES_FORCE: (
        "historical_price_force_required",
        "O preço ainda está dentro do limite de 90 dias. Confirme para "
        "pesquisar mesmo assim.",
    ),
    ManualSearchAvailability.BLOCKED_RECENT: (
        "historical_price_recently_searched",
        "Este produto já foi pesquisado recentemente.",
    ),
    ManualSearchAvailability.IN_PROGRESS: (
        "historical_price_search_in_progress",
        "Já existe uma busca de preço histórico em andamento.",
    ),
    ManualSearchAvailability.NO_IDENTITY: (
        "historical_price_no_identity",
        "Produto ainda sem identidade reconhecida -- não dá para buscar "
        "preço histórico com segurança.",
    ),
    ManualSearchAvailability.DISABLED: (
        "historical_price_search_disabled",
        "A busca de preço histórico está desativada no momento.",
    ),
}


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


def _has_dev_access(user: User) -> bool:
    """TASK-126/127: DEV abre detalhe/comparação/gráfico de qualquer
    oferta (a mesma que já vê na listagem com `all_users=true`). Checagem
    pura por papel -- nunca gera auditoria de negação para USER, que
    continua caindo em `_deny_offer_unavailable` como sempre."""
    return Permission.DEV_PANEL_ACCESS in permissions_for_role(
        getattr(user, "role", None)
    )


async def _offer_detail_for_viewer(
    session: AsyncSession, *, user: User, offer_id: UUID
) -> UserOfferDetail | None:
    detail = await get_offer_detail_for_user(
        session, offer_id=offer_id, user_id=user.id
    )
    if detail is None and _has_dev_access(user):
        detail = await get_offer_detail_for_dev(session, offer_id=offer_id)
    return detail


def _as_applied_coupon_out(
    applied_coupon: AppliedCoupon | None,
) -> AppliedCouponOut | None:
    if applied_coupon is None:
        return None
    return AppliedCouponOut(
        code=applied_coupon.code or None,
        discount_kind=applied_coupon.discount_kind,
        original_amount=applied_coupon.original_amount,
        discount_amount=applied_coupon.discount_amount,
        final_amount=applied_coupon.final_amount,
        currency=applied_coupon.currency,
    )


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
        store=StoreOut(
            id=detail.store.id, code=detail.store.code, name=detail.store.name
        ),
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
                        payment_method=item.payment_method,
                        is_highlighted=item.is_highlighted,
                    )
                    for item in detail.installments
                ],
            )
            if observation is not None
            else None
        ),
        applied_coupon=_as_applied_coupon_out(applied_coupon),
    )


def _as_summary(
    detail: UserOfferSummary,
    applied_coupon: AppliedCoupon | None = None,
    classification: OfferRelevance | None = None,
) -> OfferSummaryOut:
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
        store=StoreOut(
            id=detail.store.id, code=detail.store.code, name=detail.store.name
        ),
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
        applied_coupon=_as_applied_coupon_out(applied_coupon),
        classification=classification,
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
                store=StoreOut(
                    id=item.store.id, code=item.store.code, name=item.store.name
                ),
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
                                payment_method=option.payment_method,
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
    all_users: bool = False,
    user: User = Depends(require_web_session),
    session: AsyncSession = Depends(get_web_async_session),
    settings: Settings = Depends(get_settings),
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
    # TASK-126: `all_users` é DEV-only -- achado real (caso do 9800X3D/
    # Kabum/CPUPROMO) mostrou que não existia NENHUMA tela mostrando os
    # itens `NO_MATCH`/de outros usuários já coletados. Filtro desligado
    # por padrão (comportamento idêntico a antes desta TASK); quando
    # ligado, troca `list_user_offers` (posse + `ACCESSIBLE_RELEVANCE`)
    # por `list_all_offers_dev` (qualquer usuário, qualquer classificação,
    # `NO_MATCH` incluso) -- mesmos `OfferSummaryOut`/`OfferCard`, só a
    # fonte de dado muda.
    if all_users:
        try:
            authorize(session, user, Permission.DEV_PANEL_ACCESS)
        except AuthorizationDenied as error:
            await session.commit()
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="dev_access_denied",
                message="Você não tem acesso a esta área.",
            ) from error
        items, total = await list_all_offers_dev(
            session,
            search=q,
            store_code=store,
            condition=condition.value if condition else None,
            availability=availability.value if availability else None,
            sort=sort,
            limit=limit,
            offset=offset,
        )
    else:
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
    # FASE G (achado real, 2026-09-08): `list_offers` nunca calculava
    # `applied_coupon` -- só `get_user_offer` (detalhe de UMA Offer)
    # fazia isso. A listagem é a primeira superfície que o usuário vê;
    # sem isso, 214 cupons reais persistidos em PROD nunca apareciam no
    # site. Uma consulta em lote por Store (nunca N+1); nunca crítica --
    # falha aqui não pode impedir a listagem normal de aparecer.
    coupons_by_offer_id: dict[UUID, AppliedCoupon] = {}
    if settings.coupons_enabled and items:
        try:
            coupons_by_store = await get_active_coupons_by_store(
                session, store_ids=(item.offer.store_id for item in items)
            )
            for item in items:
                if item.observation is None:
                    continue
                applied = best_applicable_coupon(
                    item.offer,
                    coupons_by_store.get(item.offer.store_id, ()),
                    item.observation.amount,
                    item.observation.currency,
                )
                if applied is not None:
                    coupons_by_offer_id[item.offer.id] = applied
        except Exception:
            logger.warning("coupon_list_lookup_failed", exc_info=True)
    return OfferListResponse(
        items=[
            _as_summary(
                item,
                applied_coupon=coupons_by_offer_id.get(item.offer.id),
                classification=getattr(item, "classification", None),
            )
            for item in items
        ],
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
    if comparison is None and _has_dev_access(user):
        comparison = await get_offer_comparison_for_dev(session, offer_id=offer_id)
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
    store_ids: Annotated[
        list[UUID] | None,
        Query(
            description=(
                "Restringe série e métricas às lojas selecionadas (ids de"
                " Store). Omitido/vazio = todas as lojas acessíveis, mesmo"
                " comportamento anterior."
            )
        ),
    ] = None,
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
    now = datetime.now(UTC)
    selected_store_ids = frozenset(store_ids) if store_ids else None
    history = await get_offer_price_history_for_user(
        session,
        offer_id=offer_id,
        user_id=user.id,
        period=period,
        now=now,
        store_ids=selected_store_ids,
    )
    if history is None and _has_dev_access(user):
        history = await get_offer_price_history_for_dev(
            session,
            offer_id=offer_id,
            period=period,
            now=now,
            store_ids=selected_store_ids,
        )
    if history is None:
        await _deny_offer_unavailable(session, user=user, offer_id=offer_id)
    return _as_price_history(history)


async def _historical_price_response(
    session: AsyncSession,
    *,
    product: Product,
    user: User,
    settings: Settings,
    now: datetime,
) -> HistoricalPriceResponse:
    """TASK-127: tudo lido direto do banco a cada chamada -- preço que já
    foi coletado aparece na hora, sem depender de ninguém clicar no botão."""
    internal = await get_internal_historical_best(
        session, product_id=product.id, currency="BRL"
    )
    reference = await get_external_price_reference_evidence(
        session, product_id=product.id, currency="BRL"
    )
    bootstrap = await get_historical_bootstrap_state(session, product_id=product.id)
    availability = manual_search_availability(
        # Sem a credencial do César Core a busca nem consegue rodar --
        # tratado como desativada (nunca uma reserva órfã em PROCESSING).
        enabled=settings.historical_bootstrap_enabled
        and settings.cesar_core_api_key_file is not None,
        has_identity=product.identity_key is not None,
        bootstrap=bootstrap,
        now=now,
        revalidation_days=settings.historical_bootstrap_revalidation_days,
        can_force=_has_dev_access(user),
    )
    completed_at = bootstrap.completed_at if bootstrap is not None else None
    next_allowed_at = (
        completed_at + timedelta(days=settings.historical_bootstrap_revalidation_days)
        if completed_at is not None
        and availability
        in (
            ManualSearchAvailability.BLOCKED_RECENT,
            ManualSearchAvailability.REQUIRES_FORCE,
        )
        else None
    )
    return HistoricalPriceResponse(
        product_id=product.id,
        internal=(
            HistoricalPriceInternalOut(
                amount=internal.amount, currency=internal.currency
            )
            if internal is not None
            else None
        ),
        reference=(
            HistoricalPriceReferenceOut(
                amount=reference.amount,
                currency=reference.currency,
                source=reference.source,
                source_url=reference.safe_url,
                store_name=reference.store_name,
                historical_date=reference.historical_date,
                collected_at=reference.collected_at.isoformat(),
            )
            if reference is not None
            else None
        ),
        search=HistoricalPriceSearchOut(
            availability=availability,
            last_status=bootstrap.status if bootstrap is not None else None,
            last_completed_at=completed_at.isoformat() if completed_at else None,
            next_allowed_at=next_allowed_at.isoformat() if next_allowed_at else None,
        ),
    )


async def _authorized_offer_detail(
    session: AsyncSession, *, user: User, offer_id: UUID
) -> UserOfferDetail:
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
    detail = await _offer_detail_for_viewer(session, user=user, offer_id=offer_id)
    if detail is None:
        await _deny_offer_unavailable(session, user=user, offer_id=offer_id)
    return detail


@router.get(
    "/{offer_id}/historical-price",
    operation_id="get_offer_historical_price",
    summary="Preço histórico do produto desta oferta (GG + referência externa)",
)
async def get_offer_historical_price(
    offer_id: UUID,
    user: User = Depends(require_web_session),
    session: AsyncSession = Depends(get_web_async_session),
    settings: Settings = Depends(get_settings),
) -> HistoricalPriceResponse:
    detail = await _authorized_offer_detail(session, user=user, offer_id=offer_id)
    return await _historical_price_response(
        session,
        product=detail.product,
        user=user,
        settings=settings,
        now=datetime.now(UTC),
    )


async def _run_manual_historical_search(
    session_factory: async_sessionmaker[AsyncSession],
    claimed: ClaimedHistoricalBootstrap,
    *,
    ai: AIProviderManager,
    fetch: CesarCoreFetchProvider | None,
    profile: UserRole,
    settings: Settings,
    now: datetime,
) -> None:
    await run_claimed_historical_bootstrap(
        session_factory,
        claimed,
        search=lambda: build_web_search_manager(settings),
        fetch=fetch,
        ai=ai,
        profile=profile,
        now=now,
        failure_backoff_minutes=settings.market_assessment_failure_backoff_minutes,
        failure_backoff_max_minutes=settings.market_assessment_failure_backoff_max_minutes,
    )


@router.post(
    "/{offer_id}/historical-price/search",
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="search_offer_historical_price",
    summary="Disparar a busca de preço histórico deste produto",
    response_description=(
        "Busca reservada e iniciada em segundo plano -- consulte "
        "`GET /{offer_id}/historical-price` até `search.availability` "
        "deixar de ser `in_progress`."
    ),
)
async def search_offer_historical_price(
    offer_id: UUID,
    payload: HistoricalPriceSearchRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_web_session),
    session: AsyncSession = Depends(get_web_async_session),
    session_factory: async_sessionmaker[AsyncSession] = Depends(
        get_web_async_session_factory
    ),
    settings: Settings = Depends(get_settings),
) -> HistoricalPriceResponse:
    """TASK-127: fora do fluxo de coleta -- funciona mesmo para produto de
    missão pausada/encerrada. A reserva (claim) é feita aqui, numa
    transação curta; a busca em si (Search/Fetch/IA via César Core, que
    pode levar minutos) roda depois da resposta."""
    detail = await _authorized_offer_detail(session, user=user, offer_id=offer_id)
    now = datetime.now(UTC)
    current = await _historical_price_response(
        session, product=detail.product, user=user, settings=settings, now=now
    )
    force = payload.force and _has_dev_access(user)
    availability = current.search.availability
    if not (
        availability is ManualSearchAvailability.AVAILABLE
        or (availability is ManualSearchAvailability.REQUIRES_FORCE and force)
    ):
        code, message = _HISTORICAL_SEARCH_REFUSALS[availability]
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code=code,
            message=message,
            details={"search": current.search.model_dump(mode="json")},
        )
    # Perfil de IA pelo papel real: clique de USER gasta a cota USER,
    # clique de ADMIN/DEV gasta a de ADMIN/DEV (mesma regra do César Core).
    profile = ai_profile_for_user(session, user)
    ai = (
        build_user_ai_provider_manager(settings)
        if profile is UserRole.USER
        else build_admin_dev_ai_provider_manager(settings)
    )
    fetch = CesarCoreFetchProvider(
        api_key_file=settings.cesar_core_api_key_file,
        base_url=settings.cesar_core_base_url,
        service=settings.cesar_core_service,
        service_class=settings.cesar_core_service_class,
        timeout_seconds=settings.cesar_core_fetch_timeout_seconds,
    )
    claimed = await claim_manual_historical_bootstrap(
        session_factory,
        product_id=detail.product.id,
        now=now,
        revalidation_days=settings.historical_bootstrap_revalidation_days,
        lease_seconds=settings.market_assessment_lease_seconds,
        force=force,
    )
    if claimed is None:
        # Corrida real: outro clique (ou a própria coleta) reservou entre
        # a leitura acima e o claim -- devolve o estado novo, nunca uma
        # segunda busca paralela.
        refreshed = await _historical_price_response(
            session, product=detail.product, user=user, settings=settings, now=now
        )
        code, message = _HISTORICAL_SEARCH_REFUSALS.get(
            refreshed.search.availability,
            _HISTORICAL_SEARCH_REFUSALS[ManualSearchAvailability.IN_PROGRESS],
        )
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code=code,
            message=message,
            details={"search": refreshed.search.model_dump(mode="json")},
        )
    background_tasks.add_task(
        _run_manual_historical_search,
        session_factory,
        claimed,
        ai=ai,
        fetch=fetch,
        profile=profile,
        settings=settings,
        now=now,
    )
    return await _historical_price_response(
        session, product=detail.product, user=user, settings=settings, now=now
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
    detail = await _offer_detail_for_viewer(session, user=user, offer_id=offer_id)
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
