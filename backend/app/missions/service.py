"""Execução atômica do ciclo de vida persistente de missões."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.time import utc_now
from app.missions.models import (
    Mission,
    MissionCommand,
    MissionCriteria,
    MissionSource,
    MissionStatus,
    MissionTransition,
)


class MissionTransitionError(RuntimeError):
    """Erro de domínio ao tentar mudar o estado de uma missão."""


class MissionNotFoundError(MissionTransitionError):
    """A missão solicitada não existe."""


class MissionVersionConflictError(MissionTransitionError):
    """A missão mudou desde a versão conhecida pelo chamador."""


class InvalidMissionTransitionError(MissionTransitionError):
    """O comando não é permitido no estado persistido atual."""


class MissionTransitionConditionError(MissionTransitionError):
    """Uma condição obrigatória da transição não foi satisfeita."""


TRANSITIONS: dict[tuple[MissionStatus, MissionCommand], MissionStatus] = {
    (MissionStatus.DRAFT, MissionCommand.ACTIVATE): MissionStatus.ACTIVE,
    (MissionStatus.DRAFT, MissionCommand.CANCEL): MissionStatus.CANCELLED,
    (MissionStatus.DRAFT, MissionCommand.EXPIRE): MissionStatus.EXPIRED,
    (MissionStatus.ACTIVE, MissionCommand.PAUSE): MissionStatus.PAUSED,
    (MissionStatus.ACTIVE, MissionCommand.COMPLETE): MissionStatus.COMPLETED,
    (MissionStatus.ACTIVE, MissionCommand.CANCEL): MissionStatus.CANCELLED,
    (MissionStatus.ACTIVE, MissionCommand.EXPIRE): MissionStatus.EXPIRED,
    (MissionStatus.PAUSED, MissionCommand.RESUME): MissionStatus.ACTIVE,
    (MissionStatus.PAUSED, MissionCommand.COMPLETE): MissionStatus.COMPLETED,
    (MissionStatus.PAUSED, MissionCommand.CANCEL): MissionStatus.CANCELLED,
    (MissionStatus.PAUSED, MissionCommand.EXPIRE): MissionStatus.EXPIRED,
}


def transition_mission(
    session: Session,
    *,
    mission_id: UUID,
    command: MissionCommand,
    expected_state_version: int,
    actor_type: str,
    actor_id: UUID | None = None,
    reason: str | None = None,
    transitioned_at: datetime | None = None,
) -> MissionTransition:
    """Valida, altera e registra uma transição na transação do chamador."""
    if not actor_type.strip() or len(actor_type) > 32:
        raise ValueError("actor_type deve conter entre 1 e 32 caracteres.")
    if reason is not None and not reason.strip():
        raise ValueError("reason não pode ser vazio.")
    if expected_state_version < 0:
        raise ValueError("expected_state_version não pode ser negativo.")

    accepted_at = transitioned_at or utc_now()
    if accepted_at.tzinfo is None or accepted_at.utcoffset() is None:
        raise ValueError("transitioned_at deve possuir fuso horário.")

    mission = session.scalar(
        select(Mission).where(Mission.id == mission_id).with_for_update()
    )
    if mission is None:
        raise MissionNotFoundError("Missão não encontrada.")
    if mission.state_version != expected_state_version:
        raise MissionVersionConflictError("Versão de estado desatualizada.")

    try:
        next_status = TRANSITIONS[(mission.status, command)]
    except KeyError as error:
        raise InvalidMissionTransitionError(
            f"Comando {command.value} inválido para o estado {mission.status.value}."
        ) from error

    if command in {MissionCommand.ACTIVATE, MissionCommand.RESUME}:
        criteria_id = session.scalar(
            select(MissionCriteria.id).where(MissionCriteria.mission_id == mission.id)
        )
        if criteria_id is None:
            raise MissionTransitionConditionError(
                "A missão precisa de critérios válidos para ser ativada."
            )
        source_id = session.scalar(
            select(MissionSource.store_id).where(MissionSource.mission_id == mission.id)
        )
        if source_id is None:
            raise MissionTransitionConditionError(
                "A missão precisa de ao menos uma fonte selecionada."
            )
    if command is MissionCommand.RESUME and _deadline_reached(mission, accepted_at):
        raise MissionTransitionConditionError(
            "Uma missão expirada não pode ser retomada."
        )
    if command is MissionCommand.EXPIRE and not _deadline_reached(mission, accepted_at):
        raise MissionTransitionConditionError(
            "A missão só pode expirar depois de alcançar seu prazo."
        )

    previous_status = mission.status
    mission.status = next_status
    mission.state_version += 1
    mission.updated_at = accepted_at
    transition = MissionTransition(
        mission_id=mission.id,
        from_status=previous_status,
        to_status=next_status,
        command=command,
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason,
        transitioned_at=accepted_at,
    )
    session.add(transition)
    session.flush()
    return transition


def _deadline_reached(mission: Mission, accepted_at: datetime) -> bool:
    return mission.expires_at is not None and accepted_at >= mission.expires_at
