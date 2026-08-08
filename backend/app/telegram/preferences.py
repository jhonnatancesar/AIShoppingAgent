"""Preferências de notificações Telegram configuráveis por texto (TASK-037)."""

from typing import Final

from app.events import EventType
from app.users.models import User

PREFERENCES_COMMAND: Final = "/preferencias"

_USAGE: Final = (
    "Use um destes comandos:\n"
    "• /preferencias — consultar\n"
    "• /preferencias quedas ativar|desativar\n"
    "• /preferencias alvo ativar|desativar"
)


def handle_preferences_command(user: User, command: str) -> str:
    """Consulta ou altera uma preferência sem chamar IA nem controlar a sessão."""
    parts = command.strip().lower().split()
    if not parts or parts[0] != PREFERENCES_COMMAND:
        raise ValueError("invalid preferences command")
    if len(parts) == 1:
        return _render_preferences(user)
    if len(parts) != 3:
        return f"Não entendi.\n\n{_USAGE}"

    preference, action = parts[1:]
    if action not in {"ativar", "desativar"}:
        return f"A ação deve ser ativar ou desativar.\n\n{_USAGE}"
    enabled = action == "ativar"
    if preference == "quedas":
        user.notify_price_decreases = enabled
        label = "Quedas de preço"
    elif preference == "alvo":
        user.notify_target_reached = enabled
        label = "Preço-alvo atingido"
    else:
        return f"A preferência deve ser quedas ou alvo.\n\n{_USAGE}"

    state = "ativadas" if enabled else "desativadas"
    return f"{label}: notificações {state}.\n\n{_render_preferences(user)}"


def notification_is_enabled(user: User, event_type: str) -> bool:
    """Informa se o alerta pode ser enviado conforme a preferência persistida."""
    if event_type == EventType.PRICE_DECREASED_V1.value:
        return user.notify_price_decreases
    if event_type == EventType.PRICE_TARGET_REACHED_V1.value:
        return user.notify_target_reached
    raise ValueError("event type does not have a Telegram notification preference")


def _render_preferences(user: User) -> str:
    return (
        "Suas preferências de notificações:\n"
        f"• Quedas de preço: {_state(user.notify_price_decreases)}\n"
        f"• Preço-alvo atingido: {_state(user.notify_target_reached)}\n\n"
        f"{_USAGE}"
    )


def _state(enabled: bool) -> str:
    return "ativadas" if enabled else "desativadas"
