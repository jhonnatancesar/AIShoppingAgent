"""Consumidor de alertas de preço com entrega proativa pelo Telegram."""

import html
import logging
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.authentication.models import CredentialAction, UserAuthSession
from app.collection.contracts import InstallmentInterestKind, MarketplacePartyKind
from app.collection.models import OfferInstallmentOption, PriceObservation
from app.database.time import utc_now
from app.events import ConsumptionOutcome, Event, EventType
from app.events.consumption import (
    claim_unconsumed_events_async,
    count_failed_attempts_async,
    record_consumption_attempt_async,
)
from app.events.models import EventDeliveryCheckpoint
from app.missions.models import Mission
from app.observability.metrics import observe_resilience_event
from app.offers.models import Offer
from app.offers.short_links import build_offer_short_url, get_or_create_offer_short_link
from app.products.models import Product
from app.stores.models import Store
from app.telegram.bot_api import (
    TelegramBotAPIError,
    TelegramDeliveryAmbiguous,
    TelegramDeliveryError,
    TelegramMediaRejected,
    send_message,
    send_photo,
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
class _PreparedMessagePart:
    offer_id: UUID | None
    message_part: int
    text: str
    image_url: str | None = None


@dataclass(frozen=True, slots=True)
class _PreparedNotification:
    """Uma notificação pronta, sem objetos vinculados à sessão da Fase A."""

    event_id: UUID
    chat_id: int
    parts: tuple[_PreparedMessagePart, ...]


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
    public_base_url: str = "http://localhost:8000",
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
        public_base_url=public_base_url,
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
    public_base_url: str,
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
                chat_id, parts = await _prepare_notification_async(
                    session, event, public_base_url=public_base_url
                )
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
                _PreparedNotification(event_id=event.id, chat_id=chat_id, parts=parts)
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
        for part in prepared.parts:
            if part.offer_id is not None and await _checkpoint_exists(
                session_factory,
                consumer_name=consumer_name,
                event_id=prepared.event_id,
                part=part,
            ):
                continue
            await _send_part(
                prepared.chat_id,
                part,
                bot_token=bot_token,
                timeout_seconds=timeout_seconds,
                retry_after_cap_seconds=retry_after_cap_seconds,
                circuit_failure_threshold=circuit_failure_threshold,
                circuit_open_seconds=circuit_open_seconds,
            )
            if part.offer_id is not None:
                async with session_factory() as session, session.begin():
                    await session.execute(
                        postgresql_insert(EventDeliveryCheckpoint)
                        .values(
                            consumer_name=consumer_name,
                            event_id=prepared.event_id,
                            offer_id=part.offer_id,
                            message_part=part.message_part,
                        )
                        .on_conflict_do_nothing(
                            index_elements=(
                                "consumer_name",
                                "event_id",
                                "offer_id",
                                "message_part",
                            )
                        )
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


async def _checkpoint_exists(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    consumer_name: str,
    event_id: UUID,
    part: _PreparedMessagePart,
) -> bool:
    if part.offer_id is None:
        return False
    async with session_factory() as session:
        checkpoint = await session.scalar(
            select(EventDeliveryCheckpoint)
            .where(
                EventDeliveryCheckpoint.consumer_name == consumer_name,
                EventDeliveryCheckpoint.event_id == event_id,
                EventDeliveryCheckpoint.offer_id == part.offer_id,
                EventDeliveryCheckpoint.message_part == part.message_part,
            )
            .limit(1)
        )
        return isinstance(checkpoint, EventDeliveryCheckpoint)


async def _send_part(
    chat_id: int,
    part: _PreparedMessagePart,
    *,
    bot_token: SecretStr,
    timeout_seconds: float,
    retry_after_cap_seconds: float,
    circuit_failure_threshold: int,
    circuit_open_seconds: float,
) -> None:
    """Envia com `parse_mode="HTML"` sempre -- os links (`_telegram_link`)
    exigem entidade `<a href="...">` explícita para ficar clicáveis, e todo
    texto dinâmico dos templates já é escapado (`html.escape`) antes de
    chegar aqui; mensagens sem link (ex.: autenticação) são texto fixo
    seguro, sem caracteres HTML especiais."""
    kwargs = {
        "bot_token": bot_token,
        "parse_mode": "HTML",
        "timeout_seconds": timeout_seconds,
        "retry_after_cap_seconds": retry_after_cap_seconds,
        "circuit_failure_threshold": circuit_failure_threshold,
        "circuit_open_seconds": circuit_open_seconds,
    }
    if part.image_url is not None and len(part.text) <= 1024:
        try:
            await send_photo(chat_id, part.image_url, part.text, **kwargs)
            return
        except TelegramMediaRejected:
            pass
    await send_message(chat_id, part.text, **kwargs)


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
    session: AsyncSession, event: Event, *, public_base_url: str
) -> tuple[int, tuple[_PreparedMessagePart, ...]]:
    try:
        event_type = EventType(event.event_type)
    except ValueError:
        raise TelegramNotificationError("notification_payload_invalid") from None
    if event_type in {
        EventType.AUTHENTICATION_COMPLETED_V1,
        EventType.AUTHENTICATION_SESSION_EXPIRING_V1,
        EventType.AUTHENTICATION_SESSION_EXPIRED_V1,
    }:
        chat_id, text = await _prepare_authentication_notification_async(
            session, event, event_type
        )
        return chat_id, (_PreparedMessagePart(None, 0, text),)
    if event_type in {
        EventType.MISSION_PRELIST_READY_V1,
        EventType.MISSION_PRELIST_ERRATA_V1,
    }:
        return await _prepare_prelist_notification_async(
            session, event, event_type, public_base_url=public_base_url
        )
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
    (
        offer,
        product,
        store,
        observation,
        installment_options,
    ) = await _resolve_offer_context_async(session, event)
    link = await get_or_create_offer_short_link(session, offer.id)
    short_url = build_offer_short_url(public_base_url, link.token)
    return (
        user.telegram_chat_id,
        (
            _PreparedMessagePart(
                offer.id,
                0,
                _render_alert(
                    event,
                    mission.title,
                    offer,
                    product,
                    store,
                    observation,
                    installment_options,
                    short_url=short_url,
                ),
                offer.image_url,
            ),
        ),
    )


async def _resolve_offer_context_async(
    session: AsyncSession, event: Event
) -> tuple[Offer, Product, Store, PriceObservation, tuple[OfferInstallmentOption, ...]]:
    """Busca a oferta real do alerta a partir do `offer_id` do evento.

    TASK-063: o alerta precisa representar o anúncio real (título, loja,
    link), não a missão. Preço, moeda e disponibilidade continuam vindo só
    do `payload` do evento -- esta função nunca os lê nem os altera.
    """
    payload = event.payload
    if not isinstance(payload, dict):
        raise TelegramNotificationError("notification_payload_invalid")
    offer_id = _required_uuid(payload, "offer_id")
    observation_id = _required_uuid(payload, "observation_id")
    offer = await session.get(Offer, offer_id)
    if offer is None:
        raise TelegramNotificationError("notification_payload_invalid")
    product = await session.get(Product, offer.product_id)
    store = await session.get(Store, offer.store_id)
    observation = await session.get(PriceObservation, observation_id)
    if (
        product is None
        or store is None
        or observation is None
        or observation.offer_id != offer.id
    ):
        raise TelegramNotificationError("notification_payload_invalid")
    installment_options = await _installment_options_for_observation_async(
        session, observation.id
    )
    return offer, product, store, observation, installment_options


async def _installment_options_for_observation_async(
    session: AsyncSession, observation_id: UUID
) -> tuple[OfferInstallmentOption, ...]:
    """TASK-089 (extensão Telegram): opções reais persistidas para a
    observação exata sendo apresentada -- nunca a mais recente da oferta
    (essa observação já É a mais recente enviada nesta notificação),
    nunca recalculada."""
    return tuple(
        await session.scalars(
            select(OfferInstallmentOption)
            .where(OfferInstallmentOption.price_observation_id == observation_id)
            .order_by(OfferInstallmentOption.installment_count)
        )
    )


async def _prepare_prelist_notification_async(
    session: AsyncSession,
    event: Event,
    event_type: EventType,
    *,
    public_base_url: str,
) -> tuple[int, tuple[_PreparedMessagePart, ...]]:
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
        parts = await _render_prelist_ready_async(
            session, event, mission.title, public_base_url=public_base_url
        )
    else:
        parts = await _render_prelist_errata_async(
            session, event, mission.title, public_base_url=public_base_url
        )
    return user.telegram_chat_id, parts


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
                "✅ Senha criada com sucesso!\n\nAgora use /entrar para acessar sua conta."
            ),
            CredentialAction.LOGIN: (
                "✅ Login realizado com sucesso!\n\n"
                "Sua sessão ficará ativa por 12 horas."
            ),
            CredentialAction.CHANGE_PASSWORD: (
                "✅ Senha alterada com sucesso!\n\n"
                "As sessões anteriores foram encerradas.\n\n"
                "Use /entrar para acessar novamente."
            ),
            CredentialAction.RECOVER_PASSWORD: (
                "✅ Senha recuperada com sucesso!\n\n"
                "As sessões anteriores foram encerradas.\n\n"
                "Use /entrar para acessar novamente."
            ),
        }[action]
    expires_at = _required_datetime(payload, "expires_at")
    if event_type is EventType.AUTHENTICATION_SESSION_EXPIRING_V1:
        local_expiry = expires_at.astimezone(_BRAZIL_TIMEZONE)
        return (
            "⏳ Sua sessão expira em breve\n\n"
            "Ela expira em:\n"
            f"{local_expiry:%d/%m/%Y às %H:%M} — horário de Brasília\n\n"
            "Depois disso, use /entrar para acessar novamente."
        )
    if event_type is EventType.AUTHENTICATION_SESSION_EXPIRED_V1:
        return "🔒 Sua sessão expirou.\n\nUse /entrar para acessar novamente."
    raise TelegramNotificationError("notification_payload_invalid")


