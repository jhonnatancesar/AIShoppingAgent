"""Listagem geral de cupons ativos da área USER (TASK-121).

Propósito diferente de `webapp/offers_router.py::list_offers`, que já
calcula `applied_coupon` por Offer (`DEC-129`) -- aqui não há Offer nem
missão nenhuma envolvida, é a aba "Cupons" navegável livremente, com o
que o Coupon Worker coletou e ainda está ativo. Nenhuma regra de
aplicabilidade cupom<->Offer é avaliada neste endpoint."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.coupons.service import list_active_coupons
from app.database.dependency import get_web_async_session
from app.users.models import User
from app.webapp.dependency import require_web_session

router = APIRouter(prefix="/api/v1/coupons", tags=["coupons"])


class CouponStoreOut(BaseModel):
    code: str
    name: str


class CouponOut(BaseModel):
    id: UUID
    store: CouponStoreOut
    code: str | None
    discount_kind: str | None
    discount_value: Decimal | None
    minimum_purchase_amount: Decimal | None
    raw_rule_text: str | None
    valid_until: str | None
    """Texto cru do worker -- nunca parseado em data (ver
    `app.coupons.models.Coupon.valid_until`)."""
    scope_kind: str | None
    source_url: str | None
    """URL real de onde o worker coletou a evidência -- repassada crua;
    validação de esquema (http/https) e decisão de virar link ficam
    inteiramente no frontend (TASK-121, achado da revisão de cupons)."""
    last_seen_at: datetime
    """Só prova que o worker viu este cupom recentemente -- nunca prova
    validade (ver `valid_until`, texto separado, informado pela loja)."""


@router.get(
    "",
    operation_id="list_active_coupons",
    summary="Listar cupons ativos coletados",
)
async def list_coupons(
    user: User = Depends(require_web_session),
    session: AsyncSession = Depends(get_web_async_session),
    settings: Settings = Depends(get_settings),
) -> list[CouponOut]:
    if not settings.coupons_enabled:
        return []
    rows = await list_active_coupons(session)
    return [
        CouponOut(
            id=coupon.id,
            store=CouponStoreOut(code=store.code, name=store.name),
            code=coupon.code or None,
            discount_kind=coupon.discount_kind,
            discount_value=coupon.discount_value,
            minimum_purchase_amount=coupon.minimum_purchase_amount,
            raw_rule_text=coupon.raw_rule_text,
            valid_until=coupon.valid_until,
            scope_kind=coupon.scope_kind,
            source_url=coupon.source_url or None,
            last_seen_at=coupon.last_seen_at,
        )
        for coupon, store in rows
    ]
