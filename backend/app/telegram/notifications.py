"""Consumidor de alertas de preço com entrega proativa pelo Telegram."""

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.authentication.models import CredentialAction, UserAuthSession
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
TELEGRAM_AUTH_NOTIFICATION_CONSUMER = "telegram_auth_notifications_v1"
_NOTIFICATION_EVENT_TYPES = (
    EventType.PRICE_DECREASED_V1.value,
    EventType.PRICE_TARGET_REACHED_V1.value,
)
_AUTHENTICATION_EVENT_TYPES = (
    EventType.AUTHENTICATION_COMPLETED_V1.value,
    EventType.AUTHENTICATION_SESSION_EXPIRING_V1.value,
    EventType.AUTHENTICATION_SESSION_EXPIRED_V1.value,
)
_BRAZIL_TIMEZONE = ZoneInfo("America/Sao_Paulo")


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
    _consumer_name: str = TELEGRAM_NOTIFICATION_CONSUMER,
    _event_types: tuple[str, ...] = _NOTIFICATION_EVENT_TYPES,
) -> TelegramNotificationBatch:
    """Entrega um lote mantendo locks e tentativas na transação do chamador."""
    events = claim_unconsumed_events(
        session,
        consumer_name=_consumer_name,
        limit=limit,
        event_types=_event_types,
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
                consumer_name=_consumer_name,
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
            consumer_name=_consumer_name,
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


async def process_telegram_authentication_notifications(
    session: Session,
    *,
    bot_token: SecretStr,
    limit: int = 50,
    **kwargs: object,
) -> TelegramNotificationBatch:
    """Entrega confirmações e avisos de autenticação em consumidor próprio."""
    return await process_telegram_notifications(
        session,
        bot_token=bot_token,
        limit=limit,
        _consumer_name=TELEGRAM_AUTH_NOTIFICATION_CONSUMER,
        _event_types=_AUTHENTICATION_EVENT_TYPES,
        **kwargs,
    )


def _prepare_notification(session: Session, event: Event) -> tuple[int, str]:
    try:
        event_type = EventType(event.event_type)
    except ValueError:
        raise TelegramNotificationError("notification_payload_invalid") from None
    if event_type in {
        EventType.AUTHENTICATION_COMPLETED_V1,
        EventType.AUTHENTICATION_SESSION_EXPIRING_V1,
        EventType.AUTHENTICATION_SESSION_EXPIRED_V1,
    }:
        return _prepare_authentication_notification(session, event, event_type)
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


def _prepare_authentication_notification(
    session: Session, event: Event, event_type: EventType
) -> tuple[int, str]:
    payload = event.payload
    if not isinstance(payload, dict) or event.mission_id is not None:
        raise TelegramNotificationError("notification_payload_invalid")
    user_id = _required_uuid(payload, "user_id")
    auth_session: UserAuthSession | None = None
    if event_type is EventType.AUTHENTICATION_COMPLETED_V1:
        if event.aggregate_type != "user" or event.aggregate_id != user_id:
            raise TelegramNotificationError("notification_payload_invalid")
    else:
        session_id = _required_uuid(payload, "session_id")
        if event.aggregate_type != "auth_session" or event.aggregate_id != session_id:
            raise TelegramNotificationError("notification_payload_invalid")
        auth_session = session.get(UserAuthSession, session_id)
        if auth_session is None or auth_session.user_id != user_id:
            raise TelegramNotificationError("notification_payload_invalid")
        expires_at = _required_datetime(payload, "expires_at")
        if auth_session.expires_at != expires_at:
            raise TelegramNotificationError("notification_payload_invalid")
        if (
            event_type is EventType.AUTHENTICATION_SESSION_EXPIRING_V1
            and event.occurred_at >= expires_at
        ) or (
            event_type is EventType.AUTHENTICATION_SESSION_EXPIRED_V1
            and event.occurred_at < expires_at
        ):
            raise TelegramNotificationError("notification_payload_invalid")
        if auth_session.revoked_at is not None:
            raise TelegramNotificationSkipped
        if (
            event_type is EventType.AUTHENTICATION_SESSION_EXPIRING_V1
            and auth_session.expires_at <= utc_now()
        ):
            raise TelegramNotificationSkipped
    user = session.get(User, user_id)
    if user is None:
        raise TelegramNotificationError("notification_recipient_missing")
    if (
        auth_session is not None
        and user.telegram_user_id != auth_session.telegram_user_id
    ):
        raise TelegramNotificationError("notification_payload_invalid")
    if user.telegram_chat_id is None:
        raise TelegramNotificationError(
            "notification_recipient_missing", permanent=False
        )
    if not user.is_active:
        raise TelegramNotificationError(
            "notification_recipient_inactive", permanent=False
        )
    return user.telegram_chat_id, _render_authentication_message(event_type, payload)


def _render_authentication_message(
    event_type: EventType, payload: dict[str, object]
) -> str:
    if event_type is EventType.AUTHENTICATION_COMPLETED_V1:
        try:
            action = CredentialAction(_required_text(payload, "action"))
        except ValueError:
            raise TelegramNotificationError("notification_payload_invalid") from None
        return {
            CredentialAction.SET_PASSWORD: (
                "✅ Senha criada com sucesso.\nAgora use /entrar para fazer login."
            ),
            CredentialAction.LOGIN: (
                "✅ Login realizado com sucesso.\nSua sessão ficará ativa por 12 horas."
            ),
            CredentialAction.CHANGE_PASSWORD: (
                "✅ Senha alterada com sucesso.\n"
                "As sessões anteriores foram encerradas. Use /entrar novamente."
            ),
            CredentialAction.RECOVER_PASSWORD: (
                "✅ Senha recuperada com sucesso.\n"
                "As sessões anteriores foram encerradas. Use /entrar novamente."
            ),
        }[action]
    expires_at = _required_datetime(payload, "expires_at")
    if event_type is EventType.AUTHENTICATION_SESSION_EXPIRING_V1:
        local_expiry = expires_at.astimezone(_BRAZIL_TIMEZONE)
        return (
            "⏳ Sua sessão expira em breve, em "
            f"{local_expiry:%d/%m/%Y às %H:%M} (horário de Brasília).\n"
            "Depois da expiração, use /entrar para autenticar novamente."
        )
    if event_type is EventType.AUTHENTICATION_SESSION_EXPIRED_V1:
        return "🔒 Sua sessão expirou. Use /entrar para autenticar novamente."
    raise TelegramNotificationError("notification_payload_invalid")


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


def _required_text(payload: dict[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise TelegramNotificationError("notification_payload_invalid")
    return value


def _required_uuid(payload: dict[str, object], field: str) -> UUID:
    try:
        return UUID(_required_text(payload, field))
    except ValueError:
        raise TelegramNotificationError("notification_payload_invalid") from None


def _required_datetime(payload: dict[str, object], field: str) -> datetime:
    try:
        value = datetime.fromisoformat(_required_text(payload, field))
    except ValueError:
        raise TelegramNotificationError("notification_payload_invalid") from None
    if value.tzinfo is None or value.utcoffset() is None:
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
