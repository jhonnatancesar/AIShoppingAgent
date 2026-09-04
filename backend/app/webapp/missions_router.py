"""Missões da área USER pela aplicação web (TASK-092, item 2 da V1.2).

Só chama a camada de serviço já existente (`app.missions.service`/
`app.missions.query`) -- nenhuma regra de negócio nova, nenhum segundo
sistema de missões. Autenticado por `WebSession`
(`Depends(require_web_session)`, TASK-091): sessão e CSRF (métodos
mutáveis) automáticos por essa mesma dependência, em todo endpoint deste
router. Posse de missão via `app.missions.query.get_mission_for_user`
(primitivo compartilhado, TASK-092) + `deny_resource_unavailable` --
`GET`/`PATCH`/`pause`/`resume`/`cancel` respondem exatamente igual
(`403 mission_access_denied`) tanto para missão inexistente quanto para
missão de outro usuário, nunca distinguindo os dois casos na resposta."""

from decimal import Decimal
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.authorization import (
    AuthorizationDenied,
    Permission,
    authorize,
    deny_resource_unavailable,
)
from app.collection.contracts import OfferCondition
from app.core.config import get_settings
from app.core.errors import ApiError
from app.database.dependency import get_web_async_session
from app.database.time import utc_now
from app.intent.contracts import MISSION_SOURCE_CODES
from app.missions.models import (
    Mission,
    MissionCommand,
    MissionStatus,
    VariantSelectionMode,
)
from app.missions.query import (
    MissionDetail,
    MissionListExtras,
    count_missions_for_user_by_status,
    get_mission_detail_for_user,
    get_mission_for_user,
    list_missions_for_user_by_status,
    load_mission_list_extras,
)
from app.missions.service import (
    InvalidMissionTransitionError,
    MissionCreationError,
    MissionEditConditionError,
    MissionNotFoundError,
    MissionTransitionConditionError,
    MissionVariantSelectionError,
    MissionVersionConflictError,
    create_mission_from_criteria_async,
    edit_mission_criteria,
    set_mission_product_selection_async,
    transition_mission_async,
)
from app.offers.query import MissionOfferLink, list_current_offer_links_for_mission
from app.quotas import QuotaExceededError
from app.users.models import User
from app.webapp.dependency import require_web_session

router = APIRouter(prefix="/api/v1/missions", tags=["missions"])

_ACTOR_TYPE = "web"

_STATUS_FILTER_MAP: dict[str, frozenset[MissionStatus] | None] = {
    "active": frozenset({MissionStatus.ACTIVE}),
    "paused": frozenset({MissionStatus.PAUSED}),
    "cancelled": frozenset({MissionStatus.CANCELLED}),
    "completed": frozenset({MissionStatus.COMPLETED}),
    "expired": frozenset({MissionStatus.EXPIRED}),
    "all": None,
}
# Padrão da tela de listagem (decisão de preflight, 2026-08-22): ativas +
# pausadas -- o mesmo recorte que `list_visible_missions_for_user`
# (Telegram) usa para "missões que importam agora", só sem a ordenação de
# prioridade por status (aqui sempre `created_at` decrescente, estável sob
# paginação por `offset`).
_DEFAULT_LIST_STATUSES = frozenset({MissionStatus.ACTIVE, MissionStatus.PAUSED})
_MAX_LIST_LIMIT = 100


# --- Modelos de resposta -----------------------------------------------


class MissionSummary(BaseModel):
    id: UUID
    title: str
    status: MissionStatus
    state_version: int
    created_at: str
    updated_at: str
    expires_at: str | None


class MissionSourceOut(BaseModel):
    store_code: str
    store_name: str


class MissionListItem(MissionSummary):
    """Item da Lista de Missões da web (Subtask 14) -- estende
    `MissionSummary` com preço-alvo, lojas e ofertas relevantes,
    carregados em lote por `load_mission_list_extras` (nunca N+1). Só o
    endpoint de listagem usa este modelo; `create`/`edit`/`pause`/
    `resume`/`cancel` continuam devolvendo `MissionSummary` puro, sem
    mudança de contrato."""

    target_amount: Decimal | None
    target_currency: str | None
    sources: list[MissionSourceOut]
    relevant_offer_count: int


class MissionListResponse(BaseModel):
    items: list[MissionListItem]
    limit: int
    offset: int
    total: int


