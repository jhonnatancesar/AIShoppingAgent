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

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from random import uniform
from uuid import UUID

from sqlalchemy import exists, func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.collection.models import (
    CollectionRun,
    CollectionRunStatus,
    MissionOfferRelevance,
    PriceObservation,
    PromotionalWindow,
    SharedCollectionOffer,
    StoreActivityState,
)
from app.collection.normalization import Availability
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
            raise ValueError(
                "normal_max_minutes não pode ser menor que normal_min_minutes"
            )
        if self.promo_max_minutes < self.promo_min_minutes:
            raise ValueError(
                "promo_max_minutes não pode ser menor que promo_min_minutes"
            )
        # Piso absoluto da V1.2 (§8 do desenho aprovado): nenhum modo,
        # nem promocional, nem uma futura diferenciação de plano pago,
        # pode levar uma mesma necessidade (MonitoringItem, store) a ser
        # pesquisada de novo abaixo de 30 minutos -- risco de bloqueio da
        # infraestrutura compartilhada nunca é comprado por velocidade.
        if self.promo_min_minutes < 30:
            raise ValueError(
                "promo_min_minutes nunca pode ficar abaixo do piso de 30 minutos"
            )
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


# ---------------------------------------------------------------------------
# Política de frescor (pedido explícito do dono do produto, 2026-09-11):
# distingue "disponível confirmado há pouco" de "confirmação envelhecida",
# separado de "indisponível" (já tratado por `_same_commercial_state`,
# `orchestration.py` -- mudança de disponibilidade sempre gera observação
# nova) e de "sumiço silencioso" (a coleta rodou com sucesso, mas esta
# oferta específica não reapareceu -- diferente de "a coleta falhou").
# Nunca confunde falha/ausência com indisponibilidade COMPROVADA.
# ---------------------------------------------------------------------------


class OfferFreshnessStatus(StrEnum):
    CONFIRMED_RECENT = "confirmed_recent"
    CONFIRMED_STALE = "confirmed_stale"
    UNAVAILABLE = "unavailable"
    MISSING_NO_CONFIRMATION = "missing_no_confirmation"
    COLLECTION_FAILED = "collection_failed"
    NEVER_CONFIRMED = "never_confirmed"


STALE_GRACE_MULTIPLIER = 2
"""Múltiplo do intervalo MÁXIMO de cadência vigente (`CadenceDecision.
max_minutes`, recalculado no momento da LEITURA -- não congelado no
momento da confirmação, ver docstring de `resolve_offer_freshness`)
usado como limiar de "confirmação antiga". Documentado explicitamente
(pedido do dono do produto): absorve exatamente UM ciclo perdido ou
atrasado (fila cheia, backoff aplicado, jitter normal do agendamento --
`sample_next_run_at` já sorteia dentro de uma faixa, não um horário
fixo) como situação normal, nunca "antiga"; só a partir do SEGUNDO ciclo
sem reconfirmação é que o dado passa a ser tratado como potencialmente
desatualizado. Não é um prazo inventado à parte -- é sempre função da
cadência real: NORMAL (45-75min) dá 150min de tolerância; PROMO_CALENDAR/
HIGH_ACTIVITY (30-45min) dá 90min. Ver `test_offer_freshness_reacts_to_
current_cadence_mode_not_frozen_one` para o comportamento sob mudança de
modo."""


