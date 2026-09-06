"""Coleta compartilhada por `(MonitoringItem, store)` (TASK-112, fase 3A).

Prova que UMA necessidade `(MonitoringItem, store)` executa UMA coleta real
(provider chamado uma vez, `Offer`/`PriceObservation` resolvidos/persistidos
uma vez -- não N vezes confiando no dedupe da TASK-093 como rede de
segurança) e distribui o resultado para várias `Mission`s vinculadas via
fan-out individual -- sem ainda substituir a fila justa/fairness da
TASK-108 (fase 3B) nem o scheduler principal (`claim_due_collections`,
intocado).

Correção de desenho (fase 3A, rodada 2): a versão anterior chamava
`_persist_phase_a` (resolve Offer + decide/gera PriceObservation) uma vez
POR MISSION do fan-out, confiando que o dedupe TASK-093/DEC-097
("mesmo estado comercial -> reaproveita a última observação") evitaria
duplicação. Isso é um uso indevido do dedupe: DEC-097 deduplica ENTRE
coletas ao longo do TEMPO (a coleta de agora vs a de 5 minutos atrás),
não entre múltiplos BENEFICIÁRIOS da MESMA coleta -- mesmo produzindo o
resultado certo, cada mission repetia consultas e a decisão redundante/
não-redundante desnecessariamente. Agora a resolução comercial
(`_resolve_offer`, decisão redundante-ou-nova, `PriceObservation`/
`OfferInstallmentOption`) roda em `_persist_shared_offers_and_finish`,
exatamente uma vez, produzindo um resultado imutável (`_SharedOfferResult`)
compartilhado por todo o fan-out. O fan-out (`_build_mission_phase_a_outcome`)
faz só o que é genuinamente por Mission: "previous" desta Mission (DEC-048,
via `MissionOfferRelevance.last_observation_id`), relevância (determinística
+ IA), evento de disponibilidade, e monta o mesmo `_PhaseAOutcome` que
`_run_phase_b`/`_persist_phase_c` (TASK-079, reaproveitados sem nenhuma
alteração de comportamento) já sabem processar -- pré-lista,
`MissionOfferRelevance`, alerta e reset de backoff por Mission continuam
exatamente como funcionam hoje no caminho de missão única, sem duplicar
essa lógica aqui.

Correção de durabilidade (fase 3A, rodada 3): fan-out sobrevive a crash.
`_persist_shared_offers_and_finish` grava, na MESMA transação que persiste
Offer/PriceObservation, um registro durável de quais ofertas fizeram
parte da coleta (`SharedCollectionOffer` -- inclusive quando a observação
foi reaproveitada/redundante, que por definição não fica presa a esta
`collection_run_id`) e cria uma `SharedFanOutTask` (`pending`) por Mission
elegível NAQUELE momento, e só então marca a `CollectionRun` compartilhada
`SUCCEEDED`. Ou tudo isso commitou junto (run `SUCCEEDED` + ofertas +
tarefas de fan-out já duráveis) ou nada commitou (run continua `RUNNING`,
`recover_stale_runs` -- já genérico, nunca precisou de mudança -- resolve
depois do timeout, seguro repetir o provider). `resume_shared_collection_
fan_out` retoma só tarefas `pending`, na ordem das runs mais antigas para
as mais novas, reconstruindo o resultado via `SharedCollectionOffer` --
NUNCA chama o provider de novo, NUNCA reprocessa uma Mission já `done`.
`_process_pending_fan_out` é o único código de fan-out (usado tanto pelo
caminho fresco quanto pela retomada -- nunca dois jeitos diferentes).

Correção de consistência (fase 3A, rodada 3): `SharedFanOutTask` ganhou
máquina de estados -- `pending -> processing -> done` (feliz) ou
`pending -> processing -> pending` (falha transitória/corrida, retry com
backoff via `next_retry_at`/`attempt_count`). Claim atômico
(`_claim_fan_out_task`, `UPDATE ... WHERE status='pending' ...`) garante
que duas workers nunca processam a mesma `(collection_run_id,
mission_id)` ao mesmo tempo -- nunca mutex em memória, mesmo padrão de
`_claim_shared_collection`. `processing` travado além do lease
(`_FAN_OUT_PROCESSING_STALE_AFTER`) é recuperável via `recover_stale_
fan_out_tasks` (mesmo espírito de `recover_stale_runs`).

Correção de semântica final (fase 3A, rodada 4): quatro pontos concretos
que a rodada 3 ainda não provava.

1. Classificação de erro nunca assume terminal sem prova: exceções no
   fan-out são RETRYABLE por padrão (`_fail_fan_out_task_retryable`) --
   só `SharedFanOutTerminalError` (levantada só nos dois casos
   genuinamente determinísticos e irrecuperáveis de `_build_mission_
   phase_a_outcome`: Mission/critério sumiu, produto sumiu) vai direto
   para `terminal_failed` via `_fail_fan_out_task_terminal`, sem gastar
   tentativas. `attempt_count` esgotado (`_MAX_FAN_OUT_ATTEMPTS`) NUNCA
   mais vira `terminal_failed` sozinho -- vira `attention_required`:
   auditável (`last_error`/`attempt_count`), reprocessável (nada no
   schema impede resetar para `pending`), só parou de tentar sozinha
   para não fazer retry infinito. Nenhum resultado de coleta é
   descartado silenciosamente por uma dependência (banco/IA) ficar
   indisponível por tempo demais.
2. Revalidação de elegibilidade (`_mission_still_eligible_for_fan_out`):
   chamada logo após o claim atômico da tarefa, ANTES de qualquer efeito
   -- confirma que a Mission ainda existe, está `ACTIVE`, ainda aponta
   para o MESMO `MonitoringItem` (pega reconcile/relink) e ainda tem
   `MissionSource` para esta loja. Se não, a tarefa vira `skipped` (nunca
   um erro, nunca gera alerta/notificação) via `_skip_fan_out_task`; se a
   Mission for retomada depois, uma coleta FUTURA cuida disso, este
   fan-out antigo nunca é revivido.
3. Idempotência real de notificação: o disparo de Telegram usa o outbox
   de eventos já existente (TASK-080, `app.events.consumption` --
   `claim_unconsumed_events_async`/`record_consumption_attempt_async`),
   não um envio direto dentro do processamento. Combinado com a
   propriedade já provada de "nenhum `Event` duplicado sob retry"
   (rodada 3), retry do mesmo `SharedFanOutTask` nunca produz um segundo
   envio lógico -- a chave de idempotência do outbox já é por
   `(event, consumer_name)`, e o evento em si é 1 por
   `(mission, tipo, observação)`.
4. `SharedFanOutTask`/`SharedCollectionOffer` já impedem duplicação
   lógica pela própria PRIMARY KEY (`(collection_run_id, mission_id)` e
   `(collection_run_id, offer_id)`, respectivamente) -- nenhuma mudança
   de schema necessária, só confirmação (com teste de integridade no
   banco).
5. Recuperação automática: `resume_shared_collection_fan_out` chama
   `recover_stale_fan_out_tasks` como PRIMEIRO passo, sempre -- quem
   chama esta função nunca precisa lembrar de recuperar tarefas presas
   separadamente. `recover_stale_fan_out_tasks` continua exposta e
   idempotente para quem quiser chamar à parte também.

Idempotência dos efeitos individuais sob retry (crash no MEIO do
processamento de uma Mission, depois de `_persist_phase_c` já ter
commitado mas antes da tarefa virar `done`): é uma propriedade JÁ
existente do desenho da rodada 2/3, não uma correção nova -- `_persist_
phase_c` roda inteiro numa única transação atômica (ou tudo commita ou
nada), e o retry seguinte encontra `MissionOfferRelevance.last_
observation_id` já apontando para a MESMA observação desta coleta, o que
faz `_build_mission_phase_a_outcome` calcular `alert_comparison=
UNCHANGED_REUSED` -- `_persist_phase_c` pula a reavaliação de alerta
nesse caso (mesmo guard que já protege o caminho de missão única),
`needs_relevance`/`needs_display_name` também recalculam para `False`
(a classificação e o nome já existem). Nenhum efeito duplicado -- provado
com teste dedicado, não só "provavelmente dedupe".

`CollectionRun.status == SUCCEEDED` (execução compartilhada) significa
SÓ "a coleta comercial (provider + Offer/PriceObservation) terminou com
sucesso" -- NUNCA "todas as Missions foram notificadas". O fan-out tem
lifecycle PRÓPRIO e durável (`SharedFanOutTask`); para saber se todo
mundo já foi processado, consulte as tarefas daquela `collection_run_id`
(`pending`/`processing`/`done`/`skipped`/`attention_required`/
`terminal_failed`), nunca infira isso do status da `CollectionRun`.

A execução compartilhada em si (chamada ao provider) é reservada por uma
`CollectionRun` própria com `monitoring_item_id` preenchido e `mission_id
IS NULL` -- claim/lock real via `uq_collection_runs_running_monitoring_
item_store` + `SELECT ... FOR UPDATE` em `MonitoringItemStore` antes do
claim (nunca mutex em memória) -- ver `_claim_shared_collection`. Erro
isolado por Mission no fan-out nunca marca essa execução compartilhada
como falha -- ela já terminou (SUCCEEDED) antes do fan-out começar.
"""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai_provider import AIProviderManager
from app.collection.adapter import CollectionAdapter
from app.collection.cadence import CadenceConfig
from app.collection.contracts import CollectionRequest
from app.collection.models import (
    CollectionRun,
    CollectionRunStatus,
    MissionOfferRelevance,
    OfferInstallmentOption,
    PriceObservation,
    SharedCollectionOffer,
    SharedFanOutStatus,
    SharedFanOutTask,
)
from app.collection.normalization import (
    Availability,
    NormalizedCollectionResult,
    PriceNormalizer,
)
from app.collection.orchestration import (
    _RUNNING_INDEX,
    ClaimedCollection,
    PriceObservationComparison,
    _acquire_creation_locks,
    _apply_rating_snapshot,
    _CommercialState,
    _constraint_name,
    _creation_lock_keys,
    _deterministic_product_relevance,
    _failure_code,
    _installment_snapshot,
    _is_confirmed_external_block,
    _PendingOffer,
    _persist_phase_c,
    _PhaseAOutcome,
    _preview_existing_offer_and_product,
    _raw_evidence,
    _record_failure,
    _resolve_offer,
    _run_phase_b,
    _safe_source,
    _same_commercial_state,
    _select_final_candidates,
)
from app.collection.persistence import finish_collection_run, start_collection_run
from app.collection.shared_claim import (
    _apply_shared_backoff,
    _claim_shared_collection,
    _reset_shared_backoff,
    _SharedClaim,
)
from app.core.config import Settings
from app.database.time import utc_now
from app.events import AggregateType, AvailabilityChangedPayload, EventType
from app.events.service import publish_event_async
from app.missions.models import (
    Mission,
    MissionCriteria,
    MissionMonitoringItem,
    MissionSource,
    MissionStatus,
)
from app.offers.models import Offer
from app.products.models import Product
from app.search.cesar_core_fetch import CesarCoreFetchProvider
from app.stores.models import Store
from app.users.models import UserRole