class MissionCriteriaOut(BaseModel):
    search_query: str
    model: str | None
    target_amount: Decimal | None
    target_currency: str | None
    request_kind: str
    variant_selection_mode: str


class ProductVariantOut(BaseModel):
    product_id: UUID
    label: str
    attributes: dict[str, str]
    selected: bool


class MissionScheduleOut(BaseModel):
    interval_minutes: int
    next_run_at: str
    last_run_at: str | None
    is_enabled: bool


class MissionTransitionOut(BaseModel):
    from_status: MissionStatus
    to_status: MissionStatus
    command: MissionCommand
    actor_type: str
    reason: str | None
    transitioned_at: str


class MissionOfferLinkOut(BaseModel):
    id: UUID
    title: str
    store_code: str
    store_name: str
    last_seen_at: str
    condition: OfferCondition | None
    """`None` só quando a oferta ainda não tem nenhuma observação de preço
    -- nunca inventado. Ver `app.offers.query.MissionOfferLink.condition`."""


class MissionDetailResponse(BaseModel):
    id: UUID
    title: str
    status: MissionStatus
    state_version: int
    created_at: str
    updated_at: str
    expires_at: str | None
    criteria: MissionCriteriaOut | None
    sources: list[MissionSourceOut]
    schedule: MissionScheduleOut | None
    transitions: list[MissionTransitionOut]
    offers: list[MissionOfferLinkOut]
    available_variants: list[ProductVariantOut]


# --- Modelos de requisição ----------------------------------------------


class CreateMissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_query: Annotated[str, Field(min_length=1, max_length=2000)]
    model: Annotated[str | None, Field(default=None, max_length=64)]
    title: Annotated[str | None, Field(default=None, max_length=200)]
    target_amount: Decimal | None = None
    target_currency: Annotated[str | None, Field(default=None, pattern=r"^[A-Z]{3}$")]
    source_codes: list[str] = Field(default_factory=list)
    variant_product_ids: list[UUID] = Field(default_factory=list, max_length=20)
    select_all_variants: bool = False

    @model_validator(mode="after")
    def _validate(self) -> CreateMissionRequest:
        if (self.target_amount is None) != (self.target_currency is None):
            raise ValueError(
                "target_amount e target_currency devem ser informados juntos."
            )
        if self.target_amount is not None and self.target_amount < 0:
            raise ValueError("target_amount não pode ser negativo.")
        unknown = set(self.source_codes) - MISSION_SOURCE_CODES
        if unknown:
            raise ValueError(
                f"Loja(s) não reconhecida(s): {', '.join(sorted(unknown))}."
            )
        if self.select_all_variants and self.variant_product_ids:
            raise ValueError(
                "Escolha todas as variantes ou informe uma lista específica."
            )
        if len(set(self.variant_product_ids)) != len(self.variant_product_ids):
            raise ValueError("variant_product_ids não pode repetir variantes.")
        return self


class MissionCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_state_version: Annotated[int, Field(ge=0)]
    reason: Annotated[str | None, Field(default=None, min_length=1, max_length=500)]


class SelectMissionVariantsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_state_version: Annotated[int, Field(ge=0)]
    product_ids: list[UUID] = Field(default_factory=list, max_length=20)
    select_all: bool = False

    @model_validator(mode="after")
    def _validate(self) -> SelectMissionVariantsRequest:
        if self.select_all == bool(self.product_ids):
            raise ValueError("Escolha todas as variantes ou informe uma lista.")
        if len(set(self.product_ids)) != len(self.product_ids):
            raise ValueError("product_ids não pode repetir variantes.")
        return self


class EditMissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_state_version: Annotated[int, Field(ge=0)]
    target_amount: Decimal | None = None
    target_currency: Annotated[str | None, Field(default=None, pattern=r"^[A-Z]{3}$")]
    clear_target: bool = False
    source_codes: list[str] | None = None

    @model_validator(mode="after")
    def _validate(self) -> EditMissionRequest:
        if self.clear_target and (
            self.target_amount is not None or self.target_currency is not None
        ):
            raise ValueError(
                "clear_target não pode ser combinado com target_amount/target_currency."
            )
        if not self.clear_target and (self.target_amount is None) != (
            self.target_currency is None
        ):
            raise ValueError(
                "target_amount e target_currency devem ser informados juntos."
            )
        if self.source_codes is not None:
            if not self.source_codes:
                raise ValueError("source_codes não pode ser uma lista vazia.")
            unknown = set(self.source_codes) - MISSION_SOURCE_CODES
            if unknown:
                raise ValueError(
                    f"Loja(s) não reconhecida(s): {', '.join(sorted(unknown))}."
                )
        if (
            self.target_amount is None
            and self.target_currency is None
            and (not self.clear_target and self.source_codes is None)
        ):
            raise ValueError("Informe ao menos um campo para editar (alvo ou lojas).")
        return self


