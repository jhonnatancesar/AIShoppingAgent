"""TASK-136 (passo 1) -- vendas da loja gravadas na Offer, contra Postgres real."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from app.collection.contracts import RawCollectedOffer
from app.collection.normalization import PriceNormalizer
from app.collection.orchestration import _resolve_offer
from app.offers.models import Offer
from app.stores.models import Store
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration

T0 = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
TITLE = "Placa de Vídeo NVIDIA GeForce RTX 4060 8GB GDDR6"


def _item(card_text: str, at: datetime):
    return PriceNormalizer().normalize_offer(
        RawCollectedOffer(
            source_code="amazon",
            external_id="B0SALES",
            url="https://amazon.example.test/dp/B0SALES",
            title=TITLE,
            raw_price="2999.90",
            raw_currency="BRL",
            raw_availability="disponível",
            collected_at=at,
            evidence={"card_text": card_text},
        )
    )


async def _collect(integration_database, card_text: str, at: datetime):
    async with integration_database.async_sessions() as session, session.begin():
        store = await session.scalar(select(Store).where(Store.code == "amazon"))
        offer = await _resolve_offer(session, store.id, _item(card_text, at))
        return offer.id


def test_sales_are_stored_updated_and_never_erased(integration_database) -> None:
    async def run():
        offer_id = await _collect(
            integration_database, "RTX 4060\nMais de 1 mil comprados no mês passado", T0
        )
        await _collect(
            integration_database,
            "RTX 4060\n2 mil comprados no mês passado",
            T0 + timedelta(hours=1),
        )
        await _collect(
            integration_database, "RTX 4060\nsem número", T0 + timedelta(hours=2)
        )
        async with integration_database.async_sessions() as session:
            offer = await session.get(Offer, offer_id)
            return offer.sales_count, offer.sales_scope, offer.sales_observed_at

    count, scope, observed_at = asyncio.run(run())
    assert (count, scope) == (2000, "last_month"), (
        "o último valor lido fica; ausência não apaga"
    )
    assert observed_at == T0 + timedelta(hours=1)


def test_database_requires_the_sales_trio_to_be_complete(integration_database) -> None:
    asyncio.run(_collect(integration_database, "RTX 4060\n+500 vendidos", T0))
    with pytest.raises(IntegrityError):
        with integration_database.sessions.begin() as session:
            session.execute(text("UPDATE offers SET sales_scope = NULL"))