logger = logging.getLogger("app.collection.shared_collection")

V1_SOURCE_CODES = frozenset(
    {"pichau", "terabyte", "amazon", "kabum", "magalu", "mercadolivre"}
)


class SharedFanOutTerminalError(Exception):
    """Erro determinístico do fan-out compartilhado -- processar de novo
    NUNCA vai funcionar (dados/estado tornam impossível), nunca deve ser
    tratado como falha transitória retryable. `_build_mission_phase_a_
    outcome` levanta isto (em vez de uma exceção genérica) para os casos
    que ela já sabe, de antemão, que são definitivamente irrecuperáveis
    -- qualquer OUTRA exceção (banco temporariamente indisponível,
    timeout, erro inesperado) é tratada como retryable por padrão: nunca
    se assume que um erro é terminal sem prova."""


@dataclass(frozen=True, slots=True)
class SharedCollectionResult:
    """Resultado observável de uma chamada a `collect_monitoring_item_store`
    ou `resume_shared_collection_fan_out`."""

    claimed: bool
    """`False`: nada aconteceu -- não estava due, desabilitado, em backoff,
    ou outro worker já segurava o claim (`uq_collection_runs_running_
    monitoring_item_store`). Nenhum provider foi chamado."""
    provider_called: bool = False
    succeeded: bool = False
    offers_count: int = 0
    fanned_out_mission_ids: tuple[UUID, ...] = ()
    """Missions processadas com sucesso nesta chamada (tarefa -> `done`)."""
    fan_out_skipped_mission_ids: tuple[UUID, ...] = ()
    """Missions que deixaram de ser elegíveis antes do processamento
    (pausada/cancelada/desvinculada/perdeu a loja) -- nunca um erro,
    nunca notificadas (tarefa -> `skipped`)."""
    fan_out_attention_required_mission_ids: tuple[UUID, ...] = ()
    """Falha RETRYABLE que esgotou as tentativas automáticas nesta
    chamada (tarefa -> `attention_required`) -- auditável, não é perda
    definitiva, só parou de tentar sozinha."""
    fan_out_failed_mission_ids: tuple[UUID, ...] = ()
    """Erro determinístico (`SharedFanOutTerminalError`), nunca vai
    funcionar numa próxima tentativa (tarefa -> `terminal_failed`)."""
    attempted_task_count: int = 0
    """TASK-112 fase 3B: soma de `_FanOutBatchOutcome.attempted_task_
    count` de todas as runs processadas nesta chamada -- contrato
    explícito de orçamento para `sweep_shared_collection_fan_out`, nunca
    inferido somando os 4 campos acima (não cobrem retry transitório)."""


# ---------------------------------------------------------------------------
# Agenda/backoff/claim compartilhados: movidos para `app.collection.
# shared_claim` (TASK-112, fase 3B) -- módulo neutro, sem depender de
# `orchestration.py` nem de `shared_collection.py`, condição necessária
# para o scheduler unificado (`claim_due_work`, orchestration.py) poder
# chamar o claim compartilhado diretamente sem criar dependência
# circular. `_SharedClaim`/`_claim_shared_collection`/`_apply_shared_
# backoff`/`_reset_shared_backoff` importados no topo deste arquivo.
# ---------------------------------------------------------------------------


async def _finish_shared_collection(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    monitoring_item_id: UUID,
    store_id: UUID,
    status: CollectionRunStatus,
    finished_at: datetime,
    base_interval_minutes: int,
    confirmed_block: bool,
) -> None:
    async with session_factory() as session, session.begin():
        await finish_collection_run(session, run_id, status, finished_at=finished_at)
        if status is CollectionRunStatus.SUCCEEDED:
            await _reset_shared_backoff(
                session,
                monitoring_item_id=monitoring_item_id,
                store_id=store_id,
                now=finished_at,
            )
        elif confirmed_block:
            await _apply_shared_backoff(
                session,
                monitoring_item_id=monitoring_item_id,
                store_id=store_id,
                base_interval_minutes=base_interval_minutes,
                failed_at=finished_at,
            )


# ---------------------------------------------------------------------------
# Persistência comercial compartilhada -- roda EXATAMENTE UMA VEZ por
# `collect_monitoring_item_store` (item 2, fase 3A), nunca uma vez por
# Mission do fan-out. Resolve `Offer`, decide se a `PriceObservation` é
# redundante (TASK-093/DEC-097 -- aqui aplicado no sentido correto: a
# coleta de agora vs a última já registrada, não entre beneficiários desta
# mesma coleta) e persiste no máximo uma vez.
#
# Correção "fan-out durável" (item 1, rodada 3): a mesma transação que
# persiste a coleta comercial também grava `SharedCollectionOffer`
# (registro durável de quais ofertas fizeram parte desta run -- inclusive
# quando a observação foi reaproveitada/redundante, que por definição NÃO
# fica presa a `run_id` via `PriceObservation.collection_run_id`), cria
# `SharedFanOutTask` (`pending`) para cada Mission elegível NESTE momento,
# e marca a `CollectionRun` compartilhada `SUCCEEDED` -- tudo atômico: ou
# nada disso commitou (run continua `RUNNING`, seguro repetir o provider
# mais tarde) ou tudo commitou junto (run `SUCCEEDED` + ofertas +
# tarefas de fan-out já duráveis, nunca mais depende de o processo atual
# continuar vivo). Um crash a qualquer momento DEPOIS deste commit nunca
# perde informação: `resume_shared_collection_fan_out` retoma só as
# tarefas `pending`, sem chamar o provider de novo.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _SharedOfferResult:
    offer_id: UUID
    product_id: UUID
    observation_id: UUID
    amount: Decimal
    currency: str
    availability: Availability
    observed_at: datetime
    raw_title: str
    observation_created: bool


async def _eligible_mission_ids_session(
    session: AsyncSession, *, monitoring_item_id: UUID, store_id: UUID
) -> tuple[UUID, ...]:
    """Só Missions `ACTIVE`, vinculadas ao item, com a loja explicitamente
    entre suas `MissionSource` -- nunca assume que compartilhar
    monitoring_key torna toda loja/oferta automaticamente relevante."""
    rows = await session.scalars(
        select(Mission.id)
        .join(MissionMonitoringItem, MissionMonitoringItem.mission_id == Mission.id)
        .join(
            MissionSource,
            and_(
                MissionSource.mission_id == Mission.id,
                MissionSource.store_id == store_id,
            ),
        )
        .where(
            MissionMonitoringItem.monitoring_item_id == monitoring_item_id,
            Mission.status == MissionStatus.ACTIVE,
        )
        .order_by(Mission.id)
    )
    return tuple(rows)