def _render_alert(
    event: Event,
    mission_title: str,
    offer: Offer,
    product: Product,
    store: Store,
    observation: PriceObservation,
    installment_options: Sequence[OfferInstallmentOption],
    *,
    short_url: str,
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
    display_name = html.escape(product.display_name or product.name)
    mission_title_safe = html.escape(mission_title)
    marketplace_line = _marketplace_party_line(store, observation)
    link_line = _telegram_link(short_url)
    try:
        event_type = EventType(event.event_type)
        currency = _currency(payload)
        current_total = _money(payload, "current_total")
        installment_line = _installment_line(installment_options, currency)
        if event_type is EventType.PRICE_DECREASED_V1:
            previous_total = _money(payload, "previous_total")
            return (
                "📉 O PREÇO CAIU\n\n"
                f"{display_name}\n\n"
                f"🏪 {html.escape(store.name)}\n"
                f"{marketplace_line}"
                f"💰 À vista: {format_money(current_total, currency)}\n"
                f"{installment_line}"
                f"↘️ Preço anterior: {format_money(previous_total, currency)}\n"
                f"🔎 Missão: {mission_title_safe}\n\n"
                "🔗 Ver anúncio\n"
                f"{link_line}"
            )
        if event_type is EventType.PRICE_TARGET_REACHED_V1:
            target_total = _money(payload, "target_total")
            if payload.get("mission_id") != str(event.mission_id):
                raise TelegramNotificationError("notification_payload_invalid")
            return (
                "🔥 PREÇO-ALVO ENCONTRADO\n\n"
                f"{display_name}\n\n"
                f"🏪 {html.escape(store.name)}\n"
                f"{marketplace_line}"
                f"💰 À vista: {format_money(current_total, currency)}\n"
                f"{installment_line}"
                f"🎯 Preço-alvo: {format_money(target_total, currency)}\n"
                f"🔎 Missão: {mission_title_safe}\n\n"
                "🔗 Ver anúncio\n"
                f"{link_line}"
            )
    except InvalidOperation, TypeError, ValueError:
        raise TelegramNotificationError("notification_payload_invalid") from None
    raise TelegramNotificationError("notification_payload_invalid")


async def _load_offer_context_async(
    session: AsyncSession, offer_id: UUID, observation_id: UUID
) -> tuple[Offer, Product, Store, PriceObservation, tuple[OfferInstallmentOption, ...]]:
    offer = await session.get(Offer, offer_id)
    if offer is None:
        raise TelegramNotificationError("notification_payload_invalid")
    product = await session.get(Product, offer.product_id)
    store = await session.get(Store, offer.store_id)
    observation = await session.get(PriceObservation, observation_id)
    if (
        product is None
        or store is None
        or observation is None
        or observation.offer_id != offer.id
    ):
        raise TelegramNotificationError("notification_payload_invalid")
    installment_options = await _installment_options_for_observation_async(
        session, observation.id
    )
    return offer, product, store, observation, installment_options


_INSTALLMENT_INTEREST_SUFFIXES = {
    InstallmentInterestKind.INTEREST_FREE: " sem juros",
    InstallmentInterestKind.WITH_INTEREST: " com juros",
    InstallmentInterestKind.UNKNOWN: "",
}


def _select_installment_summary_option(
    options: Sequence[OfferInstallmentOption],
) -> OfferInstallmentOption | None:
    """TASK-089 (extensão Telegram): escolhe UMA opção real para resumir
    no card -- nunca a tabela inteira, nunca uma opção inventada/calculada.

    Prioridade determinística, sem juízo de valor comercial:
    1. a opção que a própria loja destacou no card da busca
       (`is_highlighted=True`, carimbada em `providers/base.py`);
    2. na ausência de destaque conhecido, a maior `installment_count`
       entre as `interest_free` (mais parcelas sem juros é a condição
       mais informativa quando não se sabe qual a loja preferia mostrar);
    3. na ausência de qualquer `interest_free`, a maior `installment_count`
       disponível, seja qual for o `interest_kind`;
    4. sem nenhuma opção persistida, `None` -- a linha `💳` some.

    Nunca calcula custo efetivo, nunca compara "vantagem" financeira entre
    condições -- é só um critério de desempate determinístico."""
    if not options:
        return None
    highlighted = next((option for option in options if option.is_highlighted), None)
    if highlighted is not None:
        return highlighted
    interest_free = [
        option
        for option in options
        if option.interest_kind is InstallmentInterestKind.INTEREST_FREE
    ]
    pool = interest_free if interest_free else options
    return max(pool, key=lambda option: option.installment_count)


def _installment_line(options: Sequence[OfferInstallmentOption], currency: str) -> str:
    """Linha `💳 Parcelado: ...` ou string vazia -- nunca `None`/`NULL`/
    `unknown` literal, nunca total calculado (`installment_total_amount`
    só aparece quando a própria loja o declarou explicitamente)."""
    option = _select_installment_summary_option(options)
    if option is None:
        return ""
    interest_suffix = _INSTALLMENT_INTEREST_SUFFIXES[option.interest_kind]
    total_suffix = (
        f" — total {format_money(option.installment_total_amount, currency)}"
        if option.installment_total_amount is not None
        else ""
    )
    summary = (
        f"{option.installment_count}x de "
        f"{format_money(option.installment_amount, currency)}"
        f"{interest_suffix}{total_suffix}"
    )
    return f"💳 Parcelado: {summary}\n"


def _marketplace_party_line(store: Store, observation: PriceObservation) -> str:
    seller = observation.seller_kind
    fulfillment = observation.fulfillment_kind
    if seller is None and fulfillment is None:
        return ""
    store_name = html.escape(store.name)
    if seller is fulfillment is MarketplacePartyKind.PLATFORM:
        official = {
            "amazon": "Amazon.com.br",
            "kabum": "KaBuM!",
        }.get(store.code, store_name)
        return f"📦 Vendido e entregue por: {official}\n"
    if seller is fulfillment is MarketplacePartyKind.MARKETPLACE_PARTNER:
        return f"📦 Vendido e entregue por: Loja parceira {store_name}\n"
    if seller is fulfillment is MarketplacePartyKind.UNKNOWN:
        return "📦 Vendedor e entrega não identificados\n"
    labels = {
        MarketplacePartyKind.PLATFORM: store_name,
        MarketplacePartyKind.MARKETPLACE_PARTNER: f"Loja parceira {store_name}",
        MarketplacePartyKind.UNKNOWN: "Não identificado",
        None: "Não avaliado",
    }
    return f"📦 Vendido por: {labels[seller]}\n🚚 Entregue por: {labels[fulfillment]}\n"


def _telegram_link(url: str) -> str:
    """Link explícito via entidade HTML (`<a href="...">`) -- a Bot API não
    garante/documenta auto-detecção de URL em texto plano (`sendMessage`
    sem `parse_mode`); só uma entidade HTML explícita garante que o link
    fique clicável em qualquer cliente. Exige `parse_mode="HTML"` no envio
    (`_send_part`) e que todo o resto do texto já esteja escapado."""
    escaped = html.escape(url, quote=True)
    return f'<a href="{escaped}">{escaped}</a>'


_PRELIST_SHIPPING_DISCLAIMER = "⚠️ Frete não incluído. Consulte o valor na loja."


async def _render_prelist_block_async(
    session: AsyncSession,
    offer_id: UUID,
    observation_id: UUID,
    amount: Decimal,
    currency: str,
    *,
    position: int,
    mission_title: str,
    public_base_url: str,
    prefix: str = "",
    suffix: str = "",
) -> _PreparedMessagePart:
    (
        offer,
        product,
        store,
        observation,
        installment_options,
    ) = await _load_offer_context_async(session, offer_id, observation_id)
    link = await get_or_create_offer_short_link(session, offer.id)
    short_url = build_offer_short_url(public_base_url, link.token)
    display_name = html.escape(product.display_name or product.name)
    number = {1: "1️⃣", 2: "2️⃣"}.get(position, f"{position}.")
    text = (
        prefix + f"{number} {display_name}\n"
        f"🏪 {html.escape(store.name)}\n"
        f"{_marketplace_party_line(store, observation)}"
        f"💰 À vista: {format_money(amount, currency)}\n"
        f"{_installment_line(installment_options, currency)}"
        f"🔎 Missão: {html.escape(mission_title)}\n"
        f"{_PRELIST_SHIPPING_DISCLAIMER}\n"
        "🔗 Ver anúncio\n"
        f"{_telegram_link(short_url)}" + suffix
    )
    return _PreparedMessagePart(offer.id, position - 1, text, offer.image_url)


async def _render_prelist_ready_async(
    session: AsyncSession,
    event: Event,
    mission_title: str,
    *,
    public_base_url: str,
) -> tuple[_PreparedMessagePart, ...]:
    """TASK-084: uma parte independente por oferta, na ordem já ranqueada.

    Ranqueadas por `amount` (preço do produto), sem frete -- ver
    `_PRELIST_SHIPPING_DISCLAIMER`.
    """
    payload = event.payload
    if not isinstance(payload, dict):
        raise TelegramNotificationError("notification_payload_invalid")
    try:
        first_offer_id = _required_uuid(payload, "first_offer_id")
        first_observation_id = _required_uuid(payload, "first_observation_id")
        first_amount = _money(payload, "first_amount")
        first_currency = _currency(payload, "first_currency")
        parts = [
            await _render_prelist_block_async(
                session,
                first_offer_id,
                first_observation_id,
                first_amount,
                first_currency,
                position=1,
                mission_title=mission_title,
                public_base_url=public_base_url,
                prefix="🧾 MELHORES OFERTAS ENCONTRADAS ATÉ AGORA\n\n",
                suffix=(
                    "\n\nA busca continua nas outras lojas. Se eu encontrar uma "
                    "oferta melhor, aviso você."
                ),
            )
        ]
        if payload.get("second_offer_id") is not None:
            second_offer_id = _required_uuid(payload, "second_offer_id")
            second_observation_id = _required_uuid(payload, "second_observation_id")
            second_amount = _money(payload, "second_amount")
            second_currency = _currency(payload, "second_currency")
            parts.append(
                await _render_prelist_block_async(
                    session,
                    second_offer_id,
                    second_observation_id,
                    second_amount,
                    second_currency,
                    position=2,
                    mission_title=mission_title,
                    public_base_url=public_base_url,
                )
            )
    except InvalidOperation, TypeError, ValueError:
        raise TelegramNotificationError("notification_payload_invalid") from None
    return tuple(parts)


async def _render_prelist_errata_async(
    session: AsyncSession,
    event: Event,
    mission_title: str,
    *,
    public_base_url: str,
) -> tuple[_PreparedMessagePart, ...]:
    """TASK-068: única correção da pré-lista -- string fixa, sem julgamento.

    Comparação por `amount` (preço do produto), sem frete -- ver
    `_PRELIST_SHIPPING_DISCLAIMER`.
    """
    payload = event.payload
    if not isinstance(payload, dict):
        raise TelegramNotificationError("notification_payload_invalid")
    try:
        offer_id = _required_uuid(payload, "offer_id")
        observation_id = _required_uuid(payload, "observation_id")
        current_amount = _money(payload, "current_amount")
        currency = _currency(payload)
        had_previous = payload.get("previous_lowest_amount") is not None
    except InvalidOperation, TypeError, ValueError:
        raise TelegramNotificationError("notification_payload_invalid") from None
    (
        offer,
        product,
        store,
        observation,
        installment_options,
    ) = await _load_offer_context_async(session, offer_id, observation_id)
    link = await get_or_create_offer_short_link(session, offer.id)
    short_url = build_offer_short_url(public_base_url, link.token)
    display_name = html.escape(product.display_name or product.name)
    if had_previous:
        header = "🔄 ATUALIZAÇÃO DA PRÉ-LISTA\n\n"
        note = (
            "Uma das lojas que ainda estava sendo consultada encontrou uma oferta "
            "melhor:"
        )
    else:
        header = "🧾 PRIMEIRA OFERTA RELEVANTE ENCONTRADA\n\n"
        note = (
            "Até agora, nenhuma oferta relevante havia sido encontrada para esta "
            "missão. Agora apareceu esta:"
        )
    text = (
        f"{header}"
        f"🔎 Missão: {html.escape(mission_title)}\n\n"
        f"{note}\n\n"
        f"1️⃣ {display_name}\n"
        f"🏪 {html.escape(store.name)}\n"
        f"{_marketplace_party_line(store, observation)}"
        f"💰 À vista: {format_money(current_amount, currency)}\n"
        f"{_installment_line(installment_options, currency)}"
        f"{_PRELIST_SHIPPING_DISCLAIMER}\n\n"
        "🔗 Ver anúncio\n"
        f"{_telegram_link(short_url)}"
    )
    return (_PreparedMessagePart(offer.id, 0, text, offer.image_url),)


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
