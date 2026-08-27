"""Primitivas de fairness compartilhadas pelo caminho antigo (por Mission)
e pelo caminho novo (por MonitoringItem) -- TASK-112, fase 3B.

Módulo neutro de propósito: `orchestration.py` e `shared_collection.py`
importam daqui, nunca um do outro para isto -- evita a dependência
circular que existiria se a reserva/aquisição de fairness vivesse dentro
de qualquer um dos dois (auditoria da fase 3B, rodada 5 de revisão).

Reserva de usuário via lock real do Postgres (`_reserve_fairness_owners`),
não mais um token comparado por igualdade: adquirida SEMPRE antes de
qualquer lock de loja, em ordem determinística de `user_id` -- é isso,
não mais uma comparação em memória, que impede duas execuções
concorrentes de `run_batch` (mesmo com `now` diferentes) de creditarem o
mesmo usuário duas vezes. Ver `docs/tasks/TASK-112.md` para o raciocínio
completo de por que um token comparado por igualdade não bastava.
"""

from datetime import datetime, timedelta
from random import uniform
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.collection.models import StoreThrottleState, UserCollectionQueueState


def queue_sort_key(state: UserCollectionQueueState | None, user_id: UUID) -> tuple:
    """Round-robin: quem nunca foi processado (`last_processed_at IS
    NULL`) sempre vence; entre os já processados, o mais antigo primeiro;
    `user_id` como desempate estável final. Mesmo critério usado desde a
    TASK-108, só extraído para reuso entre os dois caminhos."""
    last_processed_at = state.last_processed_at if state is not None else None
    if last_processed_at is None:
        return (0, str(user_id))
    return (1, last_processed_at, str(user_id))


def is_user_eligible(state: UserCollectionQueueState | None, due_at: datetime) -> bool:
    """Fora de cooldown -- sem estado, ou `next_eligible_at` nulo/já no
    passado."""
    if state is None or state.next_eligible_at is None:
        return True
    return state.next_eligible_at <= due_at


async def _advance_store_throttle(
    session: AsyncSession,
    *,
    store_id: UUID,
    claimed_at: datetime,
    min_interval_seconds: float,
) -> None:
    """Pacing GLOBAL por loja (TASK-108) -- toda claim reivindicada para
    esta loja, de qualquer usuário/missão/caminho (antigo ou
    compartilhado), empurra `next_allowed_at` para a frente. Chamado
    dentro da mesma transação/savepoint do claim que a originou, para que
    a PRÓXIMA tentativa da mesma loja no mesmo ciclo (mesmo caminho ou
    caminho diferente) já veja o novo valor."""
    next_allowed_at = claimed_at + timedelta(seconds=min_interval_seconds)
    statement = (
        postgresql_insert(StoreThrottleState)
        .values(
            store_id=store_id,
            next_allowed_at=next_allowed_at,
            updated_at=claimed_at,
        )
        .on_conflict_do_update(
            index_elements=[StoreThrottleState.store_id],
            set_={
                "next_allowed_at": next_allowed_at,
                "updated_at": claimed_at,
            },
        )
    )
    await session.execute(statement)


async def _ensure_queue_state_rows_exist(
    session: AsyncSession, user_ids: set[UUID]
) -> None:
    """Garante que toda linha de `UserCollectionQueueState` dos
    candidatos já existe -- pré-requisito para travá-las em
    `_reserve_fairness_owners` (não se trava uma linha que não existe)."""
    for user_id in user_ids:
        await session.execute(
            postgresql_insert(UserCollectionQueueState)
            .values(user_id=user_id)
            .on_conflict_do_nothing(index_elements=[UserCollectionQueueState.user_id])
        )


async def _reserve_fairness_owners(
    session: AsyncSession, *, candidate_owners: set[UUID], due_at: datetime
) -> set[UUID]:
    """Fase 1 do claim unificado (TASK-112, fase 3B): garante as linhas
    (acima), depois tenta travar cada uma, em ORDEM ASCENDENTE de
    `user_id` -- a MESMA ordem em toda execução deste código, condição
    necessária para que duas transações concorrentes nunca formem um
    ciclo de espera cruzado (nunca A trava usuário que B quer enquanto B
    trava usuário que A quer -- ambas sempre pedem na mesma ordem).

    `FOR UPDATE SKIP LOCKED`: nunca espera. Um candidato já travado por
    outro `run_batch` concorrente, ou uma linha que ficou stale, é só
    excluído do resultado -- este batch simplesmente não processa
    trabalho desse usuário nesta passada (ele continua candidato normal
    no próximo ciclo). `next_eligible_at` é revalidado sob o lock (nunca
    a leitura otimista feita durante a seleção) -- só entra no resultado
    quem está genuinamente elegível NESTE exato momento.

    O lock de cada usuário reservado permanece em mãos até o fim da
    transação do chamador (commit ou rollback) -- é ele, mantido durante
    toda a Fase 2 (claims de recurso), que impede qualquer outra
    transação de sequer começar a reservar o mesmo usuário enquanto este
    ciclo ainda não terminou."""
    await _ensure_queue_state_rows_exist(session, candidate_owners)
    reserved: set[UUID] = set()
    for user_id in sorted(candidate_owners, key=str):
        row = await session.execute(
            select(UserCollectionQueueState)
            .where(UserCollectionQueueState.user_id == user_id)
            .with_for_update(skip_locked=True)
        )
        state = row.scalar_one_or_none()
        if state is None:
            continue
        if not is_user_eligible(state, due_at):
            continue
        reserved.add(user_id)
    return reserved


async def _commit_fairness_turn_for_owner(
    session: AsyncSession,
    *,
    user_id: UUID,
    processed_at: datetime,
    turn_id: UUID,
    cooldown_min_seconds: float,
    cooldown_max_seconds: float,
) -> None:
    """Só chamada para donos com >= 1 claim real nesta passada (a
    decisão de "teve trabalho real" é do chamador). `UPDATE` simples --
    sem CAS, sem `ON CONFLICT`: o lock desta linha já está em mãos desde
    `_reserve_fairness_owners`, chamado na MESMA transação; não há
    concorrência possível sobre ela neste ponto. `last_fairness_turn_id`
    é só rastro de auditoria/depuração ("qual ciclo creditou este
    avanço") -- a garantia de exclusão mútua em si já é o lock contínuo,
    não uma comparação de token."""
    next_eligible_at = processed_at + timedelta(
        seconds=uniform(cooldown_min_seconds, cooldown_max_seconds)
    )
    await session.execute(
        update(UserCollectionQueueState)
        .where(UserCollectionQueueState.user_id == user_id)
        .values(
            last_processed_at=processed_at,
            next_eligible_at=next_eligible_at,
            last_fairness_turn_id=turn_id,
            updated_at=processed_at,
        )
    )
