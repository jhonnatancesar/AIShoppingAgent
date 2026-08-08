"""Consultas somente leitura de missões por proprietário."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.missions.models import Mission, MissionCriteria, MissionStatus

_TERMINAL_STATUSES = frozenset(
    {MissionStatus.COMPLETED, MissionStatus.CANCELLED, MissionStatus.EXPIRED}
)


class MissionReferenceError(ValueError):
    """A referência em texto livre não resolveu para exatamente uma missão."""


def list_missions_for_user(
    session: Session,
    *,
    user_id: UUID,
    limit: int = 10,
) -> list[Mission]:
    """Lista as missões mais recentes do usuário, mais recente primeiro."""
    statement = (
        select(Mission)
        .where(Mission.user_id == user_id)
        .order_by(Mission.created_at.desc())
        .limit(limit)
    )
    return list(session.scalars(statement))


def find_missions_by_reference(
    session: Session,
    *,
    user_id: UUID,
    reference: str,
) -> list[Mission]:
    """Busca missões do usuário cujo `search_query` contenha `reference`.

    Comparação case-insensitive e restrita ao proprietário; a V1 não expõe
    identificador de missão ao usuário, então uma referência em texto livre é
    o único jeito de apontar para uma missão específica.
    """
    statement = (
        select(Mission)
        .join(MissionCriteria, MissionCriteria.mission_id == Mission.id)
        .where(
            Mission.user_id == user_id,
            MissionCriteria.search_query.ilike(f"%{reference}%"),
        )
        .order_by(Mission.created_at.desc())
    )
    return list(session.scalars(statement))


def resolve_mission_for_command(
    session: Session,
    *,
    user_id: UUID,
    reference: str | None,
) -> Mission:
    """Resolve exatamente uma missão para executar um comando.

    Com `reference`, exige uma correspondência única em `search_query`. Sem
    `reference`, exige exatamente uma missão não terminal do usuário — a V1
    nunca expõe um identificador de missão, então mais de uma candidata é
    ambiguidade real, não um detalhe de implementação.
    """
    if reference:
        candidates = find_missions_by_reference(
            session, user_id=user_id, reference=reference
        )
    else:
        candidates = [
            mission
            for mission in list_missions_for_user(session, user_id=user_id, limit=50)
            if mission.status not in _TERMINAL_STATUSES
        ]

    if not candidates:
        raise MissionReferenceError("Não encontrei nenhuma missão correspondente.")
    if len(candidates) > 1:
        raise MissionReferenceError(
            "Encontrei mais de uma missão correspondente; seja mais específico."
        )
    return candidates[0]
