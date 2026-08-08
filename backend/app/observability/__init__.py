"""Observabilidade operacional sem dependência funcional externa."""

from app.observability.metrics import metrics_router
from app.observability.tracing import configure_tracing, trace_http_request

__all__ = ["configure_tracing", "metrics_router", "trace_http_request"]
