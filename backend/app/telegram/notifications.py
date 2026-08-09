"""Consumidor de alertas de preço com entrega proativa pelo Telegram."""

import logging
import random
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.database.time import utc_now
from app.events import (
    ConsumptionOutcome,
    Event,
    EventType,
    claim_unconsumed_events,
    count_failed_attempts,
    record_consumption_attempt,
)
from app.missions.models import Mission
from app.observability.metrics import observe_resilience_event
from app.telegram.bot_api import (
    TelegramBotAPIError,
    TelegramDeliveryAmbiguous,
    TelegramDeliveryError,
    send_message,
)
from app.telegram.contracts import TelegramChatType, TelegramMessage
from app.telegram.preferences import notification_is_enabled
from app.users.models import User

logger = logging.getLogger("app.telegram.notifications")

TELEGRAM_NOTIFICATION_CONSUMER = "telegram_price_alerts_v1"
_NOTIFICATION_EVENT_TYPES = (
    EventType.PRICE_DECREASED_V1.value,
    EventType.PRICE_TARGET_REACHED_V1.value,
)


class TelegramNotificationError(ValueError):
    """Indica uma falha conhecida e sanitizada ao preparar a notificação."""

    def __init__(self, failure_code: str, *, permanent: bool = True) -> None:
        self.failure_code = failure_code
        self.permanent = permanent
        super().__init__(failure_code)


class TelegramNotificationSkipped(RuntimeError):
    """O usuário desativou o tipo de alerta; o consumo termina sem envio."""


@dataclass(frozen=True, slots=True)
class TelegramNotificationBatch:
    claimed: int
    succeeded: int
    failed: int
    skipped: int
    dead_lettered: int = 0


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
    max_attempts: int = 5,
    retry_base_seconds: float = 60.0,
    retry_cap_seconds: float = 900.0,
    timeout_seconds: float = 10.0,
    retry_after_cap_seconds: float = 30.0,
    circuit_failure_threshold: int = 5,
    circuit_open_seconds: float = 30.0,
) -> TelegramNotificationBatch:
    """Entrega um lote mantendo locks e tentativas na transação do chamador."""
    events = claim_unconsumed_events(
        session,
        consumer_name=TELEGRAM_NOTIFICATION_CONSUMER,
        limit=limit,
        event_types=_NOTIFICATION_EVENT_TYPES,
        max_attempts=max_attempts,
    )
    succeeded = 0
    failed = 0
    skipped = 0
    dead_lettered = 0
    for event in events:
        failure_code: str | None = None
        outcome = ConsumptionOutcome.SUCCEEDED
        retry_after: float | None = None
        permanent = False
        try:
            recipient, text = _prepare_notification(session, event)
            await send_message(
                recipient,
                text,
                bot_token=bot_token,
                timeout_seconds=timeout_seconds,
                retry_after_cap_seconds=retry_after_cap_seconds,
                circuit_failure_threshold=circuit_failure_threshold,
                circuit_open_seconds=circuit_open_seconds,
            )
        except TelegramNotificationSkipped:
            outcome = ConsumptionOutcome.SKIPPED
        except TelegramNotificationError as error:
            failure_code = error.failure_code
            permanent = error.permanent
        except TelegramDeliveryAmbiguous as error:
            failure_code = error.code
            permanent = True
        except TelegramBotAPIError as error:
            failure_code = error.code
            permanent = not error.transient
            retry_after = error.retry_after_seconds
        except TelegramDeliveryError as error:
            failure_code = error.code
            permanent = not error.transient
        except ConnectionError:
            failure_code = "telegram_api_unavailable"
            permanent = False

        attempted_at = utc_now()
        next_retry_at = None
        if failure_code is not None:
            failures = count_failed_attempts(
                session,
                event_id=event.id,
                consumer_name=TELEGRAM_NOTIFICATION_CONSUMER,
            )
            if permanent or failures + 1 >= max_attempts:
                outcome = ConsumptionOutcome.DEAD_LETTERED
                observe_resilience_event("telegram", "dead_lettered")
            else:
                outcome = ConsumptionOutcome.FAILED
                observe_resilience_event("telegram", "retry")
                delay = _retry_delay(
                    failures + 1,
                    base_seconds=retry_base_seconds,
                    cap_seconds=retry_cap_seconds,
                    retry_after=retry_after,
                )
                next_retry_at = attempted_at + timedelta(seconds=delay)
        record_consumption_attempt(
            session,
            event=event,
            consumer_name=TELEGRAM_NOTIFICATION_CONSUMER,
            outcome=outcome,
            attempted_at=attempted_at,
            failure_code=failure_code,
            next_retry_at=next_retry_at,
        )
        if outcome is ConsumptionOutcome.SUCCEEDED:
            succeeded += 1
        elif outcome is ConsumptionOutcome.SKIPPED:
            skipped += 1
        elif outcome is ConsumptionOutcome.DEAD_LETTERED:
            dead_lettered += 1
            logger.warning(
                "telegram_notification_dead_lettered",
                extra={"notification_failure_code": failure_code},
            )
        else:
            failed += 1
            logger.warning(
                "telegram_notification_failed",
                extra={
                    "event_id": str(event.id),
                    "notification_failure_code": failure_code,
                },
            )
    return TelegramNotificationBatch(
        len(events), succeeded, failed, skipped, dead_lettered
    )


def _prepare_notification(session: Session, event: Event) -> tuple[int, str]:
    if event.mission_id is None:
        raise TelegramNotificationError("notification_mission_missing")
    mission = session.get(Mission, event.mission_id)
    if mission is None:
        raise TelegramNotificationError("notification_mission_missing")
    user = session.get(User, mission.user_id)
    if user is None:
        raise TelegramNotificationError("notification_recipient_missing")
    if not notification_is_enabled(user, event.event_type):
        raise TelegramNotificationSkipped
    if user.telegram_chat_id is None:
        raise TelegramNotificationError(
            "notification_recipient_missing", permanent=False
        )
    if not user.is_active:
        raise TelegramNotificationError(
            "notification_recipient_inactive", permanent=False
        )
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


def _retry_delay(
    attempt: int,
    *,
    base_seconds: float,
    cap_seconds: float,
    retry_after: float | None,
) -> float:
    if base_seconds <= 0 or cap_seconds <= 0:
        raise ValueError("retry delays must be positive")
    if retry_after is not None:
        return max(0.001, min(retry_after, cap_seconds))
    ceiling = min(cap_seconds, base_seconds * (2 ** max(0, attempt - 1)))
    return max(0.001, random.uniform(ceiling / 2, ceiling))
