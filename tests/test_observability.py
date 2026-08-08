"""Garantias de privacidade, cardinalidade e tracing da TASK-045."""

import asyncio
import json
import logging
from time import perf_counter
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from app.core.logging import JsonFormatter
from app.observability.context import (
    accepted_request_id,
    reset_request_id,
    set_request_id,
)
from app.observability.metrics import (
    SUPPORTED_EVENT_TYPES,
    get_metrics,
    normalized_event_type,
    normalized_method,
    normalized_route,
    observe_http_request,
    observe_worker_batch,
    status_class,
)
from app.observability.tracing import (
    _sql_operation,
    finish_http_span,
    instrument_sqlalchemy_engine,
    trace_http_request,
)
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from sqlalchemy import create_engine, text
from starlette.requests import Request


def _request(path: str, *, method: str = "GET") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"canary=query-secret",
            "headers": [],
            "client": ("test", 1),
            "server": ("test", 80),
            "root_path": "",
            "route": SimpleNamespace(path="/items/{item_id}"),
        }
    )


@pytest.fixture(scope="module")
def span_exporter() -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return exporter


def test_request_context_and_log_correlation(
    span_exporter: InMemorySpanExporter,
) -> None:
    span_exporter.clear()
    request_id = uuid4()
    token = set_request_id(request_id)
    try:
        with trace.get_tracer("test").start_as_current_span("safe-span"):
            record = logging.LogRecord(
                "app.test", logging.INFO, __file__, 1, "safe_event", (), None
            )
            payload = json.loads(
                JsonFormatter(service_name="api", environment="test").format(record)
            )
    finally:
        reset_request_id(token)

    assert payload["request_id"] == str(request_id)
    assert len(payload["trace_id"]) == 32
    assert len(payload["span_id"]) == 16
    assert payload["service"] == "api"
    assert payload["environment"] == "test"


def test_external_request_id_is_uuid_or_replaced() -> None:
    valid = uuid4()

    assert accepted_request_id(str(valid)) == valid
    assert isinstance(accepted_request_id("invalid"), UUID)
    assert isinstance(accepted_request_id("x" * 65), UUID)


def test_metric_dimensions_are_closed_and_sanitized() -> None:
    assert normalized_method("get") == "GET"
    assert normalized_method("custom") == "OTHER"
    assert status_class(503) == "5xx"
    assert status_class(999) == "other"
    assert (
        normalized_event_type(next(iter(SUPPORTED_EVENT_TYPES)))
        in SUPPORTED_EVENT_TYPES
    )
    assert normalized_event_type("future.dynamic.event") == "other"
    assert normalized_route(_request("/items/secret-canary")) == "/items/{item_id}"

    observe_http_request(
        method="GET",
        route="/items/{item_id}",
        status_code=200,
        duration_seconds=0.01,
    )
    observe_worker_batch(
        "telegram_notifier",
        started_at=perf_counter(),
        outcomes={"succeeded": 1, "failed": 0, "skipped": 0},
    )
    body = get_metrics().body.decode()

    assert 'route="/items/{item_id}"' in body
    assert "secret-canary" not in body
    assert "future.dynamic.event" not in body


def test_unknown_worker_outcome_is_rejected() -> None:
    with pytest.raises(ValueError, match="allowlisted"):
        observe_worker_batch(
            "telegram_notifier", started_at=perf_counter(), outcomes={"future": 1}
        )


@pytest.mark.parametrize(
    "path",
    ["/metrics", "/health", "/health/", "/auth", "/auth/actions"],
)
def test_operational_noise_is_excluded_from_tracing(
    span_exporter: InMemorySpanExporter, path: str
) -> None:
    span_exporter.clear()

    async def exercise() -> None:
        async with trace_http_request(_request(path)) as span:
            assert span is None

    asyncio.run(exercise())
    assert span_exporter.get_finished_spans() == ()


def test_functional_and_readiness_requests_remain_traced(
    span_exporter: InMemorySpanExporter,
) -> None:
    span_exporter.clear()

    async def exercise(path: str, route: str) -> None:
        async with trace_http_request(_request(path, method="POST")) as span:
            assert span is not None
            finish_http_span(span, route=route, status_code=201)

    asyncio.run(exercise("/items/canary-secret", "/items/{item_id}"))
    asyncio.run(exercise("/ready", "/ready"))
    spans = span_exporter.get_finished_spans()

    assert {span.name for span in spans} == {"HTTP /items/{item_id}", "HTTP /ready"}
    serialized = str([(span.name, span.attributes) for span in spans])
    assert "canary-secret" not in serialized
    assert "query-secret" not in serialized


def test_sql_tracing_omits_statement_parameters_and_results(
    span_exporter: InMemorySpanExporter,
) -> None:
    span_exporter.clear()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    instrument_sqlalchemy_engine(engine)

    with engine.connect() as connection:
        result = connection.execute(
            text("SELECT :canary"), {"canary": "postgres-canary-secret"}
        ).scalar_one()

    spans = span_exporter.get_finished_spans()
    engine.dispose()
    assert result == "postgres-canary-secret"
    assert len(spans) == 1
    assert spans[0].attributes == {
        "db.system.name": "postgresql",
        "db.operation.name": "SELECT",
    }
    assert "postgres-canary-secret" not in str(spans[0])


@pytest.mark.parametrize(
    ("statement", "operation"),
    [
        ("SELECT 1", "SELECT"),
        ("/* safe */ INSERT INTO t VALUES (1)", "INSERT"),
        ("VACUUM", "OTHER"),
        ("", "OTHER"),
    ],
)
def test_sql_operation_is_reduced_to_closed_catalog(
    statement: str, operation: str
) -> None:
    assert _sql_operation(statement) == operation
