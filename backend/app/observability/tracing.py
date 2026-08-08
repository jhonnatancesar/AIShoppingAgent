"""Tracing OTLP sanitizado para HTTP, PostgreSQL e worker."""

import re
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from opentelemetry import context, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagators.textmap import Getter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from sqlalchemy import Engine, event
from starlette.requests import Request

from app.core.config import Settings

TRACE_EXCLUDED_PATH_PATTERN = re.compile(r"^/(?:metrics|health)/?$")
_SQL_OPERATION_PATTERN = re.compile(r"^\s*(?:/\*.*?\*/\s*)*([A-Za-z]+)", re.DOTALL)
_SQL_OPERATIONS = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"})
_INSTRUMENTED_ENGINES: weakref.WeakSet[Engine] = weakref.WeakSet()
_tracing_configured = False


class _TraceHeaderGetter(Getter[Request]):
    def get(self, carrier: Request, key: str) -> list[str] | None:
        value = carrier.headers.get(key)
        return [value] if value is not None else None

    def keys(self, carrier: Request) -> list[str]:
        return ["traceparent", "tracestate"]


def configure_tracing(settings: Settings, *, service_name: str) -> None:
    """Configura somente traces; métricas seguem exclusivamente por scrape."""
    global _tracing_configured
    if not settings.observability_enabled or _tracing_configured:
        return
    resource = Resource.create(
        {
            "service.name": service_name,
            "deployment.environment.name": settings.environment,
        }
    )
    provider = TracerProvider(
        resource=resource,
        sampler=ParentBased(TraceIdRatioBased(settings.trace_sample_ratio)),
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_traces_endpoint)
        )
    )
    trace.set_tracer_provider(provider)
    _tracing_configured = True


@asynccontextmanager
async def trace_http_request(request: Request) -> AsyncIterator[trace.Span | None]:
    """Cria span HTTP sem URL/query e exclui probes ruidosos explicitamente."""
    if TRACE_EXCLUDED_PATH_PATTERN.fullmatch(request.url.path):
        yield None
        return
    parent = TraceContextTextMapPropagator().extract(
        request, getter=_TraceHeaderGetter()
    )
    tracer = trace.get_tracer("app.http")
    method = request.method.upper()
    span = tracer.start_span(
        f"{method} request",
        context=parent,
        kind=SpanKind.SERVER,
        attributes={"http.request.method": method},
    )
    token = context.attach(trace.set_span_in_context(span, parent))
    try:
        yield span
    except Exception:
        span.set_status(Status(StatusCode.ERROR))
        raise
    finally:
        context.detach(token)
        span.end()


def finish_http_span(span: trace.Span | None, *, route: str, status_code: int) -> None:
    if span is None:
        return
    span.update_name(f"HTTP {route}")
    span.set_attribute("http.route", route)
    span.set_attribute("http.response.status_code", status_code)
    if status_code >= 500:
        span.set_status(Status(StatusCode.ERROR))


def instrument_sqlalchemy_engine(engine: Engine) -> None:
    """Gera spans SQL apenas com sistema/operação, nunca statement ou valores."""
    if engine in _INSTRUMENTED_ENGINES:
        return
    _INSTRUMENTED_ENGINES.add(engine)

    @event.listens_for(engine, "before_cursor_execute")
    def _before_cursor_execute(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        execution_context: Any,
        executemany: bool,
    ) -> None:
        del conn, cursor, parameters, executemany
        operation = _sql_operation(statement)
        span = trace.get_tracer("app.database").start_span(
            f"postgresql {operation}",
            kind=SpanKind.CLIENT,
            attributes={
                "db.system.name": "postgresql",
                "db.operation.name": operation,
            },
        )
        manager = trace.use_span(span, end_on_exit=False)
        manager.__enter__()
        execution_context._aishopping_trace_state = (span, manager)

    @event.listens_for(engine, "after_cursor_execute")
    def _after_cursor_execute(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        execution_context: Any,
        executemany: bool,
    ) -> None:
        del conn, cursor, statement, parameters, executemany
        _finish_database_span(execution_context)

    @event.listens_for(engine, "handle_error")
    def _handle_error(exception_context: Any) -> None:
        state = getattr(
            exception_context.execution_context, "_aishopping_trace_state", None
        )
        if state is not None:
            span, _ = state
            span.set_status(Status(StatusCode.ERROR))
            _finish_database_span(exception_context.execution_context)


def _finish_database_span(execution_context: Any) -> None:
    state = getattr(execution_context, "_aishopping_trace_state", None)
    if state is None:
        return
    span, manager = state
    del execution_context._aishopping_trace_state
    manager.__exit__(None, None, None)
    span.end()


def _sql_operation(statement: str) -> str:
    match = _SQL_OPERATION_PATTERN.match(statement)
    if match is None:
        return "OTHER"
    operation = match.group(1).upper()
    return operation if operation in _SQL_OPERATIONS else "OTHER"
