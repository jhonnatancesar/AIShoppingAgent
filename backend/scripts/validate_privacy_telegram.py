"""Envia uma única resposta fixa de /privacidade pela Bot API real."""

import asyncio

from app.core.config import Settings
from app.database.session import create_database_engine, create_session_factory
from app.privacy.notice import privacy_notice
from app.telegram.bot_api import send_message
from app.users.models import User, UserRole
from sqlalchemy import select


def main() -> None:
    settings = Settings(observability_enabled=False)
    if settings.telegram_bot_token is None:
        raise SystemExit("Telegram bot token is not configured")
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        with session_factory() as session:
            destination = session.scalar(
                select(User.telegram_chat_id).where(
                    User.role == UserRole.DEV,
                    User.is_active.is_(True),
                    User.telegram_chat_id.is_not(None),
                )
            )
        if destination is None:
            raise SystemExit("No active DEV private destination is configured")
        asyncio.run(
            send_message(
                destination,
                privacy_notice(),
                bot_token=settings.telegram_bot_token,
                timeout_seconds=settings.external_http_timeout_seconds,
                retry_after_cap_seconds=settings.retry_after_cap_seconds,
                circuit_failure_threshold=settings.circuit_failure_threshold,
                circuit_open_seconds=settings.circuit_open_seconds,
            )
        )
        print("Telegram privacy notice delivered without personal content")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
