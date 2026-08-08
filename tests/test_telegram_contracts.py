from datetime import UTC, datetime

import pytest
from app.telegram import TelegramChatType, TelegramContractError, TelegramMessage


def _message(**overrides: object) -> TelegramMessage:
    defaults: dict[str, object] = {
        "chat_id": 123,
        "chat_type": TelegramChatType.GROUP,
        "user_id": 456,
        "text": "Quero um notebook até R$ 5000",
        "received_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return TelegramMessage(**defaults)  # type: ignore[arg-type]


def test_message_accepts_valid_fields() -> None:
    message = _message()

    assert message.chat_id == 123
    assert message.chat_type is TelegramChatType.GROUP
    assert message.user_id == 456
    assert message.text == "Quero um notebook até R$ 5000"


@pytest.mark.parametrize("field", ["chat_id", "user_id"])
def test_message_rejects_non_int_ids(field: str) -> None:
    with pytest.raises(TelegramContractError, match=f"{field} must be an int"):
        _message(**{field: "123"})


@pytest.mark.parametrize("field", ["chat_id", "user_id"])
def test_message_rejects_bool_ids(field: str) -> None:
    with pytest.raises(TelegramContractError, match=f"{field} must be an int"):
        _message(**{field: True})


def test_message_rejects_blank_text() -> None:
    with pytest.raises(TelegramContractError, match="text must not be blank"):
        _message(text="   ")


def test_message_rejects_naive_received_at() -> None:
    with pytest.raises(TelegramContractError, match="timezone"):
        _message(received_at=datetime.now())


def test_message_rejects_unknown_chat_type() -> None:
    with pytest.raises(TelegramContractError, match="chat_type"):
        _message(chat_type="forum")
