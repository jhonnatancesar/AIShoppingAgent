"""TASK-079: só o `collection_worker` chama estas funções, sempre com
`AsyncSession` -- sem chamador síncrono a preservar, por isso convertidas
diretamente (sem versão paralela síncrona)."""

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.collection.models import CollectionRun, CollectionRunStatus
from app.database.time import utc_now


class CollectionRunStateError(RuntimeError):
    pass


async def start_collection_run(
    session: AsyncSession,
    store_id: UUID,
    mission_id: UUID | None = None,
    *,
    started_at: datetime | None = None,
) -> CollectionRun:
    run = CollectionRun(
        store_id=store_id, mission_id=mission_id, started_at=started_at or utc_now()
    )
    session.add(run)
    await session.flush()
    return run


async def finish_collection_run(
    session: AsyncSession,
    run_id: UUID,
    status: CollectionRunStatus,
    *,
    finished_at: datetime | None = None,
) -> CollectionRun:
    if status is CollectionRunStatus.RUNNING:
        raise CollectionRunStateError("terminal status is required")
    run = await session.get(CollectionRun, run_id, with_for_update=True)
    if run is None:
        raise CollectionRunStateError("collection run not found")
    if run.status is not CollectionRunStatus.RUNNING:
        raise CollectionRunStateError("collection run is already terminal")
    completed_at = finished_at or utc_now()
    if completed_at < run.started_at:
        raise CollectionRunStateError("finished_at precedes started_at")
    run.status = status
    run.finished_at = completed_at
    await session.flush()
    return run
