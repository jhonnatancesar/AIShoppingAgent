"""Consultas USER de ofertas, sempre escopadas por missão do proprietário."""

import calendar
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Date, and_, cast, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.collection.contracts import OfferCondition
from app.collection.models import (
    MissionOfferRelevance,
    OfferInstallmentOption,
    PriceObservation,
)
from app.collection.normalization import Availability
from app.collection.relevance import OfferRelevance
from app.missions.models import (
    Mission,
    MissionCriteria,
    MissionProductSelection,
    VariantSelectionMode,
)
from app.offers.models import Offer
from app.products.identity import ProductRequestKind
from app.products.models import Product
from app.stores.models import Seller, Store

ACCESSIBLE_RELEVANCE = (
    OfferRelevance.MATCH,
    OfferRelevance.POSSIBLE_MATCH,
)


@dataclass(frozen=True, slots=True)
class UserOfferDetail:
    offer: Offer
    product: Product
    store: Store
    seller: Seller | None
    observation: PriceObservation | None
    installments: tuple[OfferInstallmentOption, ...]


@dataclass(frozen=True, slots=True)
class MissionOfferLink:
    offer: Offer
    product: Product
    store: Store
    condition: OfferCondition | None
    """`None` só quando a oferta ainda não tem nenhuma `PriceObservation`
    (recém-descoberta, sem coleta de preço ainda) -- nunca confundir com
    `OfferCondition.UNKNOWN` (observação existe, condição indeterminada).
    Subtask 3 (auditoria GG Oferta): antes, a tela de missão nem carregava
    esse dado."""


@dataclass(frozen=True, slots=True)
class UserOfferSummary:
    offer: Offer
    product: Product
    store: Store
    seller: Seller | None
    observation: PriceObservation | None


@dataclass(frozen=True, slots=True)
class UserComparisonOffer:
    offer: Offer
    store: Store
    seller: Seller | None
    observation: PriceObservation | None
    installments: tuple[OfferInstallmentOption, ...]


@dataclass(frozen=True, slots=True)
class UserOfferComparison:
    product: Product
    offers: tuple[UserComparisonOffer, ...]


def relevance_matches_current_criteria(*, criteria, product):
    """Regra única de elegibilidade (subtask 2): uma classificação
    histórica MATCH/POSSIBLE_MATCH só continua contando se o produto
    ainda corresponde à família/variante/seleção ATUAIS da missão que a
    gerou -- nunca confia perpetuamente numa classificação antiga.

    Até esta correção, `list_current_offer_links_for_mission` já aplicava
    esta regra só para a tela de uma missão (TASK-097); `_accessible_offer_exists`
    e `offer_for_user_statement` (tela geral de Ofertas e detalhe de
    oferta) nunca a reaplicavam, então uma oferta de variante/família
    diferente da selecionada podia continuar aparecendo fora do contexto
    da missão para sempre. Pública (sem `_`) desde a Subtask 14 -- também
    reaproveitada por `app.missions.query.count_relevant_offers_by_mission`
    (lista de Missões da web), nunca mais duas implementações
    independentes do mesmo critério.

    `criteria` pode vir de um outerjoin (missão sem `MissionCriteria`
    ainda, caso não deveria existir na prática mas não é assumido);
    `criteria.mission_id` (coluna NOT NULL) é o sentinela de "nenhuma
    linha casou". Fiel ao comportamento já existente: só `request_kind
    == PRODUCT_FAMILY` recebe a checagem extra -- `SPECIFIC_PRODUCT` e
    `GENERIC_CATEGORY` continuam confiando só na classificação."""
    return or_(
        criteria.mission_id.is_(None),
        criteria.request_kind != ProductRequestKind.PRODUCT_FAMILY.value,
        and_(
            product.identity_key.is_not(None),
            product.family_key == criteria.requested_family_key,
            or_(
                criteria.requested_variant.is_(None),
                product.variant == criteria.requested_variant,
            ),
            or_(
                criteria.variant_selection_mode != VariantSelectionMode.SELECTED,
                exists(
                    select(MissionProductSelection.product_id).where(
                        MissionProductSelection.mission_id == criteria.mission_id,
                        MissionProductSelection.product_id == product.id,
                    )
                ),
            ),
        ),
    )


