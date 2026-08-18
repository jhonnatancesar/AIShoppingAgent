"""Testes das chamadas de saída à Bot API do Telegram, sem rede real."""

import json
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest
from app.telegram.bot_api import (
    TelegramBotAPIError,
    TelegramDeliveryAmbiguous,
    TelegramMediaRejected,
    call_bot_api,
    send_message,
    send_photo,
)
from pydantic import SecretStr


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


def test_call_bot_api_builds_expected_url_and_payload() -> None:
    captured: dict[str, object] = {}

    def _fake_urlopen(request: object, timeout: int = 10) -> _FakeResponse:
        captured["url"] = request.full_url
        captured["data"] = json.loads(request.data)
        return _FakeResponse({"ok": True, "result": True})

    with patch("app.telegram.bot_api.urlopen", _fake_urlopen):
        result = call_bot_api(
            "sendMessage",
            {"chat_id": 123, "text": "oi"},
            bot_token=SecretStr("secret-token"),
        )

    assert result == {"ok": True, "result": True}
    assert captured["url"] == "https://api.telegram.org/botsecret-token/sendMessage"
    assert captured["data"] == {"chat_id": 123, "text": "oi"}


def test_call_bot_api_never_leaks_token_in_the_response() -> None:
    def _fake_urlopen(request: object, timeout: int = 10) -> _FakeResponse:
        return _FakeResponse({"ok": True, "result": True})

    with patch("app.telegram.bot_api.urlopen", _fake_urlopen):
        result = call_bot_api("getWebhookInfo", bot_token=SecretStr("secret-token"))

    assert "secret-token" not in json.dumps(result)


def test_call_bot_api_returns_decoded_error_body_on_http_error() -> None:
    def _fake_urlopen(request: object, timeout: int = 10) -> None:
        raise HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            hdrs=None,
            fp=BytesIO(json.dumps({"ok": False, "error_code": 401}).encode()),
        )

    with patch("app.telegram.bot_api.urlopen", _fake_urlopen):
        result = call_bot_api("sendMessage", bot_token=SecretStr("secret-token"))

    assert result == {"ok": False, "error_code": 401}


def test_call_bot_api_treats_unknown_network_failure_as_ambiguous() -> None:
    def _fake_urlopen(request: object, timeout: int = 10) -> None:
        raise URLError("network down")

    with patch("app.telegram.bot_api.urlopen", _fake_urlopen):
        with pytest.raises(TelegramDeliveryAmbiguous):
            call_bot_api("sendMessage", bot_token=SecretStr("secret-token"))


@pytest.mark.anyio
async def test_send_message_calls_the_bot_api_with_chat_id_and_text() -> None:
    captured: dict[str, object] = {}

    def _fake_urlopen(request: object, timeout: int = 10) -> _FakeResponse:
        captured["data"] = json.loads(request.data)
        return _FakeResponse({"ok": True, "result": True})

    with patch("app.telegram.bot_api.urlopen", _fake_urlopen):
        await send_message(123, "oi", bot_token=SecretStr("secret-token"))

    assert captured["data"] == {"chat_id": 123, "text": "oi"}


@pytest.mark.anyio
async def test_send_message_includes_parse_mode_when_provided() -> None:
    """Links clicáveis (`<a href="...">`) só funcionam com `parse_mode`
    explícito no payload -- sem isso, a Bot API trata `<...>` como texto
    literal, não como entidade HTML."""
    captured: dict[str, object] = {}

    def _fake_urlopen(request: object, timeout: int = 10) -> _FakeResponse:
        captured["data"] = json.loads(request.data)
        return _FakeResponse({"ok": True, "result": True})

    with patch("app.telegram.bot_api.urlopen", _fake_urlopen):
        await send_message(
            123,
            '<a href="https://exemplo.com/o/abc">link</a>',
            bot_token=SecretStr("secret-token"),
            parse_mode="HTML",
        )

    assert captured["data"] == {
        "chat_id": 123,
        "text": '<a href="https://exemplo.com/o/abc">link</a>',
        "parse_mode": "HTML",
    }


@pytest.mark.anyio
async def test_send_message_omits_parse_mode_by_default() -> None:
    captured: dict[str, object] = {}

    def _fake_urlopen(request: object, timeout: int = 10) -> _FakeResponse:
        captured["data"] = json.loads(request.data)
        return _FakeResponse({"ok": True, "result": True})

    with patch("app.telegram.bot_api.urlopen", _fake_urlopen):
        await send_message(123, "oi", bot_token=SecretStr("secret-token"))

    assert "parse_mode" not in captured["data"]


@pytest.mark.anyio
async def test_send_message_raises_sanitized_error_when_api_rejects() -> None:
    def _fake_urlopen(request: object, timeout: int = 10) -> _FakeResponse:
        return _FakeResponse({"ok": False, "error_code": 403})

    with (
        patch("app.telegram.bot_api.urlopen", _fake_urlopen),
        pytest.raises(TelegramBotAPIError) as captured,
    ):
        await send_message(123, "oi", bot_token=SecretStr("secret-token"))

    assert captured.value.error_code == 403
    assert "secret-token" not in str(captured.value)


@pytest.mark.anyio
async def test_ambiguous_send_timeout_is_not_retried_blindly() -> None:
    calls = 0

    def _timeout(request: object, timeout: int = 10) -> None:
        nonlocal calls
        calls += 1
        raise TimeoutError

    with (
        patch("app.telegram.bot_api.urlopen", _timeout),
        pytest.raises(TelegramDeliveryAmbiguous),
    ):
        await send_message(123, "oi", bot_token=SecretStr("secret-token"))

    assert calls == 1


@pytest.mark.anyio
async def test_send_photo_classifies_media_rejection_without_leaking_url() -> None:
    def _reject_media(request: object, timeout: int = 10) -> _FakeResponse:
        return _FakeResponse(
            {
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: failed to get HTTP URL content",
            }
        )

    with (
        patch("app.telegram.bot_api.urlopen", _reject_media),
        pytest.raises(TelegramMediaRejected),
    ):
        await send_photo(
            123,
            "https://images.example.test/item.jpg",
            "Oferta",
            bot_token=SecretStr("secret-token"),
        )


@pytest.mark.anyio
async def test_send_photo_includes_parse_mode_when_provided() -> None:
    captured: dict[str, object] = {}

    def _fake_urlopen(request: object, timeout: int = 10) -> _FakeResponse:
        captured["data"] = json.loads(request.data)
        return _FakeResponse({"ok": True, "result": True})

    with patch("app.telegram.bot_api.urlopen", _fake_urlopen):
        await send_photo(
            123,
            "https://images.example.test/item.jpg",
            '<a href="https://exemplo.com/o/abc">Ver anúncio</a>',
            bot_token=SecretStr("secret-token"),
            parse_mode="HTML",
        )

    assert captured["data"] == {
        "chat_id": 123,
        "photo": "https://images.example.test/item.jpg",
        "caption": '<a href="https://exemplo.com/o/abc">Ver anúncio</a>',
        "parse_mode": "HTML",
    }
