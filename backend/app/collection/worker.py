"""Processo contínuo da agenda e orquestração de coleta das lojas."""

import argparse
import asyncio
import logging
from time import perf_counter

from opentelemetry import trace
from opentelemetry.trace import SpanKind

from app.ai_provider import build_admin_dev_ai_provider_manager
from app.collection.adapter import CollectionAdapter
from app.collection.browser import BrowserSettings
from app.collection.identity_resolution import StoreProductIdentityResolver
from app.collection.orchestration import CollectionOrchestrator
from app.collection.providers import V1_PROVIDER_TYPES
from app.core.config import Settings
from app.core.logging import configure_logging
from app.core.resilience import RetryPolicy
from app.database.session import (
    create_async_session_factory,
    create_collection_async_database_engine,
)
from app.observability.metrics import (
    mark_worker_started,
    observe_worker_batch,
    observe_worker_failure,
    start_worker_metrics_server,
)
from app.observability.tracing import configure_tracing

logger = logging.getLogger("app.collection.worker")


# Pichau e Terabyte exigem Chromium headed (via Xvfb) para não serem
# bloqueadas por proteção anti-bot; Amazon e Kabum toleram headless.
# Mesma distinção de backend/scripts/validate_store_providers.py e
# docs/PLAYWRIGHT.md (TASK-055) — o worker de produção não a herdava.
_HEADED_SOURCES = frozenset({"pichau", "terabyte"})


def build_collection_adapter(settings: Settings) -> CollectionAdapter:
    retry_policy = RetryPolicy(
        max_attempts=settings.safe_retry_max_attempts,
        base_delay_seconds=settings.retry_base_delay_seconds,
        max_delay_seconds=settings.retry_max_delay_seconds,
        retry_after_cap_seconds=settings.retry_after_cap_seconds,
    )
    action_timeout_ms = max(1, int(settings.external_http_timeout_seconds * 1000))
    # TASK-075 (correção): navegação de página (Playwright) usa seu próprio
    # timeout, desacoplado do timeout de chamada de API/HTTP externo --
    # carregar uma página completa é mais lento que uma chamada de API.
    navigation_timeout_ms = max(
        1, int(settings.browser_navigation_timeout_seconds * 1000)
    )

    def _browser_settings(headless: bool) -> BrowserSettings:
        return BrowserSettings(
            headless=headless,
            action_timeout_ms=action_timeout_ms,
            navigation_timeout_ms=navigation_timeout_ms,
        )

    return CollectionAdapter(
        provider_type(
            _browser_settings(provider_type.source_code not in _HEADED_SOURCES),
            retry_policy=retry_policy,
            circuit_failure_threshold=settings.circuit_failure_threshold,
            circuit_open_seconds=settings.circuit_open_seconds,
            availability_fallback_max_candidates=(
                settings.availability_fallback_max_candidates
            ),
        )
        for provider_type in V1_PROVIDER_TYPES
    )


async def run_worker(
    settings: Settings,
    *,
    once: bool = False,
    poll_seconds: float | None = None,
    batch_size: int | None = None,
) -> None:
    interval = (
        settings.collection_poll_seconds if poll_seconds is None else poll_seconds
    )
    limit = settings.collection_batch_size if batch_size is None else batch_size
    if interval <= 0:
        raise ValueError("poll_seconds must be positive")
    if not 1 <= limit <= 1000:
        raise ValueError("batch_size must be between 1 and 1000")
    # TASK-079: engine assíncrono dedicado -- nenhuma chamada bloqueante do
    # SQLAlchemy/psycopg roda direto na thread do event loop neste
    # caminho (causa raiz comprovada do autodeadlock; ver docs/tasks/TASK-079.md).
    engine = create_collection_async_database_engine(settings)
    session_factory = create_async_session_factory(engine)
    orchestrator = CollectionOrchestrator(
        session_factory,
        build_collection_adapter(settings),
        ai_manager=build_admin_dev_ai_provider_manager(settings),
        # TASK-083: instâncias dedicadas (Kabum -> Amazon), nunca as da
        # coleta normal registrada em `build_collection_adapter` acima --
        # namespace de circuit breaker, volume e timeout próprios (ver
        # `StoreProductIdentityResolver`).
        identity_resolver=StoreProductIdentityResolver(),
        schedule_interval_minutes=settings.collection_schedule_interval_minutes,
        schedule_stagger_seconds=settings.collection_schedule_stagger_seconds,
        stale_run_minutes=settings.collection_stale_run_minutes,
        max_concurrency=settings.collection_max_concurrency,
        claim_deadline_seconds=settings.collection_claim_deadline_seconds,
    )
    try:
        consecutive_failures = 0
        while True:
            started_at = perf_counter()
            try:
                tracer = trace.get_tracer("app.collection.worker")
                with tracer.start_as_current_span(
                    "collection orchestration batch",
                    kind=SpanKind.CONSUMER,
                    attributes={"worker.name": "collection_orchestrator"},
                    record_exception=False,
                    set_status_on_exception=False,
                ):
                    result = await orchestrator.run_batch(limit=limit)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                observe_worker_failure("collection_orchestrator")
                logger.error(
                    "collection_batch_failed",
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
                "collection_orchestrator",
                started_at=started_at,
                outcomes={"succeeded": result.succeeded, "failed": result.failed},
            )
            logger.log(
                logging.INFO
                if result.claimed or result.recovered_stale
                else logging.DEBUG,
                "collection_batch",
                extra={
                    "collection_claimed": result.claimed,
                    "collection_succeeded": result.succeeded,
                    "collection_failed": result.failed,
                    "collection_stale_recovered": result.recovered_stale,
                },
            )
            if once:
                return
            await asyncio.sleep(interval)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float)
    parser.add_argument("--batch-size", type=int)
    arguments = parser.parse_args()
    settings = Settings()
    configure_logging(
        settings.log_level,
        service_name="aishoppingagent-collection-orchestrator",
        environment=settings.environment,
    )
    configure_tracing(settings, service_name="aishoppingagent-collection-orchestrator")
    if settings.observability_enabled:
        start_worker_metrics_server(settings.worker_metrics_port)
        mark_worker_started("collection_orchestrator")
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