def _accessible_offer_exists(*, user_id: UUID):
    relevant_product = aliased(Product)
    return exists(
        select(MissionOfferRelevance.offer_id)
        .join(Mission, Mission.id == MissionOfferRelevance.mission_id)
        .outerjoin(MissionCriteria, MissionCriteria.mission_id == Mission.id)
        .join(relevant_product, relevant_product.id == Offer.product_id)
        .where(
            MissionOfferRelevance.offer_id == Offer.id,
            Mission.user_id == user_id,
            MissionOfferRelevance.classification.in_(ACCESSIBLE_RELEVANCE),
            relevance_matches_current_criteria(
                criteria=MissionCriteria, product=relevant_product
            ),
        )
    )


def user_offers_statement(
    *,
    user_id: UUID,
    search: str | None = None,
    store_code: str | None = None,
    condition: str | None = None,
    availability: str | None = None,
    sort: str = "recent",
):
    """Lista Offers únicas acessíveis; NO_MATCH/missão alheia falham fechados."""
    latest_observation_id = (
        select(PriceObservation.id)
        .where(PriceObservation.offer_id == Offer.id)
        .order_by(PriceObservation.observed_at.desc(), PriceObservation.id.desc())
        .limit(1)
        .correlate(Offer)
        .scalar_subquery()
    )
    statement = (
        select(Offer, Product, Store, Seller, PriceObservation)
        .join(Product, Product.id == Offer.product_id)
        .join(Store, Store.id == Offer.store_id)
        .outerjoin(Seller, Seller.id == Offer.seller_id)
        .outerjoin(PriceObservation, PriceObservation.id == latest_observation_id)
        .where(_accessible_offer_exists(user_id=user_id))
    )
    if search:
        statement = statement.where(
            func.lower(func.coalesce(Product.display_name, Product.name)).contains(
                search.strip().lower()
            )
        )
    if store_code:
        statement = statement.where(Store.code == store_code)
    if condition:
        statement = statement.where(PriceObservation.condition == condition)
    if availability:
        statement = statement.where(PriceObservation.availability == availability)
    if sort == "price_asc":
        statement = statement.order_by(
            PriceObservation.total_amount.asc().nulls_last(), Offer.id
        )
    elif sort == "price_desc":
        statement = statement.order_by(
            PriceObservation.total_amount.desc().nulls_last(), Offer.id
        )
    else:
        statement = statement.order_by(Offer.last_seen_at.desc(), Offer.id)
    return statement


async def list_user_offers(
    session: AsyncSession,
    *,
    user_id: UUID,
    search: str | None,
    store_code: str | None,
    condition: str | None,
    availability: str | None,
    sort: str,
    limit: int,
    offset: int,
) -> tuple[tuple[UserOfferSummary, ...], int]:
    base = user_offers_statement(
        user_id=user_id,
        search=search,
        store_code=store_code,
        condition=condition,
        availability=availability,
        sort=sort,
    )
    total = await session.scalar(
        select(func.count()).select_from(base.order_by(None).subquery())
    )
    rows = (await session.execute(base.limit(limit).offset(offset))).all()
    return (
        tuple(
            UserOfferSummary(
                offer=offer,
                product=product,
                store=store,
                seller=seller,
                observation=observation,
            )
            for offer, product, store, seller, observation in rows
        ),
        int(total or 0),
    )


