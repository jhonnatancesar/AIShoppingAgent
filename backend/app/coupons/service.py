"""Leitura de cupons -- sem nenhuma lógica de aplicabilidade, F2/F3, alerta,
Telegram ou frontend ainda. Só consulta o que o Coupon Worker já persistiu
diretamente no mesmo PostgreSQL."""

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.coupons.models import Coupon, CouponOfferLink
from app.stores.models import Store

_ACTIVE = "active"
_LIST_ACTIVE_COUPONS_LIMIT = 200


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


async def get_active_coupons_by_store(
    session: AsyncSession, *, store_ids: Iterable[UUID]
) -> dict[UUID, tuple[Coupon, ...]]:
    """Mesmo candidato completo de `get_candidate_coupons_for_offer`
    (vinculado ou não -- `CouponOfferLink` não decide aplicabilidade,
    só agrupamento de apresentação), em lote por `Store` para evitar
    N+1 ao montar uma LISTA de ofertas (achado real: `list_offers`
    nunca calculava cupom nenhum, só o detalhe de uma Offer calculava --
    a listagem é a superfície que o usuário vê primeiro). Uma única
    query por chamada; `pricing.is_coupon_applicable` continua sendo
    quem decide, por Offer, quais desses candidatos realmente se
    aplicam."""
    ids = tuple(dict.fromkeys(store_ids))
    if not ids:
        return {}
    grouped: dict[UUID, list[Coupon]] = {store_id: [] for store_id in ids}
    rows = await session.scalars(
        select(Coupon).where(Coupon.store_id.in_(ids), Coupon.status == _ACTIVE)
    )
    for coupon in rows:
        grouped[coupon.store_id].append(coupon)
    return {store_id: tuple(coupons) for store_id, coupons in grouped.items()}


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


async def list_active_coupons(
    session: AsyncSession, *, limit: int = _LIST_ACTIVE_COUPONS_LIMIT
) -> tuple[tuple[Coupon, Store], ...]:
    """TASK-121: listagem geral de cupons ativos pra aba "Cupons" --
    propósito diferente de `get_active_coupons_by_store`/
    `get_candidate_coupons_for_offer` (que existem pra alimentar a
    avaliação de aplicabilidade cupom<->Offer, `app.coupons.pricing`).
    Aqui não há Offer nem missão nenhuma envolvida -- só "o que o Coupon
    Worker coletou e ainda está ativo", pra navegação livre do usuário.

    `Coupon` não tem `relationship()` pra `Store` (só a FK crua) -- join
    explícito numa única query evita lazy-load implícito (que quebraria
    de qualquer forma em sessão async fora de um contexto síncrono) e
    N+1 (uma query por cupom pra buscar o nome da loja). `limit` é um
    teto defensivo, não paginação -- nenhum cursor/offset é exposto."""
    rows = await session.execute(
        select(Coupon, Store)
        .join(Store, Store.id == Coupon.store_id)
        .where(Coupon.status == _ACTIVE)
        .order_by(Coupon.last_seen_at.desc())
        .limit(limit)
    )
    return tuple(rows.all())
