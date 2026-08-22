"""Pesquisa read-only de produtos persistidos na área USER (TASK-099)."""

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.collection.contracts import OfferCondition
from app.collection.normalization import Availability
from app.core.errors import ApiError
from app.database.dependency import get_web_async_session
from app.intent.contracts import MISSION_SOURCE_CODES
from app.products.identity import ProductRequestKind, classify_product_request
from app.products.search import ProductSearchHit, search_persisted_products
from app.users.models import User
from app.webapp.dependency import require_web_session

router = APIRouter(prefix="/api/v1/product-search", tags=["product-search"])


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


@router.get("", operation_id="search_persisted_products", summary="Pesquisar produtos conhecidos")
async def search_products(
    q: Annotated[str, Query(min_length=2, max_length=2000)],
    stores: Annotated[list[str] | None, Query()] = None,
    _user: User = Depends(require_web_session),
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
    identity = classify_product_request(q)
    hits = await search_persisted_products(
        session,
        query=q,
        source_codes=source_codes,
        request_identity=identity,
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
