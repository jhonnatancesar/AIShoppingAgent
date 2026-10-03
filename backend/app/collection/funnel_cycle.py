"""TASK-136 (passo 3): ciclo de coleta por item de monitoramento -- a barreira
que antecede o funil e a IA.

Cada `(item, loja)` coleta na sua própria agenda; este módulo decide quando
"todas as lojas do item rodaram pelo menos uma vez" (o ciclo fecha) e, fechado,
quais ofertas o funil (`app.collection.funnel`) manda para a IA. Decisões do
usuário (2026-10-01):

- sem tempo limite: o ciclo espera a loja mais lenta;
- loja bloqueada, com erro ou em espera de bloqueio conta como rodada (com erro):
  o ciclo fecha com ela falhando, a missão nunca fica parada por uma loja só;
- alertas também esperam o fechamento (opção A de 2026-10-03).

Estado durável em `MonitoringItem.funnel_closed_at` / `funnel_previous_closed_at`:
coletas com `finished_at` em `(previous, closed]` são o ciclo fechado. O fechamento
é decidido sob `SELECT ... FOR UPDATE` do item, então só um worker fecha; se o
processo cair depois, as `SharedFanOutTask` pendentes continuam no banco e a
retomada/varredura as processa (a escolha é recalculada do banco, sem memória).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collection.funnel import select_for_ai
from app.collection.models import (
    CollectionRun,
    CollectionRunStatus,
    PriceObservation,
    SharedCollectionOffer,
)
from app.missions.models import MonitoringItem, MonitoringItemStore
from app.offers.models import Offer
from app.products.models import Product
from app.stores.models import Store


@dataclass(frozen=True, slots=True)
class _FunnelRawOffer:
    source_code: str
    external_id: str | None
    url: str


@dataclass(frozen=True, slots=True)
class _FunnelRow:
    """Oferta já persistida, no formato que `select_for_ai` lê."""

    offer_id: UUID
    raw_offer: _FunnelRawOffer
    rating_average: Any
    review_count: int | None
    sales: tuple[int, str] | None
    total_amount: Any
    condition: Any
    availability: Any


async def try_close_cycle(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    monitoring_item_id: UUID,
    now: datetime,
) -> bool:
    """Fecha o ciclo do item se todas as lojas habilitadas já rodaram desde o
    último fechamento. Devolve `True` quando ESTA chamada fechou."""
    async with session_factory() as session, session.begin():
        item = await session.scalar(
            select(MonitoringItem)
            .where(MonitoringItem.id == monitoring_item_id)
            .with_for_update()
        )
        if item is None:
            return False
        marker = item.funnel_closed_at
        stores = (
            await session.execute(
                select(
                    MonitoringItemStore.store_id, MonitoringItemStore.next_eligible_at
                )
                .where(
                    MonitoringItemStore.monitoring_item_id == monitoring_item_id,
                    MonitoringItemStore.is_enabled.is_(True),
                )
                .order_by(MonitoringItemStore.store_id)
            )
        ).all()
        if not stores:
            return False
        query = (
            select(CollectionRun.store_id, func.max(CollectionRun.finished_at))
            .where(
                CollectionRun.monitoring_item_id == monitoring_item_id,
                CollectionRun.status != CollectionRunStatus.RUNNING,
                CollectionRun.finished_at.is_not(None),
                CollectionRun.store_id.in_([store_id for store_id, _ in stores]),
            )
            .group_by(CollectionRun.store_id)
        )
        if marker is not None:
            query = query.where(CollectionRun.finished_at > marker)
        latest = {
            store_id: finished for store_id, finished in await session.execute(query)
        }
        if not latest:
            return False
        for store_id, next_eligible_at in stores:
            if store_id in latest:
                continue
            if next_eligible_at is not None and next_eligible_at > now:
                # Loja em espera de bloqueio: conta como rodada, com erro.
                continue
            return False
        item.funnel_previous_closed_at = marker
        item.funnel_closed_at = max(latest.values())
        return True


async def cycle_allows_fan_out(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    monitoring_item_id: UUID,
    run_id: UUID,
    now: datetime,
) -> bool:
    """`True` quando a coleta `run_id` pertence a um ciclo já fechado. Se ainda
    não pertence, tenta fechar o ciclo agora (a última loja pode ter acabado de
    rodar, ou o worker anterior caiu antes de fechar)."""
    for attempt in range(2):
        async with session_factory() as session:
            closed_at = await session.scalar(
                select(MonitoringItem.funnel_closed_at).where(
                    MonitoringItem.id == monitoring_item_id
                )
            )
            finished_at = await session.scalar(
                select(CollectionRun.finished_at).where(CollectionRun.id == run_id)
            )
        if finished_at is None:
            return False
        if closed_at is not None and finished_at <= closed_at:
            return True
        if attempt == 0 and not await try_close_cycle(
            session_factory, monitoring_item_id=monitoring_item_id, now=now
        ):
            return False
    return False


async def cycle_chosen_offer_ids(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    monitoring_item_id: UUID,
    run_id: UUID,
    store_ids: frozenset[UUID] | None = None,
    is_irrelevant: Callable[[Product, UUID], bool] | None = None,
) -> frozenset[UUID] | None:
    """Ofertas do ciclo que o funil manda para a IA (pool de 10 mais populares,
    as 2 mais baratas; 1 se as avaliações apontam claramente uma só).

    `None` = sem funil para esta coleta (ela é anterior ao primeiro ciclo
    registrado, ex.: tarefas pendentes de antes da v1.5): vale o comportamento
    antigo, sem restrição."""
    async with session_factory() as session:
        item = await session.get(MonitoringItem, monitoring_item_id)
        finished_at = await session.scalar(
            select(CollectionRun.finished_at).where(CollectionRun.id == run_id)
        )
        if item is None or finished_at is None or item.funnel_closed_at is None:
            return None
        closed_at = item.funnel_closed_at
        previous = item.funnel_previous_closed_at
        if finished_at > closed_at or (
            previous is not None and finished_at <= previous
        ):
            return None
        query = (
            select(
                Offer, PriceObservation, Store.code, CollectionRun.finished_at, Product
            )
            .select_from(SharedCollectionOffer)
            .join(
                CollectionRun,
                CollectionRun.id == SharedCollectionOffer.collection_run_id,
            )
            .join(Offer, Offer.id == SharedCollectionOffer.offer_id)
            .join(
                PriceObservation,
                PriceObservation.id == SharedCollectionOffer.observation_id,
            )
            .join(Store, Store.id == Offer.store_id)
            .join(Product, Product.id == Offer.product_id)
            .where(
                CollectionRun.monitoring_item_id == monitoring_item_id,
                CollectionRun.status == CollectionRunStatus.SUCCEEDED,
                CollectionRun.finished_at <= closed_at,
            )
            .order_by(CollectionRun.finished_at, CollectionRun.id)
        )
        if previous is not None:
            query = query.where(CollectionRun.finished_at > previous)
        if store_ids is not None:
            query = query.where(Offer.store_id.in_(store_ids))
        rows: dict[UUID, _FunnelRow] = {}
        for (
            offer,
            observation,
            store_code,
            _run_finished_at,
            product,
        ) in await session.execute(query):
            # Já descartada para esta missão (regra fixa ou classificação antiga):
            # não ocupa vaga do funil.
            if is_irrelevant is not None and is_irrelevant(product, offer.id):
                continue
            sales = (
                (int(offer.sales_count), offer.sales_scope)
                if offer.sales_count is not None and offer.sales_scope is not None
                else None
            )
            # Mais recente por oferta (a query vem em ordem de coleta).
            rows[offer.id] = _FunnelRow(
                offer_id=offer.id,
                raw_offer=_FunnelRawOffer(store_code, offer.external_id, offer.url),
                rating_average=offer.rating_average,
                review_count=offer.review_count,
                sales=sales,
                total_amount=observation.total_amount,
                condition=observation.condition,
                availability=observation.availability,
            )
    selection = select_for_ai(list(rows.values()))
    return frozenset(row.offer_id for row in selection.chosen)
