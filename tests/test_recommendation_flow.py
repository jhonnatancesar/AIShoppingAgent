from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from app.collection.models import PriceObservation
from app.collection.normalization import Availability
from app.missions.models import Mission, MissionCriteria, MissionStatus
from app.offers.models import Offer
from app.products.models import Product
from app.purchase import (
    MissionNotActiveForRecommendationError,
    MissionNotFoundForRecommendationError,
    RecommendationExclusion,
    RecommendationReason,
    RecommendationResult,
    RecommendationStatus,
    compare_offers_for_mission,
    recommend_for_mission,
)
from app.stores.models import Seller, Store, StoreSourceType
from sqlalchemy.dialects import postgresql

NOW = datetime(2026, 8, 8, 15, 0, tzinfo=UTC)


class _ExecuteResult:
    def __init__(self, *, one=None, rows=None) -> None:
        self._one = one
        self._rows = rows or []

    def one_or_none(self):
        return self._one

    def all(self):
        return self._rows


class _SessionStub:
    def __init__(self, mission_row, rows) -> None:
        self._results = [
            _ExecuteResult(one=mission_row),
            _ExecuteResult(rows=rows),
        ]
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)
        return self._results.pop(0)


def _mission(
    *, status: MissionStatus = MissionStatus.ACTIVE, currency: str | None = "BRL"
) -> tuple[Mission, MissionCriteria]:
    mission = Mission(
        id=UUID(int=100),
        user_id=uuid4(),
        title="Notebook",
        status=status,
    )
    criteria = MissionCriteria(
        id=uuid4(),
        mission_id=mission.id,
        search_query="notebook",
        target_amount=Decimal("5000") if currency is not None else None,
        target_currency=currency,
    )
    return mission, criteria


def _row(
    number: int,
    *,
    total: str,
    shipping: str | None = "10",
    currency: str = "BRL",
    availability: Availability = Availability.AVAILABLE,
    observed_at: datetime = NOW,
    seller: bool = False,
):
    store = Store(
        id=UUID(int=1000 + number),
        code=f"store_{number}",
        name=f"Store {number}",
        base_url=f"https://store{number}.example",
        source_type=(
            StoreSourceType.MARKETPLACE if seller else StoreSourceType.RETAILER
        ),
    )
    product = Product(
        id=UUID(int=2000 + number),
        name=f"Product {number}",
        brand="Brand",
        model=f"Model {number}",
    )
    marketplace_seller = (
        Seller(
            id=UUID(int=3000 + number),
            store_id=store.id,
            external_id=f"seller-{number}",
            name=f"Seller {number}",
        )
        if seller
        else None
    )
    offer = Offer(
        id=UUID(int=4000 + number),
        product_id=product.id,
        store_id=store.id,
        seller_id=(marketplace_seller.id if marketplace_seller is not None else None),
        external_id=f"offer-{number}",
        url=f"https://store{number}.example/offer",
    )
    shipping_amount = Decimal(shipping) if shipping is not None else None
    amount = Decimal(total) - (shipping_amount or Decimal("0"))
    observation = PriceObservation(
        id=UUID(int=5000 + number),
        offer_id=offer.id,
        collection_run_id=uuid4(),
        amount=amount,
        shipping_amount=shipping_amount,
        total_amount=Decimal(total),
        currency=currency,
        fulfillment="Loja" if not seller else "Marketplace",
        availability=availability,
        observed_at=observed_at,
        recorded_at=observed_at + timedelta(seconds=1),
    )
    return observation, offer, product, store, marketplace_seller


def _recommend(rows, *, currency: str | None = "BRL") -> RecommendationResult:
    mission, criteria = _mission(currency=currency)
    session = _SessionStub((mission, criteria), rows)
    return recommend_for_mission(  # type: ignore[arg-type]
        session, mission.id, owner_user_id=mission.user_id
    )


def _compare(rows, *, currency: str | None = "BRL"):
    mission, criteria = _mission(currency=currency)
    session = _SessionStub((mission, criteria), rows)
    return compare_offers_for_mission(  # type: ignore[arg-type]
        session, mission.id, owner_user_id=mission.user_id
    )


