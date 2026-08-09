"""Classifica evidências persistidas pelo E2E externo da TASK-053.

O validador é somente leitura: não cria observação/evento, não executa
avaliador e não chama o notifier.
"""

import argparse
import json
from enum import StrEnum

from app.collection.models import CollectionRun, CollectionRunStatus, PriceObservation
from app.collection.normalization import Availability
from app.database.session import create_database_engine, create_session_factory
from app.events import ConsumptionOutcome, Event, EventConsumptionAttempt, EventType
from app.missions.models import Mission, MissionCriteria, MissionSource
from app.telegram.notifications import TELEGRAM_NOTIFICATION_CONSUMER
from sqlalchemy import func, select


class ExternalE2EStatus(StrEnum):
    PASS = "PASS"
    FAIL_INTERNAL = "FAIL_INTERNO"
    BLOCKED_EXTERNAL = "BLOCKED_EXTERNAL"
    PENDING = "PENDING"


def classify(mission_title: str) -> tuple[ExternalE2EStatus, dict[str, object]]:
    engine = create_database_engine()
    sessions = create_session_factory(engine)
    try:
        with sessions() as session:
            mission = session.scalar(
                select(Mission)
                .where(Mission.title == mission_title)
                .order_by(Mission.created_at.desc(), Mission.id.desc())
                .limit(1)
            )
            if mission is None:
                return ExternalE2EStatus.PENDING, {"reason": "mission_not_found"}
            criteria = session.scalar(
                select(MissionCriteria).where(MissionCriteria.mission_id == mission.id)
            )
            selected = (
                session.scalar(
                    select(func.count())
                    .select_from(MissionSource)
                    .where(MissionSource.mission_id == mission.id)
                )
                or 0
            )
            runs = list(
                session.scalars(
                    select(CollectionRun).where(CollectionRun.mission_id == mission.id)
                )
            )
            succeeded_runs = sum(
                run.status is CollectionRunStatus.SUCCEEDED for run in runs
            )
            failed_runs = sum(run.status is CollectionRunStatus.FAILED for run in runs)
            running_runs = sum(
                run.status is CollectionRunStatus.RUNNING for run in runs
            )
            run_ids = [run.id for run in runs]
            observations = (
                list(
                    session.scalars(
                        select(PriceObservation).where(
                            PriceObservation.collection_run_id.in_(run_ids)
                        )
                    )
                )
                if run_ids
                else []
            )
            # V1 (DEC-045): monitoramento de preço usa `amount` (produto),
            # não exige frete conhecido. Frete continua fora do critério de
            # elegibilidade externa.
            eligible = 0
            if criteria is not None and criteria.target_currency is not None:
                eligible = sum(
                    item.availability is Availability.AVAILABLE
                    and item.currency == criteria.target_currency
                    for item in observations
                )
            events = list(
                session.scalars(
                    select(Event).where(
                        Event.mission_id == mission.id,
                        Event.event_type == EventType.PRICE_TARGET_REACHED_V1.value,
                    )
                )
            )
            event_ids = [event.id for event in events]
            attempts = (
                list(
                    session.scalars(
                        select(EventConsumptionAttempt).where(
                            EventConsumptionAttempt.event_id.in_(event_ids),
                            EventConsumptionAttempt.consumer_name
                            == TELEGRAM_NOTIFICATION_CONSUMER,
                        )
                    )
                )
                if event_ids
                else []
            )
            terminal_success = sum(
                item.outcome is ConsumptionOutcome.SUCCEEDED for item in attempts
            )
            duplicate_terminal = (
                session.execute(
                    select(
                        EventConsumptionAttempt.event_id,
                        func.count(EventConsumptionAttempt.id),
                    )
                    .where(
                        EventConsumptionAttempt.event_id.in_(event_ids),
                        EventConsumptionAttempt.consumer_name
                        == TELEGRAM_NOTIFICATION_CONSUMER,
                        EventConsumptionAttempt.outcome.in_(
                            (
                                ConsumptionOutcome.SUCCEEDED,
                                ConsumptionOutcome.SKIPPED,
                                ConsumptionOutcome.DEAD_LETTERED,
                            )
                        ),
                    )
                    .group_by(EventConsumptionAttempt.event_id)
                    .having(func.count(EventConsumptionAttempt.id) > 1)
                ).first()
                is not None
                if event_ids
                else False
            )
            evidence = {
                "selected_sources": selected,
                "runs": len(runs),
                "succeeded_runs": succeeded_runs,
                "failed_runs": failed_runs,
                "running_runs": running_runs,
                "observations": len(observations),
                "eligible_observations": eligible,
                "target_events": len(events),
                "consumption_attempts": len(attempts),
                "successful_notifications": terminal_success,
                "duplicate_terminal_consumption": duplicate_terminal,
            }
            if duplicate_terminal:
                return ExternalE2EStatus.FAIL_INTERNAL, evidence
            if events and terminal_success:
                return ExternalE2EStatus.PASS, evidence
            if len(runs) < selected or running_runs:
                return ExternalE2EStatus.PENDING, evidence
            if len(runs) == selected and not events:
                evidence["reason"] = (
                    "no_eligible_external_evidence"
                    if not eligible
                    else "eligible_observation_without_target_event"
                )
                status = (
                    ExternalE2EStatus.BLOCKED_EXTERNAL
                    if not eligible
                    else ExternalE2EStatus.FAIL_INTERNAL
                )
                return status, evidence
            if events and not attempts:
                return ExternalE2EStatus.PENDING, evidence
            evidence["reason"] = "notification_not_delivered"
            return ExternalE2EStatus.BLOCKED_EXTERNAL, evidence
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission-title", required=True)
    arguments = parser.parse_args()
    status, evidence = classify(arguments.mission_title)
    print(json.dumps({"status": status.value, "evidence": evidence}, sort_keys=True))
    return {
        ExternalE2EStatus.PASS: 0,
        ExternalE2EStatus.FAIL_INTERNAL: 1,
        ExternalE2EStatus.BLOCKED_EXTERNAL: 2,
        ExternalE2EStatus.PENDING: 3,
    }[status]


if __name__ == "__main__":
    raise SystemExit(main())
