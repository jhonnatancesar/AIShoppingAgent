"""Configuração de logging estruturado em JSON."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

STANDARD_RECORD_ATTRIBUTES = frozenset(logging.makeLogRecord({}).__dict__) | {
    "asctime",
    "message",
}


class JsonFormatter(logging.Formatter):
    """Serializa registros de log como objetos JSON em uma única linha."""

    def format(self, record: logging.LogRecord) -> str:
        """Converte um registro padrão em um evento estruturado."""
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(
            (key, value)
            for key, value in record.__dict__.items()
            if key not in STANDARD_RECORD_ATTRIBUTES and not key.startswith("_")
        )
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(
            payload, ensure_ascii=False, default=str, separators=(",", ":")
        )


def configure_logging(level: str) -> None:
    """Configura aplicação e servidores ASGI para escrever JSON em stdout."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        server_logger = logging.getLogger(logger_name)
        server_logger.handlers.clear()
        server_logger.setLevel(level)
        server_logger.propagate = True