async def _persist_shared_offers_and_finish(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    monitoring_item_id: UUID,
    store_id: UUID,
    normalized: NormalizedCollectionResult,
    finished_at: datetime,
) -> tuple[_SharedOfferResult, ...]:
    """Roda EXATAMENTE UMA VEZ por `collect_monitoring_item_store` -- nunca
    uma vez por Mission do fan-out. Uma nova `PriceObservation` (quando
    não redundante) fica presa à `CollectionRun` COMPARTILHADA (`run_id`,
    `monitoring_item_id` preenchido) -- é ela quem de fato coletou, nunca
    a run de nenhuma Mission específica."""
    async with session_factory() as session, session.begin():
        results: list[_SharedOfferResult] = []

        # Subtask 6 (correção estrutural de deadlock -- mesmo raciocínio
        # de `orchestration.py`, ver docstring de
        # `_preview_existing_offer_and_product`): três passos, nesta
        # ordem exata. 1) identificação SOMENTE LEITURA (sem
        # `begin_nested()`, que força flush incondicional mesmo com
        # `autoflush=False`); 2) trava global -- todas as Offers já
        # existentes por `offer.id`, depois todos os Products já
        # existentes por `product_id`, sempre nessa ordem relativa; 3)
        # resolução de verdade (`_resolve_offer`, com mutação/criação),
        # agora segura. Ordem de seleção comercial abaixo continua
        # intocada.
        items_by_key: dict[tuple[str | None, str | None, str], Any] = {}
        preview: dict[
            tuple[str | None, str | None, str], tuple[Offer | None, Product | None]
        ] = {}
        creation_lock_keys: set[str] = set()
        for item in normalized.offers:
            identity_key = (
                item.seller_external_id,
                item.raw_offer.external_id,
                item.raw_offer.external_id or item.raw_offer.url,
            )
            if identity_key in items_by_key:
                continue
            items_by_key[identity_key] = item
            preview[identity_key] = await _preview_existing_offer_and_product(
                session, store_id, item
            )
            creation_lock_keys |= _creation_lock_keys(store_id, item)

        # Fase 0 (correção estrutural de deadlock, segunda rodada -- mesmo
        # raciocínio de `orchestration.py`, ver docstring de
        # `_creation_lock_keys`): trava transacional por chave lógica para
        # todo Seller/Product/Offer que este lote possa vir a criar.
        await _acquire_creation_locks(session, frozenset(creation_lock_keys))

        offer_ids = sorted({o.id for o, _ in preview.values() if o is not None})
        for offer_id in offer_ids:
            await session.scalar(
                select(Offer.id).where(Offer.id == offer_id).with_for_update()
            )
        product_ids = sorted({p.id for _, p in preview.values() if p is not None})
        for product_id in product_ids:
            await session.scalar(
                select(Product.id).where(Product.id == product_id).with_for_update()
            )

        resolved_offers: dict[tuple[str | None, str | None, str], Offer] = {}
        resolved_items: list[tuple[tuple[str | None, str | None, str], Any]] = []
        for identity_key, item in items_by_key.items():
            offer = await _resolve_offer(session, store_id, item)
            resolved_offers[identity_key] = offer
            resolved_items.append((identity_key, item))

        for identity_key, item in resolved_items:
            offer = resolved_offers[identity_key]
            latest = await session.scalar(
                select(PriceObservation)
                .where(PriceObservation.offer_id == offer.id)
                .order_by(
                    PriceObservation.observed_at.desc(), PriceObservation.id.desc()
                )
                .limit(1)
            )
            current_state = _CommercialState.from_normalized_offer(item)
            redundant = False
            if latest is not None and _same_commercial_state(latest, current_state):
                latest_installments = list(
                    await session.scalars(
                        select(OfferInstallmentOption).where(
                            OfferInstallmentOption.price_observation_id == latest.id
                        )
                    )
                )
                redundant = _installment_snapshot(
                    latest_installments
                ) == _installment_snapshot(current_state.installments)
            if redundant:
                # TASK-093: estado comercial idêntico ao já registrado
                # (dedupe ENTRE coletas ao longo do tempo, DEC-097) --
                # não grava PriceObservation redundante.
                observation = latest
            else:
                # Subtask 6 (correção de causa raiz): construído a partir
                # de `current_state` -- a MESMA instância comparada acima
                # -- nunca relendo `item` de novo (ver docstring de
                # `_CommercialState`).
                observation = PriceObservation(
                    offer_id=offer.id,
                    collection_run_id=run_id,
                    amount=current_state.amount,
                    currency=current_state.currency,
                    shipping_amount=current_state.shipping_amount,
                    total_amount=current_state.total_amount,
                    fulfillment=current_state.fulfillment,
                    seller_kind=current_state.seller_kind,
                    fulfillment_kind=current_state.fulfillment_kind,
                    condition=current_state.condition,
                    availability=current_state.availability,
                    observed_at=item.raw_offer.collected_at,
                    raw_evidence=_raw_evidence(item.raw_offer),
                )
                session.add(observation)
                await session.flush()
                for option in current_state.installments:
                    session.add(
                        OfferInstallmentOption(
                            price_observation_id=observation.id,
                            installment_count=option.installment_count,
                            installment_amount=option.installment_amount,
                            installment_total_amount=option.installment_total_amount,
                            discount_percent=option.discount_percent,
                            interest_kind=option.interest_kind,
                            is_highlighted=option.is_highlighted,
                        )
                    )
                if current_state.installments:
                    await session.flush()
            offer.last_seen_at = item.raw_offer.collected_at
            _apply_rating_snapshot(offer, item)
            # Registro durável (item 1) -- inclusive quando `observation`
            # foi reaproveitada (redundante): SEM isto, um crash antes do
            # fan-out terminar perderia para sempre a informação de que
            # esta oferta fez parte desta coleta.
            session.add(
                SharedCollectionOffer(
                    collection_run_id=run_id,
                    offer_id=offer.id,
                    observation_id=observation.id,
                )
            )
            results.append(
                _SharedOfferResult(
                    offer_id=offer.id,
                    product_id=offer.product_id,
                    observation_id=observation.id,
                    amount=observation.amount,
                    currency=observation.currency,
                    availability=observation.availability,
                    observed_at=observation.observed_at,
                    raw_title=item.raw_offer.title,
                    observation_created=not redundant,
                )
            )

        # Tarefas de fan-out duráveis (item 1) -- criadas na MESMA
        # transação que já persistiu a coleta comercial, elegibilidade
        # calculada agora (mesmo instante do commit). `ON CONFLICT DO
        # NOTHING`: idempotente, nunca reseta uma tarefa que porventura já
        # exista (não deveria, `run_id` é sempre novo aqui, mas não custa).
        mission_ids = await _eligible_mission_ids_session(
            session, monitoring_item_id=monitoring_item_id, store_id=store_id
        )
        for mission_id in mission_ids:
            await session.execute(
                postgresql_insert(SharedFanOutTask)
                .values(
                    collection_run_id=run_id,
                    mission_id=mission_id,
                    status=SharedFanOutStatus.PENDING,
                    created_at=finished_at,
                    updated_at=finished_at,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        SharedFanOutTask.collection_run_id,
                        SharedFanOutTask.mission_id,
                    ]
                )
            )

        # A run compartilhada só vira SUCCEEDED junto com tudo acima, na
        # MESMA transação -- nunca existe um estado "SUCCEEDED mas sem
        # tarefas de fan-out ainda" (item 1: elimina a janela perigosa).
        await finish_collection_run(
            session, run_id, CollectionRunStatus.SUCCEEDED, finished_at=finished_at
        )
        await _reset_shared_backoff(
            session,
            monitoring_item_id=monitoring_item_id,
            store_id=store_id,
            now=finished_at,
        )
        return tuple(results)


async def _reconstruct_shared_results(
    session_factory: async_sessionmaker[AsyncSession], *, run_id: UUID
) -> tuple[_SharedOfferResult, ...]:
    """Reconstrói o resultado de uma coleta compartilhada JÁ PERSISTIDA a
    partir só do banco (`SharedCollectionOffer`) -- nunca chama o
    provider de novo. `raw_title` vem de `PriceObservation.raw_evidence`
    (já persistido por `_raw_evidence`, mesmo quando a observação foi
    reaproveitada de um ciclo anterior -- título de uma mesma oferta é
    estável o bastante para classificação de relevância)."""
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(SharedCollectionOffer, Offer, PriceObservation)
                .join(Offer, Offer.id == SharedCollectionOffer.offer_id)
                .join(
                    PriceObservation,
                    PriceObservation.id == SharedCollectionOffer.observation_id,
                )
                .where(SharedCollectionOffer.collection_run_id == run_id)
            )
        ).all()
        results = []
        for _shared_offer, offer, observation in rows:
            raw_evidence = observation.raw_evidence or {}
            # `_raw_evidence` sempre grava "title" (nunca opcional) --
            # string vazia só é alcançável se `raw_evidence` em si for
            # `None` (observação de outra origem, nunca a deste módulo).
            raw_title = raw_evidence.get("title") or ""
            results.append(
                _SharedOfferResult(
                    offer_id=offer.id,
                    product_id=offer.product_id,
                    observation_id=observation.id,
                    amount=observation.amount,
                    currency=observation.currency,
                    availability=observation.availability,
                    observed_at=observation.observed_at,
                    raw_title=raw_title,
                    observation_created=False,
                )
            )
        return tuple(results)


