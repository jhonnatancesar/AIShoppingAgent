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

from sqlalchemy import Date, and_, cast, exists, func, null, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.collection.cadence import (
    CadenceConfig,
    OfferFreshnessStatus,
    resolve_offers_freshness_batch,
)
from app.collection.contracts import OfferCondition
from app.collection.models import (
    CollectionRun,
    CollectionRunStatus,
    MissionOfferRelevance,
    OfferInstallmentOption,
    PriceObservation,
    SharedCollectionOffer,
)
from app.collection.normalization import Availability
from app.collection.relevance import OfferRelevance
from app.coupons.models import OfferCouponPriceDay
from app.coupons.pricing import best_applicable_coupon
from app.coupons.service import get_active_coupons_by_store
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


class _DevViewer:
    """Sentinela do leitor DEV (TASK-126/127) -- sempre um objeto
    explícito, NUNCA `None`: um `user_id` ausente por bug jamais pode
    virar acesso a todas as ofertas por acidente."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "DEV_VIEWER"


DEV_VIEWER = _DevViewer()
OfferViewer = UUID | _DevViewer


def _viewer_access_clause(user_id: OfferViewer):
    """Uma `UUID` mantém EXATAMENTE a regra USER de sempre
    (`_accessible_offer_exists`); só `DEV_VIEWER` troca pela regra DEV
    (`_any_relevance_exists`). Qualquer outro valor falha fechado."""
    if user_id is DEV_VIEWER:
        return _any_relevance_exists()
    if isinstance(user_id, UUID):
        return _accessible_offer_exists(user_id=user_id)
    raise TypeError(f"viewer inválido: {user_id!r}")


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
        .where(
            _accessible_offer_exists(user_id=user_id),
            # Mesma exclusão de `comparison_offers_statement` -- uma
            # Offer substituída não aparece na listagem geral (acesso
            # direto por `offer_id` continua funcionando).
            Offer.superseded_by_id.is_(None),
        )
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


def _offer_for_dev_statement(*, offer_id: UUID):
    """TASK-126/127: mesma forma de `offer_for_user_statement`, sem dono,
    sem `ACCESSIBLE_RELEVANCE` e sem critério atual -- basta a oferta ter
    QUALQUER vínculo de missão (mesma regra da listagem DEV)."""
    return (
        select(Offer, Product, Store, Seller)
        .join(Product, Product.id == Offer.product_id)
        .join(Store, Store.id == Offer.store_id)
        .outerjoin(Seller, Seller.id == Offer.seller_id)
        .where(Offer.id == offer_id, _any_relevance_exists())
        .limit(1)
    )


async def get_offer_detail_for_user(
    session: AsyncSession, *, offer_id: UUID, user_id: UUID
) -> UserOfferDetail | None:
    return await _get_offer_detail(session, offer_id=offer_id, viewer=user_id)


async def get_offer_detail_for_dev(
    session: AsyncSession, *, offer_id: UUID
) -> UserOfferDetail | None:
    """DEV-only (chamador precisa ter checado `Permission.DEV_PANEL_ACCESS`)."""
    return await _get_offer_detail(session, offer_id=offer_id, viewer=DEV_VIEWER)


async def _get_offer_detail(
    session: AsyncSession, *, offer_id: UUID, viewer: OfferViewer
) -> UserOfferDetail | None:
    if viewer is DEV_VIEWER:
        statement = _offer_for_dev_statement(offer_id=offer_id)
    elif isinstance(viewer, UUID):
        statement = offer_for_user_statement(offer_id=offer_id, user_id=viewer)
    else:
        raise TypeError(f"viewer inválido: {viewer!r}")
    row = (await session.execute(statement)).first()
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


def comparison_offers_statement(*, product_id: UUID, user_id: OfferViewer):
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
            _viewer_access_clause(user_id),
            # Rodada de frescor (2026-09-11): uma Offer substituída
            # (`_supersede_old_unattributed_offer`, `orchestration.py`)
            # nunca aparece como alternativa de comparação -- some
            # IMEDIATAMENTE, não só quando envelhece. Acesso DIRETO por
            # `offer_id` (`get_offer_detail_for_user`) continua
            # funcionando normalmente -- só a LISTA de alternativas
            # exclui a substituída, nunca a autorização em si.
            Offer.superseded_by_id.is_(None),
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
    return await _get_offer_comparison(session, offer_id=offer_id, viewer=user_id)


async def get_offer_comparison_for_dev(
    session: AsyncSession, *, offer_id: UUID
) -> UserOfferComparison | None:
    """DEV-only (chamador precisa ter checado `Permission.DEV_PANEL_ACCESS`)."""
    return await _get_offer_comparison(session, offer_id=offer_id, viewer=DEV_VIEWER)


async def _get_offer_comparison(
    session: AsyncSession, *, offer_id: UUID, viewer: OfferViewer
) -> UserOfferComparison | None:
    anchor = await _get_offer_detail(session, offer_id=offer_id, viewer=viewer)
    if anchor is None:
        return None
    if anchor.product.identity_key is None:
        return UserOfferComparison(product=anchor.product, offers=())
    rows = list(
        (
            await session.execute(
                comparison_offers_statement(
                    product_id=anchor.product.id, user_id=viewer
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
            .outerjoin(PriceObservation, PriceObservation.id == latest_observation_id)
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


@dataclass(frozen=True, slots=True)
class AllOfferSummaryDev(UserOfferSummary):
    """TASK-126: mesmos campos de `UserOfferSummary` + a classificação de
    relevância bruta (a mais recente, quando a oferta tem mais de um
    vínculo de missão) -- só `list_all_offers_dev` devolve isto."""

    classification: OfferRelevance


def _any_relevance_exists():
    """DEV-only: existe QUALQUER vínculo `MissionOfferRelevance` para esta
    Offer -- sem checar dono, classificação (inclui `NO_MATCH` de
    propósito) nem se a missão ainda pede a variante atual. Contraparte
    deliberadamente mais permissiva de `_accessible_offer_exists`, nunca
    reaproveitada por engano no caminho USER (só `list_all_offers_dev`
    chama isto)."""
    return exists(
        select(MissionOfferRelevance.offer_id).where(
            MissionOfferRelevance.offer_id == Offer.id
        )
    )


def _all_offers_statement_dev(
    *,
    search: str | None = None,
    store_code: str | None = None,
    condition: str | None = None,
    availability: str | None = None,
    sort: str = "recent",
):
    """TASK-126: versão DEV-only de `user_offers_statement` -- mesmos
    filtros/ordenação, mas troca `_accessible_offer_exists(user_id=...)`
    (posse + `ACCESSIBLE_RELEVANCE` + critério atual) por
    `_any_relevance_exists()` -- qualquer oferta com qualquer vínculo de
    missão, de qualquer usuário, qualquer classificação. Uma linha por
    Offer (não por vínculo) -- quando há mais de um vínculo, mostra a
    classificação do MAIS RECENTE (`classified_at`), mesmo padrão de
    'última observação' já usado abaixo para preço."""
    latest_observation_id = (
        select(PriceObservation.id)
        .where(PriceObservation.offer_id == Offer.id)
        .order_by(PriceObservation.observed_at.desc(), PriceObservation.id.desc())
        .limit(1)
        .correlate(Offer)
        .scalar_subquery()
    )
    latest_classification = (
        select(MissionOfferRelevance.classification)
        .where(MissionOfferRelevance.offer_id == Offer.id)
        .order_by(MissionOfferRelevance.classified_at.desc())
        .limit(1)
        .correlate(Offer)
        .scalar_subquery()
    )
    statement = (
        select(
            Offer,
            Product,
            Store,
            Seller,
            PriceObservation,
            latest_classification.label("classification"),
        )
        .join(Product, Product.id == Offer.product_id)
        .join(Store, Store.id == Offer.store_id)
        .outerjoin(Seller, Seller.id == Offer.seller_id)
        .outerjoin(PriceObservation, PriceObservation.id == latest_observation_id)
        .where(
            _any_relevance_exists(),
            Offer.superseded_by_id.is_(None),
        )
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


async def list_all_offers_dev(
    session: AsyncSession,
    *,
    search: str | None,
    store_code: str | None,
    condition: str | None,
    availability: str | None,
    sort: str,
    limit: int,
    offset: int,
) -> tuple[tuple[AllOfferSummaryDev, ...], int]:
    base = _all_offers_statement_dev(
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
            AllOfferSummaryDev(
                offer=offer,
                product=product,
                store=store,
                seller=seller,
                observation=observation,
                classification=classification,
            )
            for offer, product, store, seller, observation, classification in rows
        ),
        int(total or 0),
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
    """Preço do ponto -- TASK-125: o preço COM cupom quando a coleta
    registrou um cupom aplicável naquele dia (decisão do usuário: "tudo
    na mesma linha"), senão o preço de tabela."""
    observation_id: UUID
    offer_id: UUID
    original_amount: Decimal | None = None
    """Preço de tabela (sem cupom) -- só quando `amount` veio de cupom."""
    coupon_code: str | None = None
    """Cupom usado (`''` = cupom automático, sem código) -- só com cupom."""


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


def _commercial_day_expr(timestamp_column=None):
    """`(<timestamp> AT TIME ZONE 'America/Sao_Paulo')::date` -- dia
    comercial local, nunca UTC (correção pós-plano, item 5). Default
    `PriceObservation.observed_at` por compatibilidade; a série do
    gráfico (`_fetch_daily_low_points`) passa `CollectionRun.started_at`
    -- o dia em que a CONFIRMAÇÃO aconteceu, não o dia em que o valor
    do preço foi originalmente observado (podem divergir quando o
    mesmo preço é reconfirmado em dias seguintes sem gerar nova
    `PriceObservation`, TASK-093)."""
    column = (
        timestamp_column
        if timestamp_column is not None
        else PriceObservation.observed_at
    )
    return cast(func.timezone("America/Sao_Paulo", column), Date)


async def _resolve_reference_currency(
    session: AsyncSession,
    *,
    product_id: UUID,
    user_id: OfferViewer,
    anchor_offer_id: UUID,
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
            _viewer_access_clause(user_id),
        )
        .order_by(PriceObservation.observed_at.desc(), PriceObservation.id.desc())
        .limit(1)
    )


