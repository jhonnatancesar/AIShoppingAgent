"""Consumidor de alertas de preço com entrega proativa pelo Telegram."""

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.database.time import utc_now
from app.events import (
    ConsumptionOutcome,
    Event,
    EventType,
    claim_unconsumed_events,
    record_consumption_attempt,
)
from app.missions.models import Mission
from app.telegram.bot_api import TelegramBotAPIError, send_message
from app.telegram.contracts import TelegramChatType, TelegramMessage
from app.users.models import User

logger = logging.getLogger("app.telegram.notifications")

TELEGRAM_NOTIFICATION_CONSUMER = "telegram_price_alerts_v1"
_NOTIFICATION_EVENT_TYPES = (
    EventType.PRICE_DECREASED_V1.value,
    EventType.PRICE_TARGET_REACHED_V1.value,
)


class TelegramNotificationError(ValueError):
    """Indica uma falha conhecida e sanitizada ao preparar a notificação."""

    def __init__(self, failure_code: str) -> None:
        self.failure_code = failure_code
        super().__init__(failure_code)


@dataclass(frozen=True, slots=True)
class TelegramNotificationBatch:
    claimed: int
    succeeded: int
    failed: int


def remember_private_notification_chat(user: User, message: TelegramMessage) -> bool:
    """Guarda como destino somente a conversa privada da própria pessoa."""
    if message.chat_type is not TelegramChatType.PRIVATE:
        return False
    if user.telegram_user_id != message.user_id or message.chat_id != message.user_id:
        raise TelegramNotificationError("private_chat_identity_mismatch")
    user.telegram_chat_id = message.chat_id
    return True


async def process_telegram_notifications(
    session: Session,
    *,
    bot_token: SecretStr,
    limit: int = 50,
) -> TelegramNotificationBatch:
    """Entrega um lote mantendo locks e tentativas na transação do chamador."""
    events = claim_unconsumed_events(
        session,
        consumer_name=TELEGRAM_NOTIFICATION_CONSUMER,
        limit=limit,
        event_types=_NOTIFICATION_EVENT_TYPES,
    )
    succeeded = 0
    failed = 0
    for event in events:
        failure_code: str | None = None
        try:
            recipient, text = _prepare_notification(session, event)
            await send_message(recipient, text, bot_token=bot_token)
        except TelegramNotificationError as error:
            failure_code = error.failure_code
        except ConnectionError, TelegramBotAPIError:
            failure_code = "telegram_delivery_failed"

        outcome = (
            ConsumptionOutcome.SUCCEEDED
            if failure_code is None
            else ConsumptionOutcome.FAILED
        )
        record_consumption_attempt(
            session,
            event=event,
            consumer_name=TELEGRAM_NOTIFICATION_CONSUMER,
            outcome=outcome,
            attempted_at=utc_now(),
            failure_code=failure_code,
        )
        if failure_code is None:
            succeeded += 1
        else:
            failed += 1
            logger.warning(
                "telegram_notification_failed",
                extra={
                    "event_id": str(event.id),
                    "notification_failure_code": failure_code,
                },
            )
    return TelegramNotificationBatch(len(events), succeeded, failed)


def _prepare_notification(session: Session, event: Event) -> tuple[int, str]:
    if event.mission_id is None:
        raise TelegramNotificationError("notification_mission_missing")
    mission = session.get(Mission, event.mission_id)
    if mission is None:
        raise TelegramNotificationError("notification_mission_missing")
    user = session.get(User, mission.user_id)
    if user is None or user.telegram_chat_id is None:
        raise TelegramNotificationError("notification_recipient_missing")
    if not user.is_active:
        raise TelegramNotificationError("notification_recipient_inactive")
    return user.telegram_chat_id, _render_alert(event, mission.title)


def _render_alert(event: Event, mission_title: str) -> str:
    payload = event.payload
    if not isinstance(payload, dict):
        raise TelegramNotificationError("notification_payload_invalid")
    try:
        event_type = EventType(event.event_type)
        currency = _currency(payload)
        current_total = _money(payload, "current_total")
        if event_type is EventType.PRICE_DECREASED_V1:
            previous_total = _money(payload, "previous_total")
            return (
                f'📉 O preço caiu na missão "{mission_title}".\n'
                f"De {_format_money(previous_total, currency)} para "
                f"{_format_money(current_total, currency)}."
            )
        if event_type is EventType.PRICE_TARGET_REACHED_V1:
            target_total = _money(payload, "target_total")
            if payload.get("mission_id") != str(event.mission_id):
                raise TelegramNotificationError("notification_payload_invalid")
            return (
                f'🎯 Preço-alvo atingido na missão "{mission_title}".\n'
                f"Preço atual: {_format_money(current_total, currency)} "
                f"(alvo: {_format_money(target_total, currency)})."
            )
    except InvalidOperation, TypeError, ValueError:
        raise TelegramNotificationError("notification_payload_invalid") from None
    raise TelegramNotificationError("notification_payload_invalid")


def _required_text(payload: dict, field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise TelegramNotificationError("notification_payload_invalid")
    return value


def _money(payload: dict, field: str) -> Decimal:
    value = Decimal(_required_text(payload, field))
    if not value.is_finite() or value < 0:
        raise TelegramNotificationError("notification_payload_invalid")
    return value


def _currency(payload: dict) -> str:
    currency = _required_text(payload, "currency")
    if (
        len(currency) != 3
        or not currency.isascii()
        or not currency.isalpha()
        or not currency.isupper()
    ):
        raise TelegramNotificationError("notification_payload_invalid")
    return currency


def _format_money(amount: Decimal, currency: str) -> str:
    formatted = f"{amount:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    prefix = "R$" if currency == "BRL" else currency
    return f"{prefix} {formatted}"
