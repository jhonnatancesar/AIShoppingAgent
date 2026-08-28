"""Claim atômico de coleta compartilhada por `(MonitoringItem, store)`
(TASK-112, fases 3A/3B) -- módulo neutro.

Isolado deliberadamente de `orchestration.py`/`shared_collection.py`:
depende só de `models.py`/`fairness.py`/`persistence.py`/`cadence.py`,
nunca do outro lado -- é isso que permite `orchestration.py` (o
scheduler unificado, `claim_due_work`) chamar o claim compartilhado
diretamente, sem criar um ciclo de import com `shared_collection.py`
(que já depende de `orchestration.py` para ~17 símbolos privados de
Fase B/C reaproveitados sem alteração).

Responsabilidade única: reservar a execução (lock + `CollectionRun` +
avanço de agenda/backoff/throttle) -- nunca chamada de rede/IA (isso é
"executar a coleta", responsabilidade de `shared_collection.py`).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collection.cadence import CadenceConfig, resolve_collection_cadence, sample_next_run_at
from app.collection.fairness import _advance_store_throttle
from app.collection.models import StoreThrottleState
from app.collection.persistence import start_collection_run
from app.missions.models import MissionMonitoringItem, MonitoringItem, MonitoringItemStore
from app.missions.schedule import next_source_backoff
from app.products.identity import CollectionCriteria, canonical_collection_criteria
from app.stores.models import Store

_SHARED_RUNNING_INDEX = "uq_collection_runs_running_monitoring_item_store"
_SOURCE_BACKOFF_CAP_MINUTES = 360
V1_SOURCE_CODES = frozenset(
    {"pichau", "terabyte", "amazon", "kabum", "magalu", "mercadolivre"}
)


def _constraint_name(error: IntegrityError) -> str | None:
    orig = getattr(error, "orig", None)
    diag = getattr(orig, "diag", None)
    return getattr(diag, "constraint_name", None)


@dataclass(frozen=True, slots=True)
class _SharedClaim:
    run_id: UUID
    criteria: CollectionCriteria
    store_code: str
    monitoring_item_id: UUID
    store_id: UUID


async def _advance_monitoring_item_store(
    session: AsyncSession,
    item_store: MonitoringItemStore,
    *,
    started_at: datetime,
    cadence_config: CadenceConfig,
) -> None:
    """TASK-112 fase 3B: cadência determinística por política
    (`app.collection.cadence`), nunca mais um intervalo fixo. Sempre
    relativo a `started_at` (agora), nunca ao `next_run_at` antigo -- ver
    `cadence.sample_next_run_at` para o motivo de abandonar o algoritmo
    de "recuperar atraso em múltiplos do intervalo" (incompatível com uma
    faixa que pode mudar de ciclo para ciclo). TASK-116: atividade alta é
    escopada a esta unidade (`monitoring_item_id`) -- resolve as Missions
    que a compartilham para restringir a contagem de mudança de preço só
    às ofertas relevantes a elas, nunca à loja inteira."""
    mission_ids = (
        await session.scalars(
            select(MissionMonitoringItem.mission_id).where(
                MissionMonitoringItem.monitoring_item_id == item_store.monitoring_item_id
            )
        )
    ).all()
    decision = await resolve_collection_cadence(
        session,
        store_id=item_store.store_id,
        scope_id=item_store.monitoring_item_id,
        mission_ids=mission_ids,
        now=started_at,
        config=cadence_config,
    )
    item_store.last_run_at = started_at
    item_store.next_run_at = sample_next_run_at(started_at, decision)
    item_store.updated_at = started_at


async def _apply_shared_backoff(
    session: AsyncSession,
    *,
    monitoring_item_id: UUID,
    store_id: UUID,
    base_interval_minutes: int,
    failed_at: datetime,
) -> None:
    """Backoff por bloqueio externo confirmado (DEC-046), na granularidade
    compartilhada -- `MonitoringItemStore.consecutive_blocks`/
    `next_eligible_at`. SEMPRE vence sobre a política de cadência
    (`app.collection.cadence`): `_claim_shared_collection_in_session`
    recusa qualquer claim cujo `next_eligible_at` ainda não passou, não
    importa o quão due `next_run_at` esteja (nem promoção nem atividade
    alta encurtam isto)."""
    item_store = await session.get(
        MonitoringItemStore, (monitoring_item_id, store_id), with_for_update=True
    )
    if item_store is None:
        return
    consecutive_blocks, delay_minutes = next_source_backoff(
        base_interval_minutes=base_interval_minutes,
        consecutive_blocks=item_store.consecutive_blocks,
        cap_minutes=_SOURCE_BACKOFF_CAP_MINUTES,
    )
    item_store.consecutive_blocks = consecutive_blocks
    item_store.next_eligible_at = failed_at + timedelta(minutes=delay_minutes)
    item_store.updated_at = failed_at


async def _reset_shared_backoff(
    session: AsyncSession, *, monitoring_item_id: UUID, store_id: UUID, now: datetime
) -> None:
    item_store = await session.get(
        MonitoringItemStore, (monitoring_item_id, store_id), with_for_update=True
    )
    if item_store is None:
        return
    if item_store.consecutive_blocks == 0 and item_store.next_eligible_at is None:
        return
    item_store.consecutive_blocks = 0
    item_store.next_eligible_at = None
    item_store.updated_at = now


async def _claim_shared_collection_in_session(
    session: AsyncSession,
    *,
    monitoring_item_id: UUID,
    store_id: UUID,
    now: datetime,
    cadence_config: CadenceConfig,
    store_min_interval_seconds: float,
    fairness_owner_user_id: UUID | None = None,
) -> _SharedClaim | None:
    """Núcleo do claim -- opera na `session` que o CHAMADOR já abriu, sem
    gerenciar transação própria. Usada por `claim_due_work`
    (`orchestration.py`, dentro da Fase 2 -- Fase A já com a reserva de
    fairness do dono resolvida, seção 3 do desenho) E, indiretamente, por
    `_claim_shared_collection` (wrapper standalone abaixo).

    `fairness_owner_user_id`: `None` só é aceitável no caminho standalone
    (`collect_monitoring_item_store` chamada direto, fora do scheduler --
    "execução shared fora da fila de fairness", nunca "esqueceram de
    gravar"). `claim_due_work` NUNCA chama isto com `None` -- só constrói
    um alvo compartilhado depois que a Fase 1
    (`app.collection.fairness._reserve_fairness_owners`) já resolveu um
    dono real."""
    item = await session.get(MonitoringItem, monitoring_item_id)
    store = await session.get(Store, store_id)
    if item is None or store is None:
        return None
    if not store.is_active or store.code not in V1_SOURCE_CODES:
        return None

    # 1. LOCK do recurso -- serializa contra qualquer outro worker no
    #    mesmo (monitoring_item_id, store_id); `SKIP LOCKED` na seleção
    #    (fora deste módulo) nunca trava aqui -- este SIM é um lock real,
    #    mas só de UMA linha específica já escolhida, nunca um scan.
    item_store = await session.get(
        MonitoringItemStore, (monitoring_item_id, store_id), with_for_update=True
    )
    if item_store is None:
        return None
    # 2. RECHECK -- sempre sob o lock, nunca a leitura otimista da
    #    seleção. Backoff (`next_eligible_at`) sempre vence: mesmo que a
    #    política de cadência tenha deixado `next_run_at` due, um bloqueio
    #    confirmado ainda não expirado recusa o claim.
    if not item_store.is_enabled:
        return None
    if item_store.next_run_at is not None and item_store.next_run_at > now:
        return None
    if item_store.next_eligible_at is not None and item_store.next_eligible_at > now:
        return None
    # 3. THROTTLE global de loja -- mesma camada usada pelo caminho antigo
    #    (`app.collection.fairness._advance_store_throttle`), agora
    #    também respeitada aqui: os dois caminhos nunca furam o intervalo
    #    mínimo entre acessos à mesma loja, não importa qual reivindicou
    #    por último.
    throttle = await session.get(StoreThrottleState, store_id)
    if throttle is not None and throttle.next_allowed_at is not None and throttle.next_allowed_at > now:
        return None

    # 4. REGISTRAR CLAIM -- índice único parcial é defesa em profundidade;
    #    sob o lock já adquirido no passo 1, nunca deveria colidir de
    #    verdade.
    try:
        async with session.begin_nested():
            run = await start_collection_run(
                session, store_id, mission_id=None,
                monitoring_item_id=monitoring_item_id, started_at=now,
            )
            # TASK-112 fase 3B: gravado na MESMA transação/savepoint do
            # claim -- se o chamador (claim_due_work) descobrir logo em
            # seguida que perdeu a reserva de fairness do dono e reverter
            # este savepoint, a CollectionRun inteira desaparece junto,
            # atribuição incluída (nunca fica um dono gravado "sem turno
            # correspondente").
            run.fairness_owner_user_id = fairness_owner_user_id
    except IntegrityError as error:
        if _constraint_name(error) != _SHARED_RUNNING_INDEX:
            raise
        return None

    # 5. AVANÇAR agenda (cadência) + throttle -- ainda dentro do escopo do
    #    chamador; só depois do commit dele é que a execução real (rede)
    #    acontece em outro lugar (`shared_collection.py`).
    await _advance_monitoring_item_store(
        session, item_store, started_at=now, cadence_config=cadence_config
    )
    await _advance_store_throttle(
        session, store_id=store_id, claimed_at=now,
        min_interval_seconds=store_min_interval_seconds,
    )
    criteria = canonical_collection_criteria(item.canonical_identity)
    return _SharedClaim(
        run_id=run.id, criteria=criteria, store_code=store.code,
        monitoring_item_id=monitoring_item_id, store_id=store_id,
    )


async def _claim_shared_collection(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    monitoring_item_id: UUID,
    store_id: UUID,
    now: datetime,
    cadence_config: CadenceConfig | None = None,
    store_min_interval_seconds: float = 2.0,
    fairness_owner_user_id: UUID | None = None,
) -> _SharedClaim | None:
    """Wrapper standalone -- assinatura/comportamento observável
    preservados para quem já chama isto direto (testes/`collect_
    monitoring_item_store`): abre sua própria transação curta e delega
    para `_claim_shared_collection_in_session`."""
    async with session_factory() as session, session.begin():
        return await _claim_shared_collection_in_session(
            session,
            monitoring_item_id=monitoring_item_id,
            store_id=store_id,
            now=now,
            cadence_config=cadence_config or CadenceConfig(),
            store_min_interval_seconds=store_min_interval_seconds,
            fairness_owner_user_id=fairness_owner_user_id,
        )
