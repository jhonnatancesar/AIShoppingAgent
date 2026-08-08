"""Valida a TASK-046 contra PostgreSQL real sem deixar dados."""

from datetime import UTC, datetime
from random import SystemRandom

from app.core.config import Settings
from app.database.session import create_database_engine, create_session_factory
from app.telegram.authentication import (
    TelegramAuthenticationFailure,
    authenticate_telegram_user,
)
from app.telegram.contracts import TelegramChatType, TelegramMessage
from app.users.models import User
from sqlalchemy import func, select


def _message(
    telegram_id: int,
    *,
    chat_type: TelegramChatType = TelegramChatType.PRIVATE,
    chat_id: int | None = None,
) -> TelegramMessage:
    return TelegramMessage(
        chat_id=telegram_id if chat_id is None else chat_id,
        chat_type=chat_type,
        user_id=telegram_id,
        text="/preferencias",
        received_at=datetime.now(UTC),
    )


def validate() -> None:
    settings = Settings()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    session = session_factory()
    transaction = session.begin()
    telegram_id = SystemRandom().randrange(8_000_000_000, 8_999_999_999)
    try:
        before = session.scalar(select(func.count()).select_from(User))
        created = authenticate_telegram_user(
            session,
            message=_message(telegram_id),
            display_name="Validação TASK-046",
        )
        if not created.authenticated or created.user is None:
            raise RuntimeError("first private contact was not authenticated")

        existing = authenticate_telegram_user(
            session,
            message=_message(telegram_id),
            display_name="Nome não confiável posterior",
        )
        if existing.user is not created.user:
            raise RuntimeError("existing identity was not resolved idempotently")

        created.user.is_active = False
        session.flush()
        inactive = authenticate_telegram_user(
            session,
            message=_message(telegram_id),
            display_name="Validação TASK-046",
        )
        if inactive.failure is not TelegramAuthenticationFailure.INACTIVE_USER:
            raise RuntimeError("inactive user was not rejected")

        after_identity = session.scalar(select(func.count()).select_from(User))
        group = authenticate_telegram_user(
            session,
            message=_message(telegram_id + 1, chat_type=TelegramChatType.GROUP),
            display_name="Não deve existir",
        )
        mismatch = authenticate_telegram_user(
            session,
            message=_message(telegram_id + 2, chat_id=telegram_id + 3),
            display_name="Não deve existir",
        )
        after_rejections = session.scalar(select(func.count()).select_from(User))

        if group.failure is not TelegramAuthenticationFailure.NON_PRIVATE_CHAT:
            raise RuntimeError("group was not rejected")
        if mismatch.failure is not TelegramAuthenticationFailure.IDENTITY_MISMATCH:
            raise RuntimeError("identity mismatch was not rejected")
        if after_identity != before + 1 or after_rejections != after_identity:
            raise RuntimeError("rejected identities changed PostgreSQL state")

        print(
            "TASK-046 PostgreSQL validation passed: "
            "first_contact=created, existing=idempotent, inactive=rejected, "
            "group=no_op, identity_mismatch=no_op"
        )
    finally:
        transaction.rollback()
        session.close()
        engine.dispose()


if __name__ == "__main__":
    validate()
