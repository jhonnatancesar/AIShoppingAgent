from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.collection.history import (
    PriceHistoryQueryError,
    get_latest_price_observation,
    list_price_history,
)
from app.collection.models import PriceObservation
from app.collection.normalization import Availability
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session


class _ScalarResult:
    def __init__(self, values: list[PriceObservation]) -> None:
        self._values = values

    def all(self) -> list[PriceObservation]:
        return self._values

    def first(self) -> PriceObservation | None:
        return self._values[0] if self._values else None


class _SessionStub:
    def __init__(self, observations: list[PriceObservation], total: int = 0) -> None:
        self.observations = observations
        self.total = total
        self.statements = []

    def scalar(self, statement):
        self.statements.append(statement)
        return self.total

    def scalars(self, statement):
        self.statements.append(statement)
        return _ScalarResult(self.observations)


def _observation() -> PriceObservation:
    return PriceObservation(
        id=UUID(int=1),
        offer_id=uuid4(),
        collection_run_id=uuid4(),
        amount=10,
        currency="BRL",
        total_amount=10,
        availability=Availability.AVAILABLE,
        observed_at=datetime.now(UTC),
    )


def test_list_price_history_builds_filtered_deterministic_page() -> None:
    observation = _observation()
    session = _SessionStub([observation], total=3)
    started = datetime.now(UTC) - timedelta(days=1)

    page = list_price_history(
        session,  # type: ignore[arg-type]
        observation.offer_id,
        observed_from=started,
        availability=Availability.AVAILABLE,
        limit=10,
        offset=2,
    )

    assert page.items == (observation,)
    assert (page.limit, page.offset, page.total) == (10, 2, 3)
    sql = str(
        session.statements[1].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "price_observations.offer_id" in sql
    assert "price_observations.observed_at >=" in sql
    assert "price_observations.availability" in sql
    assert (
        "ORDER BY price_observations.observed_at DESC, price_observations.id ASC" in sql
    )
    assert "LIMIT 10 OFFSET 2" in sql


def test_latest_price_observation_returns_first_result() -> None:
    observation = _observation()
    session = _SessionStub([observation])

    assert (
        get_latest_price_observation(
            session,
            observation.offer_id,  # type: ignore[arg-type]
        )
        is observation
    )


@pytest.mark.parametrize(
    ("limit", "offset"),
    [(0, 0), (101, 0), (1, -1)],
)
def test_history_rejects_invalid_pagination(limit: int, offset: int) -> None:
    with pytest.raises(PriceHistoryQueryError):
        list_price_history(Session(), uuid4(), limit=limit, offset=offset)


def test_history_rejects_invalid_or_naive_period() -> None:
    now = datetime.now(UTC)
    with pytest.raises(PriceHistoryQueryError):
        list_price_history(Session(), uuid4(), observed_from=datetime.now())
    with pytest.raises(PriceHistoryQueryError):
        list_price_history(
            Session(),
            uuid4(),
            observed_from=now,
            observed_to=now - timedelta(seconds=1),
        )