async def _resolve_current_amount(
    session: AsyncSession,
    *,
    product_id: UUID,
    user_id: OfferViewer,
    reference_currency: str,
    now: datetime,
    store_ids: frozenset[UUID] | None = None,
    cadence_config: CadenceConfig | None = None,
    apply_coupons: bool = False,
) -> Decimal | None:
    """Preço ATUAL -- nunca o mínimo do dia (correção pós-plano, item 2).
    Resolve a observação MAIS RECENTE de cada Offer acessível primeiro (sem
    filtro nenhum), só DEPOIS checa se aquela observação específica está
    `NEW`/`AVAILABLE`/na moeda de referência. Uma Offer cuja última
    observação ficou indisponível nunca "ressuscita" um preço antigo válido
    -- ela simplesmente não contribui para `current_amount`.

    Pedido explícito do dono do produto (rodada de frescor): "atual" só
    conta ofertas cuja confirmação mais recente é `CONFIRMED_RECENT`
    (`resolve_offer_freshness`) -- uma oferta com confirmação antiga,
    indisponível, sumida ou nunca confirmada nunca contribui, mesmo que a
    última observação SQL pareça `NEW`/`AVAILABLE` (ela pode só estar
    parada há muito tempo, sem novas coletas). Efeito colateral desejado:
    quando um vendedor Amazon antes desconhecido é identificado, a Offer
    antiga (`seller_id=NULL`) some do "atual" assim que envelhece o
    bastante, sem precisar de fusão/exclusão manual -- nunca duplica o
    preço do produto indefinidamente. `store_ids=None` mantém o
    comportamento anterior (todas as lojas acessíveis); com valor,
    restringe às lojas selecionadas."""
    effective_config = cadence_config or CadenceConfig()
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
    conditions = [
        Offer.product_id == product_id,
        _viewer_access_clause(user_id),
    ]
    if store_ids is not None:
        conditions.append(Offer.store_id.in_(store_ids))
    latest_cte = (
        select(
            Offer.id.label("offer_id"),
            Offer.store_id.label("store_id"),
            PriceObservation.amount,
            PriceObservation.currency,
            PriceObservation.condition,
            PriceObservation.availability,
            latest_rank,
        )
        .select_from(PriceObservation)
        .join(Offer, Offer.id == PriceObservation.offer_id)
        .where(*conditions)
        .cte("latest_observation_per_offer")
    )
    candidates = (
        await session.execute(
            select(
                latest_cte.c.offer_id, latest_cte.c.store_id, latest_cte.c.amount
            ).where(
                latest_cte.c.rn == 1,
                latest_cte.c.condition == OfferCondition.NEW,
                latest_cte.c.availability == Availability.AVAILABLE,
                latest_cte.c.currency == reference_currency,
            )
        )
    ).all()
    if not candidates:
        return None
    # Lote, nunca uma consulta de frescor por oferta (pedido explícito do
    # dono do produto -- mesma regra de `load_mission_list_extras` contra
    # N+1): número de consultas escala com lojas distintas, não com
    # quantas ofertas o Product tem.
    amount_by_offer = {offer_id: amount for offer_id, _store_id, amount in candidates}
    freshness_by_offer = await resolve_offers_freshness_batch(
        session,
        offers=[(offer_id, store_id) for offer_id, store_id, _amount in candidates],
        product_id=product_id,
        now=now,
        config=effective_config,
    )
    eligible = {
        offer_id: amount_by_offer[offer_id]
        for offer_id, status in freshness_by_offer.items()
        if status == OfferFreshnessStatus.CONFIRMED_RECENT
    }
    if apply_coupons and eligible:
        eligible = await _apply_current_coupons(
            session, amounts=eligible, currency=reference_currency
        )
    return min(eligible.values()) if eligible else None


