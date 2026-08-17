"""Redirect público e fail-closed para links de ofertas."""

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.urls import is_url_compatible_with_store
from app.database.dependency import get_session
from app.offers.models import Offer, OfferShortLink
from app.stores.models import Store

router = APIRouter(tags=["offers"])


@router.get("/r/{token}", include_in_schema=False, response_model=None)
def redirect_offer(
    token: str,
    request: Request,
    session: Session = Depends(get_session),
) -> RedirectResponse | JSONResponse:
    if request.query_params or not token or len(token) > 64:
        return JSONResponse(
            {"detail": "invalid short link"}, status_code=status.HTTP_400_BAD_REQUEST
        )
    link = session.get(OfferShortLink, token)
    if link is None:
        return JSONResponse(
            {"detail": "short link not found"}, status_code=status.HTTP_404_NOT_FOUND
        )
    offer = session.get(Offer, link.offer_id)
    if offer is None:
        return JSONResponse(
            {"detail": "short link unavailable"},
            status_code=status.HTTP_410_GONE,
        )
    store = session.get(Store, offer.store_id)
    if store is None or not is_url_compatible_with_store(offer.url, store.base_url):
        return JSONResponse(
            {"detail": "short link destination rejected"},
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    return RedirectResponse(
        offer.url,
        status_code=status.HTTP_302_FOUND,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )
