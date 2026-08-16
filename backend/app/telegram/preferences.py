"""Preferências de notificações Telegram configuráveis por texto (TASK-037)."""

from typing import Final

from app.events import EventType
from app.users.models import User

PREFERENCES_COMMAND: Final = "/preferencias"

_USAGE: Final = (
    "Comandos disponíveis:\n\n"
    "• /preferencias — consultar\n"
    "• /preferencias quedas ativar\n"
    "• /preferencias quedas desativar\n"
    "• /preferencias alvo ativar\n"
    "• /preferencias alvo desativar"
)


def handle_preferences_command(user: User, command: str) -> str:
    """Consulta ou altera uma preferência sem chamar IA nem controlar a sessão."""
    parts = command.strip().lower().split()
    if not parts or parts[0] != PREFERENCES_COMMAND:
        raise ValueError("invalid preferences command")
    if len(parts) == 1:
        return _render_preferences(user)
    if len(parts) != 3:
        return "Não entendi esse comando.\n\nUse /preferencias para ver as opções disponíveis."

    preference, action = parts[1:]
    if action not in {"ativar", "desativar"}:
        return 'A ação deve ser "ativar" ou "desativar".'
    enabled = action == "ativar"
    if preference == "quedas":
        user.notify_price_decreases = enabled
        label = "Quedas de preço"
    elif preference == "alvo":
        user.notify_target_reached = enabled
        label = "Preço-alvo atingido"
    else:
        return 'A preferência deve ser "quedas" ou "alvo".'

    icon = "✅" if enabled else "🔕"
    state = "ativadas" if enabled else "desativadas"
    return f"{icon} {label}: notificações {state}.\n\n{_render_preferences(user)}"


def notification_is_enabled(user: User, event_type: str) -> bool:
    """Informa se o alerta pode ser enviado conforme a preferência persistida."""
    if event_type == EventType.PRICE_DECREASED_V1.value:
        return user.notify_price_decreases
    if event_type == EventType.PRICE_TARGET_REACHED_V1.value:
        return user.notify_target_reached
    raise ValueError("event type does not have a Telegram notification preference")


def _render_preferences(user: User) -> str:
    return (
        "🔔 Suas preferências de notificações\n\n"
        f"{_state_icon(user.notify_price_decreases)} Quedas de preço: "
        f"{_state(user.notify_price_decreases)}\n"
        f"{_state_icon(user.notify_target_reached)} Preço-alvo atingido: "
        f"{_state(user.notify_target_reached)}\n\n"
        f"{_USAGE}"
    )


def _state(enabled: bool) -> str:
    return "ativadas" if enabled else "desativadas"


def _state_icon(enabled: bool) -> str:
    return "✅" if enabled else "🔕"
