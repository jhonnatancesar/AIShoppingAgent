"""EXPLAIN ANALYZE das queries de histórico de preço (TASK-098) contra
volume sintético representativo -- decisão de índice (correção pós-plano,
item 10): implementar primeiro SEM índice novo, medir com dado real, só
criar migration se houver ganho concreto demonstrado.

Volume: 1 Product "alvo" com 6 Offers (uma por loja real, TASK-104A/B),
~1 ano de observações a cada 6h (~1460 por Offer, ~8760 no total) -- e
mais 150 Products "ruído" com 1 Offer cada, ~80 observações cada
(~12000), para que `price_observations` tenha volume real (~21000
linhas) quando o planner decide o plano de consulta, não um punhado de
linhas que mascararia um Seq Scan como aceitável. Seeding roda uma única
vez (um teste só, todas as EXPLAINs) -- volume desse tamanho não precisa
ser duplicado por reexecução.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock
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
from app.offers.query import (
    _fetch_daily_low_points,
    _resolve_current_amount,
    resolve_period_range,
)
from app.products.identity import IDENTITY_VERSION, resolve_product_variant
from app.products.models import Product
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy import insert, select, text
from sqlalchemy.dialects import postgresql

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 27, 15, 0, tzinfo=UTC)
_STORE_CODES = ("pichau", "terabyte", "amazon", "kabum", "magalu", "mercadolivre")
_TARGET_OBSERVATION_INTERVAL = timedelta(hours=6)
_TARGET_SPAN_DAYS = 365
_NOISE_PRODUCT_COUNT = 150
_NOISE_OBSERVATIONS_PER_OFFER = 80


def _capture_statement(async_fn, *args, **kwargs) -> object:
    """Mesma técnica de `tests/test_webapp_offers_router.py` -- captura o
    statement real que a função de produção monta, sem reimplementar a
    query à mão (zero risco de o EXPLAIN divergir do SQL real gerado)."""
    session = MagicMock()
    captured: dict[str, object] = {}

    async def fake_execute(statement, *a, **kw):
        captured["statement"] = statement
        return MagicMock(all=lambda: [])

    async def fake_scalar(statement, *a, **kw):
        captured["statement"] = statement
        return None

    session.execute = fake_execute
    session.scalar = fake_scalar
    asyncio.run(async_fn(session, *args, **kwargs))
    return captured["statement"]


def _explain(integration_database, statement) -> str:
    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    async def _run() -> str:
        async with integration_database.async_sessions() as session:
            result = await session.execute(
                text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) {compiled}")
            )
            return "\n".join(row[0] for row in result.all())

    return asyncio.run(_run())


def _seed_synthetic_volume(integration_database):
    sessions = integration_database.sessions
    with sessions() as session:
        store_ids = {
            code: session.scalar(select(Store.id).where(Store.code == code))
            for code in _STORE_CODES
        }
    with sessions.begin() as session:
        user = User(display_name="TASK-098 EXPLAIN synthetic", role=UserRole.USER)
        session.add(user)
        session.flush()
        mission = Mission(
            user_id=user.id, title="TASK-098 EXPLAIN", status=MissionStatus.ACTIVE
        )
        session.add(mission)
        session.flush()
        mission_id = mission.id
        user_id = user.id

        variant = resolve_product_variant("NVIDIA GeForce RTX 5070 Ti")
        assert variant is not None
        target_product = Product(
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
        session.add(target_product)
        session.flush()
        target_product_id = target_product.id

        target_offers = [
            Offer(
                product_id=target_product_id,
                store_id=store_ids[code],
                url=f"https://example.invalid/task098-explain-target-{code}",
            )
            for code in _STORE_CODES
        ]
        session.add_all(target_offers)
        session.flush()
        target_offer_ids = [offer.id for offer in target_offers]
        anchor_offer_id = target_offer_ids[0]

        session.add_all(
            MissionOfferRelevance(
                mission_id=mission_id,
                offer_id=offer_id,
                classification=OfferRelevance.MATCH,
                classified_at=NOW,
            )
            for offer_id in target_offer_ids
        )

        noise_products = [
            Product(name=f"TASK-098 EXPLAIN noise product {i}")
            for i in range(_NOISE_PRODUCT_COUNT)
        ]
        session.add_all(noise_products)
        session.flush()
        noise_offers = [
            Offer(
                product_id=product.id,
                store_id=store_ids[_STORE_CODES[i % len(_STORE_CODES)]],
                url=f"https://example.invalid/task098-explain-noise-{i}",
            )
            for i, product in enumerate(noise_products)
        ]
        session.add_all(noise_offers)
        session.flush()

        collection_run_rows = []
        observation_rows = []

        def _queue(
            offer_id: object, store_id: object, observed_at: datetime, amount: str
        ) -> None:
            run_id = uuid4()
            collection_run_rows.append(
                {
                    "id": run_id,
                    "mission_id": mission_id,
                    "monitoring_item_id": None,
                    "fairness_owner_user_id": None,
                    "store_id": store_id,
                    "status": CollectionRunStatus.SUCCEEDED,
                    "started_at": observed_at,
                    "finished_at": observed_at,
                }
            )
            decimal_amount = Decimal(amount)
            observation_rows.append(
                {
                    "id": uuid4(),
                    "offer_id": offer_id,
                    "collection_run_id": run_id,
                    "amount": decimal_amount,
                    "currency": "BRL",
                    "shipping_amount": None,
                    "total_amount": decimal_amount,
                    "fulfillment": None,
                    "seller_kind": None,
                    "fulfillment_kind": None,
                    "condition": OfferCondition.NEW,
                    "availability": Availability.AVAILABLE,
                    "observed_at": observed_at,
                }
            )

        for offer, store_code in zip(target_offers, _STORE_CODES, strict=True):
            steps = int(
                timedelta(days=_TARGET_SPAN_DAYS) / _TARGET_OBSERVATION_INTERVAL
            )
            for step in range(steps):
                observed_at = NOW - _TARGET_OBSERVATION_INTERVAL * step
                base = 3800 + (step % 40) * 7 - (hash(store_code) % 5) * 11
                _queue(
                    offer.id, store_ids[store_code], observed_at, f"{max(base, 100)}.00"
                )

        for offer in noise_offers:
            for step in range(_NOISE_OBSERVATIONS_PER_OFFER):
                observed_at = NOW - timedelta(
                    days=step * (_TARGET_SPAN_DAYS / _NOISE_OBSERVATIONS_PER_OFFER)
                )
                _queue(offer.id, offer.store_id, observed_at, "199.90")

        session.execute(insert(CollectionRun), collection_run_rows)
        session.execute(insert(PriceObservation), observation_rows)

    return {
        "user_id": user_id,
        "product_id": target_product_id,
        "anchor_offer_id": anchor_offer_id,
        "total_observations": len(observation_rows),
    }


def test_explain_price_history_queries_use_existing_index(
    integration_database, capsys
) -> None:
    """Seeding roda uma única vez para as 4 EXPLAINs (3 períodos +
    current_amount) -- evita duplicar ~21000 linhas sintéticas por teste."""
    seeded = _seed_synthetic_volume(integration_database)
    with capsys.disabled():
        print(
            "\n----- EXPLAIN ANALYZE: histórico de preço (TASK-098) -----\n"
            f"Volume sintético total em price_observations: "
            f"{seeded['total_observations']}"
        )
        for period in ("1d", "1a", "all"):
            period_range = resolve_period_range(period, now=NOW)
            statement = _capture_statement(
                _fetch_daily_low_points,
                product_id=seeded["product_id"],
                user_id=seeded["user_id"],
                start_utc=period_range.start_utc,
                end_utc=period_range.end_utc,
                reference_currency="BRL",
            )
            plan = _explain(integration_database, statement)
            print(f"\n=== _fetch_daily_low_points period={period} ===")
            print(plan)
            assert "ix_price_observations_offer_observed" in plan, (
                f"period={period}: índice existente não usado -- plano real:\n" + plan
            )

        current_amount_statement = _capture_statement(
            _resolve_current_amount,
            product_id=seeded["product_id"],
            user_id=seeded["user_id"],
            reference_currency="BRL",
        )
        current_amount_plan = _explain(integration_database, current_amount_statement)
        print("\n=== _resolve_current_amount ===")
        print(current_amount_plan)
        assert "ix_price_observations_offer_observed" in current_amount_plan, (
            "current_amount: índice existente não usado -- plano real:\n"
            + current_amount_plan
        )
        print("-------------------------------------------------------------\n")
