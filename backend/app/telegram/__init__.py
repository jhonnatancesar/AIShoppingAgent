"""Fronteira de entrada do canal Telegram, sem lógica de domínio."""

from app.telegram.adapter import TelegramIntentAdapter
from app.telegram.contracts import TelegramContractError, TelegramMessage
from app.telegram.router import get_telegram_intent_adapter
from app.telegram.router import router as telegram_router

__all__ = [
    "TelegramContractError",
    "TelegramIntentAdapter",
    "TelegramMessage",
    "get_telegram_intent_adapter",
    "telegram_router",
]