def offer_for_user_statement(*, offer_id: UUID, user_id: UUID):
    """Statement fail-closed: NO_MATCH, missões alheias e relevância
    desatualizada (produto que não corresponde mais à família/variante/
    seleção atual da missão, ver `relevance_matches_current_criteria`)
    nunca autorizam."""
    return (
        select(Offer, Product, Store, Seller)
        .join(Product, Product.id == Offer.product_id)
        .join(Store, Store.id == Offer.store_id)
        .outerjoin(Seller, Seller.id == Offer.seller_id)
        .join(
            MissionOfferRelevance,
            MissionOfferRelevance.offer_id == Offer.id,
        )
        .join(Mission, Mission.id == MissionOfferRelevance.mission_id)
        .outerjoin(MissionCriteria, MissionCriteria.mission_id == Mission.id)
        .where(
            Offer.id == offer_id,
            Mission.user_id == user_id,
            MissionOfferRelevance.classification.in_(ACCESSIBLE_RELEVANCE),
            relevance_matches_current_criteria(
                criteria=MissionCriteria, product=Product
            ),
        )
        .order_by(Mission.id)
        .limit(1)
    )


async def get_offer_detail_for_user(
    session: AsyncSession, *, offer_id: UUID, user_id: UUID
) -> UserOfferDetail | None:
    row = (
        await session.execute(
            offer_for_user_statement(offer_id=offer_id, user_id=user_id)
        )
    ).first()
    if row is None:
        return None
    offer, product, store, seller = row
    observation = await session.scalar(
        select(PriceObservation)
        .where(PriceObservation.offer_id == offer.id)
        .order_by(PriceObservation.observed_at.desc(), PriceObservation.id.desc())
        .limit(1)
    )
    installments: tuple[OfferInstallmentOption, ...] = ()
    if observation is not None:
        installments = tuple(
            await session.scalars(
                select(OfferInstallmentOption)
                .where(OfferInstallmentOption.price_observation_id == observation.id)
                .order_by(
                    OfferInstallmentOption.is_highlighted.desc(),
                    OfferInstallmentOption.installment_count.asc(),
                    OfferInstallmentOption.id.asc(),
                )
            )
        )
    return UserOfferDetail(
        offer=offer,
        product=product,
        store=store,
        seller=seller,
        observation=observation,
        installments=installments,
    )


_COMPARISON_CONDITION_RANK = {"new": 0, "unknown": 1, "refurbished": 2, "used": 3}
_COMPARISON_PARTY_RANK = {"platform": 0, "marketplace_partner": 1, "unknown": 2}
_COMPARISON_AVAILABILITY_RANK = {"available": 0, "unknown": 1, "unavailable": 2}
_COMPARISON_PER_STORE_LIMIT = 5


def comparison_offers_statement(*, product_id: UUID, user_id: UUID):
    """Mesmo Product global e ownership por Offer; identidade aproximada é proibida."""
    latest_observation_id = (
        select(PriceObservation.id)
        .where(PriceObservation.offer_id == Offer.id)
        .order_by(PriceObservation.observed_at.desc(), PriceObservation.id.desc())
        .limit(1)
        .correlate(Offer)
        .scalar_subquery()
    )
    return (
        select(Offer, Store, Seller, PriceObservation)
        .join(Store, Store.id == Offer.store_id)
        .outerjoin(Seller, Seller.id == Offer.seller_id)
        .outerjoin(PriceObservation, PriceObservation.id == latest_observation_id)
        .where(
            Offer.product_id == product_id,
            _accessible_offer_exists(user_id=user_id),
        )
        .order_by(Store.code, Offer.id)
    )


def _comparison_key(row: tuple[Offer, Store, Seller | None, PriceObservation | None]):
    offer, store, _seller, observation = row
    if observation is None:
        return (store.code, 9, 9, 9, float("inf"), float("inf"), str(offer.id))
    condition = getattr(observation.condition, "value", observation.condition)
    seller_kind = getattr(observation.seller_kind, "value", observation.seller_kind)
    availability = getattr(observation.availability, "value", observation.availability)
    return (
        store.code,
        _COMPARISON_CONDITION_RANK.get(condition, 9),
        _COMPARISON_PARTY_RANK.get(seller_kind, 9),
        _COMPARISON_AVAILABILITY_RANK.get(availability, 9),
        observation.total_amount,
        observation.amount,
        str(offer.id),
    )


