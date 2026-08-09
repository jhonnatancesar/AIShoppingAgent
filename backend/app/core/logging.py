"""Configuração de logging estruturado em JSON."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace

from app.observability.context import get_request_id

STANDARD_RECORD_ATTRIBUTES = frozenset(logging.makeLogRecord({}).__dict__) | {
    "asctime",
    "message",
}
CORRELATION_FIELDS = frozenset({"request_id", "trace_id", "span_id"})
SENSITIVE_FIELD_FRAGMENTS = (
    "authorization",
    "chat_id",
    "credential",
    "display_name",
    "dsn",
    "email",
    "header",
    "message_text",
    "password",
    "payload",
    "query",
    "query_string",
    "secret",
    "token",
    "url",
    "user_id",
    "username",
    "user_text",
)


class JsonFormatter(logging.Formatter):
    """Serializa registros de log como objetos JSON em uma única linha."""

    def __init__(
        self,
        *,
        service_name: str = "aishoppingagent",
        environment: str = "development",
    ) -> None:
        super().__init__()
        self.service_name = service_name
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        """Converte um registro padrão em um evento estruturado."""
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": self.service_name,
            "environment": self.environment,
        }
        payload.update(
            (key, value)
            for key, value in record.__dict__.items()
            if key not in STANDARD_RECORD_ATTRIBUTES
            and not key.startswith("_")
            and key not in CORRELATION_FIELDS
            and not any(part in key.lower() for part in SENSITIVE_FIELD_FRAGMENTS)
        )
        request_id = get_request_id()
        if request_id is not None:
            payload["request_id"] = str(request_id)
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            payload["trace_id"] = format(span_context.trace_id, "032x")
            payload["span_id"] = format(span_context.span_id, "016x")
        if record.exc_info:
            exception_type = record.exc_info[0]
            payload["exception_type"] = (
                exception_type.__name__
                if isinstance(exception_type, type)
                else "Exception"
            )

        return json.dumps(
            payload, ensure_ascii=False, default=str, separators=(",", ":")
        )


def configure_logging(
    level: str,
    *,
    service_name: str = "aishoppingagent",
    environment: str = "development",
) -> None:
    """Configura aplicação e servidores ASGI para escrever JSON em stdout."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter(service_name=service_name, environment=environment)
    )

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        server_logger = logging.getLogger(logger_name)
        server_logger.handlers.clear()
        server_logger.setLevel(level)
        server_logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
