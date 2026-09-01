"""Registro compartilhado de bug/suporte/sugestão de loja (Web + Telegram).

`kind=STORE_SUGGESTION` é o único caso em que `message` pode ficar vazio
(comentário é opcional nesse fluxo) -- `store_name`/`store_url` só fazem
sentido para esse `kind`. A validação de qual campo é obrigatório para
cada `kind` fica na camada de aplicação (`app.feedback.service`,
Telegram e Web), não em CHECK constraints -- mantém a tabela simples e
evita acoplar regra de fluxo à definição do schema.
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.time import utc_now


class FeedbackKind(StrEnum):
    BUG = "bug"
    SUPPORT = "support"
    STORE_SUGGESTION = "store_suggestion"


class FeedbackChannel(StrEnum):
    WEB = "web"
    TELEGRAM = "telegram"


class FeedbackStatus(StrEnum):
    NEW = "new"
    REVIEWED = "reviewed"
    CLOSED = "closed"


class UserFeedback(Base):
    """Um envio de bug/suporte/sugestão de loja, de qualquer canal."""

    __tablename__ = "user_feedback"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('bug', 'support', 'store_suggestion')",
            name="ck_user_feedback_kind_values",
        ),
        CheckConstraint(
            "channel IN ('web', 'telegram')",
            name="ck_user_feedback_channel_values",
        ),
        CheckConstraint(
            "status IN ('new', 'reviewed', 'closed')",
            name="ck_user_feedback_status_values",
        ),
        Index("ix_user_feedback_status_created_at", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    kind: Mapped[FeedbackKind] = mapped_column(
        Enum(
            FeedbackKind,
            name="user_feedback_kind_values",
            native_enum=False,
            create_constraint=False,
            values_callable=lambda values: [value.value for value in values],
            length=32,
        ),
        nullable=False,
    )
    channel: Mapped[FeedbackChannel] = mapped_column(
        Enum(
            FeedbackChannel,
            name="user_feedback_channel_values",
            native_enum=False,
            create_constraint=False,
            values_callable=lambda values: [value.value for value in values],
            length=16,
        ),
        nullable=False,
    )
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    store_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    store_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[FeedbackStatus] = mapped_column(
        Enum(
            FeedbackStatus,
            name="user_feedback_status_values",
            native_enum=False,
            create_constraint=False,
            values_callable=lambda values: [value.value for value in values],
            length=16,
        ),
        nullable=False,
        default=FeedbackStatus.NEW,
        server_default=FeedbackStatus.NEW.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