async def resolve_offer_freshness(
    session: AsyncSession,
    *,
    offer_id: UUID,
    product_id: UUID,
    store_id: UUID,
    now: datetime,
    config: CadenceConfig,
) -> OfferFreshnessStatus:
    """Classifica o frescor de UMA Offer a partir da confirmação mais
    recente (`SharedCollectionOffer` + `CollectionRun`, nunca `Offer.
    last_seen_at` -- esse é só um escalar mutável sem histórico).

    Deliberado (pedido do dono do produto): o limiar de "antiga" usa a
    cadência ATUAL (`resolve_product_market_mode` no momento desta
    chamada), não a cadência vigente quando a oferta foi confirmada. Se a
    loja entrar em HIGH_ACTIVITY depois de uma confirmação feita sob
    NORMAL, essa mesma confirmação pode passar a ser considerada "antiga"
    mais cedo do que se a cadência tivesse ficado em NORMAL -- isso é
    intencional (dado potencialmente desatualizado importa mais quando o
    preço está se movendo rápido agora), documentado aqui para nunca ser
    uma reclassificação silenciosa/indefinida, e coberto por teste
    dedicado que força a transição de modo entre duas leituras da MESMA
    confirmação."""
    confirmation = (
        await session.execute(
            select(
                SharedCollectionOffer.collection_run_id,
                CollectionRun.started_at,
                PriceObservation.availability,
            )
            .select_from(SharedCollectionOffer)
            .join(
                CollectionRun,
                CollectionRun.id == SharedCollectionOffer.collection_run_id,
            )
            .join(
                PriceObservation,
                PriceObservation.id == SharedCollectionOffer.observation_id,
            )
            .where(SharedCollectionOffer.offer_id == offer_id)
            .order_by(CollectionRun.started_at.desc())
            .limit(1)
        )
    ).first()
    has_run_level_confirmation = confirmation is not None
    if confirmation is None:
        # Sem NENHUMA linha em `shared_collection_offers` para esta Offer
        # -- normal para dado coletado ANTES desta confirmação existir
        # (não há retroatividade, item 7 do design), nunca tratado como
        # "nunca confirmada" quando já existe `PriceObservation` real.
        # Cai para a última observação bruta (mesma fonte que o mecanismo
        # antigo usava) -- só perde a distinção fina falha/sumiço, que
        # depende de correlação por `CollectionRun` que este dado antigo
        # não tem; frescor por idade e indisponibilidade continuam
        # comprovados normalmente.
        fallback = (
            await session.execute(
                select(PriceObservation.observed_at, PriceObservation.availability)
                .where(PriceObservation.offer_id == offer_id)
                .order_by(
                    PriceObservation.observed_at.desc(), PriceObservation.id.desc()
                )
                .limit(1)
            )
        ).first()
        if fallback is None:
            return OfferFreshnessStatus.NEVER_CONFIRMED
        observed_at, availability = fallback
        # `Offer.last_seen_at` (TASK-093) avança a CADA coleta bem-
        # sucedida, inclusive quando o preço não muda e nenhuma
        # `PriceObservation` nova é gravada (dedupe) -- prova, sozinho,
        # que aquele MESMO estado (o da última observação) foi
        # reconfirmado depois, mesmo sem confirmação por run disponível
        # (registro legado). Nunca o contrário: só usado aqui para
        # frescor ATUAL de UMA oferta, nunca para reconstruir quantas
        # confirmações diárias houve no passado (isso continua
        # exclusivo de `shared_collection_offers`/`PriceObservation`
        # reais em `_fetch_daily_low_points` -- um único escalar não
        # reconstrói o histórico intermediário.
        offer_last_seen_at = await session.scalar(
            select(Offer.last_seen_at).where(Offer.id == offer_id)
        )
        confirmed_at = (
            max(observed_at, offer_last_seen_at)
            if offer_last_seen_at is not None
            else observed_at
        )
    else:
        _run_id, confirmed_at, availability = confirmation

    if availability != Availability.AVAILABLE:
        # Já tratado como estado próprio na coleta (`_same_commercial_
        # state`): mudança de disponibilidade sempre gera observação
        # nova -- nunca prolonga "disponível" silenciosamente. Vence
        # qualquer idade: indisponibilidade comprovada não "envelhece"
        # para virar outra coisa.
        return OfferFreshnessStatus.UNAVAILABLE

    later_failed = (
        await session.scalar(
            select(CollectionRun.id)
            .where(
                CollectionRun.store_id == store_id,
                CollectionRun.status == CollectionRunStatus.FAILED,
                CollectionRun.started_at > confirmed_at,
            )
            .limit(1)
        )
        if has_run_level_confirmation
        else None
    )
    if later_failed is not None:
        # Coleta falhou DEPOIS da última confirmação -- não é a mesma
        # coisa que "oferta indisponível" nem que "sumiço": é a
        # infraestrutura de coleta com problema agora, sem informação
        # nova sobre a oferta em si (pode voltar a funcionar e a oferta
        # ainda estar lá).
        return OfferFreshnessStatus.COLLECTION_FAILED

    missing = await session.scalar(
        select(CollectionRun.id)
        .where(
            CollectionRun.store_id == store_id,
            CollectionRun.status == CollectionRunStatus.SUCCEEDED,
            CollectionRun.started_at > confirmed_at,
        )
        .where(
            ~exists(
                select(SharedCollectionOffer.collection_run_id).where(
                    SharedCollectionOffer.collection_run_id == CollectionRun.id,
                    SharedCollectionOffer.offer_id == offer_id,
                )
            )
        )
        .limit(1)
    )
    if missing is not None:
        # A coleta RODOU com sucesso depois da última confirmação, mas
        # esta oferta especificamente não apareceu nela -- sinal mais
        # forte que "confirmação antiga" simples (a infraestrutura
        # funciona; especificamente esta oferta não foi mais encontrada).
        return OfferFreshnessStatus.MISSING_NO_CONFIRMATION

    decision = await resolve_product_market_mode(
        session, product_id=product_id, now=now, config=config
    )
    stale_after = timedelta(minutes=STALE_GRACE_MULTIPLIER * decision.max_minutes)
    # Pedido explícito do dono do produto (correção sobre a 1a versão
    # deste teste): o simples INÍCIO de uma janela acelerada
    # (PROMO_CALENDAR/HIGH_ACTIVITY) nunca pode tornar "antiga",
    # retroativamente, uma confirmação que já era "recente" sob o modo
    # anterior -- o worker ainda não teve OPORTUNIDADE de recoletar sob
    # a cadência nova. O relógio do limiar mais curto só começa a
    # contar a partir do INÍCIO da janela (`_resolve_mode_window_start`,
    # agenda real: `PromotionalWindow.starts_at` ou o início inferido da
    # janela de `StoreActivityState` vigente), nunca da confirmação
    # original quando ela é anterior à janela.
    if decision.mode != _MODE_NORMAL:
        window_start = await _resolve_mode_window_start(
            session, store_id=store_id, now=now, config=config
        )
        if window_start is not None and window_start > confirmed_at:
            effective_since = window_start
        else:
            effective_since = confirmed_at
    else:
        effective_since = confirmed_at
    if now - effective_since <= stale_after:
        return OfferFreshnessStatus.CONFIRMED_RECENT
    return OfferFreshnessStatus.CONFIRMED_STALE


