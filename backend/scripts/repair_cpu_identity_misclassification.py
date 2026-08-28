"""Repara Offers cujo Product foi criado com identidade de CPU incorreta
(TASK-114/DEC-105) -- achado real em PROD: `_cpu()` classificava
placa-mãe/cooler/RAM como CPU quando o título só citava compatibilidade
("Suporta processadores AMD Ryzen 9000/8000/7000").

Determinístico e auditável: para cada Offer cujo Product tem
`identity_key` preenchido, recalcula a identidade com o parser ATUAL
(`resolve_product_variant`) a partir do `raw_evidence["title"]` real da
observação mais recente daquela Offer -- nunca um scan genérico, nunca
um UUID fixo. Só toca a Offer se a identidade recalculada MUDAR de
`category=cpu` para "não resolvida" (o caso comprovado do incidente) --
nunca decide por heurística/fuzzy.

Nunca escreve em PriceObservation, MonitoringItem, MissionProductAlertState
ou MarketPriceAssessment -- só `Offer.product_id` e, quando necessário,
cria um Product novo (mesmo caminho que uma coleta nova seguiria).

Uso:
    python -m scripts.repair_cpu_identity_misclassification --dry-run
    python -m scripts.repair_cpu_identity_misclassification --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.database.session import create_async_database_engine, create_async_session_factory
from app.offers.models import Offer
from app.products.identity import IDENTITY_VERSION, resolve_product_variant
from app.products.models import Product
from app.stores.models import Seller  # noqa: F401 -- registra o mapper p/ FK offers.seller_id


async def _affected_offers(session: AsyncSession) -> list[Offer]:
    """Universo determinístico: toda Offer cujo Product tem
    `category='cpu'` e `identity_key` preenchido -- nunca um scan de
    todo o banco, sempre restrito ao tipo de identidade que o bug
    afetava."""
    rows = (
        await session.scalars(
            select(Offer)
            .join(Product, Product.id == Offer.product_id)
            .where(Product.category == "cpu", Product.identity_key.is_not(None))
        )
    ).all()
    return list(rows)


async def _latest_raw_title(session: AsyncSession, offer_id) -> str | None:
    from app.collection.models import PriceObservation

    observation = await session.scalar(
        select(PriceObservation)
        .where(PriceObservation.offer_id == offer_id)
        .order_by(PriceObservation.recorded_at.desc())
        .limit(1)
    )
    if observation is None or not isinstance(observation.raw_evidence, dict):
        return None
    title = observation.raw_evidence.get("title")
    return title if isinstance(title, str) and title.strip() else None


async def run(*, apply: bool) -> None:
    settings = get_settings()
    async_driver = "asyncpg" if sys.platform == "win32" else "psycopg"
    engine = create_async_database_engine(settings, async_driver=async_driver)
    session_factory = create_async_session_factory(engine)
    async with session_factory() as session:
        offers = await _affected_offers(session)
        print(f"Offers com Product category=cpu (identity_key preenchido): {len(offers)}")
        changed = 0
        for offer in offers:
            raw_title = await _latest_raw_title(session, offer.id)
            if raw_title is None:
                print(f"  SKIP {offer.id}: sem raw_evidence.title -- não recalculado")
                continue
            new_identity = resolve_product_variant(raw_title)
            current_product = await session.get(Product, offer.product_id)
            if new_identity is not None and new_identity.identity_key == (
                current_product.identity_key if current_product else None
            ):
                continue  # parser novo concorda com a identidade atual -- nada a fazer
            changed += 1
            action = (
                f"category={new_identity.category}/{new_identity.brand}/{new_identity.model}"
                if new_identity is not None
                else "identidade NÃO resolvida (fallback: Product novo com raw title)"
            )
            print(
                f"  MUDA {offer.id}: Product atual={offer.product_id} "
                f"('{current_product.name if current_product else '?'}') "
                f"raw_title='{raw_title[:80]}' -> {action}"
            )
            if not apply:
                continue
            if new_identity is not None:
                target = await session.scalar(
                    select(Product).where(Product.identity_key == new_identity.identity_key)
                )
                if target is None:
                    target = Product(
                        id=uuid4(),
                        name=new_identity.label[:300],
                        brand=new_identity.brand,
                        model=new_identity.model,
                        display_name=new_identity.label[:300],
                        category=new_identity.category,
                        family=new_identity.family,
                        variant=new_identity.variant,
                        attributes=dict(new_identity.attributes),
                        family_key=new_identity.family_key,
                        identity_key=new_identity.identity_key,
                        identity_version=IDENTITY_VERSION,
                    )
                    session.add(target)
                    await session.flush()
            else:
                target = Product(id=uuid4(), name=raw_title[:300])
                session.add(target)
                await session.flush()
            offer.product_id = target.id
        print(f"Total de Offers a corrigir: {changed}")
        if apply:
            await session.commit()
            print("Aplicado.")
        else:
            await session.rollback()
            print("Dry-run -- nada foi persistido.")
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(apply=args.apply))


if __name__ == "__main__":
    main()
