"""Conta e preferências do próprio USER na aplicação Web (TASK-101)."""

import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.authentication.delivery import email_delivery_available
from app.authentication.passwords import PasswordPolicyError
from app.authentication.service import AuthenticationError, change_password_with_current
from app.authentication.telegram_linking import (
    TelegramLinkError,
    TelegramLinkRateLimited,
    TelegramLinkStatus,
    issue_telegram_link,
    telegram_link_state,
    unlink_telegram,
)
from app.authorization import AuthorizationDenied, Permission, authorize
from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.database.dependency import get_session
from app.database.time import utc_now
from app.intent.contracts import MISSION_SOURCE_CODES
from app.quotas import get_quota_usage, next_daily_reset_at, resolve_quota_limits
from app.users.models import User, UserRole
from app.users.registration import PREFERRED_CATEGORY_CODES
from app.webapp.dependency import require_web_session
from app.webapp.router import set_csrf_cookie, set_session_cookie

router = APIRouter(prefix="/api/v1/account", tags=["webapp-account"])

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_STORE_LABELS = {
    "amazon": "Amazon",
    "kabum": "KaBuM!",
    "magalu": "Magalu",
    "mercadolivre": "Mercado Livre",
    "pichau": "Pichau",
    "terabyte": "Terabyte",
}


class AccountOptionOut(BaseModel):
    code: str
    label: str


class AccountProfileOut(BaseModel):
    id: UUID
    display_name: str
    username: str | None
    email: str | None
    email_verified_at: str | None
    email_verification_available: bool
    role: UserRole
    telegram_linked: bool
    telegram_link_status: TelegramLinkStatus
    telegram_link_expires_at: str | None
    created_at: str
    favorite_stores: list[str]
    preferred_categories: list[str]
    notify_price_decreases: bool
    notify_target_reached: bool
    available_stores: list[AccountOptionOut]
    available_categories: list[AccountOptionOut]


StoreCode = Annotated[str, Field(min_length=1, max_length=32)]
CategoryCode = Annotated[str, Field(min_length=1, max_length=64)]


class AccountProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: Annotated[str, Field(min_length=1, max_length=160)]
    email: Annotated[str, Field(max_length=254)] | None = None
    favorite_stores: Annotated[list[StoreCode], Field(max_length=32)]
    preferred_categories: Annotated[list[CategoryCode], Field(max_length=64)]

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("nome não pode ficar vazio")
        return normalized

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip().lower()
        if not _EMAIL_PATTERN.match(normalized):
            raise ValueError("e-mail inválido")
        return normalized

    @field_validator("favorite_stores")
    @classmethod
    def validate_stores(cls, value: list[str]) -> list[str]:
        normalized = sorted(set(value))
        if not set(normalized).issubset(MISSION_SOURCE_CODES):
            raise ValueError("loja preferida inválida")
        return normalized

    @field_validator("preferred_categories")
    @classmethod
    def validate_categories(cls, value: list[str]) -> list[str]:
        normalized = sorted(set(value))
        if not set(normalized).issubset(PREFERRED_CATEGORY_CODES):
            raise ValueError("categoria preferida inválida")
        return normalized


class NotificationPreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notify_price_decreases: bool
    notify_target_reached: bool


class QuotaItemOut(BaseModel):
    current: int
    limit: int
    near_limit: bool


class AccountQuotaOut(BaseModel):
    active_missions: QuotaItemOut
    store_slots: QuotaItemOut
    daily_searches: QuotaItemOut
    daily_searches_reset_at: str


class TelegramLinkChallengeOut(BaseModel):
    command: str
    expires_at: str
    account: AccountProfileOut


def _authorize_account(session: Session, user: User, permission: Permission) -> None:
    try:
        authorize(session, user, permission, resource_type="user", resource_id=user.id)
    except AuthorizationDenied as error:
        session.commit()
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="account_access_denied",
            message="Você não tem acesso a esta conta.",
        ) from error