# --- Helpers --------------------------------------------------------------


async def _deny_and_commit(
    session: AsyncSession, error: AuthorizationDenied
) -> NoReturn:
    # Commit explícito: a auditoria de negação que `deny_resource_unavailable`
    # já adicionou seria desfeita pelo rollback-on-exception de
    # `get_web_async_session` ao ver o `ApiError` propagar (mesma armadilha
    # já resolvida no webhook Telegram e em `require_admin_web_session`).
    await session.commit()
    raise ApiError(
        status_code=status.HTTP_403_FORBIDDEN,
        code="mission_access_denied",
        message="Você não tem acesso a esta missão.",
    ) from error


async def _deny_mission_unavailable(
    session: AsyncSession, *, user: User, permission: Permission, mission_id: UUID
) -> NoReturn:
    """Nega acesso sem distinguir "não existe" de "não é sua" -- sempre
    levanta (`deny_resource_unavailable` nunca retorna normalmente)."""
    try:
        deny_resource_unavailable(
            session, user, permission, resource_type="mission", resource_id=mission_id
        )
    except AuthorizationDenied as error:
        await _deny_and_commit(session, error)
    raise AssertionError("unreachable")  # deny_resource_unavailable sempre levanta


async def _require_owned_mission(
    session: AsyncSession, *, mission_id: UUID, user: User, permission: Permission
) -> Mission:
    # `get_mission_for_user` (`app.missions.query`) é o primitivo de posse
    # compartilhado (TASK-092, auditoria de 2026-08-22) -- este router só
    # decide O QUE FAZER quando a posse falha (403 + auditoria via
    # `deny_resource_unavailable`), nunca reimplementa a regra em si.
    mission = await get_mission_for_user(
        session, user_id=user.id, mission_id=mission_id
    )
    if mission is None:
        await _deny_mission_unavailable(
            session, user=user, permission=permission, mission_id=mission_id
        )
    return mission


def _resolve_status_filter(status_param: str | None) -> frozenset[MissionStatus] | None:
    if status_param is None:
        return _DEFAULT_LIST_STATUSES
    if status_param not in _STATUS_FILTER_MAP:
        raise ApiError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="invalid_status_filter",
            message="Filtro de status inválido.",
            details={"allowed": sorted(_STATUS_FILTER_MAP)},
        )
    return _STATUS_FILTER_MAP[status_param]


def _as_summary(mission: Mission) -> MissionSummary:
    return MissionSummary(
        id=mission.id,
        title=mission.title,
        status=mission.status,
        state_version=mission.state_version,
        created_at=mission.created_at.isoformat(),
        updated_at=mission.updated_at.isoformat(),
        expires_at=mission.expires_at.isoformat() if mission.expires_at else None,
    )


def _as_list_item(mission: Mission, extras: MissionListExtras) -> MissionListItem:
    return MissionListItem(
        id=mission.id,
        title=mission.title,
        status=mission.status,
        state_version=mission.state_version,
        created_at=mission.created_at.isoformat(),
        updated_at=mission.updated_at.isoformat(),
        expires_at=mission.expires_at.isoformat() if mission.expires_at else None,
        target_amount=extras.target_amount,
        target_currency=extras.target_currency,
        sources=[
            MissionSourceOut(store_code=store.code, store_name=store.name)
            for _source, store in extras.sources
        ],
        relevant_offer_count=extras.relevant_offer_count,
    )


