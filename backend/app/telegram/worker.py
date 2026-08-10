"""Processo contínuo do consumidor de notificações Telegram."""

import argparse
import asyncio
import logging
from collections.abc import Awaitable, Callable
from time import perf_counter

from opentelemetry import trace
from opentelemetry.trace import SpanKind
from sqlalchemy.orm import Session, sessionmaker

from app.authentication.notifications import (
    publish_due_authentication_notifications,
)
from app.core.config import Settings
from app.core.logging import configure_logging
from app.database.session import create_database_engine, create_session_factory
from app.observability.metrics import (
    mark_worker_started,
    observe_worker_batch,
    observe_worker_failure,
    start_worker_metrics_server,
)
from app.observability.tracing import configure_tracing
from app.telegram.notifications import (
    TelegramNotificationBatch,
    process_telegram_authentication_notifications,
    process_telegram_notifications,
    process_telegram_prelist_notifications,
)

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
        consecutive_failures = 0
        while True:
            started_at = perf_counter()
            try:
                tracer = trace.get_tracer("app.telegram.worker")
                with tracer.start_as_current_span(
                    "telegram notification batch",
                    kind=SpanKind.CONSUMER,
                    attributes={"worker.name": "telegram_notifier"},
                    record_exception=False,
                    set_status_on_exception=False,
                ):
                    with session_factory.begin() as session:
                        publish_due_authentication_notifications(session, limit=limit)
                    price_result = await _process_batch(
                        session_factory,
                        settings=settings,
                        limit=limit,
                        processor=process_telegram_notifications,
                    )
                    auth_result = await _process_batch(
                        session_factory,
                        settings=settings,
                        limit=limit,
                        processor=process_telegram_authentication_notifications,
                    )
                    prelist_result = await _process_batch(
                        session_factory,
                        settings=settings,
                        limit=limit,
                        processor=process_telegram_prelist_notifications,
                    )
                    result = _combine_batches(
                        _combine_batches(price_result, auth_result), prelist_result
                    )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                observe_worker_failure("telegram_notifier")
                logger.error(
                    "telegram_notification_batch_failed",
                    extra={"worker_failure": type(error).__name__},
                )
                if once:
                    raise
                consecutive_failures += 1
                delay = min(
                    60.0,
                    settings.worker_failure_backoff_seconds
                    * (2 ** min(consecutive_failures - 1, 5)),
                )
                await asyncio.sleep(delay)
                continue
            consecutive_failures = 0
            observe_worker_batch(
                "telegram_notifier",
                started_at=started_at,
                outcomes={
                    "succeeded": result.succeeded,
                    "failed": result.failed,
                    "skipped": result.skipped,
                    "dead_lettered": result.dead_lettered,
                },
            )
            logger.log(
                logging.INFO if result.claimed else logging.DEBUG,
                "telegram_notification_batch",
                extra={
                    "notification_claimed": result.claimed,
                    "notification_succeeded": result.succeeded,
                    "notification_failed": result.failed,
                    "notification_skipped": result.skipped,
                    "notification_dead_lettered": result.dead_lettered,
                },
            )
            if once:
                return
            await asyncio.sleep(interval)
    finally:
        engine.dispose()


async def _process_batch(
    session_factory: sessionmaker[Session],
    *,
    settings: Settings,
    limit: int,
    processor: Callable[..., Awaitable[TelegramNotificationBatch]],
) -> TelegramNotificationBatch:
    with session_factory.begin() as session:
        return await processor(
            session,
            bot_token=settings.telegram_bot_token,
            limit=limit,
            max_attempts=settings.event_consumer_max_attempts,
            retry_base_seconds=settings.event_retry_base_seconds,
            retry_cap_seconds=settings.event_retry_cap_seconds,
            timeout_seconds=settings.external_http_timeout_seconds,
            retry_after_cap_seconds=settings.retry_after_cap_seconds,
            circuit_failure_threshold=settings.circuit_failure_threshold,
            circuit_open_seconds=settings.circuit_open_seconds,
        )


def _combine_batches(
    first: TelegramNotificationBatch, second: TelegramNotificationBatch
) -> TelegramNotificationBatch:
    return TelegramNotificationBatch(
        claimed=first.claimed + second.claimed,
        succeeded=first.succeeded + second.succeeded,
        failed=first.failed + second.failed,
        skipped=first.skipped + second.skipped,
        dead_lettered=first.dead_lettered + second.dead_lettered,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float)
    parser.add_argument("--batch-size", type=int)
    arguments = parser.parse_args()
    settings = Settings()
    configure_logging(
        settings.log_level,
        service_name="aishoppingagent-telegram-notifier",
        environment=settings.environment,
    )
    configure_tracing(settings, service_name="aishoppingagent-telegram-notifier")
    if settings.observability_enabled:
        start_worker_metrics_server(settings.worker_metrics_port)
        mark_worker_started("telegram_notifier")
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
