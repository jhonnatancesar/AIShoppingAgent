"""Testes das preferências de notificações Telegram (TASK-037)."""

import pytest
from app.telegram.preferences import (
    PREFERENCES_COMMAND,
    handle_preferences_command,
    notification_is_enabled,
)
from app.users.models import User, UserRole

from backend.scripts.register_telegram_commands import _COMMANDS


def _user() -> User:
    return User(
        display_name="Cliente",
        role=UserRole.USER,
        notify_price_decreases=True,
        notify_target_reached=True,
    )


def test_preferences_command_is_registered_in_bot_menu() -> None:
    assert PREFERENCES_COMMAND == "/preferencias"
    assert any(command["command"] == "preferencias" for command in _COMMANDS)


def test_query_shows_both_preferences_enabled_by_default() -> None:
    reply = handle_preferences_command(_user(), "/preferencias")

    assert "Quedas de preço: ativadas" in reply
    assert "Preço-alvo atingido: ativadas" in reply


@pytest.mark.parametrize(
    ("command", "field", "expected"),
    [
        ("/preferencias quedas desativar", "notify_price_decreases", False),
        ("/preferencias quedas ativar", "notify_price_decreases", True),
        ("/preferencias alvo desativar", "notify_target_reached", False),
        ("/preferencias alvo ativar", "notify_target_reached", True),
    ],
)
def test_command_updates_only_selected_preference(
    command: str, field: str, expected: bool
) -> None:
    user = _user()
    if expected:
        setattr(user, field, False)
    other_field = (
        "notify_target_reached"
        if field == "notify_price_decreases"
        else "notify_price_decreases"
    )

    handle_preferences_command(user, command)

    assert getattr(user, field) is expected
    assert getattr(user, other_field) is True


@pytest.mark.parametrize(
    "command",
    [
        "/preferencias quedas",
        "/preferencias quedas talvez",
        "/preferencias email desativar",
    ],
)
def test_invalid_command_only_returns_usage_without_mutating(command: str) -> None:
    user = _user()

    reply = handle_preferences_command(user, command)

    assert "Use um destes comandos" in reply
    assert user.notify_price_decreases is True
    assert user.notify_target_reached is True


def test_non_preferences_command_is_rejected() -> None:
    with pytest.raises(ValueError, match="invalid preferences command"):
        handle_preferences_command(_user(), "/cadastro")


def test_event_types_use_independent_preferences() -> None:
    user = _user()
    user.notify_price_decreases = False

    assert notification_is_enabled(user, "price.decreased.v1") is False
    assert notification_is_enabled(user, "price.target_reached.v1") is True

    with pytest.raises(ValueError, match="does not have"):
        notification_is_enabled(user, "collection.completed.v1")


def test_notification_preferences_do_not_change_task_060_registration_fields() -> None:
    user = _user()
    user.username = "cliente"
    user.email = "cliente@example.com"
    user.favorite_stores = ["kabum"]
    user.preferred_categories = ["games"]

    handle_preferences_command(user, "/preferencias quedas desativar")

    assert user.username == "cliente"
    assert user.email == "cliente@example.com"
    assert user.favorite_stores == ["kabum"]
    assert user.preferred_categories == ["games"]
