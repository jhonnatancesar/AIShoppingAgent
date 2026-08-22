"""Testes focados da área geral USER de ofertas (TASK-100)."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from app.collection.contracts import OfferCondition
from app.collection.models import PriceObservation
from app.collection.normalization import Availability
from app.offers.models import Offer
from app.offers.query import UserOfferSummary, user_offers_statement
from app.products.models import Product
from app.stores.models import Seller, Store, StoreSourceType
from app.webapp.offers_router import _as_summary
from sqlalchemy.dialects import postgresql

NOW = datetime(2026, 8, 22, 19, 0, tzinfo=UTC)


def test_offer_list_statement_is_user_scoped_and_excludes_no_match() -> None:
    statement = user_offers_statement(user_id=uuid4())
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    ).lower()

    assert "exists" in sql
    assert "missions.user_id" in sql
    assert "mission_offer_relevance.classification in ('match', 'possible_match')" in sql
    assert "no_match" not in sql


def test_offer_list_uses_latest_observation_contract_without_internal_data() -> None:
    store = Store(
        id=uuid4(),
        code="amazon",
        name="Amazon",
        base_url="https://amazon.com.br",
        source_type=StoreSourceType.MARKETPLACE,
    )
    product = Product(id=uuid4(), name="Galaxy S24 Ultra", display_name="Galaxy S24 Ultra 512 GB")
    seller = Seller(id=uuid4(), store_id=store.id, name="Amazon.com.br")
    offer = Offer(
        id=uuid4(),
        product_id=product.id,
        store_id=store.id,
        seller_id=seller.id,
        url="https://amazon.com.br/dp/example",
        image_url="https://images.example/s24.jpg",
        last_seen_at=NOW,
        rating_average=Decimal("4.8"),
        review_count=125,
        rating_observed_at=NOW,
    )
    observation = PriceObservation(
        id=uuid4(),
        offer_id=offer.id,
        collection_run_id=uuid4(),
        amount=Decimal("4599.00"),
        total_amount=Decimal("4619.00"),
        currency="BRL",
        shipping_amount=Decimal("20.00"),
        condition=OfferCondition.NEW,
        availability=Availability.AVAILABLE,
        observed_at=NOW,
    )

    response = _as_summary(
        UserOfferSummary(
            offer=offer,
            product=product,
            store=store,
            seller=seller,
            observation=observation,
        )
    )

    assert response.id == offer.id
    assert response.title == "Galaxy S24 Ultra 512 GB"
    assert response.latest_observation is not None
    assert response.latest_observation.amount == Decimal("4599.00")
    assert response.latest_observation.condition is OfferCondition.NEW
    assert not hasattr(response, "original_url")
