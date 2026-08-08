"""Adaptador que traduz uma mensagem do Telegram em uma intenção estruturada.

O adaptador não conhece domínio de missão, não decide nem executa nada: ele
apenas encaminha o texto e o instante de recebimento da mensagem ao
`IntentInterpreter` já existente (TASK-032) e devolve o `Intent` resultante.
"""

from app.intent import Intent, IntentInterpreter
from app.telegram.contracts import TelegramMessage
from app.users.models import UserRole


class TelegramIntentAdapter:
    """Traduz `TelegramMessage` em `Intent` via `IntentInterpreter`."""

    def __init__(self, interpreter: IntentInterpreter) -> None:
        self._interpreter = interpreter

    async def interpret(
        self, message: TelegramMessage, *, profile: UserRole = UserRole.USER
    ) -> Intent:
        return await self._interpreter.interpret(
            message.text,
            requested_at=message.received_at,
            profile=profile,
        )