async def _apply_current_coupons(
    session: AsyncSession, *, amounts: dict[UUID, Decimal], currency: str
) -> dict[UUID, Decimal]:
    """TASK-125: o "atual" do gráfico usa o cupom VIGENTE agora -- mesma
    regra do card da oferta (`best_applicable_coupon` sobre os cupons
    ativos da loja), para o último ponto da linha e o "atual" nunca
    mostrarem números diferentes. Uma consulta de ofertas + uma de
    cupons, nunca uma por oferta."""
    offers = (
        await session.scalars(select(Offer).where(Offer.id.in_(tuple(amounts))))
    ).all()
    coupons_by_store = await get_active_coupons_by_store(
        session, store_ids=[offer.store_id for offer in offers]
    )
    effective = dict(amounts)
    for offer in offers:
        applied = best_applicable_coupon(
            offer,
            coupons_by_store.get(offer.store_id, ()),
            amounts[offer.id],
            currency,
        )
        if applied is not None:
            effective[offer.id] = applied.final_amount
    return effective


DailyLowRow = tuple[
    UUID, str, str, date, Decimal, UUID, UUID, Decimal | None, str | None
]
"""`(store_id, store_code, store_name, dia, preço, observation_id,
offer_id, preço_de_tabela_se_cupom, código_do_cupom)`."""


