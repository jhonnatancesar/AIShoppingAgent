"""Leitura de cupons -- sem nenhuma lógica de aplicabilidade, F2/F3, alerta,
Telegram ou frontend ainda. Só consulta o que o Coupon Worker já persistiu
diretamente no mesmo PostgreSQL."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.coupons.models import Coupon, CouponOfferLink

_ACTIVE = "active"


async def get_coupons_for_offer(
    session: AsyncSession, *, offer_id: UUID
) -> tuple[Coupon, ...]:
    """Cupons ativos já vinculados (`CouponOfferLink`) a esta `Offer` --
    o vínculo só existe depois que o GG (fase futura) decidir a
    aplicabilidade; esta função nunca decide isso, só lê o que já foi
    associado."""
    rows = await session.scalars(
        select(Coupon)
        .join(CouponOfferLink, CouponOfferLink.coupon_id == Coupon.id)
        .where(CouponOfferLink.offer_id == offer_id, Coupon.status == _ACTIVE)
    )
    return tuple(rows)


async def get_candidate_coupons_for_offer(
    session: AsyncSession, *, offer_id: UUID, store_id: UUID
) -> tuple[Coupon, ...]:
    """União dos dois grupos de candidatos que a avaliação de
    aplicabilidade (`app.coupons.pricing`) precisa: cupons já vinculados
    a esta `Offer` + cupons genéricos ainda sem vínculo da mesma `Store`.
    Nenhuma regra de aplicabilidade decidida aqui -- só reúne
    candidatos; `pricing.is_coupon_applicable` decide o resto."""
    linked = await get_coupons_for_offer(session, offer_id=offer_id)
    unlinked = await get_unlinked_coupons_for_store(session, store_id=store_id)
    return linked + unlinked


async def get_unlinked_coupons_for_store(
    session: AsyncSession, *, store_id: UUID
) -> tuple[Coupon, ...]:
    """Cupons ativos desta loja sem NENHUM vínculo de `Offer` ainda --
    candidatos genéricos, ainda não avaliados. `NOT EXISTS` em vez de
    `LEFT JOIN ... IS NULL` para não precisar de `DISTINCT` caso um
    cupom já tenha múltiplos vínculos."""
    rows = await session.scalars(
        select(Coupon).where(
            Coupon.store_id == store_id,
            Coupon.status == _ACTIVE,
            ~select(CouponOfferLink.id)
            .where(CouponOfferLink.coupon_id == Coupon.id)
            .exists(),
        )
    )
    return tuple(rows)
