"""Logging estruturado para requisições HTTP."""

import logging
from collections.abc import Awaitable, Callable
from time import perf_counter

from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("app.http")


async def log_request(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Registra o resultado da requisição sem capturar dados sensíveis."""
    started_at = perf_counter()
    context = {
        "http_method": request.method,
        "http_path": request.url.path,
    }

    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "http_request_failed",
            extra={
                **context,
                "duration_ms": round((perf_counter() - started_at) * 1000, 3),
            },
        )
        raise

    logger.info(
        "http_request_completed",
        extra={
            **context,
            "http_status_code": response.status_code,
            "duration_ms": round((perf_counter() - started_at) * 1000, 3),
        },
    )
    return response
