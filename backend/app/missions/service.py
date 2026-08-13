"""Execução atômica do ciclo de vida persistente de missões.

`transition_mission`/`create_mission_from_criteria` têm uma versão síncrona
(mantida para `scripts/validate_collection_worker.py`) e uma versão
`_async` (extensão da TASK-079, usada pelo webhook Telegram) -- as duas
compartilham exatamente a mesma lógica de validação, só divergindo nos
pontos de I/O (`await`). `edit_mission_criteria` só tem chamador no webhook,
por isso foi convertida diretamente, sem versão síncrona."""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.database.time import utc_now
from app.missions.models import (
    Mission,
    MissionCommand,
    MissionCriteria,
    MissionSchedule,
    MissionSource,
    MissionStatus,
    MissionTransition,
)
from app.missions.schedule import staggered_next_run_at
from app.stores.models import Store

_DEFAULT_V1_SOURCE_CODES = ("pichau", "terabyte", "amazon", "kabum")
_DEFAULT_SCHEDULE_INTERVAL_MINUTES = 60
_DEFAULT_SCHEDULE_STAGGER_SECONDS = 0
"""Fontes selecionáveis da V1 usadas quando o Intent não especifica nenhuma."""


class MissionCreationError(RuntimeError):
    """Falha interna inesperada ao criar uma missão (nunca causada pelo usuário)."""


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


class MissionEditConditionError(MissionTransitionError):
    """Uma condição obrigatória da edição de critérios não foi satisfeita."""


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


async def transition_mission_async(
    session: AsyncSession,
    *,
    mission_id: UUID,
    command: MissionCommand,
    expected_state_version: int,
    actor_type: str,
    actor_id: UUID | None = None,
    reason: str | None = None,
    transitioned_at: datetime | None = None,
) -> MissionTransition:
    """Equivalente assíncrono de `transition_mission` (extensão da
    TASK-079). Usado pelo webhook Telegram; `scripts/validate_collection_worker.py`
    continua na versão síncrona."""
    if not actor_type.strip() or len(actor_type) > 32:
        raise ValueError("actor_type deve conter entre 1 e 32 caracteres.")
    if reason is not None and not reason.strip():
        raise ValueError("reason não pode ser vazio.")
    if expected_state_version < 0:
        raise ValueError("expected_state_version não pode ser negativo.")

    accepted_at = transitioned_at or utc_now()
    if accepted_at.tzinfo is None or accepted_at.utcoffset() is None:
        raise ValueError("transitioned_at deve possuir fuso horário.")

    mission = await session.scalar(
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
        criteria_id = await session.scalar(
            select(MissionCriteria.id).where(MissionCriteria.mission_id == mission.id)
        )
        if criteria_id is None:
            raise MissionTransitionConditionError(
                "A missão precisa de critérios válidos para ser ativada."
            )
        source_id = await session.scalar(
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
    await session.flush()
    return transition


def _deadline_reached(mission: Mission, accepted_at: datetime) -> bool:
    return mission.expires_at is not None and accepted_at >= mission.expires_at


def create_mission_from_criteria(
    session: Session,
    *,
    user_id: UUID,
    search_query: str,
    model: str | None = None,
    target_amount: Decimal | None,
    target_currency: str | None,
    source_codes: Sequence[str],
    requested_at: datetime,
    schedule_interval_minutes: int = _DEFAULT_SCHEDULE_INTERVAL_MINUTES,
    schedule_stagger_seconds: int = _DEFAULT_SCHEDULE_STAGGER_SECONDS,
) -> tuple[Mission, tuple[str, ...]]:
    """Cria uma missão a partir de critérios e a ativa imediatamente.

    Quando `source_codes` vem vazio, usa automaticamente as quatro fontes
    selecionáveis da V1: toda missão criada por este serviço sai `active`,
    nunca `draft` por falta de fonte. Devolve a missão e as fontes
    efetivamente usadas, para que o chamador possa relatá-las ao usuário.
    """
    effective_codes = tuple(source_codes) or _DEFAULT_V1_SOURCE_CODES
    if schedule_interval_minutes <= 0:
        raise ValueError("schedule_interval_minutes deve ser positivo.")

    mission = Mission(
        id=uuid4(),
        user_id=user_id,
        title=search_query[:200],
        status=MissionStatus.DRAFT,
        state_version=0,
        created_at=requested_at,
        updated_at=requested_at,
    )
    session.add(mission)

    session.add(
        MissionCriteria(
            mission_id=mission.id,
            search_query=search_query,
            model=model,
            target_amount=target_amount,
            target_currency=target_currency,
            created_at=requested_at,
            updated_at=requested_at,
        )
    )

    stores_by_code = {
        store.code: store
        for store in session.scalars(
            select(Store).where(Store.code.in_(effective_codes))
        )
    }
    missing_codes = set(effective_codes) - stores_by_code.keys()
    if missing_codes:
        raise MissionCreationError(
            f"stores not seeded for codes: {', '.join(sorted(missing_codes))}"
        )

    for code in effective_codes:
        session.add(
            MissionSource(mission_id=mission.id, store_id=stores_by_code[code].id)
        )
    session.flush()

    transition_mission(
        session,
        mission_id=mission.id,
        command=MissionCommand.ACTIVATE,
        expected_state_version=mission.state_version,
        actor_type="telegram",
        actor_id=user_id,
        transitioned_at=requested_at,
    )
    session.add(
        MissionSchedule(
            mission_id=mission.id,
            interval_minutes=schedule_interval_minutes,
            next_run_at=staggered_next_run_at(
                requested_at, max_stagger_seconds=schedule_stagger_seconds
            ),
            is_enabled=True,
            created_at=requested_at,
            updated_at=requested_at,
        )
    )
    session.flush()
    return mission, effective_codes


async def create_mission_from_criteria_async(
    session: AsyncSession,
    *,
    user_id: UUID,
    search_query: str,
    model: str | None = None,
    target_amount: Decimal | None,
    target_currency: str | None,
    source_codes: Sequence[str],
    requested_at: datetime,
    schedule_interval_minutes: int = _DEFAULT_SCHEDULE_INTERVAL_MINUTES,
    schedule_stagger_seconds: int = _DEFAULT_SCHEDULE_STAGGER_SECONDS,
) -> tuple[Mission, tuple[str, ...]]:
    """Equivalente assíncrono de `create_mission_from_criteria` (extensão
    da TASK-079). Usado pelo webhook Telegram; `scripts/validate_collection_worker.py`
    continua na versão síncrona."""
    effective_codes = tuple(source_codes) or _DEFAULT_V1_SOURCE_CODES
    if schedule_interval_minutes <= 0:
        raise ValueError("schedule_interval_minutes deve ser positivo.")

    mission = Mission(
        id=uuid4(),
        user_id=user_id,
        title=search_query[:200],
        status=MissionStatus.DRAFT,
        state_version=0,
        created_at=requested_at,
        updated_at=requested_at,
    )
    session.add(mission)

    session.add(
        MissionCriteria(
            mission_id=mission.id,
            search_query=search_query,
            model=model,
            target_amount=target_amount,
            target_currency=target_currency,
            created_at=requested_at,
            updated_at=requested_at,
        )
    )

    stores_by_code = {
        store.code: store
        for store in await session.scalars(
            select(Store).where(Store.code.in_(effective_codes))
        )
    }
    missing_codes = set(effective_codes) - stores_by_code.keys()
    if missing_codes:
        raise MissionCreationError(
            f"stores not seeded for codes: {', '.join(sorted(missing_codes))}"
        )

    for code in effective_codes:
        session.add(
            MissionSource(mission_id=mission.id, store_id=stores_by_code[code].id)
        )
    await session.flush()

    await transition_mission_async(
        session,
        mission_id=mission.id,
        command=MissionCommand.ACTIVATE,
        expected_state_version=mission.state_version,
        actor_type="telegram",
        actor_id=user_id,
        transitioned_at=requested_at,
    )
    session.add(
        MissionSchedule(
            mission_id=mission.id,
            interval_minutes=schedule_interval_minutes,
            next_run_at=staggered_next_run_at(
                requested_at, max_stagger_seconds=schedule_stagger_seconds
            ),
            is_enabled=True,
            created_at=requested_at,
            updated_at=requested_at,
        )
    )
    await session.flush()
    return mission, effective_codes


async def edit_mission_criteria(
    session: AsyncSession,
    *,
    mission_id: UUID,
    expected_state_version: int,
    target_update: tuple[Decimal | None, str | None] | None,
    source_codes: Sequence[str] | None,
    edited_at: datetime | None = None,
) -> tuple[Mission, tuple[str, ...]]:
    """Edita preço-alvo e/ou lojas de uma missão `PAUSED` já criada (TASK-069).

    Nunca cria uma `Mission` nova nem toca `status`/`state_version`/
    `MissionTransition`/`MissionSchedule` -- edição de critérios é
    conceitualmente distinta de transição de ciclo de vida. Histórico já
    coletado (`CollectionRun`/`PriceObservation`) nunca é tocado, mesmo
    para lojas removidas.

    `target_update=None` deixa o preço-alvo intocado; um par
    `(amount, currency)` -- incluindo `(None, None)` para limpar o alvo --
    sobrescreve. `source_codes=None` deixa as lojas intocadas; uma
    sequência não vazia substitui inteiramente o conjunto de
    `MissionSource` (insere as novas, remove as que saíram). Pelo menos um
    dos dois precisa ser fornecido; devolve a missão e o conjunto de
    lojas efetivamente selecionado após a edição.
    """
    if target_update is None and source_codes is None:
        raise ValueError("target_update or source_codes must be provided")
    if target_update is not None and (target_update[0] is None) != (
        target_update[1] is None
    ):
        raise ValueError("target_update amount and currency must be paired")
    if source_codes is not None and not source_codes:
        raise MissionEditConditionError(
            "A missão precisa manter ao menos uma loja selecionada."
        )
    if expected_state_version < 0:
        raise ValueError("expected_state_version não pode ser negativo.")

    accepted_at = edited_at or utc_now()
    if accepted_at.tzinfo is None or accepted_at.utcoffset() is None:
        raise ValueError("edited_at deve possuir fuso horário.")

    mission = await session.scalar(
        select(Mission).where(Mission.id == mission_id).with_for_update()
    )
    if mission is None:
        raise MissionNotFoundError("Missão não encontrada.")
    if mission.state_version != expected_state_version:
        raise MissionVersionConflictError("Versão de estado desatualizada.")
    if mission.status is not MissionStatus.PAUSED:
        raise MissionEditConditionError(
            "Só é possível editar uma missão pausada. Pause a missão primeiro."
        )

    if target_update is not None:
        criteria = await session.scalar(
            select(MissionCriteria).where(MissionCriteria.mission_id == mission.id)
        )
        if criteria is None:
            raise MissionEditConditionError("A missão não tem critérios válidos.")
        criteria.target_amount = target_update[0]
        criteria.target_currency = target_update[1]
        criteria.updated_at = accepted_at

    effective_codes: tuple[str, ...] = ()
    if source_codes is not None:
        codes = tuple(dict.fromkeys(source_codes))
        stores_by_code = {
            store.code: store
            for store in await session.scalars(
                select(Store).where(Store.code.in_(codes))
            )
        }
        missing_codes = set(codes) - stores_by_code.keys()
        if missing_codes:
            raise MissionEditConditionError(
                f"Loja(s) não reconhecida(s): {', '.join(sorted(missing_codes))}."
            )
        current_store_ids = set(
            await session.scalars(
                select(MissionSource.store_id).where(
                    MissionSource.mission_id == mission.id
                )
            )
        )
        target_store_ids = {stores_by_code[code].id for code in codes}
        removed_store_ids = current_store_ids - target_store_ids
        if removed_store_ids:
            await session.execute(
                delete(MissionSource).where(
                    MissionSource.mission_id == mission.id,
                    MissionSource.store_id.in_(removed_store_ids),
                )
            )
        for store_id in target_store_ids - current_store_ids:
            session.add(MissionSource(mission_id=mission.id, store_id=store_id))
        await session.flush()
        effective_codes = codes

    mission.updated_at = accepted_at
    await session.flush()
    return mission, effective_codes
