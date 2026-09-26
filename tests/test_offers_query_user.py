"""Listagens USER de `app.offers.query` sem banco: SQL gerado (posse +
`ACCESSIBLE_RELEVANCE` sempre presentes) e o mapeamento das linhas. O
comportamento real contra PostgreSQL continua em
`tests/integration/test_offer_relevance_eligibility.py`."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.missions.models import VariantSelectionMode
from app.offers.query import (
    MissionOfferLink,
    UserOfferSummary,
    _comparison_key,
    list_current_offer_links_for_mission,
    list_user_offers,
    user_offers_statement,
)
from app.products.identity import ProductRequestKind
from sqlalchemy.dialects import postgresql

NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)


def _sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    ).lower()


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"search": " Galaxy "}, "'galaxy'"),
        ({"store_code": "amazon"}, "stores.code = 'amazon'"),
        ({"condition": "used"}, "price_observations.condition = 'used'"),
        ({"availability": "unavailable"}, "price_observations.availability"),
        ({"sort": "price_asc"}, "total_amount asc nulls last"),
        ({"sort": "price_desc"}, "total_amount desc nulls last"),
    ],
)
def test_user_offers_statement_filters_keep_owner_and_relevance(kwargs, expected):
    sql = _sql(user_offers_statement(user_id=uuid4(), **kwargs))
    assert expected in sql
    assert "missions.user_id" in sql
    assert "'match', 'possible_match'" in sql


def test_list_user_offers_maps_rows_and_total():
    offer, product, store = (SimpleNamespace(id=uuid4()) for _ in range(3))
    execute_result = MagicMock()
    execute_result.all.return_value = [(offer, product, store, None, None)]
    session = MagicMock()
    session.scalar = AsyncMock(return_value=None)
    session.execute = AsyncMock(return_value=execute_result)

    items, total = asyncio.run(
        list_user_offers(
            session,
            user_id=uuid4(),
            search=None,
            store_code=None,
            condition=None,
            availability=None,
            sort="recent",
            limit=24,
            offset=0,
        )
    )

    assert total == 0  # `None` do COUNT vira 0, nunca propaga
    assert items == (UserOfferSummary(offer, product, store, None, None),)


def test_mission_links_pending_family_selection_returns_nothing():
    session = MagicMock()
    session.scalar = AsyncMock(
        return_value=SimpleNamespace(
            request_kind=ProductRequestKind.PRODUCT_FAMILY.value,
            variant_selection_mode=VariantSelectionMode.PENDING,
        )
    )
    session.execute = AsyncMock(side_effect=AssertionError("não deveria consultar"))

    result = asyncio.run(
        list_current_offer_links_for_mission(
            session, mission_id=uuid4(), user_id=uuid4()
        )
    )

    assert result == ()


def test_mission_links_keep_only_latest_offer_per_store():
    store_a, store_b = SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4())
    product = SimpleNamespace(id=uuid4())
    newest_a = SimpleNamespace(id=uuid4(), last_seen_at=NOW)
    older_a = SimpleNamespace(id=uuid4(), last_seen_at=NOW - timedelta(days=1))
    only_b = SimpleNamespace(id=uuid4(), last_seen_at=NOW - timedelta(days=3))
    execute_result = MagicMock()
    # Mesma ordem do SQL real: loja, `last_seen_at` desc.
    execute_result.all.return_value = [
        (newest_a, product, store_a, "new"),
        (older_a, product, store_a, "used"),
        (only_b, product, store_b, None),
    ]
    session = MagicMock()
    session.scalar = AsyncMock(return_value=None)
    session.execute = AsyncMock(return_value=execute_result)

    links = asyncio.run(
        list_current_offer_links_for_mission(
            session, mission_id=uuid4(), user_id=uuid4()
        )
    )

    assert links == (
        MissionOfferLink(
            offer=newest_a, product=product, store=store_a, condition="new"
        ),
        MissionOfferLink(offer=only_b, product=product, store=store_b, condition=None),
    )
    sql = _sql(session.execute.await_args.args[0])
    assert "missions.user_id" in sql
    assert "'match', 'possible_match'" in sql


def test_comparison_key_ranks_commercial_quality_before_price():
    """Dentro da mesma loja: novo > desconhecido > recondicionado > usado;
    depois plataforma > parceiro; depois disponível; só então preço.
    Sem observação vai sempre para o fim, nunca na frente."""
    store = SimpleNamespace(code="kabum")

    def row(condition, seller_kind="platform", availability="available", total="100"):
        observation = SimpleNamespace(
            condition=condition,
            seller_kind=seller_kind,
            availability=availability,
            total_amount=Decimal(total),
            amount=Decimal(total),
        )
        return (SimpleNamespace(id=uuid4()), store, None, observation)

    cheap_used = row("used", total="50")
    new_partner = row("new", seller_kind="marketplace_partner")
    new_platform_expensive = row("new", total="999")
    unavailable_new = row("new", availability="unavailable", total="10")
    no_observation = (SimpleNamespace(id=uuid4()), store, None, None)

    ranked = sorted(
        [
            cheap_used,
            no_observation,
            unavailable_new,
            new_partner,
            new_platform_expensive,
        ],
        key=_comparison_key,
    )

    assert ranked == [
        new_platform_expensive,
        unavailable_new,
        new_partner,
        cheap_used,
        no_observation,
    ]