async def get_offer_comparison_for_user(
    session: AsyncSession, *, offer_id: UUID, user_id: UUID
) -> UserOfferComparison | None:
    anchor = await get_offer_detail_for_user(
        session, offer_id=offer_id, user_id=user_id
    )
    if anchor is None:
        return None
    if anchor.product.identity_key is None:
        return UserOfferComparison(product=anchor.product, offers=())
    rows = list(
        (
            await session.execute(
                comparison_offers_statement(
                    product_id=anchor.product.id, user_id=user_id
                )
            )
        ).all()
    )
    counts: dict[UUID, int] = {}
    selected = []
    for row in sorted(rows, key=_comparison_key):
        store_id = row[1].id
        if counts.get(store_id, 0) >= _COMPARISON_PER_STORE_LIMIT:
            continue
        counts[store_id] = counts.get(store_id, 0) + 1
        selected.append(row)
    result = []
    for offer, store, seller, observation in selected:
        installments: tuple[OfferInstallmentOption, ...] = ()
        if observation is not None:
            installments = tuple(
                await session.scalars(
                    select(OfferInstallmentOption)
                    .where(
                        OfferInstallmentOption.price_observation_id == observation.id
                    )
                    .order_by(
                        OfferInstallmentOption.is_highlighted.desc(),
                        OfferInstallmentOption.installment_count.desc(),
                        OfferInstallmentOption.id,
                    )
                )
            )
        result.append(
            UserComparisonOffer(offer, store, seller, observation, installments)
        )
    return UserOfferComparison(product=anchor.product, offers=tuple(result))


async def list_current_offer_links_for_mission(
    session: AsyncSession, *, mission_id: UUID, user_id: UUID
) -> tuple[MissionOfferLink, ...]:
    """Ofertas relevantes da coleta mais recente de cada loja da missão."""
    criteria = await session.scalar(
        select(MissionCriteria).where(MissionCriteria.mission_id == mission_id)
    )
    if (
        criteria is not None
        and criteria.request_kind == ProductRequestKind.PRODUCT_FAMILY.value
        and criteria.variant_selection_mode is VariantSelectionMode.PENDING
    ):
        return ()
    latest_observation_id = (
        select(PriceObservation.id)
        .where(PriceObservation.offer_id == Offer.id)
        .order_by(PriceObservation.observed_at.desc(), PriceObservation.id.desc())
        .limit(1)
        .correlate(Offer)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(Offer, Product, Store, PriceObservation.condition)
            .join(Product, Product.id == Offer.product_id)
            .join(Store, Store.id == Offer.store_id)
            .join(
                MissionOfferRelevance,
                MissionOfferRelevance.offer_id == Offer.id,
            )
            .join(Mission, Mission.id == MissionOfferRelevance.mission_id)
            .outerjoin(MissionCriteria, MissionCriteria.mission_id == Mission.id)
            .outerjoin(
                PriceObservation, PriceObservation.id == latest_observation_id
            )
            .where(
                Mission.id == mission_id,
                Mission.user_id == user_id,
                MissionOfferRelevance.classification.in_(ACCESSIBLE_RELEVANCE),
                relevance_matches_current_criteria(
                    criteria=MissionCriteria, product=Product
                ),
            )
            .order_by(Store.code, Offer.last_seen_at.desc(), Offer.id)
        )
    ).all()
    latest_seen_by_store: dict[UUID, datetime] = {}
    for offer, _product, store, _condition in rows:
        latest_seen_by_store.setdefault(store.id, offer.last_seen_at)
    return tuple(
        MissionOfferLink(offer=offer, product=product, store=store, condition=condition)
        for offer, product, store, condition in rows
        if offer.last_seen_at == latest_seen_by_store[store.id]
    )


