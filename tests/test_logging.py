"""Testes do logging estruturado."""

import asyncio
import json
import logging
from unittest.mock import Mock

import pytest
from app.core import request_logging
from app.core.logging import JsonFormatter
from starlette.requests import Request
from starlette.responses import Response


def make_request() -> Request:
    """Cria uma requisição ASGI mínima para testes do middleware."""
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/health",
            "raw_path": b"/health",
            "query_string": b"secret=not-logged",
            "headers": [],
            "client": ("test", 123),
            "server": ("test", 80),
            "root_path": "",
        }
    )


def test_json_formatter_includes_context_without_private_fields() -> None:
    """O formatador deve preservar contexto seguro em JSON válido."""
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="event_completed",
        args=(),
        exc_info=None,
    )
    record.http_method = "GET"
    record._private = "hidden"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["message"] == "event_completed"
    assert payload["http_method"] == "GET"
    assert "_private" not in payload


def test_request_log_records_safe_http_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """O middleware deve registrar resultado sem query string ou cabeçalhos."""
    info = Mock()
    monkeypatch.setattr(request_logging.logger, "info", info)

    async def respond(_: Request) -> Response:
        return Response(status_code=204)

    response = asyncio.run(request_logging.log_request(make_request(), respond))
    context = info.call_args.kwargs["extra"]

    assert response.status_code == 204
    assert context["http_method"] == "GET"
    assert context["http_path"] == "/health"
    assert context["http_status_code"] == 204
    assert context["duration_ms"] >= 0
    assert "secret" not in str(context)


def test_request_log_records_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falhas devem gerar evento estruturado e continuar propagadas."""
    exception = Mock()
    monkeypatch.setattr(request_logging.logger, "exception", exception)

    async def fail(_: Request) -> Response:
        raise RuntimeError("failure")

    with pytest.raises(RuntimeError, match="failure"):
        asyncio.run(request_logging.log_request(make_request(), fail))

    context = exception.call_args.kwargs["extra"]
    assert context["http_method"] == "GET"
    assert context["http_path"] == "/health"
    assert context["duration_ms"] >= 0