# ---------------------------------------------------------------------------
# Fan-out por Mission (item 4/5/6/9, fase 3A) -- só o que é genuinamente
# por Mission: "previous" desta Mission (DEC-048, via `MissionOfferRelevance.
# last_observation_id` -- nunca mais `CollectionRun.mission_id`, que não
# existe para uma observação compartilhada), evento de disponibilidade,
# relevância determinística/IA. Monta o MESMO `_PhaseAOutcome` que
# `_run_phase_b`/`_persist_phase_c` (TASK-079, reaproveitados sem nenhuma
# alteração) já processam -- pré-lista, `MissionOfferRelevance`, alerta e
# reset de backoff por Mission continuam funcionando exatamente como no
# caminho de missão única, sem duplicar essa lógica aqui.
# ---------------------------------------------------------------------------


async def _build_mission_phase_a_outcome(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    mission_id: UUID,
    store_id: UUID,
    shared_results: tuple[_SharedOfferResult, ...],
    completed_at: datetime,
) -> _PhaseAOutcome | None:
    async with session_factory() as session, session.begin():
        run = await session.scalar(
            select(CollectionRun).where(CollectionRun.id == run_id).with_for_update()
        )
        if run is None or run.status is not CollectionRunStatus.RUNNING:
            # Corrida com `recover_stale_runs`/outro worker sobre esta
            # mesma run -- nunca invalida a coleta compartilhada nem as
            # demais Missions do fan-out (item 9).
            return None
        mission = await session.get(Mission, mission_id)
        criteria = await session.scalar(
            select(MissionCriteria).where(MissionCriteria.mission_id == mission_id)
        )
        if mission is None or criteria is None:
            # Determinístico -- nunca vai funcionar numa próxima
            # tentativa (dados estruturalmente ausentes). A eligibilidade
            # de NEGÓCIO (pausada/cancelada/relink) já foi revalidada
            # ANTES desta chamada (`_mission_still_eligible_for_fan_out`)
            # -- chegar aqui com `mission`/`criteria` ausentes é sempre
            # uma inconsistência de dados, nunca um estado de negócio
            # válido, por isso é terminal e não retryable.
            raise SharedFanOutTerminalError("claimed mission data no longer exists")

        pending: list[_PendingOffer] = []
        for shared in shared_results:
            existing_relevance = await session.get(
                MissionOfferRelevance, (mission.id, shared.offer_id)
            )
            previous = None
            if (
                existing_relevance is not None
                and existing_relevance.last_observation_id is not None
            ):
                previous = await session.get(
                    PriceObservation, existing_relevance.last_observation_id
                )

            if previous is None:
                alert_comparison = PriceObservationComparison.FIRST_OBSERVATION
            elif previous.id == shared.observation_id:
                alert_comparison = PriceObservationComparison.UNCHANGED_REUSED
            else:
                alert_comparison = PriceObservationComparison.CHANGED

            if previous is not None and previous.availability != shared.availability:
                await publish_event_async(
                    session,
                    event_type=EventType.AVAILABILITY_CHANGED_V1,
                    aggregate_type=AggregateType.OFFER,
                    aggregate_id=shared.offer_id,
                    payload=AvailabilityChangedPayload(
                        shared.offer_id,
                        shared.observation_id,
                        previous.id,
                        previous.availability,
                        shared.availability,
                    ),
                    occurred_at=completed_at,
                    mission_id=mission.id,
                )

            product = await session.get(Product, shared.product_id)
            if product is None:
                # Determinístico -- Offer.product_id apontando para um
                # Product inexistente é uma inconsistência de dados,
                # nunca resolvida tentando de novo.
                raise SharedFanOutTerminalError("offer references a missing product")
            forced_relevance = _deterministic_product_relevance(criteria, product)

            pending.append(
                _PendingOffer(
                    offer_id=shared.offer_id,
                    product_id=shared.product_id,
                    observation_id=shared.observation_id,
                    amount=shared.amount,
                    currency=shared.currency,
                    availability=shared.availability,
                    observed_at=shared.observed_at,
                    raw_title=shared.raw_title,
                    needs_relevance=(
                        existing_relevance is None and forced_relevance is None
                    ),
                    needs_display_name=product.display_name is None,
                    observation_created=shared.observation_created,
                    alert_comparison=alert_comparison,
                    forced_relevance=forced_relevance,
                    previous_observation_id=previous.id
                    if previous is not None
                    else None,
                    previous_amount=previous.amount if previous is not None else None,
                    previous_currency=previous.currency
                    if previous is not None
                    else None,
                    previous_availability=(
                        previous.availability if previous is not None else None
                    ),
                    previous_observed_at=(
                        previous.observed_at if previous is not None else None
                    ),
                )
            )

        return _PhaseAOutcome(
            run_id=run.id,
            mission_id=mission.id,
            store_id=run.store_id,
            mission_search_query=criteria.search_query,
            target_amount=criteria.target_amount,
            target_currency=criteria.target_currency,
            completed_at=completed_at,
            offers=tuple(pending),
        )


async def _mission_still_eligible_for_fan_out(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    mission_id: UUID,
    monitoring_item_id: UUID,
    store_id: UUID,
) -> bool:
    """Revalidação determinística (item 2, correção de consistência) --
    chamada SEMPRE antes de processar uma `SharedFanOutTask`, tanto no
    caminho fresco quanto na retomada. Entre a coleta comercial acontecer
    e o fan-out desta Mission de fato rodar (retry/crash de por meio),
    o mundo pode ter mudado: a Mission pode ter sido pausada/cancelada,
    reidentificada para OUTRO `MonitoringItem` (reconcile), ou perdido a
    loja. Nenhum desses casos é um erro -- é simplesmente "essa Mission
    não quer mais este resultado", e por isso NUNCA deve gerar alerta/
    pré-lista/notificação; a tarefa vira `skipped`, nunca `done` nem
    `terminal_failed`. Se a Mission for retomada depois, uma coleta
    FUTURA cuida disso -- nunca revive um fan-out antigo."""
    async with session_factory() as session:
        mission = await session.get(Mission, mission_id)
        if mission is None or mission.status is not MissionStatus.ACTIVE:
            return False
        link = await session.get(MissionMonitoringItem, mission_id)
        if link is None or link.monitoring_item_id != monitoring_item_id:
            return False
        source = await session.get(MissionSource, (mission_id, store_id))
        if source is None:
            return False
        return True


async def _skip_fan_out_task(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    mission_id: UUID,
    now: datetime,
) -> None:
    async with session_factory() as session, session.begin():
        task = await session.get(SharedFanOutTask, (run_id, mission_id))
        if task is not None:
            task.status = SharedFanOutStatus.SKIPPED
            task.updated_at = now


async def _start_mission_fan_out_run(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    mission_id: UUID,
    store_id: UUID,
    started_at: datetime,
) -> UUID | None:
    """Reaproveita o MESMO claim por missão de sempre (`uq_collection_runs_
    running_mission_store`) -- se essa missão já tiver uma execução própria
    rodando por outro caminho (ex.: scheduler antigo, ainda intocado nesta
    fase), esta chamada simplesmente perde o claim e o fan-out pula essa
    missão neste ciclo, sem erro."""
    async with session_factory() as session, session.begin():
        try:
            async with session.begin_nested():
                run = await start_collection_run(
                    session, store_id, mission_id, started_at=started_at
                )
        except IntegrityError as error:
            if _constraint_name(error) != _RUNNING_INDEX:
                raise
            return None
        return run.id


async def _record_fan_out_failure(
    session_factory: async_sessionmaker[AsyncSession],
    claim: ClaimedCollection,
    failure_code: str,
    failed_at: datetime,
) -> None:
    try:
        async with session_factory() as session, session.begin():
            await _record_failure(session, claim, failure_code, failed_at)
    except Exception:
        logger.exception(
            "shared_collection_fan_out_failure_recording_failed",
            extra={"source_code": _safe_source(claim.source_code)},
        )