async def count_relevant_offers_by_mission(
    session: AsyncSession, *, mission_ids: Sequence[UUID]
) -> dict[UUID, int]:
    """Quantidade de ofertas relevantes por missão, para a Lista de
    Missões da web (Subtask 14, `MissionListItem.relevant_offer_count`).

    Uma única consulta agregada (`GROUP BY`) para todas as missões da
    página -- nunca uma consulta por missão. Reaproveita exatamente a
    mesma classificação persistida (`MissionOfferRelevance`) e a mesma
    regra de elegibilidade (`relevance_matches_current_criteria`) de
    `list_current_offer_links_for_mission`; não recalcula/reexecuta
    matching, só agrega o que já está gravado. Não replica a dedupe
    "última por loja" daquela função (não faz sentido para uma
    contagem), então o número aqui é a quantidade de ofertas distintas
    elegíveis, podendo ser maior que a quantidade de cards que a tela de
    detalhe mostraria (que agrupa por loja)."""
    if not mission_ids:
        return {}
    rows = (
        await session.execute(
            select(
                MissionOfferRelevance.mission_id,
                func.count(func.distinct(MissionOfferRelevance.offer_id)),
            )
            .join(Offer, Offer.id == MissionOfferRelevance.offer_id)
            .join(Product, Product.id == Offer.product_id)
            .outerjoin(
                MissionCriteria,
                MissionCriteria.mission_id == MissionOfferRelevance.mission_id,
            )
            .where(
                MissionOfferRelevance.mission_id.in_(mission_ids),
                MissionOfferRelevance.classification.in_(ACCESSIBLE_RELEVANCE),
                relevance_matches_current_criteria(
                    criteria=MissionCriteria, product=Product
                ),
            )
            .group_by(MissionOfferRelevance.mission_id)
        )
    ).all()
    return {mission_id: count for mission_id, count in rows}


# ---------------------------------------------------------------------------
# Histórico de preço por Product, ancorado em Offer (TASK-098).
#
# Entrada HTTP continua Offer-level (mesma autorização já existente,
# `authorize(..., resource_type="offer", ...)`); a agregação interna é
# Product-level (mesmo Product global entre lojas, TASK-097), reaproveitando
# `_accessible_offer_exists` como cláusula EXISTS correlata dentro da própria
# query -- nunca materializando lista de UUIDs em Python (mesmo padrão de
# `comparison_offers_statement`, TASK-103).
# ---------------------------------------------------------------------------

PriceHistoryPeriod = Literal["1d", "7d", "1m", "6m", "1a", "all"]
_SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")
_TWO_PLACES = Decimal("0.01")
_FOUR_PLACES = Decimal("0.0001")


def _subtract_calendar_months(value: datetime, months: int) -> datetime:
    """Aritmética de calendário real -- nunca aproximação fixa de 30/180/365
    dias. Clampa o dia ao último dia válido do mês de destino (ex.: 31/08
    menos 6 meses cai em 28 ou 29/02, conforme o ano)."""
    total_months = value.year * 12 + (value.month - 1) - months
    year, month0 = divmod(total_months, 12)
    month = month0 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


@dataclass(frozen=True, slots=True)
class PeriodRange:
    period: PriceHistoryPeriod
    start_utc: datetime | None
    """`None` somente para `period="all"` -- nunca vira um predicado SQL
    `>= NULL` (que eliminaria todas as linhas); o predicado de limite
    inferior simplesmente não é adicionado à query nesse caso."""
    end_utc: datetime


