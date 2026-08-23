"""Seed e verificação segura da orquestração real em um banco descartável."""

import argparse
from datetime import UTC, datetime

from app.collection.models import CollectionRun, CollectionRunStatus, PriceObservation
from app.database.session import create_database_engine, create_session_factory
from app.events import Event, EventType
from app.missions.models import Mission, MissionSource
from app.missions.service import create_mission_from_criteria
from app.users.models import User, UserRole
from sqlalchemy import func, select

_DISPLAY_NAME = "TASK-062 Docker validation"
_QUERY = "RTX 4060"


def seed(source_codes: tuple[str, ...] = ()) -> None:
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
                source_codes=source_codes,
                requested_at=datetime.now(UTC),
                actor_type="task_validation",
            )
            print(f"seeded mission with {len(sources)} sources")
            expected = len(source_codes) or 4
            if len(sources) != expected or mission.status.value != "active":
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
            expected = session.scalar(
                select(func.count(MissionSource.store_id)).where(
                    MissionSource.mission_id == mission_id
                )
            )
            runs = list(
                session.scalars(
                    select(CollectionRun).where(CollectionRun.mission_id == mission_id)
                )
            )
            if len(runs) != expected or any(
                run.status is CollectionRunStatus.RUNNING for run in runs
            ):
                raise RuntimeError("worker did not terminalize all expected sources")
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
            if event_count != expected or not observations:
                raise RuntimeError(
                    "worker pipeline did not persist its expected evidence"
                )
            succeeded = sum(run.status is CollectionRunStatus.SUCCEEDED for run in runs)
            failed = len(runs) - succeeded
            print(
                f"validated sources={expected} succeeded={succeeded} failed={failed} "
                f"observations={observations} events={event_count}"
            )
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seed", "verify"))
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        help="Restringe o seed a uma fonte (repita para mais de uma); "
        "sem uso, mantém o padrão de todas as 4 fontes V1.",
    )
    arguments = parser.parse_args()
    if arguments.action == "seed":
        seed(tuple(arguments.sources or ()))
    else:
        verify()


if __name__ == "__main__":
    main()
