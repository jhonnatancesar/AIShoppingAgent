"""Logging estruturado para requisições HTTP."""

import logging
from collections.abc import Awaitable, Callable
from time import perf_counter

from starlette.requests import Request
from starlette.responses import Response

from app.observability.context import (
    REQUEST_ID_HEADER,
    accepted_request_id,
    reset_request_id,
    set_request_id,
)
from app.observability.metrics import (
    HTTP_IN_FLIGHT,
    normalized_method,
    normalized_route,
    observe_http_request,
)
from app.observability.tracing import finish_http_span, trace_http_request

logger = logging.getLogger("app.http")


async def log_request(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Registra o resultado da requisição sem capturar dados sensíveis."""
    started_at = perf_counter()
    method = normalized_method(request.method)
    request_id = accepted_request_id(request.headers.get(REQUEST_ID_HEADER))
    request_id_token = set_request_id(request_id)
    HTTP_IN_FLIGHT.labels(method).inc()
    route = "unmatched"
    status_code = 500

    try:
        async with trace_http_request(request) as span:
            try:
                response = await call_next(request)
                status_code = response.status_code
                route = normalized_route(request)
                response.headers[REQUEST_ID_HEADER] = str(request_id)
                finish_http_span(span, route=route, status_code=status_code)
                logger.info(
                    "http_request_completed",
                    extra={
                        "http_method": method,
                        "http_route": route,
                        "http_status_code": status_code,
                        "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                    },
                )
                return response
            except Exception:
                route = normalized_route(request)
                finish_http_span(span, route=route, status_code=status_code)
                logger.error(
                    "http_request_failed",
                    extra={
                        "http_method": method,
                        "http_route": route,
                        "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                    },
                )
                raise
    finally:
        duration_seconds = perf_counter() - started_at
        observe_http_request(
            method=method,
            route=route,
            status_code=status_code,
            duration_seconds=duration_seconds,
        )
        HTTP_IN_FLIGHT.labels(method).dec()
        reset_request_id(request_id_token)