_MAX_FAN_OUT_ATTEMPTS = 5
"""Falha transitória (banco/IA/processamento) nunca vira perda
silenciosa permanente -- tenta de novo até este limite, com atraso
crescente (`_fan_out_retry_delay_minutes`). Só depois de esgotar vira
`terminal_failed`, sempre auditável via `SharedFanOutTask.last_error`."""
_FAN_OUT_RETRY_CAP_MINUTES = 60
_FAN_OUT_PROCESSING_STALE_AFTER = timedelta(minutes=10)
"""Mesmo teto de `recover_stale_runs` (TASK-079) -- uma tarefa presa em
`processing` além disso é considerada abandonada (lease expirado)."""


def _fan_out_retry_delay_minutes(attempt_count: int) -> int:
    return min(2**attempt_count, _FAN_OUT_RETRY_CAP_MINUTES)


async def _claim_fan_out_task(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    mission_id: UUID,
    now: datetime,
) -> bool:
    """Claim atômico -- `UPDATE ... WHERE status='pending' AND
    (elegível para retry)`, nunca mutex em memória. Sob concorrência
    real, o Postgres serializa duas tentativas na MESMA linha (a segunda
    bloqueia até a primeira commitar, depois reavalia o `WHERE` contra o
    estado já commitado -- `READ COMMITTED`) -- no máximo uma das duas
    afeta a linha; a outra recebe `rowcount == 0` e desiste sem erro."""
    async with session_factory() as session, session.begin():
        result = await session.execute(
            update(SharedFanOutTask)
            .where(
                SharedFanOutTask.collection_run_id == run_id,
                SharedFanOutTask.mission_id == mission_id,
                SharedFanOutTask.status == SharedFanOutStatus.PENDING,
                or_(
                    SharedFanOutTask.next_retry_at.is_(None),
                    SharedFanOutTask.next_retry_at <= now,
                ),
            )
            .values(
                status=SharedFanOutStatus.PROCESSING, claimed_at=now, updated_at=now
            )
        )
        return result.rowcount == 1


async def _complete_fan_out_task(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    mission_id: UUID,
    now: datetime,
) -> None:
    async with session_factory() as session, session.begin():
        task = await session.get(SharedFanOutTask, (run_id, mission_id))
        if task is not None:
            task.status = SharedFanOutStatus.DONE
            task.updated_at = now


async def _release_fan_out_task_for_retry(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    mission_id: UUID,
    now: datetime,
) -> None:
    """Volta para `pending` SEM incrementar `attempt_count` -- usado
    quando nada de fato foi tentado ainda (corrida de claim/run stale da
    PRÓPRIA Mission, nunca um erro real). Nunca penaliza uma Mission por
    uma corrida que não é culpa dela."""
    async with session_factory() as session, session.begin():
        task = await session.get(SharedFanOutTask, (run_id, mission_id))
        if task is not None and task.status == SharedFanOutStatus.PROCESSING:
            task.status = SharedFanOutStatus.PENDING
            task.updated_at = now


async def _fail_fan_out_task_retryable(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    mission_id: UUID,
    now: datetime,
    error_message: str,
) -> bool:
    """Erro RETRYABLE (qualquer exceção que NÃO seja `SharedFanOutTerminalError`
    -- banco temporariamente indisponível, timeout, IA/provider auxiliar
    indisponível, erro inesperado: por padrão, nunca se assume terminal
    sem prova) -- incrementa `attempt_count`, registra `last_error`.
    Abaixo do limite: volta para `pending` com `next_retry_at` no futuro
    (backoff exponencial, mesmo espírito de `next_source_backoff`/
    DEC-046) -- continua elegível para retomada automática. No limite:
    `attention_required` -- para de tentar sozinha (evita loop infinito),
    mas continua auditável e reprocessável manualmente; NUNCA a mesma
    coisa que "definitivamente impossível" (`terminal_failed`), NUNCA
    perda silenciosa. Devolve `True` quando virou `attention_required`."""
    async with session_factory() as session, session.begin():
        task = await session.get(SharedFanOutTask, (run_id, mission_id))
        if task is None:
            return False
        task.attempt_count += 1
        task.last_error = error_message[:2000]
        task.updated_at = now
        if task.attempt_count >= _MAX_FAN_OUT_ATTEMPTS:
            task.status = SharedFanOutStatus.ATTENTION_REQUIRED
            task.next_retry_at = None
            return True
        task.status = SharedFanOutStatus.PENDING
        task.next_retry_at = now + timedelta(
            minutes=_fan_out_retry_delay_minutes(task.attempt_count)
        )
        return False


async def _fail_fan_out_task_terminal(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    mission_id: UUID,
    now: datetime,
    error_message: str,
) -> None:
    """Erro determinístico (`SharedFanOutTerminalError`) -- vai direto
    para `terminal_failed`, sem gastar tentativas de retry (retry nunca
    resolveria isso), sempre auditável via `last_error`."""
    async with session_factory() as session, session.begin():
        task = await session.get(SharedFanOutTask, (run_id, mission_id))
        if task is None:
            return
        task.attempt_count += 1
        task.last_error = error_message[:2000]
        task.status = SharedFanOutStatus.TERMINAL_FAILED
        task.next_retry_at = None
        task.updated_at = now