async def _resolve_mode_window_start(
    session: AsyncSession, *, store_id: UUID, now: datetime, config: CadenceConfig
) -> datetime | None:
    """Instante em que a janela acelerada VIGENTE começou -- agenda real,
    nunca inferência solta. `PROMO_CALENDAR` tem início exato
    (`PromotionalWindow.starts_at`); `HIGH_ACTIVITY` não persiste início
    explícito, só o fim (`StoreActivityState.high_activity_until`), então
    o início é derivado subtraindo `high_activity_duration_minutes` (o
    mesmo intervalo que `_is_high_activity` usou para gravar o fim) --
    aproximação por cima (nunca subestima quanto tempo a janela já está
    ativa), o que só torna a proteção contra reclassificação prematura
    mais generosa, nunca menos. Verificação por loja inteira (não por
    escopo específico) de propósito: é só para dar crédito ao worker,
    nunca para decidir o modo em si (isso continua sendo
    `resolve_product_market_mode`, escopado como sempre)."""
    promo_start = await session.scalar(
        select(PromotionalWindow.starts_at)
        .where(PromotionalWindow.starts_at <= now, PromotionalWindow.ends_at > now)
        .order_by(PromotionalWindow.starts_at.desc())
        .limit(1)
    )
    if promo_start is not None:
        return promo_start
    high_activity_until = await session.scalar(
        select(StoreActivityState.high_activity_until)
        .where(
            StoreActivityState.store_id == store_id,
            StoreActivityState.high_activity_until > now,
        )
        .order_by(StoreActivityState.high_activity_until.desc())
        .limit(1)
    )
    if high_activity_until is not None:
        return high_activity_until - timedelta(
            minutes=config.high_activity_duration_minutes
        )
    return None


async def _resolve_mode_window_starts_batch(
    session: AsyncSession,
    *,
    store_ids: Sequence[UUID],
    now: datetime,
    config: CadenceConfig,
) -> dict[UUID, datetime | None]:
    """Mesma regra de `_resolve_mode_window_start`, para várias lojas de
    uma vez -- PROMO_CALENDAR já é global (1 consulta serve para todas);
    HIGH_ACTIVITY usa `store_id IN (...)` (1 consulta para todas as
    lojas, nunca uma por loja)."""
    promo_start = await session.scalar(
        select(PromotionalWindow.starts_at)
        .where(PromotionalWindow.starts_at <= now, PromotionalWindow.ends_at > now)
        .order_by(PromotionalWindow.starts_at.desc())
        .limit(1)
    )
    if promo_start is not None:
        return dict.fromkeys(store_ids, promo_start)
    rows = (
        await session.execute(
            select(
                StoreActivityState.store_id, StoreActivityState.high_activity_until
            ).where(
                StoreActivityState.store_id.in_(store_ids),
                StoreActivityState.high_activity_until > now,
            )
        )
    ).all()
    latest_by_store: dict[UUID, datetime] = {}
    for store_id, high_activity_until in rows:
        current = latest_by_store.get(store_id)
        if current is None or high_activity_until > current:
            latest_by_store[store_id] = high_activity_until
    return {
        store_id: (
            latest_by_store[store_id]
            - timedelta(minutes=config.high_activity_duration_minutes)
            if store_id in latest_by_store
            else None
        )
        for store_id in store_ids
    }


