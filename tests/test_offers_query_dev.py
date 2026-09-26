"""TASK-126/127: caminhos DEV de `app.offers.query` e partes puras/mockáveis
da busca manual de preço histórico -- o comportamento real contra o banco
está em `tests/integration/test_offers_all_users_dev.py` e
`test_historical_bootstrap_manual.py`; aqui ficam as garantias de SQL e de
fiação que não precisam de PostgreSQL."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.collection.relevance import OfferRelevance
from app.core.config import Settings
from app.historical_bootstrap import service as bootstrap_service
from app.historical_bootstrap.models import HistoricalBootstrapStatus
from app.historical_bootstrap.service import (
    ClaimedHistoricalBootstrap,
    claim_manual_historical_bootstrap,
    get_historical_bootstrap_state,
    run_claimed_historical_bootstrap,
)
from app.offers import query
from app.offers.query import (
    DEV_VIEWER,
    AllOfferSummaryDev,
    get_offer_comparison_for_dev,
    get_offer_detail_for_dev,
    get_offer_price_history_for_dev,
    list_all_offers_dev,
)
from app.products.models import Product
from app.users.models import UserRole
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)


def _sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    ).lower()


def _where_sql(clause) -> str:
    return _sql(select(query.Offer.id).where(clause))


# --- sentinela e predicado de acesso ----------------------------------


def test_dev_viewer_is_an_explicit_sentinel_never_none():
    assert repr(DEV_VIEWER) == "DEV_VIEWER"
    with pytest.raises(TypeError):
        query._viewer_access_clause(None)


def test_viewer_access_clause_keeps_user_rule_for_uuid():
    sql = _where_sql(query._viewer_access_clause(uuid4()))
    assert "missions.user_id" in sql
    assert "'match', 'possible_match'" in sql


def test_viewer_access_clause_dev_accepts_any_relevance_any_owner():
    sql = _where_sql(query._viewer_access_clause(DEV_VIEWER))
    assert "mission_offer_relevance" in sql
    assert "missions.user_id" not in sql
    assert "possible_match" not in sql


# --- detalhe DEV ------------------------------------------------------


def test_offer_for_dev_statement_has_no_owner_or_relevance_filter():
    sql = _sql(query._offer_for_dev_statement(offer_id=uuid4()))
    assert "offers.id =" in sql
    assert "exists" in sql
    assert "missions.user_id" not in sql
    assert "possible_match" not in sql


def test_get_offer_detail_for_dev_uses_dev_statement():
    offer, product, store = (
        SimpleNamespace(id=uuid4()),
        SimpleNamespace(id=uuid4()),
        SimpleNamespace(id=uuid4()),
    )
    execute_result = MagicMock()
    execute_result.first.return_value = (offer, product, store, None)
    session = MagicMock()
    session.execute = AsyncMock(return_value=execute_result)
    session.scalar = AsyncMock(return_value=None)

    detail = asyncio.run(get_offer_detail_for_dev(session, offer_id=uuid4()))

    assert detail is not None and detail.offer is offer
    assert detail.observation is None and detail.installments == ()
    executed = session.execute.await_args.args[0]
    assert "missions.user_id" not in _sql(executed)


def test_get_offer_detail_rejects_unknown_viewer():
    with pytest.raises(TypeError):
        asyncio.run(query._get_offer_detail(MagicMock(), offer_id=uuid4(), viewer="x"))


def test_comparison_for_dev_without_identity_returns_empty(monkeypatch):
    anchor = SimpleNamespace(product=SimpleNamespace(id=uuid4(), identity_key=None))
    detail = AsyncMock(return_value=anchor)
    monkeypatch.setattr(query, "_get_offer_detail", detail)

    comparison = asyncio.run(
        get_offer_comparison_for_dev(MagicMock(), offer_id=uuid4())
    )

    assert comparison.offers == ()
    assert detail.await_args.kwargs["viewer"] is DEV_VIEWER


def test_price_history_for_dev_passes_dev_viewer(monkeypatch):
    detail = AsyncMock(return_value=None)
    monkeypatch.setattr(query, "_get_offer_detail", detail)

    history = asyncio.run(
        get_offer_price_history_for_dev(
            MagicMock(), offer_id=uuid4(), period="1m", now=NOW
        )
    )

    assert history is None
    assert detail.await_args.kwargs["viewer"] is DEV_VIEWER


# --- listagem DEV (TASK-126) -----------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"search": " RTX "}, "'rtx'"),
        ({"store_code": "kabum"}, "stores.code = 'kabum'"),
        ({"condition": "new"}, "price_observations.condition = 'new'"),
        ({"availability": "available"}, "price_observations.availability"),
        ({"sort": "price_asc"}, "total_amount asc nulls last"),
        ({"sort": "price_desc"}, "total_amount desc nulls last"),
        ({"sort": "recent"}, "offers.last_seen_at desc"),
    ],
)
def test_all_offers_statement_dev_applies_same_filters_as_user_list(kwargs, expected):
    sql = _sql(query._all_offers_statement_dev(**kwargs))
    assert expected in sql
    assert "missions.user_id" not in sql
    assert "offers.superseded_by_id is null" in sql


def test_list_all_offers_dev_maps_rows_with_classification():
    offer, product, store = (
        SimpleNamespace(id=uuid4()),
        SimpleNamespace(id=uuid4()),
        SimpleNamespace(id=uuid4()),
    )
    execute_result = MagicMock()
    execute_result.all.return_value = [
        (offer, product, store, None, None, OfferRelevance.NO_MATCH)
    ]
    session = MagicMock()
    session.scalar = AsyncMock(return_value=1)
    session.execute = AsyncMock(return_value=execute_result)

    items, total = asyncio.run(
        list_all_offers_dev(
            session,
            search=None,
            store_code=None,
            condition=None,
            availability=None,
            sort="recent",
            limit=24,
            offset=0,
        )
    )

    assert total == 1
    assert items == (
        AllOfferSummaryDev(
            offer=offer,
            product=product,
            store=store,
            seller=None,
            observation=None,
            classification=OfferRelevance.NO_MATCH,
        ),
    )


# --- busca manual de preço histórico (TASK-127) ----------------------


def _claimed() -> ClaimedHistoricalBootstrap:
    return ClaimedHistoricalBootstrap(
        bootstrap_id=uuid4(),
        product=Product(id=uuid4(), name="RTX 5070 Ti"),
        has_existing_references=False,
    )


def test_get_historical_bootstrap_state_reads_the_product_scope():
    session = MagicMock()
    session.scalar = AsyncMock(return_value="row")

    assert asyncio.run(get_historical_bootstrap_state(session, product_id=uuid4())) == (
        "row"
    )
    sql = _sql(session.scalar.await_args.args[0])
    assert "historical_bootstraps.condition = 'new'" in sql
    assert "historical_bootstraps.currency = 'brl'" in sql


@pytest.mark.parametrize(("force", "mode"), [(False, "manual"), (True, "manual_force")])
def test_claim_manual_maps_force_to_mode_and_returns_claim(monkeypatch, force, mode):
    claimed = _claimed()
    claim = AsyncMock(return_value=claimed)
    monkeypatch.setattr(bootstrap_service, "_claim_bootstrap", claim)

    result = asyncio.run(
        claim_manual_historical_bootstrap(
            "factory",
            product_id=claimed.product.id,
            now=NOW,
            revalidation_days=90,
            lease_seconds=300,
            force=force,
        )
    )

    assert result is claimed
    assert claim.await_args.kwargs["mode"] == mode


def test_claim_manual_refused_returns_none(monkeypatch):
    monkeypatch.setattr(
        bootstrap_service,
        "_claim_bootstrap",
        AsyncMock(return_value=HistoricalBootstrapStatus.PROCESSING),
    )

    result = asyncio.run(
        claim_manual_historical_bootstrap(
            "factory",
            product_id=uuid4(),
            now=NOW,
            revalidation_days=90,
            lease_seconds=300,
            force=True,
        )
    )

    assert result is None


def _run(claimed):
    return asyncio.run(
        run_claimed_historical_bootstrap(
            "factory",
            claimed,
            search=lambda: None,
            fetch=None,
            ai=MagicMock(),
            profile=UserRole.USER,
            now=NOW,
            failure_backoff_minutes=15,
            failure_backoff_max_minutes=360,
        )
    )


def test_run_claimed_returns_status(monkeypatch):
    monkeypatch.setattr(
        bootstrap_service,
        "_execute_claimed_bootstrap",
        AsyncMock(return_value=HistoricalBootstrapStatus.COMPLETED_WITH_REFERENCES),
    )

    assert _run(_claimed()) is HistoricalBootstrapStatus.COMPLETED_WITH_REFERENCES


def test_run_claimed_never_propagates_unexpected_error(monkeypatch):
    monkeypatch.setattr(
        bootstrap_service,
        "_execute_claimed_bootstrap",
        AsyncMock(side_effect=RuntimeError("banco caiu")),
    )

    assert _run(_claimed()) is None


def test_router_background_runner_uses_settings_backoff_and_core_search(monkeypatch):
    from app.webapp import offers_router

    run = AsyncMock()
    built = MagicMock(return_value="search-manager")
    monkeypatch.setattr(offers_router, "run_claimed_historical_bootstrap", run)
    monkeypatch.setattr(offers_router, "build_web_search_manager", built)
    settings = Settings(
        cesar_core_api_key_file=Path("fake-core-key"),
        market_assessment_failure_backoff_minutes=20,
        market_assessment_failure_backoff_max_minutes=200,
    )
    claimed = _claimed()

    asyncio.run(
        offers_router._run_manual_historical_search(
            "factory",
            claimed,
            ai="ai",
            fetch=None,
            profile=UserRole.DEV,
            settings=settings,
            now=NOW,
        )
    )

    kwargs = run.await_args.kwargs
    assert run.await_args.args == ("factory", claimed)
    assert kwargs["profile"] is UserRole.DEV
    assert kwargs["failure_backoff_minutes"] == 20
    assert kwargs["failure_backoff_max_minutes"] == 200
    assert kwargs["search"]() == "search-manager"
    built.assert_called_once_with(settings)


def test_web_async_session_factory_is_the_shared_process_factory(monkeypatch):
    from app.database import dependency

    monkeypatch.setattr(dependency, "_get_web_async_session_factory", lambda: "shared")

    assert dependency.get_web_async_session_factory() == "shared"
