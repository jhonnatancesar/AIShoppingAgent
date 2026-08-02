"""Consulta e progressão da agenda recorrente de missões."""

from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database.time import utc_now
from app.missions.models import Mission, MissionSchedule, MissionStatus


def find_due_schedules(
    session: Session,
    *,
    due_at: datetime | None = None,
    limit: int = 100,
) -> list[MissionSchedule]:
    """Bloqueia e retorna agendas elegíveis em ordem determinística."""
    effective_due_at = due_at or utc_now()
    _require_aware(effective_due_at, "due_at")
    if not 1 <= limit <= 1000:
        raise ValueError("limit deve estar entre 1 e 1000.")

    statement = (
        select(MissionSchedule)
        .join(Mission, Mission.id == MissionSchedule.mission_id)
        .where(
            MissionSchedule.is_enabled.is_(True),
            MissionSchedule.next_run_at <= effective_due_at,
            Mission.status == MissionStatus.ACTIVE,
            or_(Mission.expires_at.is_(None), Mission.expires_at > effective_due_at),
        )
        .order_by(MissionSchedule.next_run_at, MissionSchedule.id)
        .limit(limit)
        .with_for_update(skip_locked=True, of=MissionSchedule)
    )
    return list(session.scalars(statement))


def advance_schedule(schedule: MissionSchedule, *, started_at: datetime) -> None:
    """Avança uma agenda vencida sem acumular execuções retroativas."""
    _require_aware(started_at, "started_at")
    if not schedule.is_enabled:
        raise ValueError("Uma agenda desabilitada não pode ser avançada.")
    if schedule.interval_minutes <= 0:
        raise ValueError("interval_minutes deve ser positivo.")
    if started_at < schedule.next_run_at:
        raise ValueError("A agenda ainda não está vencida.")

    interval = timedelta(minutes=schedule.interval_minutes)
    elapsed = started_at - schedule.next_run_at
    steps = elapsed // interval + 1
    schedule.last_run_at = started_at
    schedule.next_run_at += interval * steps
    schedule.updated_at = started_at


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} deve possuir fuso horário.")
