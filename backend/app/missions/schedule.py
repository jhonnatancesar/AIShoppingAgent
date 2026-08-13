"""Consulta e progressão da agenda recorrente de missões."""

import random
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.database.time import utc_now
from app.missions.models import Mission, MissionSchedule, MissionStatus


def _due_schedules_statement(*, due_at: datetime, limit: int):
    if not 1 <= limit <= 1000:
        raise ValueError("limit deve estar entre 1 e 1000.")
    return (
        select(MissionSchedule)
        .join(Mission, Mission.id == MissionSchedule.mission_id)
        .where(
            MissionSchedule.is_enabled.is_(True),
            MissionSchedule.next_run_at <= due_at,
            Mission.status == MissionStatus.ACTIVE,
            or_(Mission.expires_at.is_(None), Mission.expires_at > due_at),
        )
        .order_by(MissionSchedule.next_run_at, MissionSchedule.id)
        .limit(limit)
        .with_for_update(skip_locked=True, of=MissionSchedule)
    )


def find_due_schedules(
    session: Session,
    *,
    due_at: datetime | None = None,
    limit: int = 100,
) -> list[MissionSchedule]:
    """Bloqueia e retorna agendas elegíveis em ordem determinística."""
    effective_due_at = due_at or utc_now()
    _require_aware(effective_due_at, "due_at")
    statement = _due_schedules_statement(due_at=effective_due_at, limit=limit)
    return list(session.scalars(statement))


async def find_due_schedules_async(
    session: AsyncSession,
    *,
    due_at: datetime | None = None,
    limit: int = 100,
) -> list[MissionSchedule]:
    """Equivalente assíncrono de `find_due_schedules` (TASK-079).

    Usado só pelo `collection_worker`; chamadores síncronos (testes de
    agenda, outros serviços) continuam com `find_due_schedules`.
    """
    effective_due_at = due_at or utc_now()
    _require_aware(effective_due_at, "due_at")
    statement = _due_schedules_statement(due_at=effective_due_at, limit=limit)
    result = await session.scalars(statement)
    return list(result)


def staggered_next_run_at(base: datetime, *, max_stagger_seconds: int) -> datetime:
    """Desloca `base` uma única vez, só na criação/backfill da agenda.

    `advance_schedule` nunca chama isto: a cadência fixa é preservada nas
    execuções seguintes, e uma agenda já persistida não é recalculada em
    um restart do worker. O objetivo é só evitar que várias missões com o
    mesmo intervalo fiquem sincronizadas no mesmo instante.
    """
    if max_stagger_seconds < 0:
        raise ValueError("max_stagger_seconds deve ser não negativo.")
    if max_stagger_seconds == 0:
        return base
    return base + timedelta(seconds=random.uniform(0, max_stagger_seconds))


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


def next_source_backoff(
    *,
    base_interval_minutes: int,
    consecutive_blocks: int,
    cap_minutes: int = 360,
) -> tuple[int, int]:
    """Calcula o próximo backoff de uma fonte após bloqueio externo confirmado.

    Retorna `(consecutive_blocks, delay_minutes)`. `delay_minutes` cresce
    geometricamente (`base_interval_minutes * 2**consecutive_blocks`) até
    `cap_minutes`; uma vez atingido o teto, `consecutive_blocks` para de
    crescer — o contador não aumenta indefinidamente enquanto o delay
    permanecer no teto (DEC-046).
    """
    if base_interval_minutes <= 0:
        raise ValueError("base_interval_minutes deve ser positivo.")
    if consecutive_blocks < 0:
        raise ValueError("consecutive_blocks deve ser não negativo.")
    if cap_minutes <= 0:
        raise ValueError("cap_minutes deve ser positivo.")

    if consecutive_blocks > 0:
        current_delay = base_interval_minutes * (2**consecutive_blocks)
        if current_delay >= cap_minutes:
            return consecutive_blocks, cap_minutes

    candidate_blocks = consecutive_blocks + 1
    delay_minutes = min(base_interval_minutes * (2**candidate_blocks), cap_minutes)
    return candidate_blocks, delay_minutes


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} deve possuir fuso horário.")
