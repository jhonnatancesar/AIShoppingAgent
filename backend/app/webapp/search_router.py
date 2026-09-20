"""Pesquisa read-only de produtos persistidos na área USER (TASK-099).

Frente 5 (correção de escopo, 2026-09-12): esta é a ÚNICA porta de
entrada de pesquisa que nunca exige missão -- por isso é aqui, não em
`app.missions`, que o histórico real de pesquisas (`SearchReceipt`) é
gravado e consultado. `/trending` alimenta "Veja o que estão
pesquisando" (comunitário, só produtos reconhecidos); `/history/*` é
exclusivo de `Permission.DEV_PANEL_ACCESS`."""

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.collection.contracts import OfferCondition
from app.collection.normalization import Availability
from app.core.config import get_settings
from app.core.errors import ApiError
from app.database.dependency import get_web_async_session
from app.database.time import utc_now
from app.intent.contracts import MISSION_SOURCE_CODES
from app.products.identity import ProductRequestKind, classify_product_request
from app.products.search import ProductSearchHit, search_persisted_products
from app.quotas import QuotaExceededError, check_and_reserve_search_quota_async
from app.quotas.models import SearchReceipt, SearchReceiptProduct
from app.quotas.query import (
    count_all_search_history,
    count_search_history_for_user,
    list_all_search_history,
    list_search_history_for_user,
    list_trending_searched_products,
)
from app.users.models import User
from app.webapp.dependency import require_dev_web_session, require_web_session

router = APIRouter(prefix="/api/v1/product-search", tags=["product-search"])

_MAX_HISTORY_LIMIT = 100


class ProductSearchOfferOut(BaseModel):
    offer_id: UUID
    product_id: UUID
    title: str
    image_url: str | None
    original_url: str
    store_code: str
    store_name: str
    amount: Decimal
    total_amount: Decimal
    currency: str
    condition: OfferCondition
    availability: Availability
    rating_average: Decimal | None
    review_count: int | None


class ProductSearchVariantOut(BaseModel):
    product_id: UUID
    label: str
    attributes: dict[str, str]


class ProductSearchResponse(BaseModel):
    query: str
    request_kind: ProductRequestKind
    offers: list[ProductSearchOfferOut]
    variants: list[ProductSearchVariantOut]


def _offer_out(hit: ProductSearchHit) -> ProductSearchOfferOut:
    return ProductSearchOfferOut(
        offer_id=hit.offer.id,
        product_id=hit.product.id,
        title=hit.product.display_name or hit.product.name,
        image_url=hit.offer.image_url,
        original_url=hit.offer.url,
        store_code=hit.store.code,
        store_name=hit.store.name,
        amount=hit.observation.amount,
        total_amount=hit.observation.total_amount,
        currency=hit.observation.currency,
        condition=hit.observation.condition,
        availability=hit.observation.availability,
        rating_average=hit.offer.rating_average,
        review_count=hit.offer.review_count,
    )


@router.get(
    "",
    operation_id="search_persisted_products",
    summary="Pesquisar produtos conhecidos",
)
async def search_products(
    q: Annotated[str, Query(min_length=2, max_length=2000)],
    stores: Annotated[list[str] | None, Query()] = None,
    user: User = Depends(require_web_session),
    session: AsyncSession = Depends(get_web_async_session),
) -> ProductSearchResponse:
    source_codes = tuple(dict.fromkeys(stores or sorted(MISSION_SOURCE_CODES)))
    unknown = set(source_codes) - MISSION_SOURCE_CODES
    if unknown:
        raise ApiError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="invalid_search_store",
            message="Uma das lojas selecionadas não é válida.",
        )
    # TASK-107: só reserva cota depois da validação de entrada acima --
    # pesquisa inválida nunca consome `max_daily_searches`.
    try:
        receipt = await check_and_reserve_search_quota_async(
            session, user=user, settings=get_settings(), now=utc_now(), query_text=q
        )
    except QuotaExceededError as error:
        raise ApiError(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code="quota_daily_searches_exceeded",
            message=str(error),
            details={
                "kind": error.kind.value,
                "limit": error.limit,
                "current": error.current,
                "actions": list(error.actions),
            },
        ) from error
    identity = classify_product_request(q)
    hits = await search_persisted_products(
        session,
        query=q,
        source_codes=source_codes,
        request_identity=identity,
    )
    # Frente 5: liga esta pesquisa aos produtos RECONHECIDOS que ela
    # retornou -- nunca um resultado ainda sem identidade resolvida. É
    # esta ligação (não `receipt.query_text`) que "Veja o que estão
    # pesquisando" consome depois.
    recognized_product_ids = {
        hit.product.id for hit in hits if hit.product.identity_key is not None
    }
    for product_id in recognized_product_ids:
        session.add(
            SearchReceiptProduct(search_receipt_id=receipt.id, product_id=product_id)
        )
    variants_by_id = {
        hit.product.id: ProductSearchVariantOut(
            product_id=hit.product.id,
            label=hit.product.display_name or hit.product.name,
            attributes=hit.product.attributes or {},
        )
        for hit in hits
        if identity.kind is ProductRequestKind.PRODUCT_FAMILY
        and hit.product.identity_key is not None
    }
    return ProductSearchResponse(
        query=q,
        request_kind=identity.kind,
        offers=[_offer_out(hit) for hit in hits],
        variants=sorted(
            variants_by_id.values(), key=lambda item: (item.label, str(item.product_id))
        ),
    )


