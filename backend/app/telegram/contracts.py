"""Contrato imutável da mensagem bruta recebida do Telegram.

Este módulo representa apenas o formato da mensagem de entrada. Nenhuma
lógica de domínio, execução de comando ou chamada a IA pertence a este
contrato; a tradução para uma intenção estruturada cabe ao adaptador.
"""

from dataclasses import dataclass
from datetime import datetime


class TelegramContractError(ValueError):
    """Indica uma mensagem do Telegram fora do contrato esperado."""


@dataclass(frozen=True, slots=True)
class TelegramMessage:
    """Mensagem bruta recebida de uma conversa do Telegram."""

    chat_id: int
    user_id: int
    text: str
    received_at: datetime

    def __post_init__(self) -> None:
        _require_int(self.chat_id, "chat_id")
        _require_int(self.user_id, "user_id")
        if not isinstance(self.text, str) or not self.text.strip():
            raise TelegramContractError("text must not be blank")
        if (
            not isinstance(self.received_at, datetime)
            or self.received_at.utcoffset() is None
        ):
            raise TelegramContractError("received_at must include a timezone")


def _require_int(value: object, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TelegramContractError(f"{field_name} must be an int")