def _as_detail(
    detail: MissionDetail, offer_links: tuple[MissionOfferLink, ...]
) -> MissionDetailResponse:
    mission = detail.mission
    return MissionDetailResponse(
        id=mission.id,
        title=mission.title,
        status=mission.status,
        state_version=mission.state_version,
        created_at=mission.created_at.isoformat(),
        updated_at=mission.updated_at.isoformat(),
        expires_at=mission.expires_at.isoformat() if mission.expires_at else None,
        criteria=(
            MissionCriteriaOut(
                search_query=detail.criteria.search_query,
                model=detail.criteria.model,
                target_amount=detail.criteria.target_amount,
                target_currency=detail.criteria.target_currency,
                request_kind=detail.criteria.request_kind or "generic_category",
                variant_selection_mode=(
                    detail.criteria.variant_selection_mode
                    or VariantSelectionMode.NOT_REQUIRED
                ).value,
            )
            if detail.criteria
            else None
        ),
        sources=[
            MissionSourceOut(store_code=store.code, store_name=store.name)
            for _source, store in detail.sources
        ],
        schedule=(
            MissionScheduleOut(
                interval_minutes=detail.schedule.interval_minutes,
                next_run_at=detail.schedule.next_run_at.isoformat(),
                last_run_at=(
                    detail.schedule.last_run_at.isoformat()
                    if detail.schedule.last_run_at
                    else None
                ),
                is_enabled=detail.schedule.is_enabled,
            )
            if detail.schedule
            else None
        ),
        transitions=[
            MissionTransitionOut(
                from_status=transition.from_status,
                to_status=transition.to_status,
                command=transition.command,
                actor_type=transition.actor_type,
                reason=transition.reason,
                transitioned_at=transition.transitioned_at.isoformat(),
            )
            for transition in detail.transitions
        ],
        offers=[
            MissionOfferLinkOut(
                id=item.offer.id,
                title=item.product.display_name or item.product.name,
                store_code=item.store.code,
                store_name=item.store.name,
                last_seen_at=item.offer.last_seen_at.isoformat(),
                condition=item.condition,
            )
            for item in offer_links
        ],
        available_variants=[
            ProductVariantOut(
                product_id=product.id,
                label=product.display_name or product.name,
                attributes=product.attributes or {},
                selected=(
                    detail.criteria is not None
                    and (
                        detail.criteria.variant_selection_mode
                        or VariantSelectionMode.NOT_REQUIRED
                    )
                    is VariantSelectionMode.ALL
                )
                or product.id in detail.selected_product_ids,
            )
            for product in detail.available_variants
        ],
    )


def _raise_for_transition_error(error: Exception) -> NoReturn:
    if isinstance(error, MissionNotFoundError):
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="mission_not_found",
            message="Missão não encontrada.",
        ) from error
    if isinstance(error, MissionVersionConflictError):
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="mission_version_conflict",
            message="A missão mudou desde a última vez que você a viu. Atualize a página.",
        ) from error
    if isinstance(error, QuotaExceededError):
        _raise_for_quota_error(error)
    raise ApiError(
        status_code=status.HTTP_409_CONFLICT,
        code="mission_transition_rejected",
        message=str(error) or "Não foi possível concluir a operação nesta missão.",
    ) from error


def _raise_for_quota_error(error: QuotaExceededError) -> NoReturn:
    """TASK-107: nunca só 'limite excedido' -- sempre `limit`/`current`/
    `actions` estruturados para a UI oferecer ações reais."""
    raise ApiError(
        status_code=status.HTTP_409_CONFLICT,
        code=f"quota_{error.kind.value}_exceeded",
        message=str(error),
        details={
            "kind": error.kind.value,
            "limit": error.limit,
            "current": error.current,
            "actions": list(error.actions),
        },
    ) from error


