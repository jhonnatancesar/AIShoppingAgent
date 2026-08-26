"""Identidade global da TASK-097 contra PostgreSQL real."""

import asyncio
from datetime import UTC, datetime

import pytest
from app.collection.contracts import RawCollectedOffer
from app.collection.normalization import PriceNormalizer
from app.collection.orchestration import _resolve_offer
from app.offers.models import Offer
from app.products.models import Product
from app.stores.models import Store
from sqlalchemy import func, select

pytestmark = pytest.mark.integration


def test_same_variant_from_all_stores_reuses_one_global_product(
    integration_database,
) -> None:
    async def run() -> tuple[set, int]:
        async with integration_database.async_sessions.begin() as session:
            stores = tuple(await session.scalars(select(Store).order_by(Store.code)))
            # TASK-111: o catálogo seed já inclui Magalu/Mercado Livre
            # (TASK-104A/104B, migrations 20260822_0007/20260822_0008) --
            # assert atualizado para as 6 lojas reais, não mais as 4
            # originais da V1 de coleta. O teste em si continua sobre
            # dedupe global de Product/Offer, agnóstico de quantas lojas
            # existem -- rodar com todas as lojas cadastradas é o
            # comportamento correto, nunca uma lista fixa desatualizada.
            assert {store.code for store in stores} == {
                "amazon",
                "kabum",
                "pichau",
                "terabyte",
                "magalu",
                "mercadolivre",
            }
            normalizer = PriceNormalizer()
            for store in stores:
                item = normalizer.normalize_offer(
                    RawCollectedOffer(
                        source_code=store.code,
                        external_id=f"task097-{store.code}",
                        url=f"https://{store.code}.example.test/s24-ultra-512",
                        title="Samsung Galaxy S24 Ultra 512 GB",
                        raw_price="4999.90",
                        raw_currency="BRL",
                        raw_availability="disponível",
                        collected_at=datetime(2026, 8, 22, 18, 0, tzinfo=UTC),
                    )
                )
                await _resolve_offer(session, store.id, item)

        async with integration_database.async_sessions() as session:
            product_ids = set(await session.scalars(select(Offer.product_id)))
            resolved_count = await session.scalar(
                select(func.count(Product.id)).where(Product.identity_key.is_not(None))
            )
            return product_ids, resolved_count

    product_ids, resolved_count = asyncio.run(run())

    assert len(product_ids) == 1
    assert resolved_count == 1