# --- Frente 5: histórico real de pesquisas ------------------------------


class TrendingSearchedProductOut(BaseModel):
    product_id: UUID
    display_name: str
    searcher_count: int


class TrendingSearchedProductsResponse(BaseModel):
    """ "Veja o que estão pesquisando" -- nunca carrega `user_id` nem
    texto livre do usuário, só produtos reconhecidos e vetados."""

    items: list[TrendingSearchedProductOut]


@router.get(
    "/trending",
    operation_id="list_trending_searched_products",
    summary="Ver o que estão pesquisando",
    response_description="Produtos reconhecidos em alta, agregados e anônimos.",
)
async def get_trending_searched_products(
    user: User = Depends(require_web_session),
    session: AsyncSession = Depends(get_web_async_session),
) -> TrendingSearchedProductsResponse:
    products = await list_trending_searched_products(session)
    return TrendingSearchedProductsResponse(
        items=[
            TrendingSearchedProductOut(
                product_id=product.product_id,
                display_name=product.display_name,
                searcher_count=product.searcher_count,
            )
            for product in products
        ]
    )


class SearchHistoryItemOut(BaseModel):
    """Item da view DEV -- diferente de `TrendingSearchedProductOut`,
    aqui `user_id`/`query_text` são expostos de propósito: é ferramenta
    de apoio interno (`Permission.DEV_PANEL_ACCESS`), não a view
    comunitária."""

    id: UUID
    user_id: UUID
    query_text: str | None
    created_at: str


class SearchHistoryResponse(BaseModel):
    items: list[SearchHistoryItemOut]
    limit: int
    offset: int
    total: int


def _history_item(receipt: SearchReceipt) -> SearchHistoryItemOut:
    return SearchHistoryItemOut(
        id=receipt.id,
        user_id=receipt.user_id,
        query_text=receipt.query_text,
        created_at=receipt.created_at.isoformat(),
    )


@router.get(
    "/history/mine",
    operation_id="list_my_search_history",
    summary="Minhas pesquisas (DEV)",
    response_description="Página do histórico real de pesquisas do usuário logado.",
)
async def get_my_search_history(
    limit: Annotated[int, Query(ge=1, le=_MAX_HISTORY_LIMIT)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    user: User = Depends(require_dev_web_session),
    session: AsyncSession = Depends(get_web_async_session),
) -> SearchHistoryResponse:
    receipts = await list_search_history_for_user(
        session, user_id=user.id, limit=limit, offset=offset
    )
    total = await count_search_history_for_user(session, user_id=user.id)
    return SearchHistoryResponse(
        items=[_history_item(receipt) for receipt in receipts],
        limit=limit,
        offset=offset,
        total=total,
    )


@router.get(
    "/history/all",
    operation_id="list_all_search_history",
    summary="Todas as pesquisas (DEV)",
    response_description="Página do histórico real de pesquisas de todos os usuários.",
)
async def get_all_search_history(
    limit: Annotated[int, Query(ge=1, le=_MAX_HISTORY_LIMIT)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    user: User = Depends(require_dev_web_session),
    session: AsyncSession = Depends(get_web_async_session),
) -> SearchHistoryResponse:
    receipts = await list_all_search_history(session, limit=limit, offset=offset)
    total = await count_all_search_history(session)
    return SearchHistoryResponse(
        items=[_history_item(receipt) for receipt in receipts],
        limit=limit,
        offset=offset,
        total=total,
    )