async def recover_stale_fan_out_tasks(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    stale_after: timedelta = _FAN_OUT_PROCESSING_STALE_AFTER,
    limit: int = 100,
) -> int:
    """Mesmo espírito de `app.collection.orchestration.recover_stale_runs`
    (TASK-079) -- uma tarefa presa em `processing` além do lease
    (`claimed_at` velho demais) é abandonada (worker morreu no meio) e
    volta a ser `pending`, imediatamente elegível de novo (`next_retry_at`
    limpo -- não é uma falha real, não conta como tentativa). `SKIP
    LOCKED`: seguro chamar de vários workers ao mesmo tempo, sem mutex em
    memória."""
    effective_now = now or utc_now()
    cutoff = effective_now - stale_after
    tasks = list(
        await session.scalars(
            select(SharedFanOutTask)
            .where(
                SharedFanOutTask.status == SharedFanOutStatus.PROCESSING,
                SharedFanOutTask.claimed_at <= cutoff,
            )
            .order_by(SharedFanOutTask.claimed_at, SharedFanOutTask.mission_id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    for task in tasks:
        task.status = SharedFanOutStatus.PENDING
        task.next_retry_at = None
        task.updated_at = effective_now
    return len(tasks)


@dataclass(frozen=True, slots=True)
class _FanOutBatchOutcome:
    done: tuple[UUID, ...]
    skipped: tuple[UUID, ...]
    attention_required: tuple[UUID, ...]
    terminally_failed: tuple[UUID, ...]
    attempted_task_count: int = 0
    """TASK-112 fase 3B: quantas `SharedFanOutTask` foram efetivamente
    REIVINDICADAS (`_claim_fan_out_task` retornou sucesso) nesta chamada
    -- independente do resultado final (`done`/`skipped`/retry
    transitório de volta a `pending`/`attention_required`/`terminal_
    failed`/qualquer status futuro). É este campo, nunca a soma dos 4
    buckets acima, que o sweep (`sweep_shared_collection_fan_out`) usa
    para decrementar orçamento -- os buckets não cobrem retry
    transitório (task volta para `pending` sem aparecer em nenhum deles),
    então somá-los subestimaria o trabalho real feito."""


async def _process_pending_fan_out(
    session_factory: async_sessionmaker[AsyncSession],
    adapter_store_code: str,
    ai_manager: AIProviderManager,
    ai_profile: UserRole,
    *,
    run_id: UUID,
    monitoring_item_id: UUID,
    store_id: UUID,
    shared_results: tuple[_SharedOfferResult, ...],
    finished_at: datetime,
    limit: int | None = None,
    firecrawl: CesarCoreFetchProvider | None = None,
    settings: Settings | None = None,
) -> _FanOutBatchOutcome:
    """Processa `SharedFanOutTask` elegíveis (`pending`, devidas) de UMA
    `collection_run_id` -- usado tanto pelo caminho fresco (`collect_
    monitoring_item_store`) quanto pela retomada (`resume_shared_
    collection_fan_out`), nunca dois jeitos diferentes de fazer fan-out.
    `limit` (opcional) processa só as N primeiras -- útil para lotes
    controlados; nunca usado para pular Missions de propósito (as que
    ficarem de fora continuam elegíveis, retomáveis depois).

    Cada tarefa é reivindicada atomicamente (`_claim_fan_out_task`) antes
    de processar -- nunca duas workers processam a mesma Mission ao
    mesmo tempo. ANTES de qualquer efeito, revalida se a Mission ainda
    quer este resultado (item 2, correção de consistência) -- se não,
    marca `skipped`, nunca gera alerta/notificação. Uma falha transitória
    (ainda com tentativas restantes) não aparece em nenhuma lista aqui,
    continua elegível para uma retomada futura."""
    async with session_factory() as session:
        # TASK-112 fase 3B (achado real, rodada 6): ordenar por
        # `mission_id` (como era antes) é uma ordem arbitrária (UUID),
        # sem relação nenhuma com antiguidade de retry -- uma enxurrada
        # de tarefas novas (`next_retry_at IS NULL`) podia furar a frente
        # de um retry antigo já vencido só por ter um UUID menor.
        # `COALESCE(next_retry_at, created_at)`: tarefa nunca tentada
        # ordena pela hora de criação (nunca fura a frente de um retry
        # antigo), `mission_id` só como desempate final determinístico.
        query = (
            select(SharedFanOutTask.mission_id)
            .where(
                SharedFanOutTask.collection_run_id == run_id,
                SharedFanOutTask.status == SharedFanOutStatus.PENDING,
                or_(
                    SharedFanOutTask.next_retry_at.is_(None),
                    SharedFanOutTask.next_retry_at <= finished_at,
                ),
            )
            .order_by(
                func.coalesce(
                    SharedFanOutTask.next_retry_at, SharedFanOutTask.created_at
                ),
                SharedFanOutTask.mission_id,
            )
        )
        if limit is not None:
            query = query.limit(limit)
        candidate_mission_ids = tuple(await session.scalars(query))

    attempted_task_count = 0
    done: list[UUID] = []
    skipped: list[UUID] = []
    attention_required: list[UUID] = []
    terminally_failed: list[UUID] = []
    for mission_id in candidate_mission_ids:
        claimed = await _claim_fan_out_task(
            session_factory, run_id=run_id, mission_id=mission_id, now=finished_at
        )
        if not claimed:
            # Outra worker/retomada já levou esta tarefa, ou deixou de
            # ser elegível entre a listagem e o claim -- nunca processa
            # duas vezes, nunca erro. Não conta para o orçamento (TASK-112
            # fase 3B): nenhum processamento real aconteceu, só um UPDATE
            # barato que não afetou linha nenhuma.
            continue
        attempted_task_count += 1

        # Item 2 (correção de consistência): revalida ANTES de qualquer
        # efeito -- Mission pode ter sido pausada/cancelada/relinkada
        # entre a coleta comercial e este exato momento (crash/retry
        # separam os dois no tempo). Nunca um erro, nunca notifica.
        still_eligible = await _mission_still_eligible_for_fan_out(
            session_factory,
            mission_id=mission_id,
            monitoring_item_id=monitoring_item_id,
            store_id=store_id,
        )
        if not still_eligible:
            await _skip_fan_out_task(
                session_factory, run_id=run_id, mission_id=mission_id, now=finished_at
            )
            skipped.append(mission_id)
            continue

        mission_run_id = await _start_mission_fan_out_run(
            session_factory,
            mission_id=mission_id,
            store_id=store_id,
            started_at=finished_at,
        )
        if mission_run_id is None:
            # Já existe uma execução própria rodando para esta missão
            # (outro caminho) -- nada foi de fato tentado; libera sem
            # penalizar (não é uma falha real desta Mission).
            await _release_fan_out_task_for_retry(
                session_factory, run_id=run_id, mission_id=mission_id, now=finished_at
            )
            continue
        # `search_query`/`model` nunca lidos abaixo -- `mission_claim` só
        # serve para `_record_fan_out_failure` (via `claim.run_id`), que
        # também não os lê; a identidade real da Mission vem de
        # `_build_mission_phase_a_outcome` (busca `MissionCriteria` fresca).
        mission_claim = ClaimedCollection(
            mission_run_id,
            mission_id,
            store_id,
            adapter_store_code,
            "",
            finished_at,
            None,
        )
        try:
            phase_a = await _build_mission_phase_a_outcome(
                session_factory,
                run_id=mission_run_id,
                mission_id=mission_id,
                store_id=store_id,
                shared_results=shared_results,
                completed_at=finished_at,
            )
            if phase_a is None:
                # Corrida com `recover_stale_runs`/outro worker sobre esta
                # mesma run -- nada foi de fato tentado; libera sem penalizar.
                await _release_fan_out_task_for_retry(
                    session_factory,
                    run_id=run_id,
                    mission_id=mission_id,
                    now=finished_at,
                )
                continue
            ai_outcomes = await _run_phase_b(
                phase_a,
                ai_manager,
                ai_profile,
                session_factory=session_factory,
                firecrawl=firecrawl,
                settings=settings,
            )
            ok = await _persist_phase_c(
                session_factory, phase_a, ai_outcomes, settings=settings
            )
            if ok:
                await _complete_fan_out_task(
                    session_factory,
                    run_id=run_id,
                    mission_id=mission_id,
                    now=finished_at,
                )
                done.append(mission_id)
            else:
                # Run da própria Mission ficou stale entre o claim e
                # _persist_phase_c -- nada foi de fato processado ainda;
                # libera sem penalizar.
                await _release_fan_out_task_for_retry(
                    session_factory,
                    run_id=run_id,
                    mission_id=mission_id,
                    now=finished_at,
                )
        except SharedFanOutTerminalError as error:
            # Determinístico -- nunca vai funcionar numa próxima
            # tentativa; vai direto para terminal_failed, sem gastar
            # tentativas de retry (item 1, correção de consistência).
            logger.warning(
                "shared_collection_fan_out_mission_terminal_error",
                extra={
                    "mission_id": str(mission_id),
                    "source_code": _safe_source(adapter_store_code),
                },
                exc_info=True,
            )
            await _record_fan_out_failure(
                session_factory,
                mission_claim,
                "shared_fan_out_terminal_error",
                finished_at,
            )
            await _fail_fan_out_task_terminal(
                session_factory,
                run_id=run_id,
                mission_id=mission_id,
                now=finished_at,
                error_message=repr(error),
            )
            terminally_failed.append(mission_id)
        except Exception as error:
            # Erro isolado por Mission: a coleta compartilhada já terminou
            # SUCCEEDED antes deste loop começar -- uma Mission irrelevante
            # ou com erro no fan-out nunca a invalida nem impede as demais
            # Missions elegíveis. RETRYABLE por padrão (nunca se assume
            # terminal sem prova -- item 1): retry com backoff até o
            # limite; só então `attention_required` (auditável via
            # last_error, nunca perda silenciosa, nunca "descartado").
            logger.warning(
                "shared_collection_fan_out_mission_failed",
                extra={
                    "mission_id": str(mission_id),
                    "source_code": _safe_source(adapter_store_code),
                },
                exc_info=True,
            )
            await _record_fan_out_failure(
                session_factory, mission_claim, "shared_fan_out_failed", finished_at
            )
            became_attention_required = await _fail_fan_out_task_retryable(
                session_factory,
                run_id=run_id,
                mission_id=mission_id,
                now=finished_at,
                error_message=repr(error),
            )
            if became_attention_required:
                attention_required.append(mission_id)

    return _FanOutBatchOutcome(
        done=tuple(done),
        skipped=tuple(skipped),
        attention_required=tuple(attention_required),
        terminally_failed=tuple(terminally_failed),
        attempted_task_count=attempted_task_count,
    )


async def resume_shared_collection_fan_out(
    session_factory: async_sessionmaker[AsyncSession],
    ai_manager: AIProviderManager,
    *,
    monitoring_item_id: UUID,
    store_id: UUID,
    now: datetime | None = None,
    ai_profile: UserRole = UserRole.ADMIN,
    task_limit: int | None = None,
    recover_stale: bool = True,
    firecrawl: CesarCoreFetchProvider | None = None,
    settings: Settings | None = None,
) -> SharedCollectionResult:
    """Retoma fan-out pendente de coletas compartilhadas já `SUCCEEDED`
    para `(monitoring_item_id, store_id)` -- NUNCA chama o provider de
    novo nem repete a persistência comercial; reconstrói o resultado já
    persistido (`SharedCollectionOffer`) e processa só as tarefas ainda
    elegíveis (`pending`, devidas -- `SharedFanOutTask`), das runs mais
    antigas para as mais novas (preserva a ordem correta de "previous"
    por Mission -- processar fora de ordem corromperia
    `MissionOfferRelevance.last_observation_id`).

    `recover_stale=True` (default, preserva o comportamento standalone já
    documentado desde a fase 3A -- "quem chama nunca precisa lembrar de
    recuperar tarefas presas separadamente"): recupera automaticamente
    tarefas presas em `processing` além do lease (`recover_stale_fan_out_
    tasks`) como PRIMEIRO passo. TASK-112 fase 3B: `sweep_shared_
    collection_fan_out` já chama a recuperação UMA vez, globalmente,
    antes de descobrir os alvos -- passa `recover_stale=False` para não
    repetir a recuperação uma vez por alvo (achado real, rodada 6:
    chamar esta função N vezes com o default multiplicaria a recuperação
    por N+1).

    `task_limit` (opcional, `None` = sem teto, comportamento de sempre):
    orçamento de `SharedFanOutTask` a reivindicar/processar nesta
    chamada, decrementado por `_FanOutBatchOutcome.attempted_task_count`
    real (nunca pela soma dos buckets de resultado) conforme avança pelas
    runs -- para de processar mais runs assim que o orçamento esgota;
    runs/tarefas não alcançadas continuam `pending`, retomáveis na
    próxima chamada."""
    effective_now = now or utc_now()
    if recover_stale:
        async with session_factory() as session, session.begin():
            await recover_stale_fan_out_tasks(session, now=effective_now)
    async with session_factory() as session:
        runs = (
            await session.execute(
                select(CollectionRun.id, Store.code)
                .join(Store, Store.id == CollectionRun.store_id)
                .where(
                    CollectionRun.monitoring_item_id == monitoring_item_id,
                    CollectionRun.store_id == store_id,
                    CollectionRun.status == CollectionRunStatus.SUCCEEDED,
                    exists().where(
                        SharedFanOutTask.collection_run_id == CollectionRun.id,
                        SharedFanOutTask.status == SharedFanOutStatus.PENDING,
                        or_(
                            SharedFanOutTask.next_retry_at.is_(None),
                            SharedFanOutTask.next_retry_at <= effective_now,
                        ),
                    ),
                )
                .order_by(CollectionRun.finished_at, CollectionRun.id)
            )
        ).all()

    fanned_out: list[UUID] = []
    skipped: list[UUID] = []
    attention_required: list[UUID] = []
    failed: list[UUID] = []
    attempted_task_count = 0
    remaining = task_limit
    for run_id, store_code in runs:
        if remaining is not None and remaining <= 0:
            break
        shared_results = await _reconstruct_shared_results(
            session_factory, run_id=run_id
        )
        outcome = await _process_pending_fan_out(
            session_factory,
            store_code,
            ai_manager,
            ai_profile,
            run_id=run_id,
            monitoring_item_id=monitoring_item_id,
            store_id=store_id,
            shared_results=shared_results,
            finished_at=effective_now,
            limit=remaining,
            firecrawl=firecrawl,
            settings=settings,
        )
        fanned_out.extend(outcome.done)
        skipped.extend(outcome.skipped)
        attention_required.extend(outcome.attention_required)
        failed.extend(outcome.terminally_failed)
        attempted_task_count += outcome.attempted_task_count
        if remaining is not None:
            remaining -= outcome.attempted_task_count

    return SharedCollectionResult(
        claimed=False,
        provider_called=False,
        succeeded=True,
        offers_count=0,
        fanned_out_mission_ids=tuple(fanned_out),
        fan_out_skipped_mission_ids=tuple(skipped),
        fan_out_attention_required_mission_ids=tuple(attention_required),
        fan_out_failed_mission_ids=tuple(failed),
        attempted_task_count=attempted_task_count,
    )


async def _due_shared_fan_out_targets(
    session: AsyncSession, *, now: datetime, limit: int
) -> tuple[tuple[UUID, UUID, int], ...]:
    """TASK-112 fase 3B: todos os `(monitoring_item_id, store_id)`
    distintos do sistema com pelo menos uma `SharedFanOutTask` pending
    devida -- não só os claimados neste ciclo (o sweep roda todo ciclo,
    independente de claim novo). Devolve também a contagem de pendentes
    por alvo (`pending_count`), usada por `_allocate_fan_out_budget` para
    dar fairness entre targets, não só orçamento total. Ordenado por
    `MIN(COALESCE(next_retry_at, created_at))` -- mesma correção de
    `_process_pending_fan_out`, agora no nível de TARGET: alvo mais
    atrasado primeiro, nunca starvado por uma enxurrada de alvos novos.
    `limit` aqui é só quantos PARES distintos considerar (`target_scan_
    limit`) -- o orçamento real de TAREFAS é aplicado depois, por
    `_allocate_fan_out_budget`."""
    rows = (
        await session.execute(
            select(
                CollectionRun.monitoring_item_id,
                CollectionRun.store_id,
                func.count(SharedFanOutTask.mission_id).label("pending_count"),
                func.min(
                    func.coalesce(
                        SharedFanOutTask.next_retry_at, SharedFanOutTask.created_at
                    )
                ).label("earliest_due_at"),
            )
            .select_from(SharedFanOutTask)
            .join(CollectionRun, CollectionRun.id == SharedFanOutTask.collection_run_id)
            .where(
                CollectionRun.monitoring_item_id.is_not(None),
                SharedFanOutTask.status == SharedFanOutStatus.PENDING,
                or_(
                    SharedFanOutTask.next_retry_at.is_(None),
                    SharedFanOutTask.next_retry_at <= now,
                ),
            )
            .group_by(CollectionRun.monitoring_item_id, CollectionRun.store_id)
            .order_by("earliest_due_at")
            .limit(limit)
        )
    ).all()
    return tuple((item_id, store_id, count) for item_id, store_id, count, _ in rows)


def _allocate_fan_out_budget(
    targets: Sequence[tuple[UUID, UUID, int]],
    *,
    task_budget: int,
    per_target_task_cap: int,
) -> tuple[tuple[UUID, UUID, int], ...]:
    """TASK-112 fase 3B: fairness ENTRE targets, não só orçamento total --
    um alvo com backlog gigante nunca pode monopolizar o orçamento
    inteiro enquanto outros alvos, menores, também estão devidos (achado
    real, rodada 6: orçamento total sozinho não impede isso). Alocação em
    RODADAS, capada por `per_target_task_cap` por rodada, na mesma ordem
    de urgência de `targets` (já ordenado por `_due_shared_fan_out_
    targets`) -- um alvo pequeno recebe seu total já na primeira rodada,
    nunca espera um alvo maior esvaziar; rodadas seguintes distribuem o
    que sobrar do orçamento entre quem ainda tem trabalho pendente, até
    esgotar `task_budget` ou os pendentes de todos os alvos."""
    remaining_by_target = {
        (item_id, store_id): count for item_id, store_id, count in targets
    }
    allocation: dict[tuple[UUID, UUID], int] = dict.fromkeys(remaining_by_target, 0)
    budget = task_budget
    progressed = True
    while budget > 0 and progressed:
        progressed = False
        for key in remaining_by_target:
            if budget <= 0:
                break
            room = remaining_by_target[key] - allocation[key]
            if room <= 0:
                continue
            take = min(per_target_task_cap, room, budget)
            if take <= 0:
                continue
            allocation[key] += take
            budget -= take
            progressed = True
    return tuple(
        (item_id, store_id, count)
        for (item_id, store_id), count in allocation.items()
        if count > 0
    )


@dataclass(frozen=True, slots=True)
class FanOutSweepSummary:
    targets_processed: int = 0
    attempted_task_count: int = 0
    fanned_out_mission_count: int = 0
    skipped_mission_count: int = 0
    attention_required_mission_count: int = 0
    terminally_failed_mission_count: int = 0

    @classmethod
    def aggregate(
        cls, outcomes: Sequence[SharedCollectionResult]
    ) -> "FanOutSweepSummary":
        return cls(
            targets_processed=len(outcomes),
            attempted_task_count=sum(o.attempted_task_count for o in outcomes),
            fanned_out_mission_count=sum(
                len(o.fanned_out_mission_ids) for o in outcomes
            ),
            skipped_mission_count=sum(
                len(o.fan_out_skipped_mission_ids) for o in outcomes
            ),
            attention_required_mission_count=sum(
                len(o.fan_out_attention_required_mission_ids) for o in outcomes
            ),
            terminally_failed_mission_count=sum(
                len(o.fan_out_failed_mission_ids) for o in outcomes
            ),
        )


async def sweep_shared_collection_fan_out(
    session_factory: async_sessionmaker[AsyncSession],
    ai_manager: AIProviderManager,
    *,
    now: datetime | None = None,
    ai_profile: UserRole = UserRole.ADMIN,
    target_scan_limit: int = 25,
    task_budget: int = 100,
    per_target_task_cap: int = 25,
    concurrency: int = 4,
    firecrawl: CesarCoreFetchProvider | None = None,
    settings: Settings | None = None,
) -> FanOutSweepSummary:
    """TASK-112 fase 3B: sweep limitado de fan-out pendente/retry/stale --
    chamado no INÍCIO de todo ciclo do `CollectionOrchestrator.run_batch`
    (antes de gastar capacidade com coletas novas -- backlog antigo tem
    prioridade). Orçamento real em duas dimensões (rodada 6): quantos
    alvos distintos considerar (`target_scan_limit`) e quantas tarefas no
    TOTAL processar (`task_budget`, nunca "todos os pendentes") --
    nenhuma das duas pode monopolizar o worker indefinidamente. `recover_
    stale_fan_out_tasks` roda UMA vez aqui, nunca uma vez por alvo
    (`resume_shared_collection_fan_out(..., recover_stale=False)`
    abaixo)."""
    effective_now = now or utc_now()
    async with session_factory() as session, session.begin():
        await recover_stale_fan_out_tasks(session, now=effective_now)
    async with session_factory() as session:
        targets = await _due_shared_fan_out_targets(
            session, now=effective_now, limit=target_scan_limit
        )

    allocations = _allocate_fan_out_budget(
        targets, task_budget=task_budget, per_target_task_cap=per_target_task_cap
    )
    if not allocations:
        return FanOutSweepSummary()

    semaphore = asyncio.Semaphore(concurrency)

    async def _run_one(
        item_id: UUID, store_id: UUID, limit: int
    ) -> SharedCollectionResult:
        async with semaphore:
            return await resume_shared_collection_fan_out(
                session_factory,
                ai_manager,
                monitoring_item_id=item_id,
                store_id=store_id,
                now=effective_now,
                ai_profile=ai_profile,
                recover_stale=False,
                task_limit=limit,
                firecrawl=firecrawl,
                settings=settings,
            )

    outcomes = await asyncio.gather(
        *(
            _run_one(item_id, store_id, limit)
            for item_id, store_id, limit in allocations
        )
    )
    return FanOutSweepSummary.aggregate(outcomes)


async def _execute_claimed_shared_collection(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: CollectionAdapter,
    ai_manager: AIProviderManager,
    *,
    claim: _SharedClaim,
    ai_profile: UserRole,
    normalizer: PriceNormalizer | None,
    effective_now: datetime,
    base_backoff_minutes: int,
    firecrawl: CesarCoreFetchProvider | None = None,
    settings: Settings | None = None,
) -> SharedCollectionResult:
    """TASK-112 fase 3B: "rabo" de `collect_monitoring_item_store`
    fatorado -- rede + persistência comercial + fan-out do trabalho novo,
    dado um `_SharedClaim` JÁ estabelecido (fora de qualquer transação,
    disciplina TASK-079). Usada tanto por `collect_monitoring_item_store`
    (claim standalone + este rabo) quanto por `claim_due_work`
    (`orchestration.py` -- claim já feito dentro da Fase A, este rabo
    chamado depois, sob `self._semaphore`)."""
    effective_normalizer = normalizer or PriceNormalizer()
    monitoring_item_id = claim.monitoring_item_id
    store_id = claim.store_id

    try:
        result = await adapter.collect(
            CollectionRequest(
                source_code=claim.store_code,
                search_query=claim.criteria.search_query,
                requested_at=effective_now,
                # TASK-112 (fase 3A): sujeito da coleta explícito -- nunca
                # mais reaproveita `mission_id` para carregar um
                # `monitoring_item_id` (contrato corrigido, item 5).
                monitoring_item_id=monitoring_item_id,
            )
        )
    except Exception as error:
        await _finish_shared_collection(
            session_factory,
            run_id=claim.run_id,
            monitoring_item_id=monitoring_item_id,
            store_id=store_id,
            status=CollectionRunStatus.FAILED,
            # Nunca `utc_now()` fresco aqui -- `effective_now` é o único
            # relógio desta execução (controlável em teste); `finished_at`
            # precisa ser >= `started_at` do claim, sempre `effective_now`.
            finished_at=effective_now,
            base_interval_minutes=base_backoff_minutes,
            confirmed_block=_is_confirmed_external_block(error),
        )
        logger.warning(
            "shared_collection_provider_failed",
            extra={
                "source_code": _safe_source(claim.store_code),
                "failure_code": _failure_code(error),
            },
        )
        return SharedCollectionResult(
            claimed=True, provider_called=True, succeeded=False
        )

    normalized = effective_normalizer.normalize_result(result)
    selected = _select_final_candidates(
        search_query=claim.criteria.search_query,
        model=claim.criteria.model,
        source_code=claim.store_code,
        offers=normalized.offers,
    )
    selected_raw = tuple(item.raw_offer for item in selected)
    try:
        enriched_raw = await adapter.enrich_offer_details(
            claim.store_code, selected_raw
        )
    except Exception:
        logger.warning(
            "shared_collection_offer_detail_enrichment_failed",
            extra={"source_code": _safe_source(claim.store_code)},
            exc_info=True,
        )
        enriched_raw = selected_raw
    enriched = tuple(
        replace(item, raw_offer=raw_offer)
        for item, raw_offer in zip(selected, enriched_raw, strict=True)
    )
    normalized = NormalizedCollectionResult(
        raw_result=replace(result, offers=enriched_raw), offers=enriched
    )

    # Único relógio de "agora" para o resto da execução (nunca `utc_now()`
    # fresco): o instante em que a coleta em si terminou -- garante
    # `finished_at >= started_at` em toda `CollectionRun` envolvida.
    finished_at = normalized.raw_result.completed_at

    # Item 2 (persistência comercial 1x) + item 1 (fan-out durável):
    # persiste a coleta comercial, registra `SharedCollectionOffer` e cria
    # `SharedFanOutTask` (`pending`) para cada Mission elegível AGORA, e
    # marca a run `SUCCEEDED` -- tudo numa única transação atômica. A
    # partir do commit desta chamada, um crash a qualquer momento depois
    # nunca mais perde a informação de quais Missions ainda precisam do
    # fan-out (retomável via `resume_shared_collection_fan_out`, sem
    # nunca precisar chamar o provider de novo).
    shared_results = await _persist_shared_offers_and_finish(
        session_factory,
        run_id=claim.run_id,
        monitoring_item_id=monitoring_item_id,
        store_id=store_id,
        normalized=normalized,
        finished_at=finished_at,
    )

    # Item 4/9: a coleta compartilhada já terminou SUCCEEDED aqui --
    # nada no fan-out abaixo pode mais reverter esse resultado. Mesma
    # função de processamento que `resume_shared_collection_fan_out` usa
    # -- nunca dois jeitos diferentes de fazer fan-out.
    outcome = await _process_pending_fan_out(
        session_factory,
        claim.store_code,
        ai_manager,
        ai_profile,
        run_id=claim.run_id,
        monitoring_item_id=monitoring_item_id,
        store_id=store_id,
        shared_results=shared_results,
        finished_at=finished_at,
        firecrawl=firecrawl,
        settings=settings,
    )

    return SharedCollectionResult(
        claimed=True,
        provider_called=True,
        succeeded=True,
        offers_count=len(shared_results),
        fanned_out_mission_ids=outcome.done,
        fan_out_skipped_mission_ids=outcome.skipped,
        fan_out_attention_required_mission_ids=outcome.attention_required,
        fan_out_failed_mission_ids=outcome.terminally_failed,
        attempted_task_count=outcome.attempted_task_count,
    )


async def collect_monitoring_item_store(
    session_factory: async_sessionmaker[AsyncSession],
    adapter: CollectionAdapter,
    ai_manager: AIProviderManager,
    *,
    monitoring_item_id: UUID,
    store_id: UUID,
    now: datetime | None = None,
    ai_profile: UserRole = UserRole.ADMIN,
    cadence_config: CadenceConfig | None = None,
    store_min_interval_seconds: float = 2.0,
    fairness_owner_user_id: UUID | None = None,
    normalizer: PriceNormalizer | None = None,
    firecrawl: CesarCoreFetchProvider | None = None,
    settings: Settings | None = None,
) -> SharedCollectionResult:
    """Operação central da fase 3A, com claim standalone (fase 3B):
    `MonitoringItemStore` -> 1 coleta na loja -> normalização/persistência
    comercial 1 execução -> `Offer`/`PriceObservation` persistidos uma vez
    -> fan-out individual por Mission elegível.

    Chamador controla explicitamente QUAL `(monitoring_item_id, store_id)`
    processar -- não decide sozinha o que está due em lote; isso é
    trabalho do scheduler (`app.collection.orchestration.claim_due_work`).

    `fairness_owner_user_id=None` (default) é o contrato STANDALONE --
    "execução shared fora da fila de fairness" (script/ADMIN/teste
    chamando direto), nunca "o scheduler esqueceu de gravar o dono": não
    inventa fairness_owner nem toca `UserCollectionQueueState`. O
    `CollectionOrchestrator` de produção NUNCA chama esta função
    diretamente -- usa `claim_due_work`, que já claima dentro da Fase A
    (dono resolvido pela reserva de fairness) e chama só o "rabo" de
    execução (`_execute_claimed_shared_collection`) depois."""
    effective_now = now or utc_now()
    effective_cadence_config = cadence_config or CadenceConfig()

    claim = await _claim_shared_collection(
        session_factory,
        monitoring_item_id=monitoring_item_id,
        store_id=store_id,
        now=effective_now,
        cadence_config=effective_cadence_config,
        store_min_interval_seconds=store_min_interval_seconds,
        fairness_owner_user_id=fairness_owner_user_id,
    )
    if claim is None:
        return SharedCollectionResult(claimed=False)

    return await _execute_claimed_shared_collection(
        session_factory,
        adapter,
        ai_manager,
        claim=claim,
        ai_profile=ai_profile,
        normalizer=normalizer,
        effective_now=effective_now,
        base_backoff_minutes=effective_cadence_config.normal_min_minutes,
        firecrawl=firecrawl,
        settings=settings,
    )