# --- Endpoints --------------------------------------------------------------


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    operation_id="create_mission",
    summary="Criar missão",
    response_description="Missão criada e ativada.",
)
async def create_mission(
    payload: CreateMissionRequest,
    user: User = Depends(require_web_session),
    session: AsyncSession = Depends(get_web_async_session),
) -> MissionSummary:
    try:
        authorize(session, user, Permission.MISSION_CREATE)
    except AuthorizationDenied as error:
        await _deny_and_commit(session, error)

    settings = get_settings()
    try:
        mission, _sources = await create_mission_from_criteria_async(
            session,
            user_id=user.id,
            search_query=payload.search_query,
            model=payload.model,
            title=payload.title,
            target_amount=payload.target_amount,
            target_currency=payload.target_currency,
            source_codes=tuple(payload.source_codes),
            requested_at=utc_now(),
            schedule_interval_minutes=settings.collection_schedule_interval_minutes,
            schedule_stagger_seconds=settings.collection_schedule_stagger_seconds,
            actor_type=_ACTOR_TYPE,
        )
        if payload.select_all_variants or payload.variant_product_ids:
            await set_mission_product_selection_async(
                session,
                user_id=user.id,
                mission_id=mission.id,
                expected_state_version=mission.state_version,
                product_ids=tuple(payload.variant_product_ids),
                select_all=payload.select_all_variants,
                selected_at=utc_now(),
                allow_uncollected_family_products=True,
            )
    except MissionCreationError as error:
        # Lojas da V1 não semeadas no banco -- falha operacional, nunca
        # causada pelo usuário (docstring de `MissionCreationError`).
        raise ApiError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="mission_creation_failed",
            message="Não foi possível criar a missão. Tente novamente mais tarde.",
        ) from error
    except QuotaExceededError as error:
        # TASK-107: criação já ativa a missão (transition_mission_async
        # interno) -- cota estourada aqui tem a mesma resposta estruturada
        # de qualquer outra transição.
        _raise_for_quota_error(error)
    return _as_summary(mission)


@router.put(
    "/{mission_id}/variants",
    operation_id="select_mission_variants",
    summary="Selecionar variantes de uma missão por família",
)
async def select_mission_variants(
    mission_id: UUID,
    payload: SelectMissionVariantsRequest,
    user: User = Depends(require_web_session),
    session: AsyncSession = Depends(get_web_async_session),
) -> MissionSummary:
    try:
        authorize(
            session,
            user,
            Permission.MISSION_EDIT,
            resource_type="mission",
            resource_id=mission_id,
        )
    except AuthorizationDenied as error:
        await _deny_and_commit(session, error)
    mission = await _require_owned_mission(
        session,
        mission_id=mission_id,
        user=user,
        permission=Permission.MISSION_EDIT,
    )
    try:
        await set_mission_product_selection_async(
            session,
            user_id=user.id,
            mission_id=mission.id,
            expected_state_version=payload.expected_state_version,
            product_ids=tuple(payload.product_ids),
            select_all=payload.select_all,
            selected_at=utc_now(),
        )
    except MissionVersionConflictError as error:
        _raise_for_transition_error(error)
    except MissionVariantSelectionError as error:
        raise ApiError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="mission_variant_selection_invalid",
            message=str(error),
        ) from error
    except MissionVariantSelectionError as error:
        raise ApiError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="mission_variant_selection_invalid",
            message=str(error),
        ) from error
    return _as_summary(mission)


@router.get(
    "",
    operation_id="list_missions",
    summary="Listar missões do usuário",
    response_description="Página de missões, mais recente primeiro.",
)
async def list_missions(
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIST_LIMIT)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    user: User = Depends(require_web_session),
    session: AsyncSession = Depends(get_web_async_session),
) -> MissionListResponse:
    try:
        authorize(session, user, Permission.MISSION_READ)
    except AuthorizationDenied as error:
        await _deny_and_commit(session, error)

    statuses = _resolve_status_filter(status_filter)
    missions = await list_missions_for_user_by_status(
        session, user_id=user.id, statuses=statuses, limit=limit, offset=offset
    )
    total = await count_missions_for_user_by_status(
        session, user_id=user.id, statuses=statuses
    )
    extras_by_mission = await load_mission_list_extras(
        session, mission_ids=[mission.id for mission in missions]
    )
    return MissionListResponse(
        items=[
            _as_list_item(mission, extras_by_mission[mission.id])
            for mission in missions
        ],
        limit=limit,
        offset=offset,
        total=total,
    )


@router.get(
    "/{mission_id}",
    operation_id="get_mission",
    summary="Consultar detalhe de uma missão",
    response_description="Missão com critério, fontes, agenda e histórico.",
)
async def get_mission(
    mission_id: UUID,
    user: User = Depends(require_web_session),
    session: AsyncSession = Depends(get_web_async_session),
) -> MissionDetailResponse:
    try:
        authorize(session, user, Permission.MISSION_READ)
    except AuthorizationDenied as error:
        await _deny_and_commit(session, error)

    detail = await get_mission_detail_for_user(
        session, user_id=user.id, mission_id=mission_id
    )
    if detail is None:
        await _deny_mission_unavailable(
            session,
            user=user,
            permission=Permission.MISSION_READ,
            mission_id=mission_id,
        )
    offer_links = await list_current_offer_links_for_mission(
        session, mission_id=mission_id, user_id=user.id
    )
    return _as_detail(detail, offer_links)


