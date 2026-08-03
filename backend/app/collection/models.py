"""Persistência das execuções rastreáveis de coleta."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, desc, func
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.time import utc_now


class CollectionRunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CollectionRun(Base):
    __tablename__ = "collection_runs"
    __table_args__ = (
        CheckConstraint(
            "(status = 'running' AND finished_at IS NULL) OR (status IN ('succeeded', 'failed') AND finished_at IS NOT NULL)",
            name="ck_collection_runs_terminal_finished",
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_collection_runs_time_order",
        ),
        Index(
            "ix_collection_runs_mission_started_at", "mission_id", desc("started_at")
        ),
        Index("ix_collection_runs_store_started_at", "store_id", desc("started_at")),
    )
    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    mission_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    store_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[CollectionRunStatus] = mapped_column(
        Enum(
            CollectionRunStatus,
            name="collection_run_status",
            values_callable=lambda values: [v.value for v in values],
        ),
        nullable=False,
        default=CollectionRunStatus.RUNNING,
        server_default=CollectionRunStatus.RUNNING.value,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
