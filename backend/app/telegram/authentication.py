"""Autenticação mínima do canal Telegram para o MVP."""

import secrets
from dataclasses import dataclass
from enum import StrEnum

from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.telegram.contracts import TelegramChatType, TelegramMessage
from app.users.models import User
from app.users.service import get_or_create_telegram_user


class TelegramAuthenticationFailure(StrEnum):
    """Motivos fechados e seguros para recusar uma identidade do canal."""

    NON_PRIVATE_CHAT = "non_private_chat"
    IDENTITY_MISMATCH = "identity_mismatch"
    INACTIVE_USER = "inactive_user"


@dataclass(frozen=True, slots=True)
class TelegramAuthenticationResult:
    """Resultado explícito, sem transportar identificadores para logs."""

    user: User | None = None
    failure: TelegramAuthenticationFailure | None = None

    def __post_init__(self) -> None:
        if (self.user is None) == (self.failure is None):
            raise ValueError("authentication result must contain user or failure")

    @property
    def authenticated(self) -> bool:
        return self.user is not None


def webhook_secret_matches(
    provided: str | None,
    expected: SecretStr | None,
) -> bool:
    """Autentica o transporte sem permitir comparação temporal ingênua."""
    if provided is None or expected is None:
        return False
    return secrets.compare_digest(provided, expected.get_secret_value())


def authenticate_telegram_user(
    session: Session,
    *,
    message: TelegramMessage,
    display_name: str,
) -> TelegramAuthenticationResult:
    """Resolve a pessoa somente após validar a identidade privada do canal."""
    if message.chat_type is not TelegramChatType.PRIVATE:
        return TelegramAuthenticationResult(
            failure=TelegramAuthenticationFailure.NON_PRIVATE_CHAT
        )
    if message.chat_id != message.user_id:
        return TelegramAuthenticationResult(
            failure=TelegramAuthenticationFailure.IDENTITY_MISMATCH
        )

    user = get_or_create_telegram_user(
        session,
        telegram_user_id=message.user_id,
        display_name=display_name,
    )
    if not user.is_active:
        return TelegramAuthenticationResult(
            failure=TelegramAuthenticationFailure.INACTIVE_USER
        )
    return TelegramAuthenticationResult(user=user)