@router.patch(
    "/{mission_id}",
    operation_id="edit_mission",
    summary="Editar critério de uma missão pausada",
    response_description="Missão com critério atualizado.",
)
async def edit_mission(
    mission_id: UUID,
    payload: EditMissionRequest,
    user: User = Depends(require_web_session),
    session: AsyncSession = Depends(get_web_async_session),
) -> MissionSummary:
    try:
        authorize(session, user, Permission.MISSION_EDIT)
    except AuthorizationDenied as error:
        await _deny_and_commit(session, error)

    await _require_owned_mission(
        session, mission_id=mission_id, user=user, permission=Permission.MISSION_EDIT
    )

    if payload.clear_target:
        target_update: tuple[Decimal | None, str | None] | None = (None, None)
    elif payload.target_amount is not None:
        target_update = (payload.target_amount, payload.target_currency)
    else:
        target_update = None

    try:
        mission, _sources = await edit_mission_criteria(
            session,
            mission_id=mission_id,
            expected_state_version=payload.expected_state_version,
            target_update=target_update,
            source_codes=payload.source_codes,
            edited_at=utc_now(),
        )
    except (
        MissionNotFoundError,
        MissionVersionConflictError,
        MissionEditConditionError,
    ) as error:
        _raise_for_transition_error(error)
    return _as_summary(mission)


async def _run_command(
    *,
    mission_id: UUID,
    command: MissionCommand,
    payload: MissionCommandRequest,
    user: User,
    session: AsyncSession,
) -> MissionSummary:
    try:
        authorize(session, user, Permission.MISSION_TRANSITION)
    except AuthorizationDenied as error:
        await _deny_and_commit(session, error)

    await _require_owned_mission(
        session,
        mission_id=mission_id,
        user=user,
        permission=Permission.MISSION_TRANSITION,
    )

    try:
        await transition_mission_async(
            session,
            mission_id=mission_id,
            command=command,
            expected_state_version=payload.expected_state_version,
            actor_type=_ACTOR_TYPE,
            actor_id=user.id,
            reason=payload.reason,
            transitioned_at=utc_now(),
        )
    except (
        MissionNotFoundError,
        MissionVersionConflictError,
        InvalidMissionTransitionError,
        MissionTransitionConditionError,
        QuotaExceededError,
    ) as error:
        _raise_for_transition_error(error)

    mission = await session.get(Mission, mission_id)
    assert mission is not None  # acabou de ser lido/atualizado na mesma transação
    return _as_summary(mission)


@router.post(
    "/{mission_id}/pause",
    operation_id="pause_mission",
    summary="Pausar missão",
    response_description="Missão pausada.",
)
async def pause_mission(
    mission_id: UUID,
    payload: MissionCommandRequest,
    user: User = Depends(require_web_session),
    session: AsyncSession = Depends(get_web_async_session),
) -> MissionSummary:
    return await _run_command(
        mission_id=mission_id,
        command=MissionCommand.PAUSE,
        payload=payload,
        user=user,
        session=session,
    )


@router.post(
    "/{mission_id}/resume",
    operation_id="resume_mission",
    summary="Retomar missão",
    response_description="Missão retomada.",
)
async def resume_mission(
    mission_id: UUID,
    payload: MissionCommandRequest,
    user: User = Depends(require_web_session),
    session: AsyncSession = Depends(get_web_async_session),
) -> MissionSummary:
    return await _run_command(
        mission_id=mission_id,
        command=MissionCommand.RESUME,
        payload=payload,
        user=user,
        session=session,
    )


@router.post(
    "/{mission_id}/cancel",
    operation_id="cancel_mission",
    summary="Cancelar missão",
    response_description="Missão cancelada (transição terminal).",
)
async def cancel_mission(
    mission_id: UUID,
    payload: MissionCommandRequest,
    user: User = Depends(require_web_session),
    session: AsyncSession = Depends(get_web_async_session),
) -> MissionSummary:
    return await _run_command(
        mission_id=mission_id,
        command=MissionCommand.CANCEL,
        payload=payload,
        user=user,
        session=session,
    )
