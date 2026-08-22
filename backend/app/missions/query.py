"""Consultas somente leitura de missões por proprietário.

Assíncrono desde a extensão da TASK-079 (webhook Telegram) -- chamadores
são `app.telegram.router` e, desde a TASK-092, `app.webapp.missions_router`,
então não há versão síncrona a manter."""

from collections.abc import Collection
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collection.models import MissionOfferRelevance
from app.collection.relevance import OfferRelevance
from app.missions.models import (
    Mission,
    MissionCriteria,
    MissionProductSelection,
    MissionSchedule,
    MissionSource,
    MissionStatus,
    MissionTransition,
)
from app.offers.models import Offer
from app.products.models import Product
from app.stores.models import Store

_TERMINAL_STATUSES = frozenset(
    {MissionStatus.COMPLETED, MissionStatus.CANCELLED, MissionStatus.EXPIRED}
)
_VISIBLE_LIST_STATUSES = (
    MissionStatus.ACTIVE,
    MissionStatus.PAUSED,
    MissionStatus.CANCELLED,
)


class MissionReferenceError(ValueError):
    """A referência em texto livre não resolveu para exatamente uma missão."""


async def list_missions_for_user(
    session: AsyncSession,
    *,
    user_id: UUID,
    limit: int = 10,
) -> list[Mission]:
    """Lista as missões mais recentes do usuário, mais recente primeiro."""
    statement = (
        select(Mission)
        .where(Mission.user_id == user_id)
        .order_by(Mission.created_at.desc())
        .limit(limit)
    )
    return list(await session.scalars(statement))


async def list_visible_missions_for_user(
    session: AsyncSession,
    *,
    user_id: UUID,
    limit: int = 16,
) -> list[Mission]:
    """TASK-088: lista recente determinística para o comando do Telegram.

    O filtro de proprietário e status ocorre no banco; `completed` e `expired`
    nunca ocupam o limite destinado às missões visíveis.
    """
    statement = (
        select(Mission)
        .where(
            Mission.user_id == user_id,
            Mission.status.in_(_VISIBLE_LIST_STATUSES),
        )
        .order_by(
            case(
                (Mission.status == MissionStatus.ACTIVE, 0),
                (Mission.status == MissionStatus.PAUSED, 1),
                (Mission.status == MissionStatus.CANCELLED, 2),
                else_=3,
            ),
            Mission.created_at.desc(),
        )
        .limit(limit)
    )
    return list(await session.scalars(statement))


async def find_missions_by_reference(
    session: AsyncSession,
    *,
    user_id: UUID,
    reference: str,
) -> list[Mission]:
    """Busca missões do usuário cujo `search_query` contenha `reference`.

    Comparação case-insensitive e restrita ao proprietário; a V1 não expõe
    identificador de missão ao usuário, então uma referência em texto livre é
    o único jeito de apontar para uma missão específica.
    """
    statement = (
        select(Mission)
        .join(MissionCriteria, MissionCriteria.mission_id == Mission.id)
        .where(
            Mission.user_id == user_id,
            MissionCriteria.search_query.ilike(f"%{reference}%"),
        )
        .order_by(Mission.created_at.desc())
    )
    return list(await session.scalars(statement))


async def _candidates_for_command(
    session: AsyncSession,
    *,
    user_id: UUID,
    reference: str | None,
) -> list[Mission]:
    if reference:
        return await find_missions_by_reference(
            session, user_id=user_id, reference=reference
        )
    return [
        mission
        for mission in await list_missions_for_user(session, user_id=user_id, limit=50)
        if mission.status not in _TERMINAL_STATUSES
    ]


async def resolve_mission_for_command(
    session: AsyncSession,
    *,
    user_id: UUID,
    reference: str | None,
) -> Mission:
    """Resolve exatamente uma missão para executar um comando.

    Com `reference`, exige uma correspondência única em `search_query`. Sem
    `reference`, exige exatamente uma missão não terminal do usuário — a V1
    nunca expõe um identificador de missão, então mais de uma candidata é
    ambiguidade real, não um detalhe de implementação.
    """
    candidates = await _candidates_for_command(
        session, user_id=user_id, reference=reference
    )
    if not candidates:
        raise MissionReferenceError("Não encontrei nenhuma missão correspondente.")
    if len(candidates) > 1:
        raise MissionReferenceError(
            "Encontrei mais de uma missão correspondente; seja mais específico."
        )
    return candidates[0]


async def list_mission_command_candidates(
    session: AsyncSession,
    *,
    user_id: UUID,
    reference: str | None,
) -> list[Mission]:
    """TASK-085: mesmas candidatas de `resolve_mission_for_command`, mas
    sem levantar erro de ambiguidade -- usado quando o chamador oferece
    seleção numerada em vez do erro "seja mais específico"."""
    return await _candidates_for_command(session, user_id=user_id, reference=reference)


async def list_missions_for_user_by_status(
    session: AsyncSession,
    *,
    user_id: UUID,
    statuses: Collection[MissionStatus] | None,
    limit: int,
    offset: int,
) -> list[Mission]:
    """Lista missões do usuário por conjunto de status (TASK-092, tela web
    de missões). `statuses=None` lista todas, sem filtro -- distinto de
    `list_visible_missions_for_user` (Telegram), que sempre exclui
    `completed`/`expired` e usa uma ordem de prioridade por status; aqui a
    ordenação é `updated_at` decrescente (mais recentemente alterada
    primeiro -- pausar/retomar/editar/cancelar sobem a missão na lista, não
    só criar), com `id` decrescente como desempate determinístico (mesmo
    `updated_at` é possível em timestamps colididos), estável sob
    paginação por `offset` (DEC-075, correção de 2026-08-22)."""
    statement = select(Mission).where(Mission.user_id == user_id)
    if statuses is not None:
        statement = statement.where(Mission.status.in_(statuses))
    statement = (
        statement.order_by(Mission.updated_at.desc(), Mission.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(await session.scalars(statement))


async def count_missions_for_user_by_status(
    session: AsyncSession,
    *,
    user_id: UUID,
    statuses: Collection[MissionStatus] | None,
) -> int:
    """Total de missões do usuário sob o mesmo filtro de
    `list_missions_for_user_by_status`, para o `total` do envelope de
    coleção (`docs/development/api-conventions.md`)."""
    statement = select(func.count(Mission.id)).where(Mission.user_id == user_id)
    if statuses is not None:
        statement = statement.where(Mission.status.in_(statuses))
    return await session.scalar(statement) or 0


@dataclass(frozen=True)
class MissionDetail:
    """Composição somente leitura de missão + critério + fontes + agenda +
    histórico de transições (TASK-092) -- `app.missions.models` não
    declara `relationship()` nenhum (todo acesso é via `select` explícito,
    convenção já estabelecida no domínio), então esta função monta a
    mesma composição que `app.telegram.router` já monta em memória, só que
    num único lugar reutilizável pela tela de detalhe da web."""

    mission: Mission
    criteria: MissionCriteria | None
    sources: list[tuple[MissionSource, Store]]
    schedule: MissionSchedule | None
    transitions: list[MissionTransition]
    available_variants: list[Product] = field(default_factory=list)
    selected_product_ids: tuple[UUID, ...] = ()


async def get_mission_for_user(
    session: AsyncSession, *, user_id: UUID, mission_id: UUID
) -> Mission | None:
    """Primitivo de posse reutilizável por qualquer canal (TASK-092,
    auditoria de 2026-08-22, `DEC-075`) -- `None` se a missão não existir
    ou não pertencer a `user_id`, nunca distingue os dois casos (evita
    enumeração de recurso). Antes desta função, cada chamador (Telegram,
    e o próprio router web) repetia `session.get(Mission, id)` +
    `mission.user_id == user_id` -- centralizado aqui para que a regra de
    posse tenha uma única fonte de verdade, reutilizável por Web,
    Telegram e futuros clientes. Os call sites já existentes do Telegram
    não foram migrados nesta TASK (fora de escopo -- código já validado
    em produção, sem necessidade funcional de tocar); a função existe
    para uso imediato pela web e para migração futura sem duplicar a
    regra de novo."""
    mission = await session.get(Mission, mission_id)
    if mission is None or mission.user_id != user_id:
        return None
    return mission


async def get_mission_detail_for_user(
    session: AsyncSession,
    *,
    user_id: UUID,
    mission_id: UUID,
    transitions_limit: int = 20,
) -> MissionDetail | None:
    """`None` se a missão não existir ou não pertencer a `user_id` --
    nunca distingue os dois casos (mesma convenção de posse já usada em
    todo o domínio, evita enumeração)."""
    mission = await session.scalar(
        select(Mission).where(Mission.id == mission_id, Mission.user_id == user_id)
    )
    if mission is None:
        return None
    criteria = await session.scalar(
        select(MissionCriteria).where(MissionCriteria.mission_id == mission_id)
    )
    sources_result = await session.execute(
        select(MissionSource, Store)
        .join(Store, Store.id == MissionSource.store_id)
        .where(MissionSource.mission_id == mission_id)
        .order_by(Store.code)
    )
    schedule = await session.scalar(
        select(MissionSchedule).where(MissionSchedule.mission_id == mission_id)
    )
    transitions = list(
        await session.scalars(
            select(MissionTransition)
            .where(MissionTransition.mission_id == mission_id)
            .order_by(
                MissionTransition.transitioned_at.desc(), MissionTransition.id.desc()
            )
            .limit(transitions_limit)
        )
    )
    available_variants: list[Product] = []
    selected_product_ids: tuple[UUID, ...] = ()
    if criteria is not None and criteria.requested_family_key is not None:
        variants_statement = (
            select(Product)
                .join(Offer, Offer.product_id == Product.id)
                .join(
                    MissionOfferRelevance,
                    MissionOfferRelevance.offer_id == Offer.id,
                )
                .where(
                    MissionOfferRelevance.mission_id == mission_id,
                    MissionOfferRelevance.classification.in_(
                        {OfferRelevance.MATCH, OfferRelevance.POSSIBLE_MATCH}
                    ),
                    Product.family_key == criteria.requested_family_key,
                    Product.identity_key.is_not(None),
                )
                .distinct()
                .order_by(Product.display_name, Product.name, Product.id)
        )
        if criteria.requested_variant is not None:
            variants_statement = variants_statement.where(
                Product.variant == criteria.requested_variant
            )
        available_variants = list(await session.scalars(variants_statement))
        selected_product_ids = tuple(
            await session.scalars(
                select(MissionProductSelection.product_id)
                .where(MissionProductSelection.mission_id == mission_id)
                .order_by(MissionProductSelection.product_id)
            )
        )
    return MissionDetail(
        mission=mission,
        criteria=criteria,
        sources=[(source, store) for source, store in sources_result.all()],
        schedule=schedule,
        transitions=transitions,
        available_variants=available_variants,
        selected_product_ids=selected_product_ids,
    )
