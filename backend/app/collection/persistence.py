from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.collection.models import CollectionRun, CollectionRunStatus
from app.database.time import utc_now


class CollectionRunStateError(RuntimeError):
    pass


def start_collection_run(
    session: Session,
    store_id: UUID,
    mission_id: UUID | None = None,
    *,
    started_at: datetime | None = None,
) -> CollectionRun:
    run = CollectionRun(
        store_id=store_id, mission_id=mission_id, started_at=started_at or utc_now()
    )
    session.add(run)
    session.flush()
    return run


def finish_collection_run(
    session: Session,
    run_id: UUID,
    status: CollectionRunStatus,
    *,
    finished_at: datetime | None = None,
) -> CollectionRun:
    if status is CollectionRunStatus.RUNNING:
        raise CollectionRunStateError("terminal status is required")
    run = session.get(CollectionRun, run_id, with_for_update=True)
    if run is None:
        raise CollectionRunStateError("collection run not found")
    if run.status is not CollectionRunStatus.RUNNING:
        raise CollectionRunStateError("collection run is already terminal")
    completed_at = finished_at or utc_now()
    if completed_at < run.started_at:
        raise CollectionRunStateError("finished_at precedes started_at")
    run.status = status
    run.finished_at = completed_at
    session.flush()
    return run
