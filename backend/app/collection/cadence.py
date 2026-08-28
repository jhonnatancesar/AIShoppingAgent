"""Política de cadência de coleta compartilhada (TASK-112, fase 3B).

Decide o INTERVALO até a próxima coleta de um `(MonitoringItem, store)`
já bem-sucedida -- nunca confundir com três camadas vizinhas, cada uma
com um papel diferente:

- `UserCollectionQueueState`/cooldown de fairness (`app.collection.
  fairness`): decide QUEM pode consumir capacidade agora, nunca QUANDO
  uma necessidade específica de monitoramento precisa ser revisitada.
- `StoreThrottleState`: protege contra rajada de acessos à MESMA loja
  entre alvos DIFERENTES no MESMO ciclo -- não é intervalo de
  monitoramento, é pacing de curtíssimo prazo (segundos).
- Backoff por bloqueio externo confirmado (DEC-046, `next_source_
  backoff`): fica FORA desta política -- resolvido à parte por quem
  aplica o bloqueio, nunca encurtado por promoção/atividade alta. O dono
  do "vence sempre" não é um `if` aqui: é estrutural -- o claim
  (`app.collection.shared_claim`) recusa um `(item, store)` cujo
  `next_eligible_at` (backoff) ainda não passou, mesmo que `next_run_at`
  (cadência, o que esta política decide) já esteja due.

Três modos, prioridade fixa (backoff já é tratado fora, ver acima):
`PROMO_CALENDAR`/`HIGH_ACTIVITY` (30-45min por padrão) > `NORMAL`
(45-75min por padrão). Determinístico, sem IA, sem estatística
sofisticada -- pensado para a escala real da V1.2 (dezenas de lojas,
poucas centenas de itens monitorados), não para milhões de eventos.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from random import uniform
from uuid import UUID

from sqlalchemy import exists, func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.collection.models import (
    MissionOfferRelevance,
    PriceObservation,
    PromotionalWindow,
    StoreActivityState,
)
from app.missions.models import MissionMonitoringItem
from app.offers.models import Offer
from app.stores.models import Store

_MODE_NORMAL = "normal"
_MODE_PROMO_CALENDAR = "promo_calendar"
_MODE_HIGH_ACTIVITY = "high_activity"


@dataclass(frozen=True, slots=True)
class CadenceConfig:
    """Parâmetros configuráveis da política -- vêm de `Settings`
    (`app.core.config`), nunca hardcoded no meio do fluxo de claim."""

    normal_min_minutes: int = 45
    normal_max_minutes: int = 75
    promo_min_minutes: int = 30
    promo_max_minutes: int = 45
    high_activity_window_minutes: int = 30
    high_activity_change_threshold: int = 3
    high_activity_duration_minutes: int = 60

    def __post_init__(self) -> None:
        if self.normal_min_minutes <= 0 or self.promo_min_minutes <= 0:
            raise ValueError("intervalos mínimos devem ser positivos")
        if self.normal_max_minutes < self.normal_min_minutes:
            raise ValueError("normal_max_minutes não pode ser menor que normal_min_minutes")
        if self.promo_max_minutes < self.promo_min_minutes:
            raise ValueError("promo_max_minutes não pode ser menor que promo_min_minutes")
        # Piso absoluto da V1.2 (§8 do desenho aprovado): nenhum modo,
        # nem promocional, nem uma futura diferenciação de plano pago,
        # pode levar uma mesma necessidade (MonitoringItem, store) a ser
        # pesquisada de novo abaixo de 30 minutos -- risco de bloqueio da
        # infraestrutura compartilhada nunca é comprado por velocidade.
        if self.promo_min_minutes < 30:
            raise ValueError("promo_min_minutes nunca pode ficar abaixo do piso de 30 minutos")
        if self.high_activity_window_minutes <= 0:
            raise ValueError("high_activity_window_minutes deve ser positivo")
        if self.high_activity_change_threshold <= 0:
            raise ValueError("high_activity_change_threshold deve ser positivo")
        if self.high_activity_duration_minutes <= 0:
            raise ValueError("high_activity_duration_minutes deve ser positivo")


@dataclass(frozen=True, slots=True)
class CadenceDecision:
    min_minutes: int
    max_minutes: int
    mode: str


async def resolve_collection_cadence(
    session: AsyncSession,
    *,
    store_id: UUID,
    scope_id: UUID,
    mission_ids: Sequence[UUID],
    now: datetime,
    config: CadenceConfig,
) -> CadenceDecision:
    """Ponto único de decisão -- nunca espalhar `if`/`else` de datas ou de
    atividade pelo scheduler; qualquer novo caso entra aqui.

    TASK-116 (correção estrutural, achado real: HIGH_ACTIVITY acelerava a
    loja inteira por uma mudança de preço em produto totalmente alheio).
    `scope_id` é a unidade de monitoramento real (`MonitoringItem.id` no
    caminho compartilhado TASK-112, ou `Mission.id` no caminho legado
    sem `MonitoringItem`) -- chave de `StoreActivityState` junto com
    `store_id`, nunca só a loja. `mission_ids` é o conjunto de Missions
    que compartilham essa unidade (uma só no caminho legado; potencialmente
    várias no compartilhado, via `mission_monitoring_items`) -- usado para
    escopar a CONTAGEM de mudança de preço só às ofertas relevantes para
    essa unidade, nunca à loja inteira."""
    if await _is_promo_calendar_active(session, now=now):
        return CadenceDecision(
            config.promo_min_minutes, config.promo_max_minutes, _MODE_PROMO_CALENDAR
        )
    if await _is_high_activity(
        session,
        store_id=store_id,
        scope_id=scope_id,
        mission_ids=mission_ids,
        now=now,
        config=config,
    ):
        return CadenceDecision(
            config.promo_min_minutes, config.promo_max_minutes, _MODE_HIGH_ACTIVITY
        )
    return CadenceDecision(
        config.normal_min_minutes, config.normal_max_minutes, _MODE_NORMAL
    )


async def resolve_product_market_mode(
    session: AsyncSession, *, product_id: UUID, now: datetime, config: CadenceConfig
) -> CadenceDecision:
    """Modo efetivo (NORMAL/PROMO_CALENDAR/HIGH_ACTIVITY) de um `Product`
    inteiro, cross-loja (TASK-113, correção pós-plano ponto 10).

    Ponto único de resolução reutilizado tanto pelo TTL do
    `MarketPriceAssessment` (`app.market_research.service`, TASK-113
    §33.15) quanto pela janela de re-alert (`app.alerts.evaluator`,
    §33.11) -- nunca duas leituras diferentes de "loja relevante".
    `PROMO_CALENDAR` já é global (`_is_promo_calendar_active` não filtra
    por loja); `HIGH_ACTIVITY` é avaliado por loja, mas aqui percorre
    TODA loja com `Offer` deste `product_id` e `Store.is_active` (sinal
    já existente, mesmo usado para desativar a Terabyte, DEC-070 --
    nenhum conceito novo de "loja relevante/ativa" foi inventado). Pior
    caso (mais agressivo) vence: uma única loja ativa em HIGH_ACTIVITY já
    é suficiente para tratar o Product inteiro como acelerado.

    TASK-116 (achado real em PROD, v1.2.6): `_is_high_activity` passou a
    exigir `scope_id`/`mission_ids` -- esta função nunca conhece
    "o" `scope_id` de um Product (um mesmo Product numa loja pode ter
    Offers cobertas por Missions de escopos diferentes), então resolve,
    por loja, TODOS os escopos reais que uma coleta usaria
    (`_resolve_market_mode_scopes`, mesmo agrupamento por
    `MonitoringItem.id`/`Mission.id` que `shared_claim`/`orchestration`
    já usam) e testa cada um -- nunca `scope_id=None` para calar o erro."""
    if await _is_promo_calendar_active(session, now=now):
        return CadenceDecision(
            config.promo_min_minutes, config.promo_max_minutes, _MODE_PROMO_CALENDAR
        )
    store_ids = (
        await session.scalars(
            select(Offer.store_id)
            .distinct()
            .join(Store, Store.id == Offer.store_id)
            .where(Offer.product_id == product_id, Store.is_active.is_(True))
        )
    ).all()
    for store_id in store_ids:
        scopes = await _resolve_market_mode_scopes(
            session, product_id=product_id, store_id=store_id
        )
        for scope_id, mission_ids in scopes:
            if await _is_high_activity(
                session,
                store_id=store_id,
                scope_id=scope_id,
                mission_ids=mission_ids,
                now=now,
                config=config,
            ):
                return CadenceDecision(
                    config.promo_min_minutes,
                    config.promo_max_minutes,
                    _MODE_HIGH_ACTIVITY,
                )
    return CadenceDecision(
        config.normal_min_minutes, config.normal_max_minutes, _MODE_NORMAL
    )


async def _resolve_market_mode_scopes(
    session: AsyncSession, *, product_id: UUID, store_id: UUID
) -> list[tuple[UUID, list[UUID]]]:
    """Reconstrói, para `(product_id, store_id)`, os mesmos escopos de
    cadência que uma coleta real usaria (TASK-116): agrupa as Missions
    relevantes às Offers deste Product nesta loja (`mission_offer_
    relevance`, mesmo join sem filtro de `classification` que
    `_is_high_activity` já usa) por `MonitoringItem.id` quando existe
    vínculo em `mission_monitoring_items` (caminho compartilhado,
    TASK-112), ou por `Mission.id` isolado quando não existe (caminho
    legado, sem `MonitoringItem`) -- nunca inventa um conceito de escopo
    novo, sempre o mesmo usado por `shared_claim._advance_monitoring_
    item_store` e `orchestration` na coleta real."""
    rows = (
        await session.execute(
            select(
                MissionOfferRelevance.mission_id,
                MissionMonitoringItem.monitoring_item_id,
            )
            .select_from(MissionOfferRelevance)
            .join(Offer, Offer.id == MissionOfferRelevance.offer_id)
            .outerjoin(
                MissionMonitoringItem,
                MissionMonitoringItem.mission_id == MissionOfferRelevance.mission_id,
            )
            .where(Offer.product_id == product_id, Offer.store_id == store_id)
            .distinct()
        )
    ).all()
    scopes: dict[UUID, list[UUID]] = {}
    for mission_id, monitoring_item_id in rows:
        scope_id = monitoring_item_id if monitoring_item_id is not None else mission_id
        scopes.setdefault(scope_id, []).append(mission_id)
    return list(scopes.items())


def sample_next_run_at(started_at: datetime, decision: CadenceDecision) -> datetime:
    """Sempre relativo a AGORA (o instante da coleta bem-sucedida que
    está sendo agendada), nunca ao `next_run_at` antigo -- ao contrário
    do algoritmo fixo anterior (que "recuperava atraso" avançando em
    múltiplos inteiros do intervalo), uma cadência com faixa variável
    (NORMAL/PROMO/HIGH_ACTIVITY podem mudar de um ciclo para o outro) não
    tem um "múltiplo do intervalo" estável para recuperar contra -- e não
    precisa: o objetivo nunca foi bater um horário de relógio fixo, só
    manter o intervalo aproximado entre coletas, com jitter."""
    minutes = uniform(decision.min_minutes, decision.max_minutes)
    return started_at + timedelta(minutes=minutes)


async def _is_promo_calendar_active(session: AsyncSession, *, now: datetime) -> bool:
    return (
        await session.scalar(
            select(PromotionalWindow.id)
            .where(
                PromotionalWindow.starts_at <= now,
                PromotionalWindow.ends_at > now,
            )
            .limit(1)
        )
    ) is not None


async def _is_high_activity(
    session: AsyncSession,
    *,
    store_id: UUID,
    scope_id: UUID,
    mission_ids: Sequence[UUID],
    now: datetime,
    config: CadenceConfig,
) -> bool:
    """Atividade é por UNIDADE DE MONITORAMENTO (`scope_id`) + loja, nunca
    pela loja inteira (TASK-116, correção estrutural -- ver docstring de
    `resolve_collection_cadence`). A contagem de mudança de preço é
    restrita às ofertas ligadas às `mission_ids` desta unidade
    (`mission_offer_relevance`) -- um produto totalmente alheio mudando de
    preço na mesma loja nunca acelera esta unidade.

    Correção (achado real, auditoria pós-implementação): uma nova
    `PriceObservation` é gravada tanto quando o estado comercial muda
    (`PriceObservationComparison.CHANGED`) quanto na PRIMEIRA observação
    de uma Offer (`FIRST_OBSERVATION`, `latest is None` em `_persist_
    phase_a`/`_persist_shared_offers_and_finish`) -- as duas deixam
    `redundant=False` e criam linha nova. Contar toda linha nova
    confundiria "a loja mudou preço" com "começamos a monitorar um
    produto novo" (Offer nova, Mission nova, `MonitoringItem` novo) --
    nenhum desses eventos é atividade comercial real da loja.

    Sinal correto, ainda sem schema novo: por construção (TASK-093/
    DEC-097), toda `PriceObservation` gravada representa um estado
    DIFERENTE do que a precedeu PARA A MESMA Offer (senão a linha
    anterior seria reaproveitada, nunca uma nova criada) -- então "esta
    observação tem uma observação mais antiga para a mesma Offer" já é,
    por si só, exatamente "isto é CHANGED, não FIRST_OBSERVATION". A
    query abaixo exclui via `NOT EXISTS` justamente as observações que
    são a primeira da sua própria Offer."""
    state = await session.get(
        StoreActivityState, (store_id, scope_id), with_for_update=True
    )
    if (
        state is not None
        and state.high_activity_until is not None
        and state.high_activity_until > now
    ):
        return True
    if not mission_ids:
        return False
    window_start = now - timedelta(minutes=config.high_activity_window_minutes)
    earlier = aliased(PriceObservation)
    change_count = await session.scalar(
        select(func.count(func.distinct(PriceObservation.id)))
        .select_from(PriceObservation)
        .join(Offer, Offer.id == PriceObservation.offer_id)
        .join(
            MissionOfferRelevance,
            MissionOfferRelevance.offer_id == PriceObservation.offer_id,
        )
        .where(
            Offer.store_id == store_id,
            MissionOfferRelevance.mission_id.in_(mission_ids),
            PriceObservation.observed_at >= window_start,
            PriceObservation.observed_at <= now,
            # Exclui FIRST_OBSERVATION -- só conta quando existe uma
            # observação mais antiga para a MESMA Offer (prova de que
            # este registro é uma mudança real, nunca uma chegada nova).
            exists().where(
                earlier.offer_id == PriceObservation.offer_id,
                earlier.observed_at < PriceObservation.observed_at,
            ),
        )
    )
    if change_count is None or change_count < config.high_activity_change_threshold:
        return False
    high_activity_until = now + timedelta(minutes=config.high_activity_duration_minutes)
    await session.execute(
        postgresql_insert(StoreActivityState)
        .values(
            store_id=store_id,
            scope_id=scope_id,
            high_activity_until=high_activity_until,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[StoreActivityState.store_id, StoreActivityState.scope_id],
            set_={"high_activity_until": high_activity_until, "updated_at": now},
        )
    )
    return True
