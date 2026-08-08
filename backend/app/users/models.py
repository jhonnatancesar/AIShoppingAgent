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


class User(Base):
    """Identidade interna proprietária de recursos do sistema."""

    __tablename__ = "users"
    __table_args__ = (
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
            create_constraint=True,
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
    username: Mapped[str | None] = mapped_column(
        String(32),
        unique=True,
        nullable=True,
    )
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
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
