"""Modelo persistente de usuários."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.time import utc_now


class UserRole(StrEnum):
    """Perfis internos previstos para a V1."""

    USER = "USER"
    ADMIN = "ADMIN"
    DEV = "DEV"


class UserLifecycleStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    BLOCKED = "blocked"
    DELETED = "deleted"


class User(Base):
    """Identidade interna proprietária de recursos do sistema."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('USER', 'ADMIN', 'DEV')",
            name="user_role_values",
        ),
        CheckConstraint(
            "lifecycle_status IN ('active', 'inactive', 'blocked', 'deleted')",
            name="user_lifecycle_status_values",
        ),
        CheckConstraint(
            "btrim(display_name) <> ''",
            name="ck_users_display_name_not_blank",
        ),
        CheckConstraint(
            "username IS NULL OR btrim(username) <> ''",
            name="ck_users_username_not_blank",
        ),
        CheckConstraint(
            "email IS NULL OR btrim(email) <> ''",
            name="ck_users_email_not_blank",
        ),
        CheckConstraint(
            "telegram_chat_id IS NULL OR "
            "(telegram_user_id IS NOT NULL AND telegram_chat_id = telegram_user_id)",
            name="ck_users_telegram_private_chat",
        ),
        CheckConstraint(
            "max_active_missions_override IS NULL OR max_active_missions_override > 0",
            name="ck_users_max_active_missions_override_positive",
        ),
        CheckConstraint(
            "max_store_slots_override IS NULL OR max_store_slots_override > 0",
            name="ck_users_max_store_slots_override_positive",
        ),
        CheckConstraint(
            "max_daily_searches_override IS NULL OR max_daily_searches_override > 0",
            name="ck_users_max_daily_searches_override_positive",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            name="user_role_values",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            length=16,
        ),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    lifecycle_status: Mapped[UserLifecycleStatus] = mapped_column(
        Enum(
            UserLifecycleStatus,
            name="user_lifecycle_status_values",
            values_callable=lambda values: [value.value for value in values],
            native_enum=False,
            create_constraint=False,
            length=16,
        ),
        nullable=False,
        default=UserLifecycleStatus.ACTIVE,
        server_default=UserLifecycleStatus.ACTIVE.value,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    telegram_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        unique=True,
        nullable=True,
    )
    telegram_chat_id: Mapped[int | None] = mapped_column(
        BigInteger,
        unique=True,
        nullable=True,
    )
    notify_price_decreases: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    notify_target_reached: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    username: Mapped[str | None] = mapped_column(
        String(32),
        unique=True,
        nullable=True,
    )
    email: Mapped[str | None] = mapped_column(String(254), unique=True, nullable=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """Subtask 9: só é preenchido por uma confirmação real de
    `VerificationChallenge(purpose=email_verification, channel=email)` --
    nunca no cadastro, nunca por coincidência com o e-mail do Telegram,
    nunca por `password_reset`/`password_change` em qualquer canal."""
    favorite_stores: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)),
        nullable=False,
        default=list,
        server_default="{}",
    )
    preferred_categories: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)),
        nullable=False,
        default=list,
        server_default="{}",
    )
    registration_step: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pending_intent: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # TASK-107: override de cota por usuário, definido pelo DEV/ADMIN.
    # `NULL` (padrão) usa o default do sistema (`Settings.default_max_*`) --
    # nunca um plano/tier novo, só um valor pontual por usuário (`DEC-094`).
    max_active_missions_override: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    max_store_slots_override: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    max_daily_searches_override: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )
