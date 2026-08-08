"""Fronteira de entrada do canal Telegram, sem lógica de domínio."""

from app.telegram.adapter import TelegramIntentAdapter
from app.telegram.authentication import (
    TelegramAuthenticationFailure,
    TelegramAuthenticationResult,
    authenticate_telegram_user,
    webhook_secret_matches,
)
from app.telegram.contracts import (
    TelegramChatType,
    TelegramContractError,
    TelegramMessage,
)
from app.telegram.router import get_telegram_intent_adapters
from app.telegram.router import router as telegram_router

__all__ = [
    "TelegramChatType",
    "TelegramAuthenticationFailure",
    "TelegramAuthenticationResult",
    "TelegramContractError",
    "TelegramIntentAdapter",
    "TelegramMessage",
    "authenticate_telegram_user",
    "get_telegram_intent_adapters",
    "telegram_router",
    "webhook_secret_matches",
]
