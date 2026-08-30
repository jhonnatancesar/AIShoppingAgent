"""Subtask 2 (auditoria GG Oferta): a tela geral de Ofertas e o detalhe de
uma Offer devem reaplicar a MESMA regra de elegibilidade que
`list_current_offer_links_for_mission` já aplicava só para a tela de
missão (TASK-097) -- uma classificação histórica MATCH/POSSIBLE_MATCH
nunca deve valer para sempre se o produto não corresponde mais à
família/variante/seleção ATUAL da missão que a gerou.

Prova contra PostgreSQL real porque a causa raiz é uma diferença de SQL
(EXISTS/JOIN) entre três consultas -- um mock de sessão não teria pego o
drift original nem provaria a correção. Roda só via
`python scripts/run_integration_tests.py tests/integration/test_offer_relevance_eligibility.py`.
"""

import asyncio
from datetime import UTC, datetime
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
from app.missions.models import (
    Mission,
    MissionCriteria,
    MissionProductSelection,
    MissionStatus,
    VariantSelectionMode,
)
from app.offers.models import Offer
from app.offers.query import (
    get_offer_detail_for_user,
    list_current_offer_links_for_mission,
    list_user_offers,
)
from app.products.identity import ProductRequestKind
from app.products.models import Product
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy import select

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


def _user(sessions) -> User:
    with sessions.begin() as session:
        user = User(display_name="subtask-2 synthetic", role=UserRole.USER)
        session.add(user)
        session.flush()
        session.expunge(user)
        return user


def _mission(sessions, *, user_id, title: str) -> Mission:
    with sessions.begin() as session:
        mission = Mission(user_id=user_id, title=title, status=MissionStatus.ACTIVE)
        session.add(mission)
        session.flush()
        session.expunge(mission)
        return mission


def _criteria(
    sessions,
    *,
    mission_id,
    search_query: str,
    request_kind: ProductRequestKind,
    family_key: str | None,
    variant_selection_mode: VariantSelectionMode,
) -> None:
    with sessions.begin() as session:
        session.add(
            MissionCriteria(
                mission_id=mission_id,
                search_query=search_query,
                request_kind=request_kind.value,
                requested_family_key=family_key,
                requested_identity_key=None,
                requested_variant=None,
                variant_selection_mode=variant_selection_mode,
            )
        )


def _select_product(sessions, *, mission_id, product_id) -> None:
    with sessions.begin() as session:
        session.add(
            MissionProductSelection(mission_id=mission_id, product_id=product_id)
        )


def _store(sessions, *, code: str) -> Store:
    with sessions() as session:
        return session.scalar(select(Store).where(Store.code == code))


def _product(sessions, *, family_key: str, variant: str, model: str) -> Product:
    with sessions.begin() as session:
        product = Product(
            name=f"{model} {variant}",
            brand="NVIDIA",
            model=model,
            category="gpu",
            family=model,
            variant=variant,
            family_key=family_key,
            identity_key=f"{family_key}:{variant}:{uuid4().hex[:8]}",
            identity_version=1,
        )
        session.add(product)
        session.flush()
        session.expunge(product)
        return product


def _offer(sessions, *, product_id, store_id) -> Offer:
    with sessions.begin() as session:
        offer = Offer(
            product_id=product_id,
            store_id=store_id,
            url=f"https://example.invalid/subtask2-{uuid4().hex[:12]}",
        )
        session.add(offer)
        session.flush()
        session.expunge(offer)
        return offer


def _link(
    sessions, *, mission_id, offer_id, classification=OfferRelevance.MATCH
) -> None:
    with sessions.begin() as session:
        session.add(
            MissionOfferRelevance(
                mission_id=mission_id,
                offer_id=offer_id,
                classification=classification,
                classified_at=NOW,
            )
        )


def _observe(sessions, *, offer_id, store_id, mission_id) -> None:
    with sessions.begin() as session:
        run = CollectionRun(
            mission_id=mission_id,
            store_id=store_id,
            status=CollectionRunStatus.SUCCEEDED,
            started_at=NOW,
            finished_at=NOW,
        )
        session.add(run)
        session.flush()
        session.add(
            PriceObservation(
                offer_id=offer_id,
                collection_run_id=run.id,
                amount="100.00",
                currency="BRL",
                total_amount="100.00",
                condition=OfferCondition.NEW,
                availability=Availability.AVAILABLE,
                observed_at=NOW,
            )
        )