def test_recommendation_selects_lowest_determinable_total_and_keeps_evidence() -> None:
    eligible = _row(1, total="90", seller=False)
    previous = _row(
        101,
        total="120",
        observed_at=NOW - timedelta(days=1),
        seller=False,
    )
    previous = (
        previous[0],
        eligible[1],
        eligible[2],
        eligible[3],
        eligible[4],
    )
    previous[0].offer_id = eligible[1].id
    unknown_shipping = _row(2, total="50", shipping=None, seller=True)
    other_currency = _row(3, total="40", currency="USD")
    unavailable = _row(4, total="30", availability=Availability.UNAVAILABLE)

    result = _recommend(
        [eligible, previous, unknown_shipping, other_currency, unavailable]
    )

    assert result.status is RecommendationStatus.RECOMMENDED
    assert result.reason is None
    assert result.recommendation is not None
    assert result.recommendation.offer_id == eligible[1].id
    assert len(result.evidence) == 4
    assert result.recommendation.seller_id is None
    assert result.recommendation.seller_name is None
    assert result.recommendation.history.observation_count == 2
    assert result.recommendation.history.comparable_observation_count == 2
    assert result.recommendation.history.previous_observation_id == previous[0].id
    assert result.recommendation.history.lowest_observation_id == eligible[0].id

    by_offer = {item.offer_id: item for item in result.evidence}
    assert by_offer[unknown_shipping[1].id].seller_name == "Seller 2"
    assert by_offer[unknown_shipping[1].id].exclusions == (
        RecommendationExclusion.SHIPPING_UNKNOWN,
    )
    assert by_offer[other_currency[1].id].exclusions == (
        RecommendationExclusion.CURRENCY_MISMATCH,
    )
    assert by_offer[unavailable[1].id].exclusions == (
        RecommendationExclusion.UNAVAILABLE,
    )


def test_equal_totals_prefer_fresher_observation_then_offer_id() -> None:
    older = _row(1, total="100", observed_at=NOW - timedelta(minutes=1))
    newer_high_id = _row(3, total="100", observed_at=NOW)
    newer_low_id = _row(2, total="100", observed_at=NOW)

    result = _recommend([older, newer_high_id, newer_low_id])

    assert result.recommendation is not None
    assert result.recommendation.offer_id == newer_low_id[1].id


@pytest.mark.parametrize(
    ("rows", "currency", "reason"),
    [
        ([], "BRL", RecommendationReason.NO_OBSERVATIONS),
        (
            [_row(1, total="100", availability=Availability.UNAVAILABLE)],
            "BRL",
            RecommendationReason.NO_AVAILABLE_OFFERS,
        ),
        (
            [_row(1, total="100", currency="USD")],
            "BRL",
            RecommendationReason.NO_CURRENCY_COMPATIBLE_OFFERS,
        ),
        (
            [_row(1, total="100", shipping=None)],
            "BRL",
            RecommendationReason.NO_DETERMINABLE_TOTALS,
        ),
        (
            [_row(1, total="100")],
            None,
            RecommendationReason.MISSION_CURRENCY_MISSING,
        ),
    ],
)
def test_insufficient_data_has_explicit_reason(rows, currency, reason) -> None:
    result = _recommend(rows, currency=currency)

    assert result.status is RecommendationStatus.INSUFFICIENT_DATA
    assert result.recommendation is None
    assert result.reason is reason


def test_latest_observation_controls_current_eligibility() -> None:
    latest = _row(
        1,
        total="80",
        availability=Availability.UNAVAILABLE,
        observed_at=NOW,
    )
    older = _row(
        101,
        total="70",
        availability=Availability.AVAILABLE,
        observed_at=NOW - timedelta(days=1),
    )
    older = (older[0], latest[1], latest[2], latest[3], latest[4])
    older[0].offer_id = latest[1].id

    result = _recommend([latest, older])

    assert result.reason is RecommendationReason.NO_AVAILABLE_OFFERS
    assert result.evidence[0].history.comparable_observation_count == 1
    assert result.evidence[0].history.lowest_observation_id == older[0].id


