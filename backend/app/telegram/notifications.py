"""Consumidor de alertas de preço com entrega proativa pelo Telegram."""

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.authentication.models import CredentialAction, UserAuthSession
from app.database.time import utc_now
from app.events import ConsumptionOutcome, Event, EventType
from app.events.consumption import (
    claim_unconsumed_events_async,
    count_failed_attempts_async,
    record_consumption_attempt_async,
)
from app.missions.models import Mission
from app.observability.metrics import observe_resilience_event
from app.offers.models import Offer
from app.products.models import Product
from app.stores.models import Store
from app.telegram.bot_api import (
    TelegramBotAPIError,
    TelegramDeliveryAmbiguous,
    TelegramDeliveryError,
    send_message,
)
from app.telegram.contracts import TelegramChatType, TelegramMessage
from app.telegram.formatting import format_money
from app.telegram.preferences import notification_is_enabled
from app.users.models import User

logger = logging.getLogger("app.telegram.notifications")

TELEGRAM_NOTIFICATION_CONSUMER = "telegram_price_alerts_v1"
TELEGRAM_AUTH_NOTIFICATION_CONSUMER = "telegram_auth_notifications_v1"
TELEGRAM_PRELIST_CONSUMER = "telegram_prelist_v1"
_NOTIFICATION_EVENT_TYPES = (
    EventType.PRICE_DECREASED_V1.value,
    EventType.PRICE_TARGET_REACHED_V1.value,
)
_AUTHENTICATION_EVENT_TYPES = (
    EventType.AUTHENTICATION_COMPLETED_V1.value,
    EventType.AUTHENTICATION_SESSION_EXPIRING_V1.value,
    EventType.AUTHENTICATION_SESSION_EXPIRED_V1.value,
)
# TASK-068: consumer próprio, separado dos alertas de queda/alvo (TASK-037)
# -- a pré-lista é informativa, não um alerta, e não fica sujeita às
# preferências notify_price_decreases/notify_target_reached.
_PRELIST_EVENT_TYPES = (
    EventType.MISSION_PRELIST_READY_V1.value,
    EventType.MISSION_PRELIST_ERRATA_V1.value,
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


@dataclass(frozen=True, slots=True)
class _PreparedNotification:
    """Uma notificação já pronta para envio, sem nada vinculado à sessão da
    Fase A -- só tipos primitivos, para nunca disparar lazy-load na Fase B
    (TASK-080)."""

    event_id: UUID
    chat_id: int
    text: str


async def process_telegram_notifications(
    session_factory: async_sessionmaker[AsyncSession],
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
    """Entrega um lote em três fases (TASK-080, mesmo padrão da TASK-079):

    Fase A (transação curta): reivindica os eventos e prepara
    destinatário/texto -- eventos que falham na preparação (dados
    inválidos, destinatário ausente, preferência desativada) já são
    resolvidos aqui, sem nenhum I/O externo. `COMMIT` libera o lock de
    reivindicação imediatamente, antes de qualquer envio.

    Fase B (sem transação): envia cada notificação preparada via Telegram.

    Fase C (transação curta, uma por evento -- nunca em lote): registra o
    resultado do envio e decide retry/dead-letter. Cada evento é
    confirmado (`COMMIT`) individualmente, para que uma falha num evento
    posterior do mesmo lote nunca desfaça o registro de um evento anterior
    já entregue com sucesso.

    Janela de crash conhecida (idêntica, em espírito, à do código anterior
    a esta TASK): se o processo for encerrado exatamente entre o retorno
    bem-sucedido de `send_message` e o `COMMIT` da Fase C daquele evento, a
    mensagem já foi entregue mas o consumo não fica registrado -- o
    próximo lote reivindica o mesmo evento de novo e reenvia (semântica
    "ao menos uma vez", nunca "exatamente uma vez", sem mudança nesta
    TASK). A diferença é que essa janela agora é por evento, não mais por
    lote inteiro: eventos já confirmados antes do crash permanecem
    confirmados.
    """
    (
        claimed,
        to_send,
        succeeded,
        failed,
        skipped,
        dead_lettered,
    ) = await _claim_and_prepare(
        session_factory,
        consumer_name=_consumer_name,
        event_types=_event_types,
        limit=limit,
        max_attempts=max_attempts,
        retry_base_seconds=retry_base_seconds,
        retry_cap_seconds=retry_cap_seconds,
    )
    for prepared in to_send:
        outcome = await _send_and_record(
            session_factory,
            prepared,
            bot_token=bot_token,
            consumer_name=_consumer_name,
            max_attempts=max_attempts,
            retry_base_seconds=retry_base_seconds,
            retry_cap_seconds=retry_cap_seconds,
            timeout_seconds=timeout_seconds,
            retry_after_cap_seconds=retry_after_cap_seconds,
            circuit_failure_threshold=circuit_failure_threshold,
            circuit_open_seconds=circuit_open_seconds,
        )
        if outcome is ConsumptionOutcome.SUCCEEDED:
            succeeded += 1
        elif outcome is ConsumptionOutcome.DEAD_LETTERED:
            dead_lettered += 1
        else:
            failed += 1
    return TelegramNotificationBatch(claimed, succeeded, failed, skipped, dead_lettered)


async def process_telegram_authentication_notifications(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    bot_token: SecretStr,
    limit: int = 50,
    **kwargs: object,
) -> TelegramNotificationBatch:
    """Entrega confirmações e avisos de autenticação em consumidor próprio."""
    return await process_telegram_notifications(
        session_factory,
        bot_token=bot_token,
        limit=limit,
        _consumer_name=TELEGRAM_AUTH_NOTIFICATION_CONSUMER,
        _event_types=_AUTHENTICATION_EVENT_TYPES,
        **kwargs,
    )


async def process_telegram_prelist_notifications(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    bot_token: SecretStr,
    limit: int = 50,
    **kwargs: object,
) -> TelegramNotificationBatch:
    """Entrega a pré-lista informativa (TASK-068) em consumidor próprio."""
    return await process_telegram_notifications(
        session_factory,
        bot_token=bot_token,
        limit=limit,
        _consumer_name=TELEGRAM_PRELIST_CONSUMER,
        _event_types=_PRELIST_EVENT_TYPES,
        **kwargs,
    )


async def _claim_and_prepare(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    consumer_name: str,
    event_types: tuple[str, ...],
    limit: int,
    max_attempts: int,
    retry_base_seconds: float,
    retry_cap_seconds: float,
) -> tuple[int, list[_PreparedNotification], int, int, int, int]:
    """Fase A (TASK-080): reivindica e prepara, tudo numa única transação
    curta -- nenhum `await` externo acontece aqui. Devolve
    `(claimed, to_send, succeeded=0, failed, skipped, dead_lettered)`;
    `succeeded` sempre começa em 0 porque sucesso só existe depois do
    envio (Fase B/C)."""
    to_send: list[_PreparedNotification] = []
    failed = 0
    skipped = 0
    dead_lettered = 0
    async with session_factory() as session, session.begin():
        events = await claim_unconsumed_events_async(
            session,
            consumer_name=consumer_name,
            limit=limit,
            event_types=event_types,
            max_attempts=max_attempts,
        )
        for event in events:
            try:
                chat_id, text = await _prepare_notification_async(session, event)
            except TelegramNotificationSkipped:
                await _decide_and_record_outcome(
                    session,
                    event,
                    consumer_name=consumer_name,
                    initial_outcome=ConsumptionOutcome.SKIPPED,
                    failure_code=None,
                    permanent=False,
                    retry_after=None,
                    max_attempts=max_attempts,
                    retry_base_seconds=retry_base_seconds,
                    retry_cap_seconds=retry_cap_seconds,
                )
                skipped += 1
                continue
            except TelegramNotificationError as error:
                outcome = await _decide_and_record_outcome(
                    session,
                    event,
                    consumer_name=consumer_name,
                    initial_outcome=ConsumptionOutcome.FAILED,
                    failure_code=error.failure_code,
                    permanent=error.permanent,
                    retry_after=None,
                    max_attempts=max_attempts,
                    retry_base_seconds=retry_base_seconds,
                    retry_cap_seconds=retry_cap_seconds,
                )
                if outcome is ConsumptionOutcome.DEAD_LETTERED:
                    dead_lettered += 1
                else:
                    failed += 1
                continue
            to_send.append(
                _PreparedNotification(event_id=event.id, chat_id=chat_id, text=text)
            )
    return len(events), to_send, 0, failed, skipped, dead_lettered


async def _send_and_record(
    session_factory: async_sessionmaker[AsyncSession],
    prepared: _PreparedNotification,
    *,
    bot_token: SecretStr,
    consumer_name: str,
    max_attempts: int,
    retry_base_seconds: float,
    retry_cap_seconds: float,
    timeout_seconds: float,
    retry_after_cap_seconds: float,
    circuit_failure_threshold: int,
    circuit_open_seconds: float,
) -> ConsumptionOutcome:
    """Fase B (envio, sem transação) + Fase C (registro, transação curta
    por evento) -- TASK-080."""
    failure_code: str | None = None
    permanent = False
    retry_after: float | None = None
    try:
        await send_message(
            prepared.chat_id,
            prepared.text,
            bot_token=bot_token,
            timeout_seconds=timeout_seconds,
            retry_after_cap_seconds=retry_after_cap_seconds,
            circuit_failure_threshold=circuit_failure_threshold,
            circuit_open_seconds=circuit_open_seconds,
        )
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

    async with session_factory() as session, session.begin():
        event = await session.get(Event, prepared.event_id)
        if event is None:
            # Eventos são append-only (nunca apagados) -- só chegaria aqui
            # por corrupção de dados externa a este fluxo; sem outcome
            # seguro para registrar, encerra sem tentar de novo aqui.
            raise TelegramNotificationError(
                "notification_event_missing", permanent=True
            )
        outcome = await _decide_and_record_outcome(
            session,
            event,
            consumer_name=consumer_name,
            initial_outcome=ConsumptionOutcome.SUCCEEDED,
            failure_code=failure_code,
            permanent=permanent,
            retry_after=retry_after,
            max_attempts=max_attempts,
            retry_base_seconds=retry_base_seconds,
            retry_cap_seconds=retry_cap_seconds,
        )
    return outcome


async def _decide_and_record_outcome(
    session: AsyncSession,
    event: Event,
    *,
    consumer_name: str,
    initial_outcome: ConsumptionOutcome,
    failure_code: str | None,
    permanent: bool,
    retry_after: float | None,
    max_attempts: int,
    retry_base_seconds: float,
    retry_cap_seconds: float,
) -> ConsumptionOutcome:
    """Decide FAILED/DEAD_LETTERED (quando há falha) ou aceita o outcome já
    terminal (SUCCEEDED/SKIPPED) e registra -- compartilhado entre falha de
    preparação (Fase A) e falha/sucesso de envio (Fase C), para as duas
    nunca divergirem de critério de retry (TASK-080)."""
    attempted_at = utc_now()
    outcome = initial_outcome
    next_retry_at = None
    if failure_code is not None:
        failures = await count_failed_attempts_async(
            session, event_id=event.id, consumer_name=consumer_name
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
    await record_consumption_attempt_async(
        session,
        event=event,
        consumer_name=consumer_name,
        outcome=outcome,
        attempted_at=attempted_at,
        failure_code=failure_code,
        next_retry_at=next_retry_at,
    )
    if outcome is ConsumptionOutcome.DEAD_LETTERED:
        logger.warning(
            "telegram_notification_dead_lettered",
            extra={"notification_failure_code": failure_code},
        )
    elif outcome is ConsumptionOutcome.FAILED:
        logger.warning(
            "telegram_notification_failed",
            extra={
                "event_id": str(event.id),
                "notification_failure_code": failure_code,
            },
        )
    return outcome


async def _prepare_notification_async(
    session: AsyncSession, event: Event
) -> tuple[int, str]:
    try:
        event_type = EventType(event.event_type)
    except ValueError:
        raise TelegramNotificationError("notification_payload_invalid") from None
    if event_type in {
        EventType.AUTHENTICATION_COMPLETED_V1,
        EventType.AUTHENTICATION_SESSION_EXPIRING_V1,
        EventType.AUTHENTICATION_SESSION_EXPIRED_V1,
    }:
        return await _prepare_authentication_notification_async(
            session, event, event_type
        )
    if event_type in {
        EventType.MISSION_PRELIST_READY_V1,
        EventType.MISSION_PRELIST_ERRATA_V1,
    }:
        return await _prepare_prelist_notification_async(session, event, event_type)
    if event.mission_id is None:
        raise TelegramNotificationError("notification_mission_missing")
    mission = await session.get(Mission, event.mission_id)
    if mission is None:
        raise TelegramNotificationError("notification_mission_missing")
    user = await session.get(User, mission.user_id)
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
    offer, product, store = await _resolve_offer_context_async(session, event)
    return (
        user.telegram_chat_id,
        _render_alert(event, mission.title, offer, product, store),
    )


async def _resolve_offer_context_async(
    session: AsyncSession, event: Event
) -> tuple[Offer, Product, Store]:
    """Busca a oferta real do alerta a partir do `offer_id` do evento.

    TASK-063: o alerta precisa representar o anúncio real (título, loja,
    link), não a missão. Preço, moeda e disponibilidade continuam vindo só
    do `payload` do evento -- esta função nunca os lê nem os altera.
    """
    payload = event.payload
    if not isinstance(payload, dict):
        raise TelegramNotificationError("notification_payload_invalid")
    offer_id = _required_uuid(payload, "offer_id")
    offer = await session.get(Offer, offer_id)
    if offer is None:
        raise TelegramNotificationError("notification_payload_invalid")
    product = await session.get(Product, offer.product_id)
    store = await session.get(Store, offer.store_id)
    if product is None or store is None:
        raise TelegramNotificationError("notification_payload_invalid")
    return offer, product, store


async def _prepare_prelist_notification_async(
    session: AsyncSession, event: Event, event_type: EventType
) -> tuple[int, str]:
    """TASK-068: nunca sujeita a `notification_is_enabled` -- a pré-lista
    dispara no máximo uma vez (mais a correção, também no máximo uma vez),
    fora das preferências de queda/alvo da TASK-037."""
    if event.mission_id is None:
        raise TelegramNotificationError("notification_mission_missing")
    mission = await session.get(Mission, event.mission_id)
    if mission is None:
        raise TelegramNotificationError("notification_mission_missing")
    user = await session.get(User, mission.user_id)
    if user is None:
        raise TelegramNotificationError("notification_recipient_missing")
    if user.telegram_chat_id is None:
        raise TelegramNotificationError(
            "notification_recipient_missing", permanent=False
        )
    if not user.is_active:
        raise TelegramNotificationError(
            "notification_recipient_inactive", permanent=False
        )
    if event_type is EventType.MISSION_PRELIST_READY_V1:
        text = await _render_prelist_ready_async(session, event, mission.title)
    else:
        text = await _render_prelist_errata_async(session, event, mission.title)
    return user.telegram_chat_id, text


async def _prepare_authentication_notification_async(
    session: AsyncSession, event: Event, event_type: EventType
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
        auth_session = await session.get(UserAuthSession, session_id)
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
    user = await session.get(User, user_id)
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
                "✅ Senha criada com sucesso!\n\nAgora use /entrar para fazer login."
            ),
            CredentialAction.LOGIN: (
                "✅ Login realizado com sucesso!\n\n"
                "Sua sessão ficará ativa por 12 horas."
            ),
            CredentialAction.CHANGE_PASSWORD: (
                "✅ Senha alterada com sucesso!\n\n"
                "As sessões anteriores foram encerradas — use /entrar novamente."
            ),
            CredentialAction.RECOVER_PASSWORD: (
                "✅ Senha recuperada com sucesso!\n\n"
                "As sessões anteriores foram encerradas — use /entrar novamente."
            ),
        }[action]
    expires_at = _required_datetime(payload, "expires_at")
    if event_type is EventType.AUTHENTICATION_SESSION_EXPIRING_V1:
        local_expiry = expires_at.astimezone(_BRAZIL_TIMEZONE)
        return (
            "⏳ Sua sessão expira em breve\n\n"
            f"{local_expiry:%d/%m/%Y às %H:%M} (horário de Brasília). "
            "Depois disso, use /entrar para autenticar novamente."
        )
    if event_type is EventType.AUTHENTICATION_SESSION_EXPIRED_V1:
        return "🔒 Sua sessão expirou.\n\nUse /entrar para autenticar novamente."
    raise TelegramNotificationError("notification_payload_invalid")


def _render_alert(
    event: Event,
    mission_title: str,
    offer: Offer,
    product: Product,
    store: Store,
) -> str:
    """Monta a mensagem do alerta com dados reais da oferta.

    TASK-063: nome do produto, loja e link vêm sempre do anúncio real
    (`product`/`store`/`offer`); a missão aparece só como contexto
    secundário. Preço, moeda e disponibilidade continuam vindo
    exclusivamente do `payload` do evento -- nunca são inferidos aqui. O
    template é sempre uma string fixa no código, nunca gerada por IA;
    `product.display_name` (normalizado por IA, TASK-063) é usado quando
    disponível, com `product.name` (título bruto) como alternativa segura
    quando a normalização ainda não aconteceu ou falhou.
    """
    payload = event.payload
    if not isinstance(payload, dict):
        raise TelegramNotificationError("notification_payload_invalid")
    display_name = product.display_name or product.name
    try:
        event_type = EventType(event.event_type)
        currency = _currency(payload)
        current_total = _money(payload, "current_total")
        if event_type is EventType.PRICE_DECREASED_V1:
            previous_total = _money(payload, "previous_total")
            return (
                "📉 QUEDA DE PREÇO\n\n"
                f"{display_name}\n\n"
                f"🏪 {store.name}\n"
                f"💰 {format_money(current_total, currency)} "
                f"(antes: {format_money(previous_total, currency)})\n"
                f"🔎 Missão: {mission_title}\n\n"
                "🔗 Ver anúncio\n"
                f"{offer.url}"
            )
        if event_type is EventType.PRICE_TARGET_REACHED_V1:
            target_total = _money(payload, "target_total")
            if payload.get("mission_id") != str(event.mission_id):
                raise TelegramNotificationError("notification_payload_invalid")
            return (
                "🔥 PREÇO ENCONTRADO\n\n"
                f"{display_name}\n\n"
                f"🏪 {store.name}\n"
                f"💰 {format_money(current_total, currency)}\n"
                f"🎯 Alvo: {format_money(target_total, currency)}\n"
                f"🔎 Missão: {mission_title}\n\n"
                "🔗 Ver anúncio\n"
                f"{offer.url}"
            )
    except InvalidOperation, TypeError, ValueError:
        raise TelegramNotificationError("notification_payload_invalid") from None
    raise TelegramNotificationError("notification_payload_invalid")


async def _load_offer_context_async(
    session: AsyncSession, offer_id: UUID
) -> tuple[Offer, Product, Store]:
    offer = await session.get(Offer, offer_id)
    if offer is None:
        raise TelegramNotificationError("notification_payload_invalid")
    product = await session.get(Product, offer.product_id)
    store = await session.get(Store, offer.store_id)
    if product is None or store is None:
        raise TelegramNotificationError("notification_payload_invalid")
    return offer, product, store


_PRELIST_SHIPPING_DISCLAIMER = (
    "⚠️ Valores sem frete. O frete será calculado/consultado na loja."
)


async def _render_prelist_block_async(
    session: AsyncSession, offer_id: UUID, amount: Decimal, currency: str
) -> str:
    offer, product, store = await _load_offer_context_async(session, offer_id)
    display_name = product.display_name or product.name
    return (
        f"🏪 {store.name}\n"
        f"{display_name}\n"
        f"💰 {format_money(amount, currency)}\n"
        "🔗 Ver anúncio\n"
        f"{offer.url}"
    )


async def _render_prelist_ready_async(
    session: AsyncSession, event: Event, mission_title: str
) -> str:
    """TASK-068: até 2 ofertas já encontradas, sem julgamento -- string fixa.

    Ranqueadas por `amount` (preço do produto), sem frete -- ver
    `_PRELIST_SHIPPING_DISCLAIMER`.
    """
    payload = event.payload
    if not isinstance(payload, dict):
        raise TelegramNotificationError("notification_payload_invalid")
    try:
        first_offer_id = _required_uuid(payload, "first_offer_id")
        first_amount = _money(payload, "first_amount")
        first_currency = _currency(payload, "first_currency")
        blocks = [
            await _render_prelist_block_async(
                session, first_offer_id, first_amount, first_currency
            )
        ]
        if payload.get("second_offer_id") is not None:
            second_offer_id = _required_uuid(payload, "second_offer_id")
            second_amount = _money(payload, "second_amount")
            second_currency = _currency(payload, "second_currency")
            blocks.append(
                await _render_prelist_block_async(
                    session, second_offer_id, second_amount, second_currency
                )
            )
    except InvalidOperation, TypeError, ValueError:
        raise TelegramNotificationError("notification_payload_invalid") from None
    return (
        "🧾 MELHORES OFERTAS ENCONTRADAS ATÉ AGORA\n\n"
        f"Missão: {mission_title}\n\n"
        "Das lojas que você selecionou, essas são as melhores ofertas "
        "encontradas até agora:\n\n"
        + "\n\n".join(blocks)
        + f"\n\n{_PRELIST_SHIPPING_DISCLAIMER}"
        "\n\nAinda estamos buscando nas outras lojas -- você será avisado "
        "se encontrarmos algo melhor."
    )


async def _render_prelist_errata_async(
    session: AsyncSession, event: Event, mission_title: str
) -> str:
    """TASK-068: única correção da pré-lista -- string fixa, sem julgamento.

    Comparação por `amount` (preço do produto), sem frete -- ver
    `_PRELIST_SHIPPING_DISCLAIMER`.
    """
    payload = event.payload
    if not isinstance(payload, dict):
        raise TelegramNotificationError("notification_payload_invalid")
    try:
        offer_id = _required_uuid(payload, "offer_id")
        current_amount = _money(payload, "current_amount")
        currency = _currency(payload)
        had_previous = payload.get("previous_lowest_amount") is not None
    except InvalidOperation, TypeError, ValueError:
        raise TelegramNotificationError("notification_payload_invalid") from None
    offer, product, store = await _load_offer_context_async(session, offer_id)
    display_name = product.display_name or product.name
    if had_previous:
        header = "✏️ CORREÇÃO DA PRÉ-LISTA\n\n"
        note = (
            "Uma das lojas que ainda estava buscando encontrou um preço "
            "melhor do que o mostrado antes:"
        )
    else:
        header = "🧾 PRIMEIRA OFERTA RELEVANTE ENCONTRADA\n\n"
        note = (
            "Ainda não tínhamos encontrado nenhuma oferta relevante para "
            "essa missão -- aqui está a primeira:"
        )
    return (
        f"{header}"
        f"Missão: {mission_title}\n\n"
        f"{note}\n\n"
        f"🏪 {store.name}\n"
        f"{display_name}\n"
        f"💰 {format_money(current_amount, currency)}\n"
        "🔗 Ver anúncio\n"
        f"{offer.url}\n\n"
        f"{_PRELIST_SHIPPING_DISCLAIMER}"
    )


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


def _currency(payload: dict, field: str = "currency") -> str:
    currency = _required_text(payload, field)
    if (
        len(currency) != 3
        or not currency.isascii()
        or not currency.isalpha()
        or not currency.isupper()
    ):
        raise TelegramNotificationError("notification_payload_invalid")
    return currency


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
