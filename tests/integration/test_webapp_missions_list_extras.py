"""Subtask 14 (redesign de Missões): `MissionListExtras`/
`load_mission_list_extras` (`app.missions.query`) e
`count_relevant_offers_by_mission` (`app.offers.query`) contra
PostgreSQL real -- prova que preço-alvo/lojas/contagem de ofertas
relevantes da nova Lista de Missões da web vêm de relações já
persistidas (`MissionCriteria`, `MissionSource`, `MissionOfferRelevance`),
nunca recalculando matching, e que o número de consultas SQL não escala
com a quantidade de missões da página (sem N+1)."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.collection.models import MissionOfferRelevance
from app.collection.relevance import OfferRelevance
from app.missions.models import (
    Mission,
    MissionCriteria,
    MissionSource,
    MissionStatus,
    VariantSelectionMode,
)
from app.missions.query import load_mission_list_extras
from app.offers.models import Offer
from app.products.identity import ProductRequestKind
from app.products.models import Product
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy import event, select

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _seed_user(sessions, *, username: str) -> User:
    with sessions.begin() as session:
        user = User(display_name=username, role=UserRole.USER, username=username)
        session.add(user)
        session.flush()
        session.expunge(user)
        return user


def _seed_mission(
    sessions,
    *,
    user_id,
    title: str,
    target_amount=None,
    target_currency=None,
    sources: tuple[str, ...] = (),
    request_kind: str = ProductRequestKind.GENERIC_CATEGORY.value,
    requested_family_key: str | None = None,
    variant_selection_mode: VariantSelectionMode = VariantSelectionMode.NOT_REQUIRED,
) -> Mission:
    with sessions.begin() as session:
        mission = Mission(user_id=user_id, title=title, status=MissionStatus.ACTIVE)
        session.add(mission)
        session.flush()
        session.add(
            MissionCriteria(
                mission_id=mission.id,
                search_query=title,
                target_amount=target_amount,
                target_currency=target_currency,
                request_kind=request_kind,
                requested_family_key=requested_family_key,
                variant_selection_mode=variant_selection_mode,
            )
        )
        stores = {
            store.code: store
            for store in session.scalars(select(Store).where(Store.code.in_(sources)))
        }
        for code in sources:
            session.add(MissionSource(mission_id=mission.id, store_id=stores[code].id))
        session.flush()
        session.expunge(mission)
        return mission


def _seed_relevant_offer(
    sessions, *, mission_id, store_code: str, classification=OfferRelevance.MATCH
) -> None:
    with sessions.begin() as session:
        store = session.scalar(select(Store).where(Store.code == store_code))
        product = Product(
            name=f"Produto {uuid4().hex[:8]}",
            brand="Marca",
            model="Modelo",
            category="gpu",
            family="Familia",
            variant="Padrao",
            family_key=f"familia-{uuid4().hex[:8]}",
            identity_key=f"identidade-{uuid4().hex[:8]}",
            identity_version=1,
        )
        session.add(product)
        session.flush()
        offer = Offer(
            product_id=product.id,
            store_id=store.id,
            url=f"https://example.invalid/subtask14-{uuid4().hex[:12]}",
        )
        session.add(offer)
        session.flush()
        session.add(
            MissionOfferRelevance(
                mission_id=mission_id,
                offer_id=offer.id,
                classification=classification,
                classified_at=NOW,
            )
        )


def test_load_mission_list_extras_uses_only_persisted_relations(
    integration_database,
) -> None:
    user = _seed_user(integration_database.sessions, username="webmissions-extras1")
    with_data = _seed_mission(
        integration_database.sessions,
        user_id=user.id,
        title="RTX 5070 Ti",
        target_amount="4500.00",
        target_currency="BRL",
        sources=("kabum", "amazon"),
    )
    _seed_relevant_offer(integration_database.sessions, mission_id=with_data.id, store_code="kabum")
    _seed_relevant_offer(integration_database.sessions, mission_id=with_data.id, store_code="amazon")
    # NO_MATCH nunca conta -- prova que a agregação filtra por classificação,
    # não só por existir um vínculo em MissionOfferRelevance.
    _seed_relevant_offer(
        integration_database.sessions,
        mission_id=with_data.id,
        store_code="kabum",
        classification=OfferRelevance.NO_MATCH,
    )
    without_data = _seed_mission(
        integration_database.sessions, user_id=user.id, title="Sem alvo/lojas/ofertas"
    )

    async def run():
        async with integration_database.async_sessions.begin() as session:
            return await load_mission_list_extras(
                session, mission_ids=[with_data.id, without_data.id]
            )

    extras = asyncio.run(run())

    assert extras[with_data.id].target_amount == 4500
    assert extras[with_data.id].target_currency == "BRL"
    assert {store.code for _source, store in extras[with_data.id].sources} == {"kabum", "amazon"}
    assert extras[with_data.id].relevant_offer_count == 2

    assert extras[without_data.id].target_amount is None
    assert extras[without_data.id].target_currency is None
    assert extras[without_data.id].sources == ()
    assert extras[without_data.id].relevant_offer_count == 0


def test_load_mission_list_extras_zeroes_count_for_pending_variant_family(
    integration_database,
) -> None:
    """Mesma regra que `list_current_offer_links_for_mission` já aplica no
    detalhe (TASK-097): uma missão de família de produto ainda aguardando
    seleção de variante não deve mostrar uma contagem que o detalhe
    (`GET /missions/{id}`) não confirmaria."""
    user = _seed_user(integration_database.sessions, username="webmissions-extras2")
    pending_family = _seed_mission(
        integration_database.sessions,
        user_id=user.id,
        title="Notebook gamer (família)",
        request_kind=ProductRequestKind.PRODUCT_FAMILY.value,
        requested_family_key="notebook-gamer",
        variant_selection_mode=VariantSelectionMode.PENDING,
    )
    _seed_relevant_offer(integration_database.sessions, mission_id=pending_family.id, store_code="kabum")

    async def run():
        async with integration_database.async_sessions.begin() as session:
            return await load_mission_list_extras(session, mission_ids=[pending_family.id])

    extras = asyncio.run(run())
    assert extras[pending_family.id].relevant_offer_count == 0


def test_load_mission_list_extras_query_count_does_not_scale_with_page_size(
    integration_database,
) -> None:
    """Sem N+1 (regra obrigatória da Subtask 14): o número de consultas SQL
    de `load_mission_list_extras` precisa ser o mesmo para 1 ou para N
    missões da página -- nunca uma consulta por missão."""
    user = _seed_user(integration_database.sessions, username="webmissions-extras3")
    missions = [
        _seed_mission(
            integration_database.sessions,
            user_id=user.id,
            title=f"Missão {index}",
            target_amount="1000.00",
            target_currency="BRL",
            sources=("kabum",),
        )
        for index in range(5)
    ]
    for mission in missions:
        _seed_relevant_offer(integration_database.sessions, mission_id=mission.id, store_code="kabum")

    def count_queries(mission_ids: list) -> int:
        counter = {"n": 0}

        def before_cursor_execute(*_args, **_kwargs):
            counter["n"] += 1

        engine = integration_database.async_engine.sync_engine
        event.listen(engine, "before_cursor_execute", before_cursor_execute)
        try:
            async def run():
                async with integration_database.async_sessions.begin() as session:
                    await load_mission_list_extras(session, mission_ids=mission_ids)

            asyncio.run(run())
        finally:
            event.remove(engine, "before_cursor_execute", before_cursor_execute)
        return counter["n"]

    queries_for_one = count_queries([missions[0].id])
    queries_for_five = count_queries([mission.id for mission in missions])

    assert queries_for_one == queries_for_five, (
        f"esperava o mesmo número de consultas para 1 e para 5 missões "
        f"(sem N+1); obtive {queries_for_one} para 1 e {queries_for_five} para 5"
    )
