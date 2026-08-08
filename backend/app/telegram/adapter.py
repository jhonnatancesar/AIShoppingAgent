"""Adaptador que traduz uma mensagem do Telegram em uma intenção estruturada.

O adaptador não conhece domínio de missão, não decide nem executa nada: ele
apenas encaminha o texto ao `IntentInterpreter` já existente (TASK-032) e
devolve o `Intent` resultante. Não repassa `message.received_at` como
`requested_at`: esse horário vem do relógio do Telegram, uma fonte
diferente da que gera `finished_at` na resposta do provedor, e comparar os
dois faz `validate_provider_response` falhar de verdade sempre que os
relógios não estiverem sincronizados (visto em produção, TASK-058).
"""

from app.ai_provider import AIProviderManager
from app.intent import Intent, IntentInterpreter
from app.telegram.contracts import TelegramMessage
from app.users.models import UserRole


class TelegramIntentAdapter:
    """Traduz `TelegramMessage` em `Intent` via `IntentInterpreter`."""

    def __init__(self, interpreter: IntentInterpreter) -> None:
        self._interpreter = interpreter

    @property
    def manager(self) -> AIProviderManager:
        return self._interpreter.manager

    async def interpret(
        self, message: TelegramMessage, *, profile: UserRole = UserRole.USER
    ) -> Intent:
        return await self._interpreter.interpret(message.text, profile=profile)