async def _fetch_daily_low_points(
    session: AsyncSession,
    *,
    product_id: UUID,
    user_id: OfferViewer,
    start_utc: datetime | None,
    end_utc: datetime,
    reference_currency: str,
    store_ids: frozenset[UUID] | None = None,
    apply_coupons: bool = False,
) -> list[DailyLowRow]:
    """Menor preço CONFIRMADO por (Store, dia comercial) -- linhas da série
    exibida no gráfico. Desempate: menor `amount`; empate, confirmação mais
    recente; empate ainda, `id` da observação como último critério
    (correção pós-plano, item 3). `start_utc=None` (period `all`) nunca
    vira um predicado `>= NULL` -- o filtro de limite inferior simplesmente
    não entra na query (correção pós-plano, item 1). `store_ids=None`
    mantém todas as lojas acessíveis; com valor, restringe às lojas
    selecionadas -- as métricas (`_compute_metrics`) herdam esse mesmo
    filtro automaticamente por operarem sobre estas mesmas linhas (pedido
    do dono do produto: indicadores acompanham a seleção).

    Fonte da linha (correção de 2026-09-12, pedido explícito do dono do
    produto): UNION de duas origens, nunca só `PriceObservation.
    observed_at`.

    (1) `SharedCollectionOffer` JOIN `CollectionRun` -- fonte PRIMÁRIA
    para dado novo: cada `CollectionRun` bem-sucedida grava exatamente
    UM `SharedCollectionOffer` por oferta processada
    (`orchestration._persist_phase_a`), MESMO quando a `PriceObservation`
    foi reaproveitada por dedupe (TASK-093, preço comercialmente
    idêntico ao já registrado). É o que permite duas coletas em dias
    diferentes com o MESMO preço aparecerem como DUAS confirmações
    distintas (uma por dia real de coleta) sem duplicar o estado
    comercial (continua existindo só UMA `PriceObservation`) --
    `CollectionRun.started_at` decide o dia comercial dessas linhas,
    nunca `PriceObservation.observed_at` (que não muda quando a
    observação é reaproveitada).

    (2) `PriceObservation.observed_at` direto -- fallback para dado
    LEGADO anterior a esta correção, sem nenhuma linha em
    `SharedCollectionOffer` ainda: nunca descartado, só preenche dias
    que a fonte (1) não cobre. As duas fontes juntas nunca perdem uma
    confirmação real nem inventam uma que não aconteceu.

    TASK-125 (`apply_coupons`, espelha `Settings.coupons_enabled`): cada
    confirmação usa o preço COM cupom quando a coleta registrou um cupom
    aplicável para aquela (Offer, observação, dia) em
    `OfferCouponPriceDay` -- o menor preço do dia passa a ser o menor
    preço EFETIVO. Só existe dado a partir do deploy da TASK-125 (decisão
    do usuário: nada reconstruído); sem registro, o ponto continua no
    preço de tabela."""
    legacy_conditions = [
        Offer.product_id == product_id,
        _viewer_access_clause(user_id),
        PriceObservation.condition == OfferCondition.NEW,
        PriceObservation.availability == Availability.AVAILABLE,
        PriceObservation.currency == reference_currency,
        PriceObservation.observed_at <= end_utc,
    ]
    confirmed_conditions = [
        Offer.product_id == product_id,
        _viewer_access_clause(user_id),
        CollectionRun.status == CollectionRunStatus.SUCCEEDED,
        PriceObservation.condition == OfferCondition.NEW,
        PriceObservation.availability == Availability.AVAILABLE,
        PriceObservation.currency == reference_currency,
        CollectionRun.started_at <= end_utc,
    ]
    if store_ids is not None:
        legacy_conditions.append(Offer.store_id.in_(store_ids))
        confirmed_conditions.append(Offer.store_id.in_(store_ids))
    if start_utc is not None:
        legacy_conditions.append(PriceObservation.observed_at >= start_utc)
        confirmed_conditions.append(CollectionRun.started_at >= start_utc)

    def _with_coupon(statement, day_expr):
        # Cupom do MESMO dia da confirmação, na MESMA observação.
        if not apply_coupons:
            return statement.add_columns(
                PriceObservation.amount.label("amount"),
                PriceObservation.amount.label("original_amount"),
                null().label("coupon_code"),
            )
        return statement.add_columns(
            func.coalesce(
                OfferCouponPriceDay.final_amount, PriceObservation.amount
            ).label("amount"),
            PriceObservation.amount.label("original_amount"),
            OfferCouponPriceDay.coupon_code.label("coupon_code"),
        ).outerjoin(
            OfferCouponPriceDay,
            and_(
                OfferCouponPriceDay.offer_id == PriceObservation.offer_id,
                OfferCouponPriceDay.observation_id == PriceObservation.id,
                OfferCouponPriceDay.commercial_day == day_expr,
            ),
        )

    legacy_day = _commercial_day_expr(PriceObservation.observed_at)
    legacy_rows = _with_coupon(
        select(
            Offer.store_id.label("store_id"),
            Store.code.label("store_code"),
            Store.name.label("store_name"),
            legacy_day.label("commercial_day"),
            PriceObservation.id.label("observation_id"),
            PriceObservation.offer_id.label("offer_id"),
            PriceObservation.observed_at.label("confirmed_at"),
        )
        .select_from(PriceObservation)
        .join(Offer, Offer.id == PriceObservation.offer_id)
        .join(Store, Store.id == Offer.store_id)
        .where(*legacy_conditions),
        legacy_day,
    )
    confirmed_day = _commercial_day_expr(CollectionRun.started_at)
    confirmed_rows = _with_coupon(
        select(
            Offer.store_id.label("store_id"),
            Store.code.label("store_code"),
            Store.name.label("store_name"),
            confirmed_day.label("commercial_day"),
            PriceObservation.id.label("observation_id"),
            PriceObservation.offer_id.label("offer_id"),
            CollectionRun.started_at.label("confirmed_at"),
        )
        .select_from(SharedCollectionOffer)
        .join(
            CollectionRun, CollectionRun.id == SharedCollectionOffer.collection_run_id
        )
        .join(
            PriceObservation,
            PriceObservation.id == SharedCollectionOffer.observation_id,
        )
        .join(Offer, Offer.id == SharedCollectionOffer.offer_id)
        .join(Store, Store.id == Offer.store_id)
        .where(*confirmed_conditions),
        confirmed_day,
    )
    candidates_cte = legacy_rows.union_all(confirmed_rows).cte(
        "daily_confirmation_candidates"
    )
    daily_rank = (
        func.row_number()
        .over(
            partition_by=(candidates_cte.c.store_id, candidates_cte.c.commercial_day),
            order_by=(
                candidates_cte.c.amount.asc(),
                candidates_cte.c.confirmed_at.desc(),
                candidates_cte.c.observation_id.asc(),
            ),
        )
        .label("rn")
    )
    ranked_cte = select(candidates_cte, daily_rank).cte("ranked_daily_confirmations")
    statement = (
        select(
            ranked_cte.c.store_id,
            ranked_cte.c.store_code,
            ranked_cte.c.store_name,
            ranked_cte.c.commercial_day,
            ranked_cte.c.amount,
            ranked_cte.c.observation_id,
            ranked_cte.c.offer_id,
            ranked_cte.c.original_amount,
            ranked_cte.c.coupon_code,
        )
        .where(ranked_cte.c.rn == 1)
        .order_by(ranked_cte.c.store_code, ranked_cte.c.commercial_day)
    )
    rows = (await session.execute(statement)).all()
    return [
        (
            store_id,
            store_code,
            store_name,
            day,
            amount,
            observation_id,
            offer_id,
            original_amount if coupon_code is not None else None,
            coupon_code,
        )
        for (
            store_id,
            store_code,
            store_name,
            day,
            amount,
            observation_id,
            offer_id,
            original_amount,
            coupon_code,
        ) in rows
    ]


