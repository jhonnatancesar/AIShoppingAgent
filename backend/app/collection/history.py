"""Consultas somente leitura sobre o histórico imutável de preços."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.collection.models import PriceObservation
from app.collection.normalization import Availability


class PriceHistoryQueryError(ValueError):
    """Indica parâmetros inválidos para uma consulta histórica."""


@dataclass(frozen=True, slots=True)
class PriceHistoryPage:
    """Página estável de observações e sua contagem total filtrada."""

    items: tuple[PriceObservation, ...]
    limit: int
    offset: int
    total: int


def list_price_history(
    session: Session,
    offer_id: UUID,
    *,
    observed_from: datetime | None = None,
    observed_to: datetime | None = None,
    availability: Availability | None = None,
    limit: int = 50,
    offset: int = 0,
) -> PriceHistoryPage:
    """Lista o histórico de uma oferta do mais recente para o mais antigo."""
    _validate_page(limit, offset)
    _validate_period(observed_from, observed_to)

    filters = [PriceObservation.offer_id == offer_id]
    if observed_from is not None:
        filters.append(PriceObservation.observed_at >= observed_from)
    if observed_to is not None:
        filters.append(PriceObservation.observed_at <= observed_to)
    if availability is not None:
        filters.append(PriceObservation.availability == availability)

    total = session.scalar(
        select(func.count()).select_from(PriceObservation).where(*filters)
    )
    items = session.scalars(
        select(PriceObservation)
        .where(*filters)
        .order_by(PriceObservation.observed_at.desc(), PriceObservation.id.asc())
        .limit(limit)
        .offset(offset)
    ).all()
    return PriceHistoryPage(tuple(items), limit, offset, total or 0)


def get_latest_price_observation(
    session: Session, offer_id: UUID
) -> PriceObservation | None:
    """Retorna a observação mais recente da oferta sem alterar seu histórico."""
    return session.scalars(
        select(PriceObservation)
        .where(PriceObservation.offer_id == offer_id)
        .order_by(PriceObservation.observed_at.desc(), PriceObservation.id.asc())
        .limit(1)
    ).first()


def _validate_page(limit: int, offset: int) -> None:
    if not 1 <= limit <= 100:
        raise PriceHistoryQueryError("limit must be between 1 and 100")
    if offset < 0:
        raise PriceHistoryQueryError("offset must be non-negative")


def _validate_period(
    observed_from: datetime | None, observed_to: datetime | None
) -> None:
    for value in (observed_from, observed_to):
        if value is not None and value.utcoffset() is None:
            raise PriceHistoryQueryError("history timestamps must include a timezone")
    if (
        observed_from is not None
        and observed_to is not None
        and observed_from > observed_to
    ):
        raise PriceHistoryQueryError("observed_from must not exceed observed_to")
