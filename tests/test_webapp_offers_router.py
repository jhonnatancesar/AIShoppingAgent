"""Contrato e ownership da página USER de oferta (TASK-095)."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.collection.contracts import (
    InstallmentInterestKind,
    MarketplacePartyKind,
    OfferCondition,
)
from app.collection.models import OfferInstallmentOption, PriceObservation
from app.collection.normalization import Availability
from app.core.errors import register_api_error_handler
from app.database.dependency import get_web_async_session
from app.offers.models import Offer
from app.offers.query import (
    UserComparisonOffer,
    UserOfferComparison,
    UserOfferDetail,
    comparison_offers_statement,
    get_offer_detail_for_user,
    offer_for_user_statement,
)
from app.products.models import Product
from app.stores.models import Seller, Store
from app.users.models import User, UserRole
from app.webapp.dependency import WEB_SESSION_COOKIE_NAME
from app.webapp.offers_router import _as_comparison, router
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

NOW = datetime(2026, 8, 22, 15, 0, tzinfo=UTC)


def _user() -> User:
    return User(id=uuid4(), display_name="USER", role=UserRole.USER, username="user")


def _detail() -> UserOfferDetail:
    store = Store(
        id=uuid4(), code="amazon", name="Amazon", base_url="https://amazon.com.br"
    )
    seller = Seller(id=uuid4(), store_id=store.id, name="Amazon.com.br")
    product = Product(id=uuid4(), name="Título bruto", display_name="Galaxy S24 Ultra")
    offer = Offer(
        id=uuid4(),
        product_id=product.id,
        store_id=store.id,
        seller_id=seller.id,
        url="https://amazon.com.br/dp/example",
        image_url="https://images.example/offer.jpg",
        rating_average=Decimal("4.80"),
        review_count=2256,
        rating_observed_at=NOW,
        last_seen_at=NOW,
    )
    observation = PriceObservation(
        id=uuid4(),
        offer_id=offer.id,
        collection_run_id=uuid4(),
        amount=Decimal("4599.00"),
        currency="BRL",
        shipping_amount=Decimal("20.00"),
        total_amount=Decimal("4619.00"),
        fulfillment="Amazon.com.br",
        seller_kind=MarketplacePartyKind.PLATFORM,
        fulfillment_kind=MarketplacePartyKind.PLATFORM,
        condition=OfferCondition.NEW,
        availability=Availability.AVAILABLE,
        observed_at=NOW,
    )
    installment = OfferInstallmentOption(
        id=uuid4(),
        price_observation_id=observation.id,
        installment_count=12,
        installment_amount=Decimal("383.25"),
        interest_kind=InstallmentInterestKind.INTEREST_FREE,
        is_highlighted=True,
    )
    return UserOfferDetail(offer, product, store, seller, observation, (installment,))


@pytest.fixture
def session() -> MagicMock:
    value = MagicMock()
    value.commit = AsyncMock()
    value.add = MagicMock()
    return value


@pytest.fixture
def client(session: MagicMock, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = FastAPI()
    register_api_error_handler(app)
    app.include_router(router)
    app.dependency_overrides[get_web_async_session] = lambda: session
    owner = _user()
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user", lambda *a, **k: owner
    )
    return TestClient(app)


def _cookies() -> dict[str, str]:
    return {WEB_SESSION_COOKIE_NAME: "task095-web-session"}


def test_user_accesses_offer_linked_to_own_mission(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    detail = _detail()
    monkeypatch.setattr(
        "app.webapp.offers_router.get_offer_detail_for_user",
        AsyncMock(return_value=detail),
    )

    response = client.get(f"/api/v1/offers/{detail.offer.id}", cookies=_cookies())

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Galaxy S24 Ultra"
    assert body["rating"] == {
        "average": "4.80",
        "review_count": 2256,
        "observed_at": NOW.isoformat(),
    }
    assert body["latest_observation"]["amount"] == "4599.00"
    assert body["latest_observation"]["installments"][0]["installment_count"] == 12


def test_other_user_cannot_access_offer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.webapp.offers_router.get_offer_detail_for_user",
        AsyncMock(return_value=None),
    )

    response = client.get(f"/api/v1/offers/{uuid4()}", cookies=_cookies())

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "offer_access_denied"


def test_no_match_does_not_grant_access(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    statement = offer_for_user_statement(offer_id=uuid4(), user_id=uuid4())
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "'match', 'possible_match'" in sql
    assert "'no_match'" not in sql
    assert "missions.user_id" in sql
    monkeypatch.setattr(
        "app.webapp.offers_router.get_offer_detail_for_user",
        AsyncMock(return_value=None),
    )

    response = client.get(f"/api/v1/offers/{uuid4()}", cookies=_cookies())

    assert response.status_code == 403


def test_offer_query_uses_latest_price_observation() -> None:
    expected = _detail()
    execute_result = MagicMock()
    execute_result.first.return_value = (
        expected.offer,
        expected.product,
        expected.store,
        expected.seller,
    )
    session = MagicMock()
    session.execute = AsyncMock(return_value=execute_result)
    session.scalar = AsyncMock(return_value=expected.observation)
    session.scalars = AsyncMock(return_value=list(expected.installments))

    result = asyncio.run(
        get_offer_detail_for_user(session, offer_id=expected.offer.id, user_id=uuid4())
    )

    assert result is not None
    assert result.observation is expected.observation
    latest_statement = session.scalar.await_args.args[0]
    sql = str(latest_statement.compile(dialect=postgresql.dialect()))
    assert "price_observations.observed_at DESC" in sql
    assert "price_observations.id DESC" in sql


def test_comparison_query_requires_same_product_and_user_owned_relevance() -> None:
    product_id, user_id = uuid4(), uuid4()
    sql = str(
        comparison_offers_statement(product_id=product_id, user_id=user_id).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    ).lower()
    assert f"offers.product_id = '{product_id}'" in sql
    assert f"missions.user_id = '{user_id}'" in sql
    assert "classification in ('match', 'possible_match')" in sql
    assert "no_match" not in sql


def test_comparison_response_preserves_source_bound_data() -> None:
    detail = _detail()
    detail.product.identity_key = "resolved-key"
    detail.product.variant = "512 GB"
    response = _as_comparison(
        UserOfferComparison(
            product=detail.product,
            offers=(
                UserComparisonOffer(
                    detail.offer,
                    detail.store,
                    detail.seller,
                    detail.observation,
                    detail.installments,
                ),
            ),
        )
    )
    assert response.comparable is True
    assert response.variant == "512 GB"
    assert response.offers[0].store.code == "amazon"
    assert response.offers[0].rating is not None
    assert response.offers[0].latest_observation is not None
    assert response.offers[0].latest_observation.total_amount == Decimal("4619.00")
