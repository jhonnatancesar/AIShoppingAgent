"""Métricas Prometheus de cardinalidade limitada para API e worker."""

from collections.abc import Mapping
from time import perf_counter

from fastapi import APIRouter, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)
from starlette.requests import Request
from starlette.routing import Match

METRICS_REGISTRY = CollectorRegistry(auto_describe=True)
HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"})
WORKER_OUTCOMES = frozenset({"succeeded", "failed", "skipped", "dead_lettered"})
WORKERS = frozenset({"telegram_notifier", "collection_orchestrator"})
RESILIENCE_COMPONENTS = frozenset({"webhook", "telegram", "ai", "store", "worker"})
RESILIENCE_EVENTS = frozenset(
    {
        "rate_limited",
        "replay",
        "retry",
        "circuit_open",
        "dead_lettered",
        "failure",
        "cesar_core_disaster_fallback",
    }
)
SUPPORTED_EVENT_TYPES = frozenset(
    {
        "price.decreased.v1",
        "price.target_reached.v1",
        "authentication.completed.v1",
        "authentication.session_expiring.v1",
        "authentication.session_expired.v1",
    }
)

HTTP_REQUESTS = Counter(
    "aishopping_http_requests_total",
    "Total de requisições HTTP concluídas.",
    ("method", "route", "status_class"),
    registry=METRICS_REGISTRY,
)
HTTP_DURATION = Histogram(
    "aishopping_http_request_duration_seconds",
    "Duração de requisições HTTP.",
    ("method", "route"),
    registry=METRICS_REGISTRY,
)
HTTP_IN_FLIGHT = Gauge(
    "aishopping_http_requests_in_flight",
    "Requisições HTTP atualmente em processamento.",
    ("method",),
    registry=METRICS_REGISTRY,
)
WORKER_UP = Gauge(
    "aishopping_worker_up",
    "Indica que o processo do worker iniciou sua instrumentação.",
    ("worker",),
    registry=METRICS_REGISTRY,
)
WORKER_BATCHES = Counter(
    "aishopping_worker_batches_total",
    "Total de lotes concluídos pelo worker.",
    ("worker",),
    registry=METRICS_REGISTRY,
)
WORKER_BATCH_DURATION = Histogram(
    "aishopping_worker_batch_duration_seconds",
    "Duração dos lotes processados pelo worker.",
    ("worker",),
    registry=METRICS_REGISTRY,
)
WORKER_EVENTS = Counter(
    "aishopping_worker_events_total",
    "Eventos concluídos pelo worker por resultado fechado.",
    ("worker", "outcome"),
    registry=METRICS_REGISTRY,
)
RESILIENCE_TOTAL = Counter(
    "aishopping_resilience_events_total",
    "Eventos operacionais de resiliência por catálogo fechado.",
    ("component", "event"),
    registry=METRICS_REGISTRY,
)

metrics_router = APIRouter(include_in_schema=False)


@metrics_router.get("/metrics", include_in_schema=False)
def get_metrics() -> Response:
    return Response(generate_latest(METRICS_REGISTRY), media_type=CONTENT_TYPE_LATEST)


def normalized_method(method: str) -> str:
    candidate = method.upper()
    return candidate if candidate in HTTP_METHODS else "OTHER"


def normalized_route(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path.startswith("/"):
        return path
    application = request.scope.get("app")
    router = getattr(application, "router", None)
    for candidate in getattr(router, "routes", ()):
        match, _ = candidate.matches(request.scope)
        if match is Match.FULL:
            candidate_path = getattr(candidate, "path", None)
            if isinstance(candidate_path, str) and candidate_path.startswith("/"):
                return candidate_path
    return "unmatched"


def status_class(status_code: int) -> str:
    return f"{status_code // 100}xx" if 100 <= status_code <= 599 else "other"


def normalized_event_type(event_type: str) -> str:
    """Impede que novos tipos criem séries sem revisão da allowlist."""
    return event_type if event_type in SUPPORTED_EVENT_TYPES else "other"


def observe_http_request(
    *, method: str, route: str, status_code: int, duration_seconds: float
) -> None:
    safe_method = normalized_method(method)
    HTTP_REQUESTS.labels(safe_method, route, status_class(status_code)).inc()
    HTTP_DURATION.labels(safe_method, route).observe(duration_seconds)


def start_worker_metrics_server(port: int) -> None:
    start_http_server(port, registry=METRICS_REGISTRY)


def mark_worker_started(worker: str) -> None:
    _require_worker(worker)
    WORKER_UP.labels(worker).set(1)


def observe_worker_batch(
    worker: str,
    *,
    started_at: float,
    outcomes: Mapping[str, int],
) -> None:
    _require_worker(worker)
    WORKER_BATCHES.labels(worker).inc()
    WORKER_BATCH_DURATION.labels(worker).observe(perf_counter() - started_at)
    for outcome, count in outcomes.items():
        if outcome not in WORKER_OUTCOMES:
            raise ValueError("worker outcome is not allowlisted")
        WORKER_EVENTS.labels(worker, outcome).inc(count)


def observe_worker_failure(worker: str) -> None:
    _require_worker(worker)
    observe_resilience_event("worker", "failure")


def _require_worker(worker: str) -> None:
    if worker not in WORKERS:
        raise ValueError("worker is not allowlisted")


def observe_resilience_event(component: str, event: str) -> None:
    if component not in RESILIENCE_COMPONENTS:
        raise ValueError("resilience component is not allowlisted")
    if event not in RESILIENCE_EVENTS:
        raise ValueError("resilience event is not allowlisted")
    RESILIENCE_TOTAL.labels(component, event).inc()