def _label(code: str) -> str:
    return code.replace("_", " ").title()


def _as_account(
    session: Session,
    user: User,
    *,
    link_status: TelegramLinkStatus | None = None,
    link_expires_at: str | None = None,
) -> AccountProfileOut:
    if link_status is None:
        resolved_status, resolved_expires_at = telegram_link_state(session, user=user)
        link_status = resolved_status
        link_expires_at = (
            resolved_expires_at.isoformat() if resolved_expires_at is not None else None
        )
    return AccountProfileOut(
        id=user.id,
        display_name=user.display_name,
        username=user.username,
        email=user.email,
        email_verified_at=(
            user.email_verified_at.isoformat() if user.email_verified_at else None
        ),
        email_verification_available=email_delivery_available(get_settings()),
        role=user.role,
        telegram_linked=user.telegram_user_id is not None,
        telegram_link_status=link_status,
        telegram_link_expires_at=link_expires_at,
        created_at=user.created_at.isoformat(),
        favorite_stores=list(user.favorite_stores or []),
        preferred_categories=list(user.preferred_categories or []),
        notify_price_decreases=user.notify_price_decreases,
        notify_target_reached=user.notify_target_reached,
        available_stores=[
            AccountOptionOut(code=code, label=_STORE_LABELS.get(code, _label(code)))
            for code in sorted(MISSION_SOURCE_CODES)
        ],
        available_categories=[
            AccountOptionOut(code=code, label=_label(code))
            for code in sorted(PREFERRED_CATEGORY_CODES)
        ],
    )


@router.get("", operation_id="get_user_account", summary="Consultar a própria conta")
def get_account(
    user: User = Depends(require_web_session),
    session: Session = Depends(get_session),
) -> AccountProfileOut:
    _authorize_account(session, user, Permission.PROFILE_MANAGE)
    return _as_account(session, user)


@router.get(
    "/quota",
    operation_id="get_user_account_quota",
    summary="Consultar cota de capacidade da própria conta",
)
def get_account_quota(
    user: User = Depends(require_web_session),
    session: Session = Depends(get_session),
) -> AccountQuotaOut:
    """TASK-107: sempre visível, mesmo longe do limite -- a UI decide
    quando destacar o aviso usando `near_limit`."""
    _authorize_account(session, user, Permission.PROFILE_MANAGE)
    now = utc_now()
    settings = get_settings()
    limits = resolve_quota_limits(user, settings)
    usage = get_quota_usage(session, user.id, now=now)
    threshold = settings.quota_warning_threshold

    def _item(current: int, limit: int) -> QuotaItemOut:
        near_limit = limit > 0 and (current / limit) >= threshold
        return QuotaItemOut(current=current, limit=limit, near_limit=near_limit)

    return AccountQuotaOut(
        active_missions=_item(usage.active_missions, limits.max_active_missions),
        store_slots=_item(usage.store_slots, limits.max_store_slots),
        daily_searches=_item(usage.daily_searches, limits.max_daily_searches),
        daily_searches_reset_at=next_daily_reset_at(now).isoformat(),
    )


@router.put("/profile", operation_id="update_user_account_profile")
def update_account_profile(
    payload: AccountProfileUpdate,
    user: User = Depends(require_web_session),
    session: Session = Depends(get_session),
) -> AccountProfileOut:
    _authorize_account(session, user, Permission.PROFILE_MANAGE)
    user.display_name = payload.display_name
    user.email = payload.email
    user.favorite_stores = payload.favorite_stores
    user.preferred_categories = payload.preferred_categories
    try:
        session.flush()
    except IntegrityError as error:
        # Subtask 9 (validação de segurança): `uq_users_email` só passou a
        # existir nesta subtask -- antes, dois usuários podiam ter o mesmo
        # e-mail em silêncio; agora essa colisão precisa virar 409, nunca
        # um 500 não tratado subindo do flush.
        raise ApiError(
            status_code=409,
            code="email_taken",
            message="Esse e-mail já está em uso por outra conta.",
        ) from error
    return _as_account(session, user)