def test_recommendation_rejects_missing_or_inactive_mission() -> None:
    missing_session = _SessionStub(None, [])
    with pytest.raises(MissionNotFoundForRecommendationError):
        recommend_for_mission(  # type: ignore[arg-type]
            missing_session, UUID(int=100), owner_user_id=UUID(int=200)
        )

    mission, criteria = _mission(status=MissionStatus.PAUSED)
    inactive_session = _SessionStub((mission, criteria), [])
    with pytest.raises(MissionNotActiveForRecommendationError):
        recommend_for_mission(  # type: ignore[arg-type]
            inactive_session, mission.id, owner_user_id=mission.user_id
        )


def test_recommendation_query_is_restricted_to_mission_sources_and_success() -> None:
    mission, criteria = _mission()
    session = _SessionStub((mission, criteria), [])

    recommend_for_mission(  # type: ignore[arg-type]
        session, mission.id, owner_user_id=mission.user_id
    )

    sql = str(
        session.statements[1].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "JOIN mission_sources" in sql
    assert "collection_runs.mission_id" in sql
    assert "collection_runs.status = 'succeeded'" in sql
    assert "collection_runs.store_id = stores.id" in sql
    assert "ORDER BY offers.id ASC" in sql
    mission_sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "missions.user_id" in mission_sql
    assert str(mission.user_id) in mission_sql


def test_recommendation_result_enforces_consistent_terminal_shape() -> None:
    mission_id = uuid4()
    with pytest.raises(ValueError, match="requires a reason"):
        RecommendationResult(
            mission_id=mission_id,
            status=RecommendationStatus.INSUFFICIENT_DATA,
            currency="BRL",
            recommendation=None,
            reason=None,
            evidence=(),
        )


def test_comparison_position_one_is_the_same_recommendation_for_same_data() -> None:
    older = _row(1, total="100", observed_at=NOW - timedelta(minutes=1))
    lower = _row(2, total="90", observed_at=NOW)
    same_total_newer = _row(3, total="100", observed_at=NOW)
    unknown_shipping = _row(4, total="50", shipping=None)
    rows = [older, lower, same_total_newer, unknown_shipping]

    recommendation = _recommend(rows)
    comparison = _compare(rows)

    assert recommendation.recommendation is not None
    assert comparison.recommendation_offer_id == recommendation.recommendation.offer_id
    assert comparison.items[0].offer_id == recommendation.recommendation.offer_id
    assert [item.position for item in comparison.items] == [1, 2, 3, None]
    assert [item.total_amount for item in comparison.items[:3]] == [
        Decimal("90"),
        Decimal("100"),
        Decimal("100"),
    ]


def test_comparison_never_exposes_unknown_shipping_as_total() -> None:
    unknown_shipping = _row(1, total="50", shipping=None)

    comparison = _compare([unknown_shipping])

    item = comparison.items[0]
    assert comparison.status is RecommendationStatus.INSUFFICIENT_DATA
    assert comparison.reason is RecommendationReason.NO_DETERMINABLE_TOTALS
    assert item.amount == Decimal("50")
    assert item.shipping_amount is None
    assert item.total_amount is None
    assert item.position is None
    assert item.exclusions == (RecommendationExclusion.SHIPPING_UNKNOWN,)


def test_ineligible_comparison_order_never_uses_price() -> None:
    store_two_cheapest = _row(2, total="1", shipping=None)
    store_one_expensive = _row(1, total="9999", shipping=None)

    comparison = _compare([store_two_cheapest, store_one_expensive])

    assert [item.store_code for item in comparison.items] == ["store_1", "store_2"]
    assert all(item.position is None for item in comparison.items)
    assert all(item.total_amount is None for item in comparison.items)


def test_comparison_keeps_all_ineligible_reasons_without_ranking() -> None:
    unavailable = _row(
        1,
        total="80",
        availability=Availability.UNAVAILABLE,
    )
    wrong_currency = _row(2, total="70", currency="USD")

    comparison = _compare([wrong_currency, unavailable])

    assert comparison.status is RecommendationStatus.INSUFFICIENT_DATA
    assert comparison.recommendation_offer_id is None
    assert all(not item.eligible and item.position is None for item in comparison.items)
    assert {item.exclusions for item in comparison.items} == {
        (RecommendationExclusion.UNAVAILABLE,),
        (RecommendationExclusion.CURRENCY_MISMATCH,),
    }
