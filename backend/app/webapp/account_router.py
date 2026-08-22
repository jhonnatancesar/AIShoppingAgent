"""Conta e preferências do próprio USER na aplicação Web (TASK-101)."""

import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.authentication.telegram_linking import (
    TelegramLinkError,
    TelegramLinkRateLimited,
    TelegramLinkStatus,
    issue_telegram_link,
    telegram_link_state,
    unlink_telegram,
)
from app.authorization import AuthorizationDenied, Permission, authorize
from app.core.errors import ApiError
from app.database.dependency import get_session
from app.intent.contracts import MISSION_SOURCE_CODES
from app.users.models import User, UserRole
from app.users.registration import PREFERRED_CATEGORY_CODES
from app.webapp.dependency import require_web_session

router = APIRouter(prefix="/api/v1/account", tags=["webapp-account"])

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_STORE_LABELS = {
    "amazon": "Amazon",
    "kabum": "KaBuM!",
    "magalu": "Magalu",
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
        normalized = value.strip()
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
