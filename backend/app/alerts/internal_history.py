"""Melhor preço interno confiável por `Product` (TASK-113, §33.7).

Correção pós-plano (ponto 9): o conceito é "melhor preço confiável
NOSSO para o Product exato", sempre PRODUCT-GLOBAL -- nunca filtrado por
`MissionOfferRelevance.classification == MATCH`, que é uma relevância
POR MISSION (duas Missions podem classificar a mesma `Offer` de forma
diferente, DEC do TASK-063), não um critério comercial objetivo do
Product. Critérios usados são só os que já existem no modelo real:
`PriceObservation.condition` (nunca aceitar `USED`/`REFURBISHED` como
piso -- só `NEW`), `PriceObservation.availability` (só `AVAILABLE`) e
moeda igual à de referência. Sempre `PriceObservation.amount` (preço à
vista), nunca `total_amount`/parcelado -- mesma base de DEC-045 já usada
pelo evaluator.
"""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collection.contracts import OfferCondition
from app.collection.models import PriceObservation
from app.collection.normalization import Availability
from app.offers.models import Offer


@dataclass(frozen=True, slots=True)
class InternalHistoricalBest:
    amount: Decimal
    currency: str


async def get_internal_historical_best(
    session: AsyncSession, *, product_id: UUID, currency: str
) -> InternalHistoricalBest | None:
    """`None` quando não há nenhuma observação comparável -- nunca
    inventa um piso a partir de dado usado/indisponível/moeda diferente."""
    row = (
        await session.execute(
            select(PriceObservation.amount, PriceObservation.currency)
            .join(Offer, Offer.id == PriceObservation.offer_id)
            .where(
                Offer.product_id == product_id,
                PriceObservation.condition == OfferCondition.NEW,
                PriceObservation.availability == Availability.AVAILABLE,
                PriceObservation.currency == currency,
            )
            .order_by(PriceObservation.amount.asc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    return InternalHistoricalBest(amount=row.amount, currency=row.currency)
