"""Processo contínuo do consumidor de notificações Telegram."""

import argparse
import asyncio
import logging

from app.core.config import Settings
from app.core.logging import configure_logging
from app.database.session import create_database_engine, create_session_factory
from app.telegram.notifications import process_telegram_notifications

logger = logging.getLogger("app.telegram.worker")


async def run_worker(
    settings: Settings,
    *,
    once: bool = False,
    poll_seconds: float | None = None,
    batch_size: int | None = None,
) -> None:
    """Processa lotes continuamente ou exatamente um lote com ``once``."""
    if settings.telegram_bot_token is None:
        raise RuntimeError("AISHOPPING_TELEGRAM_BOT_TOKEN is required")
    interval = (
        poll_seconds
        if poll_seconds is not None
        else settings.telegram_notification_poll_seconds
    )
    limit = (
        batch_size
        if batch_size is not None
        else settings.telegram_notification_batch_size
    )
    if interval <= 0:
        raise ValueError("poll_seconds must be positive")
    if not 1 <= limit <= 1000:
        raise ValueError("batch_size must be between 1 and 1000")
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        while True:
            with session_factory.begin() as session:
                result = await process_telegram_notifications(
                    session,
                    bot_token=settings.telegram_bot_token,
                    limit=limit,
                )
            logger.log(
                logging.INFO if result.claimed else logging.DEBUG,
                "telegram_notification_batch",
                extra={
                    "notification_claimed": result.claimed,
                    "notification_succeeded": result.succeeded,
                    "notification_failed": result.failed,
                    "notification_skipped": result.skipped,
                },
            )
            if once:
                return
            await asyncio.sleep(interval)
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float)
    parser.add_argument("--batch-size", type=int)
    arguments = parser.parse_args()
    settings = Settings()
    configure_logging(settings.log_level)
    asyncio.run(
        run_worker(
            settings,
            once=arguments.once,
            poll_seconds=arguments.poll_seconds,
            batch_size=arguments.batch_size,
        )
    )


if __name__ == "__main__":
    main()
