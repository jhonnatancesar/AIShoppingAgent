"""Seed e verificação segura da orquestração real em um banco descartável."""

import argparse
from datetime import UTC, datetime

from app.collection.models import CollectionRun, CollectionRunStatus, PriceObservation
from app.database.session import create_database_engine, create_session_factory
from app.events import Event, EventType
from app.missions.models import Mission
from app.missions.service import create_mission_from_criteria
from app.users.models import User, UserRole
from sqlalchemy import func, select

_DISPLAY_NAME = "TASK-062 Docker validation"
_QUERY = "RTX 4060"


def seed() -> None:
    engine = create_database_engine()
    sessions = create_session_factory(engine)
    try:
        with sessions.begin() as session:
            if session.scalar(
                select(User.id).where(User.display_name == _DISPLAY_NAME)
            ):
                raise RuntimeError("validation seed already exists")
            user = User(display_name=_DISPLAY_NAME, role=UserRole.USER)
            session.add(user)
            session.flush()
            mission, sources = create_mission_from_criteria(
                session,
                user_id=user.id,
                search_query=_QUERY,
                target_amount=None,
                target_currency=None,
                source_codes=(),
                requested_at=datetime.now(UTC),
                actor_type="task_validation",
            )
            print(f"seeded mission with {len(sources)} sources")
            if len(sources) != 4 or mission.status.value != "active":
                raise RuntimeError("validation mission was not created correctly")
    finally:
        engine.dispose()


def verify() -> None:
    engine = create_database_engine()
    sessions = create_session_factory(engine)
    try:
        with sessions.begin() as session:
            user = session.scalar(
                select(User).where(User.display_name == _DISPLAY_NAME)
            )
            if user is None:
                raise RuntimeError("validation seed not found")
            mission_id = session.scalar(
                select(Mission.id).where(Mission.user_id == user.id).limit(1)
            )
            if mission_id is None:
                raise RuntimeError("worker did not publish collection events")
            runs = list(
                session.scalars(
                    select(CollectionRun).where(CollectionRun.mission_id == mission_id)
                )
            )
            if len(runs) != 4 or any(
                run.status is CollectionRunStatus.RUNNING for run in runs
            ):
                raise RuntimeError("worker did not terminalize all four sources")
            event_count = session.scalar(
                select(func.count(Event.id)).where(
                    Event.mission_id == mission_id,
                    Event.event_type.in_(
                        (
                            EventType.COLLECTION_COMPLETED_V1.value,
                            EventType.COLLECTION_FAILED_V1.value,
                        )
                    ),
                )
            )
            observations = session.scalar(
                select(func.count(PriceObservation.id))
                .join(
                    CollectionRun,
                    CollectionRun.id == PriceObservation.collection_run_id,
                )
                .where(CollectionRun.mission_id == mission_id)
            )
            if event_count != 4 or not observations:
                raise RuntimeError(
                    "worker pipeline did not persist its expected evidence"
                )
            succeeded = sum(run.status is CollectionRunStatus.SUCCEEDED for run in runs)
            failed = len(runs) - succeeded
            print(
                f"validated sources=4 succeeded={succeeded} failed={failed} "
                f"observations={observations} events={event_count}"
            )
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seed", "verify"))
    arguments = parser.parse_args()
    (seed if arguments.action == "seed" else verify)()


if __name__ == "__main__":
    main()
