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

from dataclasses import dataclass
from datetime import datetime, timedelta
from random import uniform
from uuid import UUID

from sqlalchemy import exists, func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.collection.models import (
    PriceObservation,
    PromotionalWindow,
    StoreActivityState,
)
from app.offers.models import Offer

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
    session: AsyncSession, *, store_id: UUID, now: datetime, config: CadenceConfig
) -> CadenceDecision:
    """Ponto único de decisão -- nunca espalhar `if`/`else` de datas ou de
    atividade pelo scheduler; qualquer novo caso entra aqui."""
    if await _is_promo_calendar_active(session, now=now):
        return CadenceDecision(
            config.promo_min_minutes, config.promo_max_minutes, _MODE_PROMO_CALENDAR
        )
    if await _is_high_activity(session, store_id=store_id, now=now, config=config):
        return CadenceDecision(
            config.promo_min_minutes, config.promo_max_minutes, _MODE_HIGH_ACTIVITY
        )
    return CadenceDecision(
        config.normal_min_minutes, config.normal_max_minutes, _MODE_NORMAL
    )


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
    session: AsyncSession, *, store_id: UUID, now: datetime, config: CadenceConfig
) -> bool:
    """Atividade é POR LOJA, nunca por item/produto individual (decisão
    explícita -- sem modelo preditivo, sem frequência por produto).

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
    state = await session.get(StoreActivityState, store_id, with_for_update=True)
    if (
        state is not None
        and state.high_activity_until is not None
        and state.high_activity_until > now
    ):
        return True
    window_start = now - timedelta(minutes=config.high_activity_window_minutes)
    earlier = aliased(PriceObservation)
    change_count = await session.scalar(
        select(func.count(PriceObservation.id))
        .select_from(PriceObservation)
        .join(Offer, Offer.id == PriceObservation.offer_id)
        .where(
            Offer.store_id == store_id,
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
        .values(store_id=store_id, high_activity_until=high_activity_until, updated_at=now)
        .on_conflict_do_update(
            index_elements=[StoreActivityState.store_id],
            set_={"high_activity_until": high_activity_until, "updated_at": now},
        )
    )
    return True