def resolve_period_range(period: PriceHistoryPeriod, *, now: datetime) -> PeriodRange:
    """Fronteiras calculadas no dia comercial de `America/Sao_Paulo`, nunca
    em UTC -- `now` precisa ser timezone-aware; a conversão para UTC só
    acontece no final, para comparar com `PriceObservation.observed_at`."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now_local = now.astimezone(_SAO_PAULO_TZ)
    today_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "1d":
        start_local = today_start_local
    elif period == "7d":
        start_local = today_start_local - timedelta(days=6)
    elif period == "1m":
        start_local = _subtract_calendar_months(today_start_local, 1)
    elif period == "6m":
        start_local = _subtract_calendar_months(today_start_local, 6)
    elif period == "1a":
        start_local = _subtract_calendar_months(today_start_local, 12)
    elif period == "all":
        return PeriodRange(period=period, start_utc=None, end_utc=now)
    else:
        raise ValueError(f"unknown price history period: {period}")
    return PeriodRange(
        period=period, start_utc=start_local.astimezone(UTC), end_utc=now
    )


@dataclass(frozen=True, slots=True)
class PriceHistoryPoint:
    day: date
    amount: Decimal
    observation_id: UUID
    offer_id: UUID


@dataclass(frozen=True, slots=True)
class PriceHistorySeries:
    store_id: UUID
    store_code: str
    store_name: str
    points: tuple[PriceHistoryPoint, ...]


@dataclass(frozen=True, slots=True)
class PriceHistoryMetrics:
    current_amount: Decimal | None
    min_amount: Decimal | None
    max_amount: Decimal | None
    average_amount: Decimal | None
    variation_percent: Decimal | None


@dataclass(frozen=True, slots=True)
class OfferPriceHistory:
    product_id: UUID
    comparable: bool
    reason: str | None
    period: PriceHistoryPeriod
    currency: str | None
    period_from: datetime | None
    period_to: datetime
    series: tuple[PriceHistorySeries, ...]
    metrics: PriceHistoryMetrics | None


def _commercial_day_expr():
    """`(observed_at AT TIME ZONE 'America/Sao_Paulo')::date` -- dia
    comercial local, nunca UTC (correção pós-plano, item 5)."""
    return cast(func.timezone("America/Sao_Paulo", PriceObservation.observed_at), Date)


async def _resolve_reference_currency(
    session: AsyncSession, *, product_id: UUID, user_id: UUID, anchor_offer_id: UUID
) -> str | None:
    """Moeda de referência, cadeia determinística de duas etapas (correção
    pós-plano, item 4): (A) a observação mais recente da própria Offer
    âncora, se existir -- nunca ignorada em favor de outra Offer só porque
    esta é mais recente; (B) só quando a âncora não tem NENHUMA observação,
    cai para a observação mais recente entre as demais Offers acessíveis do
    Product; (C) `None` (nunca BRL inventado) quando nenhuma das duas achar
    nada."""
    anchor_currency = await session.scalar(
        select(PriceObservation.currency)
        .where(PriceObservation.offer_id == anchor_offer_id)
        .order_by(PriceObservation.observed_at.desc(), PriceObservation.id.desc())
        .limit(1)
    )
    if anchor_currency is not None:
        return anchor_currency
    return await session.scalar(
        select(PriceObservation.currency)
        .select_from(PriceObservation)
        .join(Offer, Offer.id == PriceObservation.offer_id)
        .where(
            Offer.product_id == product_id,
            _accessible_offer_exists(user_id=user_id),
        )
        .order_by(PriceObservation.observed_at.desc(), PriceObservation.id.desc())
        .limit(1)
    )


async def _resolve_current_amount(
    session: AsyncSession, *, product_id: UUID, user_id: UUID, reference_currency: str
) -> Decimal | None:
    """Preço ATUAL -- nunca o mínimo do dia (correção pós-plano, item 2).
    Resolve a observação MAIS RECENTE de cada Offer acessível primeiro (sem
    filtro nenhum), só DEPOIS checa se aquela observação específica está
    `NEW`/`AVAILABLE`/na moeda de referência. Uma Offer cuja última
    observação ficou indisponível nunca "ressuscita" um preço antigo válido
    -- ela simplesmente não contribui para `current_amount`."""
    latest_rank = (
        func.row_number()
        .over(
            partition_by=PriceObservation.offer_id,
            order_by=(
                PriceObservation.observed_at.desc(),
                PriceObservation.id.desc(),
            ),
        )
        .label("rn")
    )
    latest_cte = (
        select(
            PriceObservation.amount,
            PriceObservation.currency,
            PriceObservation.condition,
            PriceObservation.availability,
            latest_rank,
        )
        .select_from(PriceObservation)
        .join(Offer, Offer.id == PriceObservation.offer_id)
        .where(
            Offer.product_id == product_id,
            _accessible_offer_exists(user_id=user_id),
        )
        .cte("latest_observation_per_offer")
    )
    statement = select(func.min(latest_cte.c.amount)).where(
        latest_cte.c.rn == 1,
        latest_cte.c.condition == OfferCondition.NEW,
        latest_cte.c.availability == Availability.AVAILABLE,
        latest_cte.c.currency == reference_currency,
    )
    return await session.scalar(statement)


async def _fetch_daily_low_points(
    session: AsyncSession,
    *,
    product_id: UUID,
    user_id: UUID,
    start_utc: datetime | None,
    end_utc: datetime,
    reference_currency: str,
) -> list[tuple[UUID, str, str, date, Decimal, UUID, UUID]]:
    """Menor `PriceObservation.amount` comercialmente válido por (Store,
    dia comercial) -- linhas da série exibida no gráfico. Desempate: menor
    `amount`; empate, observação mais recente; empate ainda, `id` como
    último critério (correção pós-plano, item 3). `start_utc=None` (period
    `all`) nunca vira um predicado `>= NULL` -- o filtro de limite inferior
    simplesmente não entra na query (correção pós-plano, item 1)."""
    commercial_day = _commercial_day_expr()
    conditions = [
        Offer.product_id == product_id,
        _accessible_offer_exists(user_id=user_id),
        PriceObservation.condition == OfferCondition.NEW,
        PriceObservation.availability == Availability.AVAILABLE,
        PriceObservation.currency == reference_currency,
        PriceObservation.observed_at <= end_utc,
    ]
    if start_utc is not None:
        conditions.append(PriceObservation.observed_at >= start_utc)
    daily_rank = (
        func.row_number()
        .over(
            partition_by=(Offer.store_id, commercial_day),
            order_by=(
                PriceObservation.amount.asc(),
                PriceObservation.observed_at.desc(),
                PriceObservation.id.asc(),
            ),
        )
        .label("rn")
    )
    ranked_cte = (
        select(
            Offer.store_id.label("store_id"),
            Store.code.label("store_code"),
            Store.name.label("store_name"),
            commercial_day.label("commercial_day"),
            PriceObservation.amount.label("amount"),
            PriceObservation.id.label("observation_id"),
            PriceObservation.offer_id.label("offer_id"),
            daily_rank,
        )
        .select_from(PriceObservation)
        .join(Offer, Offer.id == PriceObservation.offer_id)
        .join(Store, Store.id == Offer.store_id)
        .where(*conditions)
        .cte("ranked_daily_observations")
    )
    statement = (
        select(
            ranked_cte.c.store_id,
            ranked_cte.c.store_code,
            ranked_cte.c.store_name,
            ranked_cte.c.commercial_day,
            ranked_cte.c.amount,
            ranked_cte.c.observation_id,
            ranked_cte.c.offer_id,
        )
        .where(ranked_cte.c.rn == 1)
        .order_by(ranked_cte.c.store_code, ranked_cte.c.commercial_day)
    )
    rows = (await session.execute(statement)).all()
    return [tuple(row) for row in rows]


def _compute_metrics(
    rows: list[tuple[UUID, str, str, date, Decimal, UUID, UUID]],
    *,
    current_amount: Decimal | None,
) -> PriceHistoryMetrics:
    """`daily_market_low(dia) = MIN(store_daily_low de todas as Stores
    naquele dia)` -- min/max/average derivam SEMPRE desse agregado por dia,
    nunca da média bruta dos pontos por Store (evita viés por dias com mais
    ou menos lojas coletadas, correção pós-plano item 3)."""
    daily_low: dict[date, Decimal] = {}
    for _store_id, _code, _name, day, amount, _obs_id, _offer_id in rows:
        current = daily_low.get(day)
        if current is None or amount < current:
            daily_low[day] = amount
    if not daily_low:
        return PriceHistoryMetrics(
            current_amount=current_amount,
            min_amount=None,
            max_amount=None,
            average_amount=None,
            variation_percent=None,
        )
    ordered_days = sorted(daily_low)
    values = [daily_low[day] for day in ordered_days]
    min_amount = min(values)
    max_amount = max(values)
    average_amount = (sum(values, start=Decimal(0)) / len(values)).quantize(
        _FOUR_PLACES, rounding=ROUND_HALF_UP
    )
    baseline = daily_low[ordered_days[0]]
    variation_percent = None
    if current_amount is not None and baseline != 0:
        variation_percent = (
            (current_amount - baseline) / baseline * Decimal(100)
        ).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    return PriceHistoryMetrics(
        current_amount=current_amount,
        min_amount=min_amount,
        max_amount=max_amount,
        average_amount=average_amount,
        variation_percent=variation_percent,
    )


async def get_offer_price_history_for_user(
    session: AsyncSession,
    *,
    offer_id: UUID,
    user_id: UUID,
    period: PriceHistoryPeriod,
    now: datetime,
) -> OfferPriceHistory | None:
    """`None` quando a Offer não é acessível ao usuário -- o router converte
    isso em 403, mesmo mecanismo de autorização já existente para Offer
    (nenhum `resource_type="product"` novo)."""
    anchor = await get_offer_detail_for_user(
        session, offer_id=offer_id, user_id=user_id
    )
    if anchor is None:
        return None
    period_range = resolve_period_range(period, now=now)
    product_id = anchor.product.id
    if anchor.product.identity_key is None:
        return OfferPriceHistory(
            product_id=product_id,
            comparable=False,
            reason="unresolved_product_identity",
            period=period,
            currency=None,
            period_from=period_range.start_utc,
            period_to=period_range.end_utc,
            series=(),
            metrics=None,
        )
    reference_currency = await _resolve_reference_currency(
        session, product_id=product_id, user_id=user_id, anchor_offer_id=offer_id
    )
    if reference_currency is None:
        # Nenhuma observação em nenhuma Offer acessível -- resposta vazia
        # honesta, nunca fallback de moeda inventado (correção pós-plano,
        # item 4, caso C).
        return OfferPriceHistory(
            product_id=product_id,
            comparable=True,
            reason=None,
            period=period,
            currency=None,
            period_from=period_range.start_utc,
            period_to=period_range.end_utc,
            series=(),
            metrics=None,
        )
    current_amount = await _resolve_current_amount(
        session,
        product_id=product_id,
        user_id=user_id,
        reference_currency=reference_currency,
    )
    rows = await _fetch_daily_low_points(
        session,
        product_id=product_id,
        user_id=user_id,
        start_utc=period_range.start_utc,
        end_utc=period_range.end_utc,
        reference_currency=reference_currency,
    )
    series_points: dict[UUID, list[PriceHistoryPoint]] = defaultdict(list)
    store_meta: dict[UUID, tuple[str, str]] = {}
    for (
        store_id,
        store_code,
        store_name,
        day,
        amount,
        observation_id,
        offer_id_row,
    ) in rows:
        store_meta[store_id] = (store_code, store_name)
        series_points[store_id].append(
            PriceHistoryPoint(
                day=day,
                amount=amount,
                observation_id=observation_id,
                offer_id=offer_id_row,
            )
        )
    series = tuple(
        PriceHistorySeries(
            store_id=store_id,
            store_code=code,
            store_name=name,
            points=tuple(sorted(series_points[store_id], key=lambda p: p.day)),
        )
        for store_id, (code, name) in sorted(
            store_meta.items(), key=lambda item: item[1][0]
        )
    )
    metrics = _compute_metrics(rows, current_amount=current_amount)
    return OfferPriceHistory(
        product_id=product_id,
        comparable=True,
        reason=None,
        period=period,
        currency=reference_currency,
        period_from=period_range.start_utc,
        period_to=period_range.end_utc,
        series=series,
        metrics=metrics,
    )
