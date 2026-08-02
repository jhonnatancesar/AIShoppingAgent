"""Modelo persistente e append-only de auditoria."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.time import utc_now


class AuditEntry(Base):
    """Fato auditável imutável, distinto de logs operacionais."""

    __tablename__ = "audit_entries"
    __table_args__ = (
        CheckConstraint(
            "btrim(actor_type) <> ''",
            name="ck_audit_entries_actor_type_not_blank",
        ),
        CheckConstraint(
            "btrim(action) <> ''",
            name="ck_audit_entries_action_not_blank",
        ),
        CheckConstraint(
            "btrim(resource_type) <> ''",
            name="ck_audit_entries_resource_type_not_blank",
        ),
        CheckConstraint(
            "jsonb_typeof(metadata) = 'object'",
            name="ck_audit_entries_metadata_object",
        ),
        Index(
            "ix_audit_entries_resource_history",
            "resource_type",
            "resource_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_audit_entries_actor_history",
            "actor_id",
            "created_at",
            postgresql_where="actor_id IS NOT NULL",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    entry_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
