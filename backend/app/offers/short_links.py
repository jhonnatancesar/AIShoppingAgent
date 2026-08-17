"""Criação idempotente de links curtos vinculados a uma Offer."""

import secrets
from urllib.parse import quote
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.urls import normalize_http_url
from app.offers.models import OfferShortLink

_TOKEN_BYTES = 18
_MAX_TOKEN_ATTEMPTS = 5


async def get_or_create_offer_short_link(
    session: AsyncSession, offer_id: UUID
) -> OfferShortLink:
    existing = await session.scalar(
        select(OfferShortLink).where(OfferShortLink.offer_id == offer_id).limit(1)
    )
    if isinstance(existing, OfferShortLink):
        return existing
    for _ in range(_MAX_TOKEN_ATTEMPTS):
        candidate = OfferShortLink(
            token=secrets.token_urlsafe(_TOKEN_BYTES), offer_id=offer_id
        )
        try:
            async with session.begin_nested():
                session.add(candidate)
                await session.flush()
            return candidate
        except IntegrityError:
            winner = await session.scalar(
                select(OfferShortLink)
                .where(OfferShortLink.offer_id == offer_id)
                .limit(1)
            )
            if isinstance(winner, OfferShortLink):
                return winner
    raise RuntimeError("offer_short_link_token_collision")


def build_offer_short_url(public_base_url: str, token: str) -> str:
    base = normalize_http_url(public_base_url)
    if base is None:
        raise ValueError("public base URL must be HTTP/HTTPS")
    return f"{base.rstrip('/')}/r/{quote(token, safe='')}"
