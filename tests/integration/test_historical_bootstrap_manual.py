"""TASK-127: reserva/execução MANUAL do bootstrap histórico (botão
"Buscar preço histórico") e abertura DEV de qualquer oferta (furo da
TASK-126 corrigido junto) -- contra PostgreSQL real, porque as regras
vivem no `ON CONFLICT ... WHERE` do claim e nos predicados SQL de acesso.
Roda só via
`python scripts/run_integration_tests.py tests/integration/test_historical_bootstrap_manual.py`.
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
from app.historical_bootstrap.models import (
    ExternalPriceReference,
    HistoricalBootstrap,
    HistoricalBootstrapStatus,
)
from app.historical_bootstrap.service import (
    claim_manual_historical_bootstrap,
    run_claimed_historical_bootstrap,
    run_historical_bootstrap,
)
from app.missions.models import Mission, MissionStatus
from app.offers.models import Offer
from app.offers.query import (
    get_offer_comparison_for_dev,
    get_offer_detail_for_dev,
    get_offer_detail_for_user,
    get_offer_price_history_for_dev,
)
from app.products.identity import IDENTITY_VERSION, resolve_product_variant
from app.products.models import Product
from app.search.contracts import WebSearchResponse, WebSearchResult
from app.search.manager import WebSearchManager
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy import func, select

pytestmark = pytest.mark.integration
NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)
_SNIPPET_RESULT = WebSearchResult(
    "NVIDIA GeForce RTX 5070 Ti histórico de preço",
    "https://www.example-comparador.com.br/rtx-5070-ti-historico",
    "NVIDIA GeForce RTX 5070 Ti 01/08/2026 R$ 4.999,90 no menor preço já registrado.",
    1,
)


class Search:
    def __init__(self, results=()):
        self.results, self.calls = results, 0

    async def search(self, query, *, limit, correlation_id):
        self.calls += 1
        return WebSearchResponse(
            tuple(self.results[:limit]), "cesar_core", "searxng-search", correlation_id
        )


class FailingSearch:
    def __init__(self):
        self.calls = 0

    async def search(self, query, *, limit, correlation_id):
        self.calls += 1
        raise RuntimeError("simulated search failure")


class NoAI:
    async def generate(self, request):
        raise AssertionError("AI não deveria ser usada")


def _product(database) -> Product:
    variant = resolve_product_variant("NVIDIA GeForce RTX 5070 Ti")
    assert variant is not None
    with database.sessions.begin() as session:
        item = Product(
            name="NVIDIA GeForce RTX 5070 Ti",
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
        session.add(item)
        session.flush()
        session.expunge(item)
        return item


def _store(sessions, *, code: str) -> Store:
    with sessions() as session:
        return session.scalar(select(Store).where(Store.code == code))


def _user(sessions) -> User:
    with sessions.begin() as session:
        user = User(display_name="task-127 synthetic", role=UserRole.USER)
        session.add(user)
        session.flush()
        session.expunge(user)
        return user


def _mission(sessions, *, user_id) -> Mission:
    with sessions.begin() as session:
        mission = Mission(user_id=user_id, title="RTX", status=MissionStatus.ACTIVE)
        session.add(mission)
        session.flush()
        session.expunge(mission)
        return mission


def _offer(sessions, *, product_id, store_id, mission_id, classification) -> Offer:
    with sessions.begin() as session:
        offer = Offer(
            product_id=product_id,
            store_id=store_id,
            url=f"https://example.invalid/task-127-{uuid4().hex[:12]}",
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
        session.expunge(offer)
        return offer


def _observe(sessions, *, offer_id, store_id, mission_id, observed_at) -> None:
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
        session.add(
            PriceObservation(
                offer_id=offer_id,
                collection_run_id=run.id,
                amount=Decimal("4999.90"),
                currency="BRL",
                total_amount=Decimal("4999.90"),
                condition=OfferCondition.NEW,
                availability=Availability.AVAILABLE,
                observed_at=observed_at,
            )
        )


def _seed_sufficient_internal_history(sessions, *, product_id) -> None:
    """2 lojas, 30+ dias -- critério exato de `internal_history_is_sufficient`."""
    user = _user(sessions)
    mission = _mission(sessions, user_id=user.id)
    for code, offset_days in (("pichau", 31), ("terabyte", 0)):
        store = _store(sessions, code=code)
        offer = _offer(
            sessions,
            product_id=product_id,
            store_id=store.id,
            mission_id=mission.id,
            classification=OfferRelevance.MATCH,
        )
        _observe(
            sessions,
            offer_id=offer.id,
            store_id=store.id,
            mission_id=mission.id,
            observed_at=NOW - timedelta(days=offset_days),
        )


def _claim(database, product_id, *, now, force=False):
    return asyncio.run(
        claim_manual_historical_bootstrap(
            database.async_sessions,
            product_id=product_id,
            now=now,
            revalidation_days=90,
            lease_seconds=300,
            force=force,
        )
    )


def _run_claimed(database, claimed, *, search, now):
    return asyncio.run(
        run_claimed_historical_bootstrap(
            database.async_sessions,
            claimed,
            search=lambda: WebSearchManager(search),
            fetch=None,
            ai=NoAI(),
            profile=UserRole.USER,
            now=now,
            failure_backoff_minutes=15,
            failure_backoff_max_minutes=360,
        )
    )


def _run_auto(database, product_id, *, search, now):
    return asyncio.run(
        run_historical_bootstrap(
            database.async_sessions,
            product_id=product_id,
            search=lambda: WebSearchManager(search),
            fetch=None,
            ai=NoAI(),
            profile=UserRole.ADMIN,
            now=now,
            revalidation_days=90,
            lease_seconds=300,
            failure_backoff_minutes=15,
            failure_backoff_max_minutes=360,
        )
    )


def test_manual_search_runs_even_with_sufficient_internal_history(
    integration_database,
) -> None:
    """Exatamente os produtos que o fluxo automático nunca busca -- o
    motivo de existir do botão."""
    product = _product(integration_database)
    _seed_sufficient_internal_history(
        integration_database.sessions, product_id=product.id
    )
    auto_search = Search((_SNIPPET_RESULT,))
    assert (
        _run_auto(integration_database, product.id, search=auto_search, now=NOW) is None
    )
    assert auto_search.calls == 0

    claimed = _claim(integration_database, product.id, now=NOW)
    assert claimed is not None
    manual_search = Search((_SNIPPET_RESULT,))
    status = _run_claimed(integration_database, claimed, search=manual_search, now=NOW)

    assert status is HistoricalBootstrapStatus.COMPLETED_WITH_REFERENCES
    assert manual_search.calls >= 1
    with integration_database.sessions() as session:
        ref = session.scalar(
            select(ExternalPriceReference).where(
                ExternalPriceReference.product_id == product.id
            )
        )
        assert ref is not None and ref.amount == Decimal("4999.9000")


def test_manual_claim_blocked_within_window_until_forced(integration_database) -> None:
    product = _product(integration_database)
    first = _claim(integration_database, product.id, now=NOW)
    assert first is not None
    _run_claimed(integration_database, first, search=Search(), now=NOW)

    # Busca concluída (mesmo sem achar nada) há 1 dia: USER não repete.
    assert _claim(integration_database, product.id, now=NOW + timedelta(days=1)) is None
    # DEV, depois da confirmação, força.
    forced = _claim(
        integration_database, product.id, now=NOW + timedelta(days=1), force=True
    )
    assert forced is not None
    _run_claimed(
        integration_database, forced, search=Search(), now=NOW + timedelta(days=1)
    )
    # Fora da janela de 90 dias (contada da ÚLTIMA conclusão): qualquer um.
    assert (
        _claim(integration_database, product.id, now=NOW + timedelta(days=92))
        is not None
    )


def test_manual_claim_retries_failed_search_ignoring_backoff(
    integration_database,
) -> None:
    product = _product(integration_database)
    assert (
        _run_auto(integration_database, product.id, search=FailingSearch(), now=NOW)
        is None
    )
    with integration_database.sessions() as session:
        row = session.scalar(
            select(HistoricalBootstrap).where(
                HistoricalBootstrap.product_id == product.id
            )
        )
        assert row.status is HistoricalBootstrapStatus.FAILED
        assert row.retry_after > NOW

    assert _claim(integration_database, product.id, now=NOW) is not None


def test_manual_claim_never_steals_an_active_lease(integration_database) -> None:
    product = _product(integration_database)
    assert _claim(integration_database, product.id, now=NOW) is not None
    later = NOW + timedelta(seconds=10)
    assert _claim(integration_database, product.id, now=later) is None
    assert _claim(integration_database, product.id, now=later, force=True) is None
    with integration_database.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(HistoricalBootstrap)
                .where(HistoricalBootstrap.product_id == product.id)
            )
            == 1
        )


def test_dev_opens_other_users_no_match_offer_detail_comparison_and_history(
    integration_database,
) -> None:
    """Furo da TASK-126: com o filtro "todos os usuários", DEV clicava em
    "Ver detalhes" de uma oferta de outro usuário/NO_MATCH e tomava 403."""
    sessions = integration_database.sessions
    owner = _user(sessions)
    someone_else = _user(sessions)
    mission = _mission(sessions, user_id=owner.id)
    product = _product(integration_database)
    store = _store(sessions, code="kabum")
    offer = _offer(
        sessions,
        product_id=product.id,
        store_id=store.id,
        mission_id=mission.id,
        classification=OfferRelevance.NO_MATCH,
    )
    _observe(
        sessions,
        offer_id=offer.id,
        store_id=store.id,
        mission_id=mission.id,
        observed_at=NOW,
    )

    async def _run():
        async with integration_database.async_sessions() as session:
            as_owner = await get_offer_detail_for_user(
                session, offer_id=offer.id, user_id=owner.id
            )
            as_other = await get_offer_detail_for_user(
                session, offer_id=offer.id, user_id=someone_else.id
            )
            as_dev = await get_offer_detail_for_dev(session, offer_id=offer.id)
            comparison = await get_offer_comparison_for_dev(session, offer_id=offer.id)
            history = await get_offer_price_history_for_dev(
                session, offer_id=offer.id, period="1m", now=NOW + timedelta(hours=1)
            )
            return as_owner, as_other, as_dev, comparison, history

    as_owner, as_other, as_dev, comparison, history = asyncio.run(_run())

    assert as_owner is None  # NO_MATCH nunca abre para USER (inalterado)
    assert as_other is None
    assert as_dev is not None and as_dev.offer.id == offer.id
    assert comparison is not None
    assert {item.offer.id for item in comparison.offers} == {offer.id}
    assert history is not None and history.comparable is True
    assert [series.store_id for series in history.series] == [store.id]
