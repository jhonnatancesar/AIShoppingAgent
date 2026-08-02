"""Modelo persistente de missões e seu vocabulário de estados."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    desc,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.time import utc_now


class MissionStatus(StrEnum):
    """Estados persistidos definidos pelo ciclo de vida da TASK-018."""

    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class Mission(Base):
    """Intenção de compra e fonte de verdade para seu estado atual."""

    __tablename__ = "missions"
    __table_args__ = (
        CheckConstraint("btrim(title) <> ''", name="ck_missions_title_not_blank"),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at",
            name="ck_missions_expiration_after_creation",
        ),
        CheckConstraint(
            "state_version >= 0",
            name="ck_missions_state_version_non_negative",
        ),
        Index(
            "ix_missions_user_status_created_at",
            "user_id",
            "status",
            desc("created_at"),
        ),
        Index(
            "ix_missions_pending_expiration",
            "status",
            "expires_at",
            postgresql_where=(
                "expires_at IS NOT NULL AND "
                "status NOT IN ('completed', 'cancelled', 'expired')"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[MissionStatus] = mapped_column(
        Enum(
            MissionStatus,
            name="mission_status",
            values_callable=lambda statuses: [status.value for status in statuses],
            validate_strings=True,
        ),
        nullable=False,
        default=MissionStatus.DRAFT,
        server_default=MissionStatus.DRAFT.value,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    state_version: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
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
