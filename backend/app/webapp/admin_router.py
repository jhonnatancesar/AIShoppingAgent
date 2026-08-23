"""Painel e operações ADMIN/DEV, sempre fail-closed (TASK-102)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.admin.service_ops import (
    ControllerServiceOps,
    ManagedService,
    ServiceOperation,
    ServiceOpsUnavailable,
)
from app.audit.models import AuditEntry
from app.authentication.models import (
    CredentialActionToken,
    TelegramLinkToken,
    UserAuthSession,
    UserCredential,
    WebSession,
)
from app.authentication.passwords import (
    PasswordPolicyError,
    hash_password,
    validate_password,
)
from app.collection.models import CollectionRun, CollectionRunStatus
from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.database.dependency import get_session
from app.database.time import utc_now
from app.events.models import ConsumptionOutcome, Event, EventConsumptionAttempt
from app.missions.models import Mission, MissionCommand, MissionSchedule, MissionStatus
from app.missions.service import MissionTransitionError, transition_mission
from app.stores.models import Store
from app.users.models import User, UserLifecycleStatus, UserRole
from app.webapp.dependency import require_admin_web_session

router = APIRouter(prefix="/api/v1/admin", tags=["web-admin"])


class CountSummary(BaseModel):
    total: int
    active: int = 0
    failed_24h: int = 0


class StoreSummary(BaseModel):
    id: UUID
    code: str
    name: str
    is_active: bool
    last_run_status: str | None
    last_run_at: datetime | None


class ServiceSummary(BaseModel):
    service: ManagedService
    status: str
    detail: str | None = None


class DashboardResponse(BaseModel):
    generated_at: datetime
    api: str
    postgresql: str
    redis: str
    users: CountSummary
    missions: CountSummary
    collections: CountSummary
    events: CountSummary
    stores: list[StoreSummary]
    workers: list[ServiceSummary]
    ai_history_available: bool = False
    circuit_state_available: bool = False


class UserItem(BaseModel):
    id: UUID
    display_name: str
    username: str | None
    email: str | None
    role: UserRole
    lifecycle_status: UserLifecycleStatus
    is_active: bool
    mission_count: int = 0
    max_active_missions_override: int | None = None
    max_store_slots_override: int | None = None
    max_daily_searches_override: int | None = None


class UserListResponse(BaseModel):
    items: list[UserItem]


class CreateUserRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=160)
    username: str = Field(min_length=1, max_length=32)
    email: str | None = Field(default=None, max_length=254)
    role: UserRole = UserRole.USER
    password: str


class UpdateUserRequest(BaseModel):
    role: UserRole | None = None
    lifecycle_status: UserLifecycleStatus | None = None
    # TASK-107: campos de cota são tri-state via `model_fields_set` --
    # ausente do payload = não mexe; presente com um inteiro = define o
    # override; presente como `null` explícito = limpa o override (volta
    # ao default do sistema). Diferente de `role`/`lifecycle_status`
    # acima, onde `None` sempre significa "não mexer" (não há caso de uso
    # para "limpar" esses dois campos).
    max_active_missions_override: Annotated[int | None, Field(gt=0)] = None
    max_store_slots_override: Annotated[int | None, Field(gt=0)] = None
    max_daily_searches_override: Annotated[int | None, Field(gt=0)] = None
    reason: str = Field(min_length=3, max_length=500)


class DeleteUserRequest(BaseModel):
    confirmation: str
    reason: str = Field(min_length=3, max_length=500)


class MissionItem(BaseModel):
    id: UUID
    title: str
    status: MissionStatus
    state_version: int


class MissionCommandRequest(BaseModel):
    command: Literal["pause", "resume", "cancel"]
    expected_state_version: int
    reason: str = Field(min_length=3, max_length=500)


class StoreUpdateRequest(BaseModel):
    enabled: bool
    reason: str = Field(min_length=3, max_length=500)


class CollectionTriggerRequest(BaseModel):
    mission_id: UUID
    reason: str = Field(min_length=3, max_length=500)
    confirmation: bool


class ServiceActionRequest(BaseModel):
    service: ManagedService
    operation: Literal["start", "restart"]
    confirmation: bool
    reason: str = Field(min_length=3, max_length=500)


def _audit(
    session: Session,
    actor: User,
    action: str,
    resource_type: str,
    resource_id: UUID,
    metadata: dict,
) -> None:
    session.add(
        AuditEntry(
            actor_type="web_admin",
            actor_id=actor.id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            entry_metadata=metadata,
        )
    )


def _worker_states(settings: Settings) -> list[ServiceSummary]:
    adapter = ControllerServiceOps(settings)
    result = []
    for service in ManagedService:
        try:
            state = adapter.status(service)
            result.append(
                ServiceSummary(
                    service=service, status=state.status, detail=state.detail
                )
            )
        except ServiceOpsUnavailable:
            result.append(
                ServiceSummary(
                    service=service,
                    status="unavailable",
                    detail="Controlador indisponível",
                )
            )
    return result


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(
    session: Session = Depends(get_session),
    _: User = Depends(require_admin_web_session),
    settings: Settings = Depends(get_settings),
) -> DashboardResponse:
    now = utc_now()
    since = now - timedelta(hours=24)
    users_total = session.scalar(select(func.count()).select_from(User)) or 0
    users_active = (
        session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.lifecycle_status == UserLifecycleStatus.ACTIVE)
        )
        or 0
    )
    mission_total = session.scalar(select(func.count()).select_from(Mission)) or 0
    mission_active = (
        session.scalar(
            select(func.count())
            .select_from(Mission)
            .where(Mission.status == MissionStatus.ACTIVE)
        )
        or 0
    )
    collection_total = (
        session.scalar(
            select(func.count())
            .select_from(CollectionRun)
            .where(CollectionRun.started_at >= since)
        )
        or 0
    )
    collection_failed = (
        session.scalar(
            select(func.count())
            .select_from(CollectionRun)
            .where(
                CollectionRun.started_at >= since,
                CollectionRun.status == CollectionRunStatus.FAILED,
            )
        )
        or 0
    )
    event_total = (
        session.scalar(
            select(func.count()).select_from(Event).where(Event.occurred_at >= since)
        )
        or 0
    )
    event_failed = (
        session.scalar(
            select(func.count())
            .select_from(EventConsumptionAttempt)
            .where(
                EventConsumptionAttempt.attempted_at >= since,
                EventConsumptionAttempt.outcome.in_(
                    [ConsumptionOutcome.FAILED, ConsumptionOutcome.DEAD_LETTERED]
                ),
            )
        )
        or 0
    )
    stores = []
    for store in session.scalars(select(Store).order_by(Store.name, Store.id)):
        latest = session.scalar(
            select(CollectionRun)
            .where(CollectionRun.store_id == store.id)
            .order_by(CollectionRun.started_at.desc(), CollectionRun.id.desc())
            .limit(1)
        )
        stores.append(
            StoreSummary(
                id=store.id,
                code=store.code,
                name=store.name,
                is_active=store.is_active,
                last_run_status=latest.status.value if latest else None,
                last_run_at=latest.started_at if latest else None,
            )
        )
    return DashboardResponse(
        generated_at=now,
        api="healthy",
        postgresql="healthy",
        redis="not_configured",
        users=CountSummary(total=users_total, active=users_active),
        missions=CountSummary(total=mission_total, active=mission_active),
        collections=CountSummary(total=collection_total, failed_24h=collection_failed),
        events=CountSummary(total=event_total, failed_24h=event_failed),
        stores=stores,
        workers=_worker_states(settings),
    )


@router.get("/users", response_model=UserListResponse)
def list_users(
    q: str | None = Query(default=None, max_length=100),
    session: Session = Depends(get_session),
    _: User = Depends(require_admin_web_session),
) -> UserListResponse:
    statement = select(User).order_by(User.created_at.desc(), User.id)
    if q:
        statement = statement.where(
            User.display_name.ilike(f"%{q}%")
            | User.username.ilike(f"%{q}%")
            | User.email.ilike(f"%{q}%")
        )
    items = []
    for user in session.scalars(statement.limit(200)):
        count = (
            session.scalar(
                select(func.count())
                .select_from(Mission)
                .where(Mission.user_id == user.id)
            )
            or 0
        )
        items.append(
            UserItem(
                id=user.id,
                display_name=user.display_name,
                username=user.username,
                email=user.email,
                role=user.role,
                lifecycle_status=user.lifecycle_status,
                is_active=user.is_active,
                mission_count=count,
                max_active_missions_override=user.max_active_missions_override,
                max_store_slots_override=user.max_store_slots_override,
                max_daily_searches_override=user.max_daily_searches_override,
            )
        )
    return UserListResponse(items=items)


@router.post("/users", response_model=UserItem, status_code=201)
def create_user(
    payload: CreateUserRequest,
    session: Session = Depends(get_session),
    actor: User = Depends(require_admin_web_session),
) -> UserItem:
    try:
        password = validate_password(payload.password, username=payload.username)
    except PasswordPolicyError as error:
        raise ApiError(
            status_code=422, code="invalid_password", message=str(error)
        ) from error
    user = User(
        display_name=payload.display_name.strip(),
        username=payload.username.strip().lower(),
        email=payload.email.strip().lower() if payload.email else None,
        role=payload.role,
        is_active=True,
        lifecycle_status=UserLifecycleStatus.ACTIVE,
    )
    session.add(user)
    session.flush()
    session.add(UserCredential(user_id=user.id, password_hash=hash_password(password)))
    _audit(
        session,
        actor,
        "admin.user.created",
        "user",
        user.id,
        {"role": payload.role.value},
    )
    session.commit()
    return UserItem(
        id=user.id,
        display_name=user.display_name,
        username=user.username,
        email=user.email,
        role=user.role,
        lifecycle_status=user.lifecycle_status,
        is_active=True,
    )


def _stop_user_missions(
    session: Session, user_id: UUID, actor: User, reason: str
) -> None:
    now = utc_now()
    for mission in session.scalars(
        select(Mission).where(Mission.user_id == user_id).with_for_update()
    ):
        if mission.status is MissionStatus.ACTIVE:
            transition_mission(
                session,
                mission_id=mission.id,
                command=MissionCommand.PAUSE,
                expected_state_version=mission.state_version,
                actor_type="web_admin",
                actor_id=actor.id,
                reason=reason,
                transitioned_at=now,
            )
    session.execute(
        update(MissionSchedule)
        .where(
            MissionSchedule.mission_id.in_(
                select(Mission.id).where(Mission.user_id == user_id)
            )
        )
        .values(is_enabled=False, updated_at=now)
    )


def _revoke_sessions(session: Session, user_id: UUID) -> None:
    now = utc_now()
    session.execute(
        update(WebSession)
        .where(WebSession.user_id == user_id, WebSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    session.execute(
        update(UserAuthSession)
        .where(UserAuthSession.user_id == user_id, UserAuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )


@router.patch("/users/{user_id}", response_model=UserItem)
def update_user(
    user_id: UUID,
    payload: UpdateUserRequest,
    session: Session = Depends(get_session),
    actor: User = Depends(require_admin_web_session),
) -> UserItem:
    user = session.scalar(select(User).where(User.id == user_id).with_for_update())
    if not user or user.lifecycle_status is UserLifecycleStatus.DELETED:
        raise ApiError(
            status_code=404, code="user_not_found", message="Usuário não encontrado."
        )
    if payload.role is not None:
        user.role = payload.role
    if payload.lifecycle_status is not None:
        user.lifecycle_status = payload.lifecycle_status
        user.is_active = payload.lifecycle_status is UserLifecycleStatus.ACTIVE
        if not user.is_active:
            _revoke_sessions(session, user.id)
            _stop_user_missions(session, user.id, actor, payload.reason)
    # TASK-107: tri-state -- só mexe no override se o campo veio no
    # payload (mesmo que `null`, que limpa o override e volta ao default).
    fields_set = payload.model_fields_set
    if "max_active_missions_override" in fields_set:
        user.max_active_missions_override = payload.max_active_missions_override
    if "max_store_slots_override" in fields_set:
        user.max_store_slots_override = payload.max_store_slots_override
    if "max_daily_searches_override" in fields_set:
        user.max_daily_searches_override = payload.max_daily_searches_override
    _audit(
        session,
        actor,
        "admin.user.updated",
        "user",
        user.id,
        {
            "role": user.role.value,
            "status": user.lifecycle_status.value,
            "max_active_missions_override": user.max_active_missions_override,
            "max_store_slots_override": user.max_store_slots_override,
            "max_daily_searches_override": user.max_daily_searches_override,
            "reason": payload.reason,
        },
    )
    session.commit()
    return UserItem(
        id=user.id,
        display_name=user.display_name,
        username=user.username,
        email=user.email,
        role=user.role,
        lifecycle_status=user.lifecycle_status,
        is_active=user.is_active,
        max_active_missions_override=user.max_active_missions_override,
        max_store_slots_override=user.max_store_slots_override,
        max_daily_searches_override=user.max_daily_searches_override,
        mission_count=session.scalar(
            select(func.count()).select_from(Mission).where(Mission.user_id == user.id)
        )
        or 0,
    )


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: UUID,
    payload: DeleteUserRequest,
    session: Session = Depends(get_session),
    actor: User = Depends(require_admin_web_session),
) -> None:
    user = session.scalar(select(User).where(User.id == user_id).with_for_update())
    if not user or user.lifecycle_status is UserLifecycleStatus.DELETED:
        raise ApiError(
            status_code=404, code="user_not_found", message="Usuário não encontrado."
        )
    if payload.confirmation not in {str(user.id), user.username}:
        raise ApiError(
            status_code=409,
            code="confirmation_required",
            message="Confirmação forte inválida.",
        )
    _revoke_sessions(session, user.id)
    _stop_user_missions(session, user.id, actor, payload.reason)
    session.execute(delete(WebSession).where(WebSession.user_id == user.id))
    session.execute(delete(UserAuthSession).where(UserAuthSession.user_id == user.id))
    for model in (CredentialActionToken, TelegramLinkToken):
        session.execute(delete(model).where(model.user_id == user.id))
    session.execute(delete(UserCredential).where(UserCredential.user_id == user.id))
    user.display_name = f"Usuário removido {str(user.id)[:8]}"
    user.username = None
    user.email = None
    user.telegram_user_id = None
    user.telegram_chat_id = None
    user.favorite_stores = []
    user.preferred_categories = []
    user.pending_intent = None
    user.registration_step = None
    user.role = UserRole.USER
    user.is_active = False
    user.lifecycle_status = UserLifecycleStatus.DELETED
    user.deleted_at = utc_now()
    _audit(
        session,
        actor,
        "admin.user.deleted",
        "user",
        user.id,
        {"reason": payload.reason, "tombstone": True},
    )
    session.commit()


@router.get("/users/{user_id}/missions", response_model=list[MissionItem])
def user_missions(
    user_id: UUID,
    session: Session = Depends(get_session),
    _: User = Depends(require_admin_web_session),
) -> list[MissionItem]:
    return [
        MissionItem(
            id=m.id, title=m.title, status=m.status, state_version=m.state_version
        )
        for m in session.scalars(
            select(Mission)
            .where(Mission.user_id == user_id)
            .order_by(Mission.created_at.desc())
        )
    ]


@router.post("/missions/{mission_id}/command", response_model=MissionItem)
def mission_command(
    mission_id: UUID,
    payload: MissionCommandRequest,
    session: Session = Depends(get_session),
    actor: User = Depends(require_admin_web_session),
) -> MissionItem:
    try:
        transition_mission(
            session,
            mission_id=mission_id,
            command=MissionCommand(payload.command),
            expected_state_version=payload.expected_state_version,
            actor_type="web_admin",
            actor_id=actor.id,
            reason=payload.reason,
        )
    except MissionTransitionError as error:
        raise ApiError(
            status_code=409, code="mission_transition_rejected", message=str(error)
        ) from error
    mission = session.get(Mission, mission_id)
    _audit(
        session,
        actor,
        "admin.mission.command",
        "mission",
        mission_id,
        {"command": payload.command, "reason": payload.reason},
    )
    session.commit()
    return MissionItem(
        id=mission.id,
        title=mission.title,
        status=mission.status,
        state_version=mission.state_version,
    )


@router.patch("/providers/{store_id}", response_model=StoreSummary)
def update_provider(
    store_id: UUID,
    payload: StoreUpdateRequest,
    session: Session = Depends(get_session),
    actor: User = Depends(require_admin_web_session),
) -> StoreSummary:
    store = session.get(Store, store_id)
    if not store:
        raise ApiError(
            status_code=404, code="store_not_found", message="Loja não encontrada."
        )
    store.is_active = payload.enabled
    _audit(
        session,
        actor,
        "admin.provider.updated",
        "store",
        store.id,
        {"enabled": payload.enabled, "reason": payload.reason},
    )
    session.commit()
    return StoreSummary(
        id=store.id,
        code=store.code,
        name=store.name,
        is_active=store.is_active,
        last_run_status=None,
        last_run_at=None,
    )


@router.post("/collections/trigger", status_code=202)
def trigger_collection(
    payload: CollectionTriggerRequest,
    session: Session = Depends(get_session),
    actor: User = Depends(require_admin_web_session),
) -> dict[str, str]:
    if not payload.confirmation:
        raise ApiError(
            status_code=409, code="confirmation_required", message="Confirme o disparo."
        )
    schedule = session.scalar(
        select(MissionSchedule)
        .where(MissionSchedule.mission_id == payload.mission_id)
        .with_for_update()
    )
    if not schedule:
        raise ApiError(
            status_code=404, code="schedule_not_found", message="Missão sem agenda."
        )
    schedule.next_run_at = utc_now()
    schedule.is_enabled = True
    _audit(
        session,
        actor,
        "admin.collection.triggered",
        "mission",
        payload.mission_id,
        {"reason": payload.reason},
    )
    session.commit()
    return {"status": "scheduled"}


@router.post("/services/action", response_model=ServiceSummary)
def service_action(
    payload: ServiceActionRequest,
    session: Session = Depends(get_session),
    actor: User = Depends(require_admin_web_session),
    settings: Settings = Depends(get_settings),
) -> ServiceSummary:
    if not payload.confirmation:
        raise ApiError(
            status_code=409,
            code="confirmation_required",
            message="Confirme a operação.",
        )
    operation = ServiceOperation(payload.operation)
    try:
        state = ControllerServiceOps(settings).execute(payload.service, operation)
    except ServiceOpsUnavailable as error:
        raise ApiError(
            status_code=503,
            code="ops_controller_unavailable",
            message="Controlador operacional indisponível.",
        ) from error
    _audit(
        session,
        actor,
        f"admin.service.{operation.value}",
        "logical_service",
        uuid5(NAMESPACE_URL, payload.service.value),
        {
            "service": payload.service.value,
            "reason": payload.reason,
            "result": state.status,
        },
    )
    session.commit()
    return ServiceSummary(
        service=payload.service, status=state.status, detail=state.detail
    )


@router.get("/api-keys/status")
def api_keys_status(_: User = Depends(require_admin_web_session)) -> dict[str, object]:
    return {
        "enabled": False,
        "authentication_enabled": False,
        "issuance_enabled": False,
        "message": "Em breve / desativado",
    }
