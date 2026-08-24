"""Resolução da config persistida e editável pelo ADMIN (TASK-108).

`NULL` em qualquer campo do override usa o default passado pelo chamador
(normalmente vindo de `Settings`) -- mesmo padrão já usado por
`resolve_quota_limits` (TASK-107, `app/quotas/service.py`): função pura,
sem sessão própria -- o chamador (`orchestration.py`, via `AsyncSession`;
`admin_router.py`, via `Session` síncrona) busca a linha (ou `None`) do
jeito que já usa, e só passa o resultado aqui. Chamado a cada
`run_batch`: uma mudança feita pelo ADMIN nunca precisa de restart do
worker para valer.
"""

from dataclasses import dataclass

from app.collection.models import CollectionQueueConfig

COLLECTION_QUEUE_CONFIG_ID = 1


@dataclass(frozen=True, slots=True)
class QueueConfig:
    max_concurrent_user_batches: int
    user_cooldown_min_seconds: float
    user_cooldown_max_seconds: float
    store_min_interval_seconds: float


def resolve_queue_config(
    row: CollectionQueueConfig | None,
    *,
    default_max_concurrent_user_batches: int,
    default_user_cooldown_min_seconds: float,
    default_user_cooldown_max_seconds: float,
    default_store_min_interval_seconds: float,
) -> QueueConfig:
    return QueueConfig(
        max_concurrent_user_batches=(
            row.max_concurrent_user_batches_override
            if row is not None and row.max_concurrent_user_batches_override is not None
            else default_max_concurrent_user_batches
        ),
        user_cooldown_min_seconds=(
            row.user_cooldown_min_seconds_override
            if row is not None and row.user_cooldown_min_seconds_override is not None
            else default_user_cooldown_min_seconds
        ),
        user_cooldown_max_seconds=(
            row.user_cooldown_max_seconds_override
            if row is not None and row.user_cooldown_max_seconds_override is not None
            else default_user_cooldown_max_seconds
        ),
        store_min_interval_seconds=(
            row.store_min_interval_seconds_override
            if row is not None and row.store_min_interval_seconds_override is not None
            else default_store_min_interval_seconds
        ),
    )
