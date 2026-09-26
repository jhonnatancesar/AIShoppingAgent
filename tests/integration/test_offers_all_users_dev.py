"""TASK-126: `list_all_offers_dev` (DEV-only, usado por `GET /offers?
all_users=true`) precisa mostrar ofertas de QUALQUER usuário e QUALQUER
classificação (incluindo `NO_MATCH`) que `list_user_offers` (USER,
`/offers` normal) sempre excluiu -- prova contra PostgreSQL real porque a
causa raiz é uma diferença de filtro SQL (`_accessible_offer_exists` vs.
`_any_relevance_exists`), mesmo padrão de
`test_offer_relevance_eligibility.py`. Roda só via
`python scripts/run_integration_tests.py tests/integration/test_offers_all_users_dev.py`.
"""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.collection.models import MissionOfferRelevance
from app.collection.relevance import OfferRelevance
from app.missions.models import Mission, MissionStatus
from app.offers.models import Offer
from app.offers.query import list_all_offers_dev, list_user_offers
from app.products.models import Product
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy import select

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def _user(sessions) -> User:
    with sessions.begin() as session:
        user = User(display_name="task-126 synthetic", role=UserRole.USER)
        session.add(user)
        session.flush()
        session.expunge(user)
        return user


def _mission(sessions, *, user_id) -> Mission:
    with sessions.begin() as session:
        mission = Mission(
            user_id=user_id, title="RTX 5070 Ti", status=MissionStatus.ACTIVE
        )
        session.add(mission)
        session.flush()
        session.expunge(mission)
        return mission


def _store(sessions, *, code: str) -> Store:
    with sessions() as session:
        return session.scalar(select(Store).where(Store.code == code))


def _product(sessions, *, name: str) -> Product:
    with sessions.begin() as session:
        product = Product(name=name)
        session.add(product)
        session.flush()
        session.expunge(product)
        return product


def _offer(sessions, *, product_id, store_id) -> Offer:
    with sessions.begin() as session:
        offer = Offer(
            product_id=product_id,
            store_id=store_id,
            url=f"https://example.invalid/task-126-{uuid4().hex[:12]}",
        )
        session.add(offer)
        session.flush()
        session.expunge(offer)
        return offer


def _link(sessions, *, mission_id, offer_id, classification: OfferRelevance) -> None:
    with sessions.begin() as session:
        session.add(
            MissionOfferRelevance(
                mission_id=mission_id,
                offer_id=offer_id,
                classification=classification,
                classified_at=NOW,
            )
        )


def _dev_offer_ids(integration_database) -> set:
    async def _run():
        async with integration_database.async_sessions() as session:
            items, _total = await list_all_offers_dev(
                session,
                search=None,
                store_code=None,
                condition=None,
                availability=None,
                sort="recent",
                limit=50,
                offset=0,
            )
            return {item.offer.id for item in items}

    return asyncio.run(_run())


def _user_offer_ids(integration_database, *, user_id) -> set:
    async def _run():
        async with integration_database.async_sessions() as session:
            items, _total = await list_user_offers(
                session,
                user_id=user_id,
                search=None,
                store_code=None,
                condition=None,
                availability=None,
                sort="recent",
                limit=50,
                offset=0,
            )
            return {item.offer.id for item in items}

    return asyncio.run(_run())


def test_dev_all_users_view_includes_no_match_and_other_users_offers(
    integration_database,
) -> None:
    sessions = integration_database.sessions
    owner = _user(sessions)
    someone_else = _user(sessions)
    mission_owner = _mission(sessions, user_id=owner.id)
    mission_other = _mission(sessions, user_id=someone_else.id)
    matched_product = _product(sessions, name="RTX 5070 Ti Asus")
    discarded_product = _product(sessions, name="Controle Gamer 8BitDo Ultimate 2C")
    other_user_product = _product(sessions, name="Placa-mãe X870E")
    store_a = _store(sessions, code="amazon")
    store_b = _store(sessions, code="kabum")
    store_c = _store(sessions, code="pichau")
    matched_offer = _offer(sessions, product_id=matched_product.id, store_id=store_a.id)
    discarded_offer = _offer(
        sessions, product_id=discarded_product.id, store_id=store_b.id
    )
    other_user_offer = _offer(
        sessions, product_id=other_user_product.id, store_id=store_c.id
    )
    _link(
        sessions,
        mission_id=mission_owner.id,
        offer_id=matched_offer.id,
        classification=OfferRelevance.MATCH,
    )
    _link(
        sessions,
        mission_id=mission_owner.id,
        offer_id=discarded_offer.id,
        classification=OfferRelevance.NO_MATCH,
    )
    _link(
        sessions,
        mission_id=mission_other.id,
        offer_id=other_user_offer.id,
        classification=OfferRelevance.MATCH,
    )

    dev_offer_ids = _dev_offer_ids(integration_database)
    owner_offer_ids = _user_offer_ids(integration_database, user_id=owner.id)

    assert dev_offer_ids == {matched_offer.id, discarded_offer.id, other_user_offer.id}
    assert owner_offer_ids == {matched_offer.id}  # USER: só a própria, MATCH só
