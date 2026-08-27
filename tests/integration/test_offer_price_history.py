"""TASK-098: histórico de preço por Product ancorado em Offer, contra
PostgreSQL real -- prova ownership via EXISTS correlato, agregação
diária por (Store, dia comercial), resolução de `current_amount` sem
ressuscitar preço obsoleto, resolução determinística de moeda e o caso
`comparable=false` (identidade não resolvida). Sem mocks de sessão --
o que `tests/test_webapp_offers_router.py` cobre com SQL compilado, este
arquivo prova executando contra o banco de verdade. Roda só via
`python scripts/run_integration_tests.py`.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from app.collection.contracts import OfferCondition
from app.collection.models import (
    CollectionRun,
    CollectionRunStatus,
    MissionOfferRelevance,
    PriceObservation,
)
from app.collection.normalization import Availability
from app.collection.relevance import OfferRelevance
from app.missions.models import Mission, MissionStatus
from app.offers.models import Offer
from app.offers.query import get_offer_price_history_for_user
from app.products.identity import IDENTITY_VERSION, resolve_product_variant
from app.products.models import Product
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy import select

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 27, 15, 0, tzinfo=UTC)


def _user(sessions) -> User:
    with sessions.begin() as session:
        user = User(display_name="TASK-098 synthetic", role=UserRole.USER)
        session.add(user)
        session.flush()
        session.expunge(user)
        return user


def _mission(sessions, *, user_id) -> Mission:
    with sessions.begin() as session:
        mission = Mission(
            user_id=user_id, title="TASK-098 mission", status=MissionStatus.ACTIVE
        )
        session.add(mission)
        session.flush()
        session.expunge(mission)
        return mission


def _store(sessions, *, code: str) -> Store:
    with sessions() as session:
        return session.scalar(select(Store).where(Store.code == code))


def _product(sessions, *, comparable: bool, title: str) -> Product:
    with sessions.begin() as session:
        if comparable:
            variant = resolve_product_variant(title)
            assert variant is not None
            product = Product(
                name=title,
                brand=variant.brand,
                model=variant.model,
                category=variant.category,
                family=variant.family,
                variant=variant.variant,
                attributes=dict(variant.attributes),
                family_key=variant.family_key,
                identity_key=variant.identity_key,
                identity_version=IDENTITY_VERSION,
            )
        else:
            product = Product(name=title)
        session.add(product)
        session.flush()
        session.expunge(product)
        return product


def _offer(sessions, *, product_id, store_id) -> Offer:
    with sessions.begin() as session:
        offer = Offer(
            product_id=product_id,
            store_id=store_id,
            url=f"https://example.invalid/task098-{uuid4().hex[:12]}",
        )
        session.add(offer)
        session.flush()
        session.expunge(offer)
        return offer


def _link(sessions, *, mission_id, offer_id) -> None:
    with sessions.begin() as session:
        session.add(
            MissionOfferRelevance(
                mission_id=mission_id,
                offer_id=offer_id,
                classification=OfferRelevance.MATCH,
                classified_at=NOW,
            )
        )


def _observe(
    sessions,
    *,
    offer_id,
    store_id,
    mission_id,
    amount: str,
    currency: str = "BRL",
    condition: OfferCondition = OfferCondition.NEW,
    availability: Availability = Availability.AVAILABLE,
    observed_at: datetime,
) -> PriceObservation:
    with sessions.begin() as session:
        run = CollectionRun(
            mission_id=mission_id,
            store_id=store_id,
            status=CollectionRunStatus.SUCCEEDED,
            started_at=observed_at,
            finished_at=observed_at,
        )
        session.add(run)
        session.flush()
        decimal_amount = Decimal(amount)
        observation = PriceObservation(
            offer_id=offer_id,
            collection_run_id=run.id,
            amount=decimal_amount,
            currency=currency,
            total_amount=decimal_amount,
            condition=condition,
            availability=availability,
            observed_at=observed_at,
        )
        session.add(observation)
        session.flush()
        session.expunge(observation)
        return observation


def _fetch(integration_database, *, offer_id, user_id, period="all", now=NOW):
    async def _run():
        async with integration_database.async_sessions() as session:
            return await get_offer_price_history_for_user(
                session, offer_id=offer_id, user_id=user_id, period=period, now=now
            )

    return asyncio.run(_run())


def _setup_single_offer(integration_database, *, comparable: bool = True):
    sessions = integration_database.sessions
    user = _user(sessions)
    mission = _mission(sessions, user_id=user.id)
    store = _store(sessions, code="amazon")
    product = _product(
        sessions, comparable=comparable, title="NVIDIA GeForce RTX 5070 Ti"
    )
    offer = _offer(sessions, product_id=product.id, store_id=store.id)
    _link(sessions, mission_id=mission.id, offer_id=offer.id)
    return sessions, user, mission, store, product, offer


# --- A: period=all é genuinamente irrestrito no tempo -----------------------


def test_period_all_returns_observation_older_than_any_finite_window(
    integration_database,
) -> None:
    sessions, user, mission, store, _product, offer = _setup_single_offer(
        integration_database
    )
    old_observed_at = NOW - timedelta(days=900)  # muito além de 1a
    _observe(
        sessions,
        offer_id=offer.id,
        store_id=store.id,
        mission_id=mission.id,
        amount="3500.00",
        observed_at=old_observed_at,
    )

    all_result = _fetch(
        integration_database, offer_id=offer.id, user_id=user.id, period="all"
    )
    one_year_result = _fetch(
        integration_database, offer_id=offer.id, user_id=user.id, period="1a"
    )

    assert all_result.comparable is True
    assert len(all_result.series) == 1
    assert all_result.series[0].points[0].amount == Decimal("3500.00")
    assert one_year_result.series == ()  # fora da janela de 1 ano


# --- B: mesmo dia comercial, duas observações -- ponto = mínimo, atual = mais recente válida --


def test_same_day_two_observations_daily_point_is_low_current_is_latest(
    integration_database,
) -> None:
    sessions, user, mission, store, _product, offer = _setup_single_offer(
        integration_database
    )
    morning = NOW.replace(hour=9)
    evening = NOW.replace(hour=21)
    _observe(
        sessions,
        offer_id=offer.id,
        store_id=store.id,
        mission_id=mission.id,
        amount="3900.00",
        observed_at=morning,
    )
    _observe(
        sessions,
        offer_id=offer.id,
        store_id=store.id,
        mission_id=mission.id,
        amount="4300.00",
        observed_at=evening,
    )

    result = _fetch(
        integration_database,
        offer_id=offer.id,
        user_id=user.id,
        period="7d",
        now=evening,
    )

    assert len(result.series) == 1
    points = result.series[0].points
    assert len(points) == 1
    assert points[0].amount == Decimal("3900.00")  # menor do dia
    assert result.metrics.current_amount == Decimal("4300.00")  # mais recente válida


# --- C: última observação inválida nunca ressuscita preço antigo válido -----


def test_offer_unavailable_today_does_not_resurrect_yesterdays_valid_price(
    integration_database,
) -> None:
    sessions, user, mission, store, _product, offer = _setup_single_offer(
        integration_database
    )
    yesterday = NOW - timedelta(days=1)
    _observe(
        sessions,
        offer_id=offer.id,
        store_id=store.id,
        mission_id=mission.id,
        amount="3900.00",
        availability=Availability.AVAILABLE,
        observed_at=yesterday,
    )
    _observe(
        sessions,
        offer_id=offer.id,
        store_id=store.id,
        mission_id=mission.id,
        amount="3900.00",
        availability=Availability.UNAVAILABLE,
        observed_at=NOW,
    )

    result = _fetch(
        integration_database, offer_id=offer.id, user_id=user.id, period="all"
    )

    assert result.metrics.current_amount is None  # nenhuma Offer válida agora
    assert result.metrics.min_amount == Decimal("3900.00")  # histórico continua real


# --- E: moeda resolvida deterministicamente quando a âncora não tem observação --


def test_currency_resolves_from_another_accessible_offer_when_anchor_has_none(
    integration_database,
) -> None:
    sessions = integration_database.sessions
    user = _user(sessions)
    mission = _mission(sessions, user_id=user.id)
    store_amazon = _store(sessions, code="amazon")
    store_kabum = _store(sessions, code="kabum")
    product = _product(sessions, comparable=True, title="NVIDIA GeForce RTX 5070 Ti")
    anchor_offer = _offer(sessions, product_id=product.id, store_id=store_amazon.id)
    other_offer = _offer(sessions, product_id=product.id, store_id=store_kabum.id)
    _link(sessions, mission_id=mission.id, offer_id=anchor_offer.id)
    _link(sessions, mission_id=mission.id, offer_id=other_offer.id)
    _observe(
        sessions,
        offer_id=other_offer.id,
        store_id=store_kabum.id,
        mission_id=mission.id,
        amount="3999.00",
        currency="BRL",
        observed_at=NOW,
    )

    result = _fetch(
        integration_database, offer_id=anchor_offer.id, user_id=user.id, period="all"
    )

    assert result.currency == "BRL"
    assert len(result.series) == 1
    assert result.series[0].store_code == "kabum"


# --- F: zero observações válidas em qualquer Offer acessível ----------------


def test_no_valid_observation_anywhere_returns_empty_without_fallback(
    integration_database,
) -> None:
    sessions, user, _mission, _store, _product, offer = _setup_single_offer(
        integration_database
    )

    result = _fetch(
        integration_database, offer_id=offer.id, user_id=user.id, period="all"
    )

    assert result.comparable is True
    assert result.currency is None
    assert result.series == ()
    assert result.metrics is None


# --- Identidade não resolvida: comparable=false, nunca None/404 -------------


def test_unresolved_identity_returns_comparable_false_with_reason(
    integration_database,
) -> None:
    sessions, user, _mission, _store, _product, offer = _setup_single_offer(
        integration_database, comparable=False
    )

    result = _fetch(
        integration_database, offer_id=offer.id, user_id=user.id, period="all"
    )

    assert result.comparable is False
    assert result.reason == "unresolved_product_identity"
    assert result.series == ()
    assert result.metrics is None


# --- Autorização real: oferta sem vínculo de missão do usuário -> None ------


def test_offer_without_user_relevance_link_is_not_accessible(
    integration_database,
) -> None:
    sessions = integration_database.sessions
    owner = _user(sessions)
    stranger = _user(sessions)
    mission = _mission(sessions, user_id=owner.id)
    store = _store(sessions, code="amazon")
    product = _product(sessions, comparable=True, title="NVIDIA GeForce RTX 5070 Ti")
    offer = _offer(sessions, product_id=product.id, store_id=store.id)
    _link(sessions, mission_id=mission.id, offer_id=offer.id)
    _observe(
        sessions,
        offer_id=offer.id,
        store_id=store.id,
        mission_id=mission.id,
        amount="3900.00",
        observed_at=NOW,
    )

    result = _fetch(
        integration_database, offer_id=offer.id, user_id=stranger.id, period="all"
    )

    assert result is None


# --- Agregação entre lojas: uma linha de série por Store --------------------


def test_all_stores_view_has_one_series_per_store(integration_database) -> None:
    sessions = integration_database.sessions
    user = _user(sessions)
    mission = _mission(sessions, user_id=user.id)
    store_amazon = _store(sessions, code="amazon")
    store_kabum = _store(sessions, code="kabum")
    product = _product(sessions, comparable=True, title="NVIDIA GeForce RTX 5070 Ti")
    offer_amazon = _offer(sessions, product_id=product.id, store_id=store_amazon.id)
    offer_kabum = _offer(sessions, product_id=product.id, store_id=store_kabum.id)
    _link(sessions, mission_id=mission.id, offer_id=offer_amazon.id)
    _link(sessions, mission_id=mission.id, offer_id=offer_kabum.id)
    _observe(
        sessions,
        offer_id=offer_amazon.id,
        store_id=store_amazon.id,
        mission_id=mission.id,
        amount="4599.00",
        observed_at=NOW,
    )
    _observe(
        sessions,
        offer_id=offer_kabum.id,
        store_id=store_kabum.id,
        mission_id=mission.id,
        amount="4399.00",
        observed_at=NOW,
    )

    result = _fetch(
        integration_database, offer_id=offer_amazon.id, user_id=user.id, period="all"
    )

    codes = {series.store_code for series in result.series}
    assert codes == {"amazon", "kabum"}
    assert result.metrics.min_amount == Decimal("4399.00")


# --- Desempate dentro da mesma loja/dia: duas Offers, mesma loja ------------


def test_two_offers_same_store_same_day_collapse_to_one_point_at_the_low(
    integration_database,
) -> None:
    sessions = integration_database.sessions
    user = _user(sessions)
    mission = _mission(sessions, user_id=user.id)
    store = _store(sessions, code="amazon")
    product = _product(sessions, comparable=True, title="NVIDIA GeForce RTX 5070 Ti")
    offer_a = _offer(sessions, product_id=product.id, store_id=store.id)
    offer_b = _offer(sessions, product_id=product.id, store_id=store.id)
    _link(sessions, mission_id=mission.id, offer_id=offer_a.id)
    _link(sessions, mission_id=mission.id, offer_id=offer_b.id)
    _observe(
        sessions,
        offer_id=offer_a.id,
        store_id=store.id,
        mission_id=mission.id,
        amount="4599.00",
        observed_at=NOW,
    )
    _observe(
        sessions,
        offer_id=offer_b.id,
        store_id=store.id,
        mission_id=mission.id,
        amount="4199.99",
        observed_at=NOW,
    )

    result = _fetch(
        integration_database, offer_id=offer_a.id, user_id=user.id, period="all"
    )

    assert len(result.series) == 1
    assert len(result.series[0].points) == 1
    assert result.series[0].points[0].amount == Decimal("4199.99")


# --- Moeda divergente nunca é misturada --------------------------------------


def test_offer_with_different_currency_never_mixes_into_reference_series(
    integration_database,
) -> None:
    sessions = integration_database.sessions
    user = _user(sessions)
    mission = _mission(sessions, user_id=user.id)
    store_amazon = _store(sessions, code="amazon")
    store_kabum = _store(sessions, code="kabum")
    product = _product(sessions, comparable=True, title="NVIDIA GeForce RTX 5070 Ti")
    anchor_offer = _offer(sessions, product_id=product.id, store_id=store_amazon.id)
    foreign_offer = _offer(sessions, product_id=product.id, store_id=store_kabum.id)
    _link(sessions, mission_id=mission.id, offer_id=anchor_offer.id)
    _link(sessions, mission_id=mission.id, offer_id=foreign_offer.id)
    _observe(
        sessions,
        offer_id=anchor_offer.id,
        store_id=store_amazon.id,
        mission_id=mission.id,
        amount="4599.00",
        currency="BRL",
        observed_at=NOW,
    )
    _observe(
        sessions,
        offer_id=foreign_offer.id,
        store_id=store_kabum.id,
        mission_id=mission.id,
        amount="999.00",
        currency="USD",
        observed_at=NOW,
    )

    result = _fetch(
        integration_database, offer_id=anchor_offer.id, user_id=user.id, period="all"
    )

    assert result.currency == "BRL"  # âncora tem observação própria -- vence
    assert len(result.series) == 1
    assert result.series[0].store_code == "amazon"