def _general_offer_summaries(integration_database, *, user_id):
    async def _run():
        async with integration_database.async_sessions() as session:
            summaries, total = await list_user_offers(
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
            return summaries, total

    return asyncio.run(_run())


def _general_offer_ids(integration_database, *, user_id) -> set:
    summaries, _total = _general_offer_summaries(integration_database, user_id=user_id)
    return {summary.offer.id for summary in summaries}


def _detail_is_accessible(integration_database, *, offer_id, user_id) -> bool:
    async def _run():
        async with integration_database.async_sessions() as session:
            return (
                await get_offer_detail_for_user(
                    session, offer_id=offer_id, user_id=user_id
                )
                is not None
            )

    return asyncio.run(_run())


def _mission_offer_ids(integration_database, *, mission_id, user_id) -> set:
    async def _run():
        async with integration_database.async_sessions() as session:
            links = await list_current_offer_links_for_mission(
                session, mission_id=mission_id, user_id=user_id
            )
            return {link.offer.id for link in links}

    return asyncio.run(_run())


def test_offer_of_unselected_variant_disappears_from_general_offers_and_detail(
    integration_database,
) -> None:
    """O bug relatado: dentro da missão a variante ASUS foi selecionada e a
    Gigabyte corretamente some -- mas antes desta correção a Gigabyte
    continuava aparecendo para sempre na tela geral de Ofertas e no
    detalhe direto, porque as duas nunca reaplicavam a seleção atual."""
    sessions = integration_database.sessions
    user = _user(sessions)
    mission = _mission(sessions, user_id=user.id, title="RTX 5070 Ti")
    _criteria(
        sessions,
        mission_id=mission.id,
        search_query="RTX 5070 Ti",
        request_kind=ProductRequestKind.PRODUCT_FAMILY,
        family_key="gpu:nvidia:rtx-5070-ti",
        variant_selection_mode=VariantSelectionMode.SELECTED,
    )
    selected_product = _product(
        sessions,
        family_key="gpu:nvidia:rtx-5070-ti",
        variant="asus",
        model="RTX 5070 Ti",
    )
    other_product = _product(
        sessions,
        family_key="gpu:nvidia:rtx-5070-ti",
        variant="gigabyte",
        model="RTX 5070 Ti",
    )
    _select_product(sessions, mission_id=mission.id, product_id=selected_product.id)
    store = _store(sessions, code="amazon")
    selected_offer = _offer(sessions, product_id=selected_product.id, store_id=store.id)
    other_offer = _offer(sessions, product_id=other_product.id, store_id=store.id)
    _link(sessions, mission_id=mission.id, offer_id=selected_offer.id)
    _link(sessions, mission_id=mission.id, offer_id=other_offer.id)
    _observe(
        sessions, offer_id=selected_offer.id, store_id=store.id, mission_id=mission.id
    )
    _observe(
        sessions, offer_id=other_offer.id, store_id=store.id, mission_id=mission.id
    )

    general_offer_ids = _general_offer_ids(integration_database, user_id=user.id)
    mission_offer_ids = _mission_offer_ids(
        integration_database, mission_id=mission.id, user_id=user.id
    )

    assert selected_offer.id in general_offer_ids
    assert other_offer.id not in general_offer_ids  # DEPOIS: some da tela geral
    assert selected_offer.id in mission_offer_ids
    assert other_offer.id not in mission_offer_ids  # já funcionava antes (TASK-097)
    assert _detail_is_accessible(
        integration_database, offer_id=selected_offer.id, user_id=user.id
    )
    assert not _detail_is_accessible(
        integration_database, offer_id=other_offer.id, user_id=user.id
    )  # DEPOIS: 404 fail-closed, não mais acessível direto pela URL


def test_offer_of_different_family_never_appears(integration_database) -> None:
    """Família totalmente diferente (não só variante) -- a IA classificou
    como POSSIBLE_MATCH em algum momento, mas o produto nem é da mesma
    família que a missão pede."""
    sessions = integration_database.sessions
    user = _user(sessions)
    mission = _mission(sessions, user_id=user.id, title="RTX 5070 Ti")
    _criteria(
        sessions,
        mission_id=mission.id,
        search_query="RTX 5070 Ti",
        request_kind=ProductRequestKind.PRODUCT_FAMILY,
        family_key="gpu:nvidia:rtx-5070-ti",
        variant_selection_mode=VariantSelectionMode.ALL,
    )
    wrong_family_product = _product(
        sessions,
        family_key="gpu:nvidia:rtx-4070",
        variant="asus",
        model="RTX 4070",
    )
    store = _store(sessions, code="kabum")
    wrong_family_offer = _offer(
        sessions, product_id=wrong_family_product.id, store_id=store.id
    )
    _link(
        sessions,
        mission_id=mission.id,
        offer_id=wrong_family_offer.id,
        classification=OfferRelevance.POSSIBLE_MATCH,
    )
    _observe(
        sessions,
        offer_id=wrong_family_offer.id,
        store_id=store.id,
        mission_id=mission.id,
    )

    general_offer_ids = _general_offer_ids(integration_database, user_id=user.id)

    assert wrong_family_offer.id not in general_offer_ids


def test_offer_still_eligible_through_a_different_mission_of_the_same_user(
    integration_database,
) -> None:
    """Calibração contra rigidez demais: uma oferta que ficou desatualizada
    para a Missão A (que já selecionou outra variante) continua acessível
    se a Missão B (mesmo usuário, ainda sem seleção estreita) também a
    classificou como relevante -- a regra é 'existe PELO MENOS uma missão
    atual para a qual a oferta ainda vale', nunca tudo-ou-nada."""
    sessions = integration_database.sessions
    user = _user(sessions)
    narrow_mission = _mission(sessions, user_id=user.id, title="RTX 5070 Ti (ASUS)")
    _criteria(
        sessions,
        mission_id=narrow_mission.id,
        search_query="RTX 5070 Ti",
        request_kind=ProductRequestKind.PRODUCT_FAMILY,
        family_key="gpu:nvidia:rtx-5070-ti",
        variant_selection_mode=VariantSelectionMode.SELECTED,
    )
    broad_mission = _mission(sessions, user_id=user.id, title="RTX 5070 Ti (qualquer)")
    _criteria(
        sessions,
        mission_id=broad_mission.id,
        search_query="RTX 5070 Ti",
        request_kind=ProductRequestKind.PRODUCT_FAMILY,
        family_key="gpu:nvidia:rtx-5070-ti",
        variant_selection_mode=VariantSelectionMode.ALL,
    )
    selected_product = _product(
        sessions,
        family_key="gpu:nvidia:rtx-5070-ti",
        variant="asus",
        model="RTX 5070 Ti",
    )
    other_product = _product(
        sessions,
        family_key="gpu:nvidia:rtx-5070-ti",
        variant="gigabyte",
        model="RTX 5070 Ti",
    )
    _select_product(
        sessions, mission_id=narrow_mission.id, product_id=selected_product.id
    )
    store = _store(sessions, code="amazon")
    other_offer = _offer(sessions, product_id=other_product.id, store_id=store.id)
    _link(sessions, mission_id=narrow_mission.id, offer_id=other_offer.id)
    _link(sessions, mission_id=broad_mission.id, offer_id=other_offer.id)
    _observe(
        sessions,
        offer_id=other_offer.id,
        store_id=store.id,
        mission_id=broad_mission.id,
    )

    summaries, total = _general_offer_summaries(integration_database, user_id=user.id)
    matching_rows = [s for s in summaries if s.offer.id == other_offer.id]

    # Verificação de cardinalidade (subtask 2, item 1): a oferta é
    # endossada por DUAS missões válidas (narrow_mission e broad_mission),
    # cenário exato em que um JOIN ingênuo em vez de EXISTS duplicaria a
    # linha -- `_accessible_offer_exists` usa EXISTS(...), que colapsa
    # para um único booleano por Offer independentemente de quantas
    # missões endossem; `mission_offer_relevance` também não ajudaria a
    # duplicar aqui, pois sua PK é `(mission_id, offer_id)`, não
    # `offer_id` sozinho. Confirma sem ambiguidade: 1 linha, nunca 2.
    assert len(matching_rows) == 1
    assert total == 1


def test_specific_product_and_generic_category_missions_are_unaffected(
    integration_database,
) -> None:
    """Fidelidade ao comportamento já existente: só PRODUCT_FAMILY ganha a
    checagem extra -- SPECIFIC_PRODUCT/GENERIC_CATEGORY continuam
    confiando só na classificação, como já era antes desta subtask."""
    sessions = integration_database.sessions
    user = _user(sessions)
    mission = _mission(sessions, user_id=user.id, title="Mouse gamer qualquer")
    _criteria(
        sessions,
        mission_id=mission.id,
        search_query="mouse gamer",
        request_kind=ProductRequestKind.GENERIC_CATEGORY,
        family_key=None,
        variant_selection_mode=VariantSelectionMode.NOT_REQUIRED,
    )
    product = _product(
        sessions,
        family_key="peripheral:mouse:generic",
        variant="generic",
        model="Mouse Gamer X",
    )
    store = _store(sessions, code="kabum")
    offer = _offer(sessions, product_id=product.id, store_id=store.id)
    _link(sessions, mission_id=mission.id, offer_id=offer.id)
    _observe(sessions, offer_id=offer.id, store_id=store.id, mission_id=mission.id)

    general_offer_ids = _general_offer_ids(integration_database, user_id=user.id)

    assert offer.id in general_offer_ids