async def resolve_offers_freshness_batch(
    session: AsyncSession,
    *,
    offers: Sequence[tuple[UUID, UUID]],
    product_id: UUID,
    now: datetime,
    config: CadenceConfig,
) -> dict[UUID, OfferFreshnessStatus]:
    """Versão em lote de `resolve_offer_freshness`: classifica TODAS as
    `offers` (pares `(offer_id, store_id)`) de um Product numa única
    passada, com número de consultas que NUNCA escala com a quantidade
    de ofertas -- só com o número de LOJAS distintas entre elas
    (pequeno e fixo no V1: Amazon, KaBuM!, Magalu, Mercado Livre,
    Pichau, Terabyte). Pedido explícito do dono do produto: chamar
    `resolve_offer_freshness` uma vez por oferta candidata em
    `_resolve_current_amount` seria exatamente o padrão N+1 que o
    projeto já proíbe em outros lugares (ver `load_mission_list_extras`,
    `test_load_mission_list_extras_query_count_does_not_scale_with_
    page_size`) -- esta função existe para `_resolve_current_amount`
    nunca cair nisso. Mesma classificação, mesmas regras (frescor,
    fallback via `last_seen_at`, falha vs. sumiço, janela de cadência
    sem retroatividade) de `resolve_offer_freshness` -- só reorganizada
    para consultar em lote."""
    if not offers:
        return {}
    offer_ids = [offer_id for offer_id, _ in offers]
    store_by_offer = dict(offers)
    store_ids = list({store_id for _, store_id in offers})

    # 1) confirmação mais recente por oferta -- 1 consulta para todas.
    confirmation_rank = (
        func.row_number()
        .over(
            partition_by=SharedCollectionOffer.offer_id,
            order_by=CollectionRun.started_at.desc(),
        )
        .label("rn")
    )
    confirmation_rows = (
        await session.execute(
            select(
                SharedCollectionOffer.offer_id,
                CollectionRun.started_at,
                PriceObservation.availability,
                confirmation_rank,
            )
            .select_from(SharedCollectionOffer)
            .join(
                CollectionRun,
                CollectionRun.id == SharedCollectionOffer.collection_run_id,
            )
            .join(
                PriceObservation,
                PriceObservation.id == SharedCollectionOffer.observation_id,
            )
            .where(SharedCollectionOffer.offer_id.in_(offer_ids))
        )
    ).all()
    confirmed: dict[UUID, tuple[datetime, Availability]] = {
        offer_id: (started_at, availability)
        for offer_id, started_at, availability, rank in confirmation_rows
        if rank == 1
    }

    # 2) fallback (última PriceObservation + Offer.last_seen_at) só para
    # quem não tem confirmação por run -- 2 consultas no total, nunca
    # uma por oferta.
    unconfirmed_ids = [offer_id for offer_id in offer_ids if offer_id not in confirmed]
    fallback: dict[UUID, tuple[datetime, Availability]] = {}
    if unconfirmed_ids:
        obs_rank = (
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
        obs_rows = (
            await session.execute(
                select(
                    PriceObservation.offer_id,
                    PriceObservation.observed_at,
                    PriceObservation.availability,
                    obs_rank,
                ).where(PriceObservation.offer_id.in_(unconfirmed_ids))
            )
        ).all()
        latest_obs = {
            offer_id: (observed_at, availability)
            for offer_id, observed_at, availability, rank in obs_rows
            if rank == 1
        }
        last_seen_rows = (
            await session.execute(
                select(Offer.id, Offer.last_seen_at).where(
                    Offer.id.in_(unconfirmed_ids)
                )
            )
        ).all()
        last_seen_by_offer = dict(last_seen_rows)
        for offer_id, (observed_at, availability) in latest_obs.items():
            last_seen_at = last_seen_by_offer.get(offer_id)
            confirmed_at = (
                max(observed_at, last_seen_at)
                if last_seen_at is not None
                else observed_at
            )
            fallback[offer_id] = (confirmed_at, availability)

    merged: dict[UUID, tuple[datetime, Availability]] = {**fallback, **confirmed}

    result: dict[UUID, OfferFreshnessStatus] = {}
    pending_ids: list[UUID] = []
    for offer_id in offer_ids:
        if offer_id not in merged:
            result[offer_id] = OfferFreshnessStatus.NEVER_CONFIRMED
            continue
        _confirmed_at, availability = merged[offer_id]
        if availability != Availability.AVAILABLE:
            result[offer_id] = OfferFreshnessStatus.UNAVAILABLE
            continue
        pending_ids.append(offer_id)
    if not pending_ids:
        return result

    # 3) runs candidatas (FAILED/SUCCEEDED mais novas que a confirmação)
    # -- 1 consulta cobrindo TODAS as lojas envolvidas de uma vez,
    # filtrando pelo menor `confirmed_at` entre elas (o filtro fino por
    # oferta é feito em Python a seguir, sem nova consulta).
    stores_pending = {store_by_offer[offer_id] for offer_id in pending_ids}
    min_confirmed_at = min(merged[offer_id][0] for offer_id in pending_ids)
    run_rows = (
        await session.execute(
            select(
                CollectionRun.store_id,
                CollectionRun.started_at,
                CollectionRun.status,
                CollectionRun.id,
            ).where(
                CollectionRun.store_id.in_(stores_pending),
                CollectionRun.started_at > min_confirmed_at,
                CollectionRun.status.in_(
                    (CollectionRunStatus.FAILED, CollectionRunStatus.SUCCEEDED)
                ),
            )
        )
    ).all()
    runs_by_store: dict[UUID, list[tuple[datetime, CollectionRunStatus, UUID]]] = (
        defaultdict(list)
    )
    for store_id, started_at, status, run_id in run_rows:
        runs_by_store[store_id].append((started_at, status, run_id))

    # 4) confirmações já registradas para essas runs candidatas -- 1
    # consulta, nunca uma por (run, oferta).
    candidate_run_ids = {
        run_id for rows in runs_by_store.values() for *_rest, run_id in rows
    }
    confirmed_pairs: set[tuple[UUID, UUID]] = set()
    if candidate_run_ids:
        pair_rows = (
            await session.execute(
                select(
                    SharedCollectionOffer.collection_run_id,
                    SharedCollectionOffer.offer_id,
                ).where(SharedCollectionOffer.collection_run_id.in_(candidate_run_ids))
            )
        ).all()
        confirmed_pairs = set(pair_rows)

    still_pending: list[UUID] = []
    for offer_id in pending_ids:
        store_id = store_by_offer[offer_id]
        confirmed_at, _ = merged[offer_id]
        later_runs = [r for r in runs_by_store.get(store_id, ()) if r[0] > confirmed_at]
        if any(status == CollectionRunStatus.FAILED for _, status, _ in later_runs):
            result[offer_id] = OfferFreshnessStatus.COLLECTION_FAILED
            continue
        if any(
            status == CollectionRunStatus.SUCCEEDED
            and (run_id, offer_id) not in confirmed_pairs
            for _, status, run_id in later_runs
        ):
            result[offer_id] = OfferFreshnessStatus.MISSING_NO_CONFIRMATION
            continue
        still_pending.append(offer_id)
    if not still_pending:
        return result

    # 5) cadência: 1x por Product (já O(1)); início de janela acelerada:
    # 1 consulta cobrindo todas as lojas envolvidas de uma vez.
    decision = await resolve_product_market_mode(
        session, product_id=product_id, now=now, config=config
    )
    stale_after = timedelta(minutes=STALE_GRACE_MULTIPLIER * decision.max_minutes)
    window_starts: dict[UUID, datetime | None] = (
        await _resolve_mode_window_starts_batch(
            session, store_ids=store_ids, now=now, config=config
        )
        if decision.mode != _MODE_NORMAL
        else {}
    )
    for offer_id in still_pending:
        store_id = store_by_offer[offer_id]
        confirmed_at, _ = merged[offer_id]
        window_start = window_starts.get(store_id)
        effective_since = (
            window_start
            if window_start is not None and window_start > confirmed_at
            else confirmed_at
        )
        result[offer_id] = (
            OfferFreshnessStatus.CONFIRMED_RECENT
            if now - effective_since <= stale_after
            else OfferFreshnessStatus.CONFIRMED_STALE
        )
    return result
