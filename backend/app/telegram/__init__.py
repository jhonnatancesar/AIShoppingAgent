"""Fronteira de entrada do canal Telegram, sem lógica de domínio."""

from app.telegram.adapter import TelegramIntentAdapter
from app.telegram.contracts import TelegramContractError, TelegramMessage

__all__ = [
    "TelegramContractError",
    "TelegramIntentAdapter",
    "TelegramMessage",
]
