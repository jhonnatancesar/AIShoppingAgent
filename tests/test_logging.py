"""Testes do logging estruturado."""

import asyncio
import json
import logging
import sys
from unittest.mock import Mock

import pytest
from app.core import request_logging
from app.core.logging import JsonFormatter
from app.observability.context import MAX_EXTERNAL_REQUEST_ID_LENGTH
from starlette.requests import Request
from starlette.responses import Response


def make_request(headers: list[tuple[bytes, bytes]] | None = None) -> Request:
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
            "headers": headers or [],
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
    record.authorization_header = "Bearer secret-canary"
    record.request_id = "spoofed"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["message"] == "event_completed"
    assert payload["http_method"] == "GET"
    assert "_private" not in payload
    assert "authorization_header" not in payload
    assert "request_id" not in payload


def test_json_formatter_preserves_collection_failure_diagnostic_fields() -> None:
    """Os nomes locais da TASK-076 não colidem com a redação de segurança."""
    record = logging.LogRecord(
        name="app.collection.orchestration",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="collection_source_failed",
        args=(),
        exc_info=None,
    )
    record.error_class = "ProviderNavigationError"
    record.error_detail = "pichau navigation failed with status 504"
    record.provider_status = 504
    record.failure_stage = "navigation"
    record.failure_traceback = "Traceback (most recent call last): ..."
    record.provider_url = "https://sensitive.invalid"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["error_class"] == "ProviderNavigationError"
    assert payload["error_detail"] == "pichau navigation failed with status 504"
    assert payload["provider_status"] == 504
    assert payload["failure_stage"] == "navigation"
    assert payload["failure_traceback"].startswith("Traceback")
    assert "provider_url" not in payload


def test_json_formatter_removes_personal_fields_and_raw_exception() -> None:
    """PII em extras ou mensagem de exceção nunca deve sair no JSON."""
    canary = "privacy-canary@example.invalid"
    try:
        raise RuntimeError(canary)
    except RuntimeError:
        exception = sys.exc_info()
    record = logging.LogRecord(
        name="app.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="controlled_failure",
        args=(),
        exc_info=exception,
    )
    record.telegram_chat_id = 123456789
    record.owner_user_id = "private-user"
    record.email = canary
    record.product_url = f"https://example.invalid/{canary}"
    record.message_text = canary

    serialized = JsonFormatter(environment="production").format(record)
    payload = json.loads(serialized)

    assert payload["exception_type"] == "RuntimeError"
    assert "exception" not in payload
    assert canary not in serialized
    assert "123456789" not in serialized
    assert "private-user" not in serialized


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
    assert context["http_route"] == "unmatched"
    assert context["http_status_code"] == 204
    assert context["duration_ms"] >= 0
    assert "secret" not in str(context)
    assert response.headers["X-Request-ID"]


def test_request_log_records_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falhas devem gerar evento estruturado e continuar propagadas."""
    error = Mock()
    monkeypatch.setattr(request_logging.logger, "error", error)

    async def fail(_: Request) -> Response:
        raise RuntimeError("failure")

    with pytest.raises(RuntimeError, match="failure"):
        asyncio.run(request_logging.log_request(make_request(), fail))

    context = error.call_args.kwargs["extra"]
    assert context["http_method"] == "GET"
    assert context["http_route"] == "unmatched"
    assert context["duration_ms"] >= 0


def test_request_id_accepts_only_valid_short_uuid() -> None:
    accepted = "ef820a1e-c466-42af-9e76-18ee2f1f75fd"

    async def respond(_: Request) -> Response:
        return Response(status_code=200)

    valid_response = asyncio.run(
        request_logging.log_request(
            make_request([(b"x-request-id", accepted.encode())]), respond
        )
    )
    invalid_response = asyncio.run(
        request_logging.log_request(
            make_request([(b"x-request-id", b"not-a-uuid")]), respond
        )
    )
    oversized_response = asyncio.run(
        request_logging.log_request(
            make_request(
                [(b"x-request-id", b"a" * (MAX_EXTERNAL_REQUEST_ID_LENGTH + 1))]
            ),
            respond,
        )
    )

    assert valid_response.headers["X-Request-ID"] == accepted
    assert invalid_response.headers["X-Request-ID"] != "not-a-uuid"
    assert oversized_response.headers["X-Request-ID"] != "a" * 65
