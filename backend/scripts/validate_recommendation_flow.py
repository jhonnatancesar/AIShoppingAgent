"""Valida a TASK-038 contra PostgreSQL real sem deixar dados persistidos."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.collection.models import (
    CollectionRun,
    CollectionRunStatus,
    PriceObservation,
)
from app.collection.normalization import Availability
from app.core.config import Settings
from app.database.session import create_database_engine, create_session_factory
from app.missions.models import (
    Mission,
    MissionCriteria,
    MissionSource,
    MissionStatus,
)
from app.offers.models import Offer
from app.products.models import Product
from app.purchase import (
    RecommendationExclusion,
    RecommendationReason,
    RecommendationStatus,
    recommend_for_mission,
)
from app.stores.models import Seller, Store
from app.users.models import User, UserRole
from sqlalchemy import func, select


def validate() -> None:
    settings = Settings()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    session = session_factory()
    transaction = session.begin()
    try:
        stores = {
            store.code: store
            for store in session.scalars(
                select(Store).where(Store.code.in_(("pichau", "amazon", "kabum")))
            )
        }
        if set(stores) != {"pichau", "amazon", "kabum"}:
            raise RuntimeError("required V1 stores are not seeded")

        user = User(
            display_name="Validação temporária TASK-038",
            role=UserRole.USER,
            is_active=True,
        )
        session.add(user)
        session.flush()
        mission = _mission(session, user, stores["pichau"], stores["amazon"])
        now = datetime.now(UTC)

        recommended = _offer(session, stores["pichau"], "eligible")
        competitor = _offer(session, stores["pichau"], "competitor")
        marketplace_seller = Seller(
            store_id=stores["amazon"].id,
            external_id=f"task-038-{user.id}",
            name="Vendedor temporário TASK-038",
        )
        session.add(marketplace_seller)
        session.flush()
        unknown_shipping = _offer(
            session,
            stores["amazon"],
            "unknown-shipping",
            seller=marketplace_seller,
        )
        wrong_currency = _offer(
            session,
            stores["amazon"],
            "wrong-currency",
            seller=marketplace_seller,
        )
        unselected = _offer(session, stores["kabum"], "unselected")

        pichau_run = _run(session, mission, stores["pichau"], now)
        amazon_run = _run(session, mission, stores["amazon"], now)
        kabum_run = _run(session, mission, stores["kabum"], now)
        previous = _observation(
            session,
            recommended,
            pichau_run,
            amount="3000",
            shipping="100",
            observed_at=now - timedelta(days=1),
        )
        current = _observation(
            session,
            recommended,
            pichau_run,
            amount="2800",
            shipping="100",
            observed_at=now,
        )
        _observation(
            session,
            competitor,
            pichau_run,
            amount="2850",
            shipping="100",
            observed_at=now,
        )
        _observation(
            session,
            unknown_shipping,
            amazon_run,
            amount="2000",
            shipping=None,
            observed_at=now,
        )
        _observation(
            session,
            wrong_currency,
            amazon_run,
            amount="1000",
            shipping="10",
            currency="USD",
            observed_at=now,
        )
        _observation(
            session,
            unselected,
            kabum_run,
            amount="500",
            shipping="10",
            observed_at=now,
        )
        session.flush()

        result = recommend_for_mission(session, mission.id)
        if result.status is not RecommendationStatus.RECOMMENDED:
            raise RuntimeError(f"unexpected recommendation status: {result.status}")
        if (
            result.recommendation is None
            or result.recommendation.offer_id != recommended.id
        ):
            raise RuntimeError("lowest eligible total was not recommended")
        if result.recommendation.seller_id is not None:
            raise RuntimeError("retailer seller should remain optional")
        if len(result.evidence) != 4:
            raise RuntimeError("selected-source evidence set is incorrect")
        if any(item.offer_id == unselected.id for item in result.evidence):
            raise RuntimeError("unselected source leaked into recommendation evidence")
        by_offer = {item.offer_id: item for item in result.evidence}
        if by_offer[unknown_shipping.id].exclusions != (
            RecommendationExclusion.SHIPPING_UNKNOWN,
        ):
            raise RuntimeError("unknown shipping was not excluded")
        if (
            RecommendationExclusion.CURRENCY_MISMATCH
            not in by_offer[wrong_currency.id].exclusions
        ):
            raise RuntimeError("incompatible currency was not excluded")
        history = result.recommendation.history
        if (
            history.previous_observation_id != previous.id
            or history.lowest_observation_id != current.id
        ):
            raise RuntimeError("historical evidence is not identifiable")

        insufficient = _mission(session, user, stores["pichau"])
        insufficient_offer = _offer(session, stores["pichau"], "only-unknown-shipping")
        insufficient_run = _run(session, insufficient, stores["pichau"], now)
        _observation(
            session,
            insufficient_offer,
            insufficient_run,
            amount="1500",
            shipping=None,
            observed_at=now,
        )
        session.flush()
        insufficient_result = recommend_for_mission(session, insufficient.id)
        if (
            insufficient_result.status is not RecommendationStatus.INSUFFICIENT_DATA
            or insufficient_result.reason
            is not RecommendationReason.NO_DETERMINABLE_TOTALS
        ):
            raise RuntimeError("unknown-only totals did not return insufficient_data")

        print(
            "TASK-038 PostgreSQL validation passed:",
            {
                "status": result.status.value,
                "evidence_count": len(result.evidence),
                "history_count": history.observation_count,
                "insufficient_reason": insufficient_result.reason.value,
            },
        )
    finally:
        transaction.rollback()
        session.close()
        verification_session = session_factory()
        try:
            temporary_users = verification_session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.display_name == "Validação temporária TASK-038")
            )
            temporary_products = verification_session.scalar(
                select(func.count())
                .select_from(Product)
                .where(Product.name.like("Produto temporário %"))
            )
            if temporary_users or temporary_products:
                raise RuntimeError("TASK-038 validation left temporary data")
        finally:
            verification_session.close()
            engine.dispose()


def _mission(session, user: User, *stores: Store) -> Mission:
    mission = Mission(
        user_id=user.id,
        title="Validação temporária TASK-038",
        status=MissionStatus.ACTIVE,
        state_version=1,
    )
    session.add(mission)
    session.flush()
    session.add(
        MissionCriteria(
            mission_id=mission.id,
            search_query="notebook",
            target_amount=Decimal("5000"),
            target_currency="BRL",
        )
    )
    session.add_all(
        MissionSource(mission_id=mission.id, store_id=store.id) for store in stores
    )
    session.flush()
    return mission


def _offer(
    session,
    store: Store,
    suffix: str,
    *,
    seller: Seller | None = None,
) -> Offer:
    product = Product(name=f"Produto temporário {suffix}")
    session.add(product)
    session.flush()
    offer = Offer(
        product_id=product.id,
        store_id=store.id,
        seller_id=seller.id if seller is not None else None,
        external_id=f"task-038-{suffix}",
        url=f"{store.base_url.rstrip('/')}/task-038-{suffix}",
    )
    session.add(offer)
    session.flush()
    return offer


def _run(
    session, mission: Mission, store: Store, started_at: datetime
) -> CollectionRun:
    run = CollectionRun(
        mission_id=mission.id,
        store_id=store.id,
        status=CollectionRunStatus.SUCCEEDED,
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=1),
    )
    session.add(run)
    session.flush()
    return run


def _observation(
    session,
    offer: Offer,
    run: CollectionRun,
    *,
    amount: str,
    shipping: str | None,
    observed_at: datetime,
    currency: str = "BRL",
) -> PriceObservation:
    item_amount = Decimal(amount)
    shipping_amount = Decimal(shipping) if shipping is not None else None
    observation = PriceObservation(
        offer_id=offer.id,
        collection_run_id=run.id,
        amount=item_amount,
        shipping_amount=shipping_amount,
        total_amount=item_amount + (shipping_amount or Decimal("0")),
        currency=currency,
        availability=Availability.AVAILABLE,
        observed_at=observed_at,
        raw_evidence={"validation": "TASK-038"},
    )
    session.add(observation)
    session.flush()
    return observation


if __name__ == "__main__":
    validate()
