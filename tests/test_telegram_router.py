import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.ai_provider import AIProviderQuotaExceeded, AIProviderUnavailable
from app.core.config import Settings
from app.intent import Intent, IntentKind
from app.main import app
from app.telegram.contracts import TelegramMessage
from app.telegram.router import (
    TelegramUpdate,
    _TelegramChat,
    _TelegramIncomingMessage,
    _TelegramSender,
    receive_telegram_webhook,
)


class _FakeAdapter:
    def __init__(self, outcome: Intent | Exception) -> None:
        self.outcome = outcome
        self.calls: list[TelegramMessage] = []

    async def interpret(self, message: TelegramMessage) -> Intent:
        self.calls.append(message)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "_env_file": None,
        "telegram_webhook_secret": "correct-secret",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _update(**overrides: object) -> TelegramUpdate:
    defaults: dict[str, object] = {
        "update_id": 1,
        "message": _TelegramIncomingMessage(
            text="Quero um notebook até R$ 5000",
            date=1754586000,
            chat=_TelegramChat(id=111),
            from_=_TelegramSender(id=222),
        ),
    }
    defaults.update(overrides)
    return TelegramUpdate(**defaults)  # type: ignore[arg-type]


def _intent() -> Intent:
    return Intent(
        correlation_id=uuid4(),
        kind=IntentKind.CREATE_MISSION,
        raw_message="Quero um notebook até R$ 5000",
        interpreted_at=datetime.now(UTC),
    )


@pytest.mark.anyio
async def test_valid_secret_and_text_message_returns_204_and_calls_adapter() -> None:
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapter=adapter,  # type: ignore[arg-type]
        settings=_settings(),
    )

    assert response.status_code == 204
    assert len(adapter.calls) == 1
    message = adapter.calls[0]
    assert message.chat_id == 111
    assert message.user_id == 222
    assert message.text == "Quero um notebook até R$ 5000"
    assert message.received_at == datetime.fromtimestamp(1754586000, tz=UTC)


@pytest.mark.anyio
@pytest.mark.parametrize("token", [None, "wrong-secret"])
async def test_missing_or_wrong_secret_returns_401_without_calling_adapter(
    token: str | None,
) -> None:
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token=token,
        adapter=adapter,  # type: ignore[arg-type]
        settings=_settings(),
    )

    assert response.status_code == 401
    payload = json.loads(response.body)
    assert payload["error"]["code"] == "telegram_webhook_unauthorized"
    assert adapter.calls == []


@pytest.mark.anyio
async def test_unconfigured_secret_rejects_every_request() -> None:
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="anything",
        adapter=adapter,  # type: ignore[arg-type]
        settings=_settings(telegram_webhook_secret=None),
    )

    assert response.status_code == 401
    assert adapter.calls == []


@pytest.mark.anyio
async def test_update_without_message_is_a_no_op_204() -> None:
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(message=None),
        x_telegram_bot_api_secret_token="correct-secret",
        adapter=adapter,  # type: ignore[arg-type]
        settings=_settings(),
    )

    assert response.status_code == 204
    assert adapter.calls == []


@pytest.mark.anyio
async def test_message_without_text_is_a_no_op_204() -> None:
    adapter = _FakeAdapter(_intent())
    update = _update(
        message=_TelegramIncomingMessage(
            text=None,
            date=1754586000,
            chat=_TelegramChat(id=111),
            from_=_TelegramSender(id=222),
        )
    )

    response = await receive_telegram_webhook(
        update=update,
        x_telegram_bot_api_secret_token="correct-secret",
        adapter=adapter,  # type: ignore[arg-type]
        settings=_settings(),
    )

    assert response.status_code == 204
    assert adapter.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("error", [AIProviderQuotaExceeded(), AIProviderUnavailable()])
async def test_ai_provider_failure_still_returns_204(error: Exception) -> None:
    adapter = _FakeAdapter(error)

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapter=adapter,  # type: ignore[arg-type]
        settings=_settings(),
    )

    assert response.status_code == 204
    assert len(adapter.calls) == 1


def test_telegram_webhook_route_is_exposed_in_openapi() -> None:
    operation = app.openapi()["paths"]["/telegram/webhook"]["post"]

    assert operation["tags"] == ["telegram"]
    assert operation["operationId"] == "receive_telegram_webhook"
    assert operation["summary"] == "Receber atualização do Telegram"
    assert (
        operation["responses"]["204"]["description"]
        == "Atualização autenticada e processada."
    )
