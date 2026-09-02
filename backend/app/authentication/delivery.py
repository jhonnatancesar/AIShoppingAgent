"""Entrega de código de verificação (Subtask 9) -- abstração limpa por
canal, para nunca fingir envio: um canal só aparece como disponível
quando há implementação/configuração real por trás.

`TelegramDeliveryProvider` é real (reaproveita `app.telegram.bot_api.
send_message`, mesmo bot já usado pelo resto do produto). Não existe
provider de e-mail real hoje (auditado: nenhuma infraestrutura de SMTP/
SendGrid/etc. no projeto) -- `EmailDeliveryProvider` fica pronta para
ativação futura, mas `email_delivery_available` só é `True` quando
`Settings.email_delivery_provider` estiver configurado."""

from abc import ABC, abstractmethod

from pydantic import SecretStr

from app.authentication.models import VerificationChannel, VerificationPurpose
from app.core.config import Settings
from app.telegram.bot_api import TelegramDeliveryError, send_message
from app.users.models import User

_CODE_MESSAGE_BY_PURPOSE: dict[VerificationPurpose, str] = {
    VerificationPurpose.PASSWORD_RESET: "🔐 Código para recuperar sua senha",
    VerificationPurpose.PASSWORD_CHANGE: "🔐 Código para alterar sua senha",
    VerificationPurpose.EMAIL_VERIFICATION: "📧 Código para verificar seu e-mail",
}


class DeliveryUnavailable(RuntimeError):
    """O canal não está disponível para esta entrega (nunca finge envio)."""


class VerificationDeliveryProvider(ABC):
    channel: VerificationChannel

    @abstractmethod
    async def deliver(self, *, user: User, code: str, purpose: VerificationPurpose) -> None:
        """Entrega o código em claro; levanta `DeliveryUnavailable` se o
        canal não puder atender esta entrega específica (ex.: usuário sem
        `telegram_chat_id`, apesar de o canal em geral existir)."""


class TelegramDeliveryProvider(VerificationDeliveryProvider):
    channel = VerificationChannel.TELEGRAM

    def __init__(self, *, bot_token: SecretStr, timeout_seconds: float) -> None:
        self._bot_token = bot_token
        self._timeout_seconds = timeout_seconds

    async def deliver(self, *, user: User, code: str, purpose: VerificationPurpose) -> None:
        if user.telegram_chat_id is None:
            raise DeliveryUnavailable("usuário sem chat do Telegram vinculado")
        title = _CODE_MESSAGE_BY_PURPOSE[purpose]
        text = (
            f"{title}\n\n{code}\n\n"
            "Não compartilhe esse código. Ele expira em 10 minutos e vale "
            "só para esta solicitação."
        )
        try:
            await send_message(
                user.telegram_chat_id,
                text,
                bot_token=self._bot_token,
                timeout_seconds=self._timeout_seconds,
            )
        except TelegramDeliveryError as error:
            raise DeliveryUnavailable("falha ao entregar pelo Telegram") from error


class EmailDeliveryProvider(VerificationDeliveryProvider):
    """Reservado para quando um provider real de e-mail existir
    (`Settings.email_delivery_provider`). Nunca instanciado hoje --
    `email_delivery_available` sempre devolve `False`, então nenhum
    chamador chega a usar esta classe em produção ainda."""

    channel = VerificationChannel.EMAIL

    async def deliver(self, *, user: User, code: str, purpose: VerificationPurpose) -> None:
        raise DeliveryUnavailable("nenhum provider de e-mail configurado")


def email_delivery_available(settings: Settings) -> bool:
    return bool(settings.email_delivery_provider)


def available_channels(user: User, *, settings: Settings) -> frozenset[VerificationChannel]:
    """Só oferece um canal quando ele é genuinamente utilizável por este
    usuário -- nunca uma opção falsa (Subtask 9, seção "ESCOLHA DO
    CANAL")."""
    channels: set[VerificationChannel] = set()
    if user.telegram_user_id is not None and user.telegram_chat_id is not None:
        channels.add(VerificationChannel.TELEGRAM)
    if user.email is not None and email_delivery_available(settings):
        channels.add(VerificationChannel.EMAIL)
    return frozenset(channels)