def _compute_metrics(
    rows: list[DailyLowRow],
    *,
    current_amount: Decimal | None,
) -> PriceHistoryMetrics:
    """`daily_market_low(dia) = MIN(store_daily_low de todas as Stores
    naquele dia)` -- min/max/average derivam SEMPRE desse agregado por dia,
    nunca da média bruta dos pontos por Store (evita viés por dias com mais
    ou menos lojas coletadas, correção pós-plano item 3)."""
    daily_low: dict[date, Decimal] = {}
    for row in rows:
        day, amount = row[3], row[4]
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
    store_ids: frozenset[UUID] | None = None,
    cadence_config: CadenceConfig | None = None,
    apply_coupons: bool = False,
) -> OfferPriceHistory | None:
    """`None` quando a Offer não é acessível ao usuário -- o router converte
    isso em 403, mesmo mecanismo de autorização já existente para Offer
    (nenhum `resource_type="product"` novo)."""
    return await _get_offer_price_history(
        session,
        offer_id=offer_id,
        user_id=user_id,
        period=period,
        now=now,
        store_ids=store_ids,
        cadence_config=cadence_config,
        apply_coupons=apply_coupons,
    )


async def get_offer_price_history_for_dev(
    session: AsyncSession,
    *,
    offer_id: UUID,
    period: PriceHistoryPeriod,
    now: datetime,
    store_ids: frozenset[UUID] | None = None,
    cadence_config: CadenceConfig | None = None,
    apply_coupons: bool = False,
) -> OfferPriceHistory | None:
    """DEV-only (chamador precisa ter checado `Permission.DEV_PANEL_ACCESS`):
    série/métricas de TODAS as ofertas do Product, de qualquer usuário."""
    return await _get_offer_price_history(
        session,
        offer_id=offer_id,
        user_id=DEV_VIEWER,
        period=period,
        now=now,
        store_ids=store_ids,
        cadence_config=cadence_config,
        apply_coupons=apply_coupons,
    )


async def _get_offer_price_history(
    session: AsyncSession,
    *,
    offer_id: UUID,
    user_id: OfferViewer,
    period: PriceHistoryPeriod,
    now: datetime,
    store_ids: frozenset[UUID] | None = None,
    cadence_config: CadenceConfig | None = None,
    apply_coupons: bool = False,
) -> OfferPriceHistory | None:
    anchor = await _get_offer_detail(session, offer_id=offer_id, viewer=user_id)
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
        now=now,
        store_ids=store_ids,
        cadence_config=cadence_config,
        apply_coupons=apply_coupons,
    )
    rows = await _fetch_daily_low_points(
        session,
        product_id=product_id,
        user_id=user_id,
        start_utc=period_range.start_utc,
        end_utc=period_range.end_utc,
        reference_currency=reference_currency,
        store_ids=store_ids,
        apply_coupons=apply_coupons,
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
        original_amount,
        coupon_code,
    ) in rows:
        store_meta[store_id] = (store_code, store_name)
        series_points[store_id].append(
            PriceHistoryPoint(
                day=day,
                amount=amount,
                observation_id=observation_id,
                offer_id=offer_id_row,
                original_amount=original_amount,
                coupon_code=coupon_code,
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