@router.put(
    "/notification-preferences",
    operation_id="update_user_notification_preferences",
)
def update_notification_preferences(
    payload: NotificationPreferencesUpdate,
    user: User = Depends(require_web_session),
    session: Session = Depends(get_session),
) -> AccountProfileOut:
    _authorize_account(session, user, Permission.NOTIFICATION_PREFERENCES_MANAGE)
    user.notify_price_decreases = payload.notify_price_decreases
    user.notify_target_reached = payload.notify_target_reached
    return _as_account(session, user)


@router.post(
    "/telegram-link",
    operation_id="start_user_telegram_link",
    summary="Iniciar vínculo opcional com Telegram",
)
def start_telegram_link(
    user: User = Depends(require_web_session),
    session: Session = Depends(get_session),
) -> TelegramLinkChallengeOut:
    _authorize_account(session, user, Permission.PROFILE_MANAGE)
    try:
        challenge = issue_telegram_link(session, user=user)
    except TelegramLinkRateLimited as error:
        raise ApiError(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code="telegram_link_rate_limited",
            message="Muitas tentativas. Tente novamente mais tarde.",
        ) from error
    except TelegramLinkError as error:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="telegram_link_unavailable",
            message="Não foi possível iniciar a vinculação.",
        ) from error
    return TelegramLinkChallengeOut(
        command=challenge.command,
        expires_at=challenge.expires_at.isoformat(),
        account=_as_account(
            session,
            user,
            link_status=TelegramLinkStatus.PENDING,
            link_expires_at=challenge.expires_at.isoformat(),
        ),
    )


@router.delete(
    "/telegram-link",
    operation_id="delete_user_telegram_link",
    summary="Desvincular Telegram sem excluir a conta",
)
def delete_telegram_link(
    user: User = Depends(require_web_session),
    session: Session = Depends(get_session),
) -> AccountProfileOut:
    _authorize_account(session, user, Permission.PROFILE_MANAGE)
    unlink_telegram(session, user=user)
    return _as_account(
        session,
        user,
        link_status=TelegramLinkStatus.NOT_LINKED,
        link_expires_at=None,
    )


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: SecretStr = Field(min_length=1, max_length=128)
    new_password: SecretStr = Field(min_length=1, max_length=128)
    new_password_confirmation: SecretStr = Field(min_length=1, max_length=128)


@router.put(
    "/password",
    operation_id="change_user_account_password",
    summary="Alterar a própria senha (autenticado)",
)
def change_account_password(
    payload: ChangePasswordRequest,
    response: Response,
    user: User = Depends(require_web_session),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AccountProfileOut:
    """Subtask 9: verifica a senha atual antes de aceitar a nova
    (`change_password_with_current`); a sessão Web atual é rotacionada
    (revogada + reemitida), então o cookie precisa ser trocado aqui --
    nunca deixa o navegador com um cookie que a troca de senha acabou de
    invalidar."""
    _authorize_account(session, user, Permission.PROFILE_MANAGE)
    if (
        payload.new_password.get_secret_value()
        != payload.new_password_confirmation.get_secret_value()
    ):
        raise ApiError(
            status_code=422,
            code="password_confirmation_mismatch",
            message="As senhas informadas não conferem.",
        )
    try:
        raw_token = change_password_with_current(
            session,
            user=user,
            current_password=payload.current_password.get_secret_value(),
            new_password=payload.new_password.get_secret_value(),
        )
    except AuthenticationError as error:
        raise ApiError(
            status_code=422,
            code="current_password_invalid",
            message="A senha atual informada está incorreta.",
        ) from error
    except PasswordPolicyError as error:
        raise ApiError(status_code=422, code="weak_password", message=str(error)) from error
    set_session_cookie(response, raw_token=raw_token, settings=settings)
    set_csrf_cookie(response, settings=settings)
    return _as_account(session, user)
