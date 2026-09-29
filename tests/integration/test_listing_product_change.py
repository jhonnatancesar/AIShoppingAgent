"""TASK-132 (Parte A) -- o anúncio passou a descrever outro produto, contra
Postgres real: a troca só vale depois de 2 coletas seguidas com o mesmo título
novo, o histórico antigo fica numa Offer arquivada e a relevância recomeça."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from app.collection.contracts import RawCollectedOffer
from app.collection.models import CollectionRun, CollectionRunStatus, PriceObservation
from app.collection.normalization import Availability, PriceNormalizer
from app.collection.orchestration import _resolve_offer
from app.missions.models import Mission, MissionStatus, MonitoringItem  # noqa: F401
from app.offers.models import Offer, OfferIdentityWatch
from app.products.models import Product
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy import func, select

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 28, 10, 0, tzinfo=UTC)
TITLE_A = "Placa de Vídeo NVIDIA GeForce RTX 4060 8GB GDDR6"
TITLE_B = "Placa de Vídeo NVIDIA GeForce RTX 4070 12GB GDDR6X"


def _item(store_code: str, title: str, at: datetime):
    return PriceNormalizer().normalize_offer(
        RawCollectedOffer(
            source_code=store_code,
            external_id="B0D8WH9NG3",
            url="https://amazon.example.test/dp/B0D8WH9NG3",
            title=title,
            raw_price="2999.90",
            raw_currency="BRL",
            raw_availability="disponível",
            collected_at=at,
        )
    )


async def _collect(integration_database, title: str, at: datetime):
    async with integration_database.async_sessions() as session, session.begin():
        store = await session.scalar(select(Store).where(Store.code == "amazon"))
        offer = await _resolve_offer(session, store.id, _item("amazon", title, at))
        return offer.id, store.id


async def _add_observation(session, offer_id, store_id, at: datetime) -> None:
    user = User(display_name="listing-change", role=UserRole.USER)
    session.add(user)
    await session.flush()
    mission = Mission(user_id=user.id, title="m", status=MissionStatus.ACTIVE)
    session.add(mission)
    await session.flush()
    run = CollectionRun(
        id=uuid4(),
        mission_id=mission.id,
        monitoring_item_id=None,
        store_id=store_id,
        status=CollectionRunStatus.SUCCEEDED,
        started_at=at,
        finished_at=at,
    )
    session.add(run)
    await session.flush()
    session.add(
        PriceObservation(
            id=uuid4(),
            offer_id=offer_id,
            collection_run_id=run.id,
            amount=Decimal("2999.90"),
            currency="BRL",
            total_amount=Decimal("2999.90"),
            availability=Availability.AVAILABLE,
            observed_at=at,
            raw_evidence={"title": "seed"},
        )
    )


def test_change_needs_two_consecutive_collections_and_keeps_history(
    integration_database,
) -> None:
    async def run():
        offer_id, store_id = await _collect(integration_database, TITLE_A, T0)
        async with integration_database.async_sessions() as session, session.begin():
            await _add_observation(session, offer_id, store_id, T0)
        # 1ª coleta com o título novo: só vigia
        await _collect(integration_database, TITLE_B, T0 + timedelta(hours=1))
        async with integration_database.async_sessions() as session:
            offer = await session.get(Offer, offer_id)
            product_a = await session.get(Product, offer.product_id)
            watch = await session.get(OfferIdentityWatch, offer_id)
            assert "4060" in product_a.model
            assert watch is not None and watch.sightings == 1
        # a coleta do título novo grava a própria observação
        async with integration_database.async_sessions() as session, session.begin():
            await _add_observation(session, offer_id, store_id, T0 + timedelta(hours=1))
        # 2ª coleta: aplica
        await _collect(integration_database, TITLE_B, T0 + timedelta(hours=2))
        async with integration_database.async_sessions() as session:
            offer = await session.get(Offer, offer_id)
            product_b = await session.get(Product, offer.product_id)
            assert "4070" in product_b.model
            assert await session.get(OfferIdentityWatch, offer_id) is None
            archived = await session.scalar(
                select(Offer).where(Offer.superseded_by_id == offer_id)
            )
            assert archived is not None
            assert archived.external_id.startswith("B0D8WH9NG3~arquivada-")
            old_product = await session.get(Product, archived.product_id)
            assert "4060" in old_product.model
            old_count = await session.scalar(
                select(func.count())
                .select_from(PriceObservation)
                .where(PriceObservation.offer_id == archived.id)
            )
            live_count = await session.scalar(
                select(func.count())
                .select_from(PriceObservation)
                .where(PriceObservation.offer_id == offer_id)
            )
            return old_count, live_count

    old_count, live_count = asyncio.run(run())
    assert old_count == 1, "o histórico do produto antigo ficou na Offer arquivada"
    assert live_count == 1, "o histórico do produto novo recomeça na Offer viva"


def test_change_back_to_original_title_clears_the_watch(integration_database) -> None:
    async def run():
        offer_id, _ = await _collect(integration_database, TITLE_A, T0)
        await _collect(integration_database, TITLE_B, T0 + timedelta(hours=1))
        await _collect(integration_database, TITLE_A, T0 + timedelta(hours=2))
        await _collect(integration_database, TITLE_B, T0 + timedelta(hours=3))
        async with integration_database.async_sessions() as session:
            offer = await session.get(Offer, offer_id)
            product = await session.get(Product, offer.product_id)
            watch = await session.get(OfferIdentityWatch, offer_id)
            archived = await session.scalar(
                select(func.count())
                .select_from(Offer)
                .where(Offer.superseded_by_id == offer_id)
            )
            return product.model, watch.sightings, archived

    model, sightings, archived = asyncio.run(run())
    assert "4060" in model, "oscilação A→B→A→B nunca chega a duas coletas seguidas"
    assert sightings == 1
    assert archived == 0


def test_unrecognized_title_marks_watch_for_ai_without_changing(
    integration_database,
) -> None:
    async def run():
        offer_id, _ = await _collect(integration_database, TITLE_A, T0)
        await _collect(
            integration_database,
            "Produto misterioso sem nenhuma pista de modelo xyz",
            T0 + timedelta(hours=1),
        )
        async with integration_database.async_sessions() as session:
            offer = await session.get(Offer, offer_id)
            product = await session.get(Product, offer.product_id)
            watch = await session.get(OfferIdentityWatch, offer_id)
            return product.model, watch.needs_ai

    model, needs_ai = asyncio.run(run())
    assert "4060" in model
    assert needs_ai is True


class _AI:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request):
        from app.ai_provider import AIResponse

        self.calls += 1
        return AIResponse(
            request_id=request.request_id,
            provider="stub",
            model="stub-identity-model",
            content="{}",
            finished_at=datetime.now(UTC),
        )


def test_sweep_spends_ai_only_on_watched_new_titles_and_caps_attempts(
    integration_database,
) -> None:
    from app.products.listing_title_sweep import sweep_listing_titles

    ai = _AI()

    async def run():
        offer_id, _ = await _collect(integration_database, TITLE_A, T0)
        # título estável: nenhuma vigilância, nenhuma IA
        idle = await sweep_listing_titles(
            integration_database.async_sessions, ai_manager=ai, budget=3
        )
        await _collect(
            integration_database,
            "Produto misterioso sem nenhuma pista de modelo xyz",
            T0 + timedelta(hours=1),
        )
        summaries = [
            await sweep_listing_titles(
                integration_database.async_sessions, ai_manager=ai, budget=3
            )
            for _ in range(5)
        ]
        async with integration_database.async_sessions() as session:
            watch = await session.get(OfferIdentityWatch, offer_id)
            return idle, summaries, watch.ai_attempts

    idle, summaries, attempts = asyncio.run(run())
    assert idle.claimed == 0
    assert [s.claimed for s in summaries] == [1, 1, 1, 0, 0], "no máximo 3 tentativas"
    assert all(s.changed == 0 for s in summaries)
    assert attempts == 3


def test_relevance_restarts_after_the_change(integration_database) -> None:
    from app.collection.models import MissionOfferRelevance
    from app.missions.models import Mission, MissionStatus
    from app.offers.listing_change import apply_listing_change
    from app.products.identity import resolve_product_variant
    from app.products.models import Product

    async def run():
        offer_id, _ = await _collect(integration_database, TITLE_A, T0)
        async with integration_database.async_sessions() as session, session.begin():
            user = User(display_name="rel", role=UserRole.USER)
            session.add(user)
            await session.flush()
            mission = Mission(user_id=user.id, title="m", status=MissionStatus.ACTIVE)
            session.add(mission)
            await session.flush()
            session.add(
                MissionOfferRelevance(
                    mission_id=mission.id,
                    offer_id=offer_id,
                    classification="match",
                    classified_at=T0,
                )
            )
        async with integration_database.async_sessions() as session, session.begin():
            offer = await session.get(Offer, offer_id)
            identity = resolve_product_variant(TITLE_B)
            assert await apply_listing_change(
                session, offer, identity, first_seen_at=T0 + timedelta(hours=1)
            )
        async with integration_database.async_sessions() as session:
            remaining = await session.scalar(
                select(func.count()).select_from(MissionOfferRelevance)
            )
            product = await session.get(
                Product, (await session.get(Offer, offer_id)).product_id
            )
            return remaining, product.model

    remaining, model = asyncio.run(run())
    assert remaining == 0
    assert "4070" in model
