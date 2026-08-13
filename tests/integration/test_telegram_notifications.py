"""Integração real (TASK-080): telegram_notifier nunca mantém transação
aberta durante o envio Telegram, e o registro por evento é independente."""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from datetime import UTC, datetime
from uuid import uuid4

# psycopg em modo assíncrono não suporta o ProactorEventLoop, padrão do
# asyncio no Windows -- exige SelectorEventLoop. Escopado só a este arquivo
# (primeira vez que testes de integração assíncronos rodam localmente no
# Windows; antes só validados no servidor Ubuntu -- ver TASK-079).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import pytest
from app.authentication.models import CredentialAction
from app.database.session import (
    create_async_session_factory,
    create_telegram_async_database_engine,
)
from app.events import AggregateType, AuthenticationCompletedPayload, EventType
from app.events.models import ConsumptionOutcome, Event, EventConsumptionAttempt
from app.events.service import publish_event
from app.telegram.notifications import (
    TELEGRAM_AUTH_NOTIFICATION_CONSUMER,
    process_telegram_authentication_notifications,
)
from app.users.models import User, UserRole
from pydantic import SecretStr
from sqlalchemy import select, text

pytestmark = pytest.mark.integration

_SUSTAINED_IDLE_THRESHOLD_SECONDS = 0.1


def _seed_user(session, *, telegram_user_id: int) -> User:
    user = User(
        display_name=f"Integração {telegram_user_id}",
        role=UserRole.USER,
        is_active=True,
        telegram_user_id=telegram_user_id,
        telegram_chat_id=telegram_user_id,
    )
    session.add(user)
    session.flush()
    return user


def _publish_completion_event(session, user: User, *, now: datetime) -> Event:
    return publish_event(
        session,
        event_type=EventType.AUTHENTICATION_COMPLETED_V1,
        aggregate_type=AggregateType.USER,
        aggregate_id=user.id,
        payload=AuthenticationCompletedPayload(
            user_id=user.id, action=CredentialAction.LOGIN
        ),
        occurred_at=now,
    )


def _poll_idle_in_transaction(
    engine, stop_event: threading.Event, sustained: list
) -> None:
    """Mesmo padrão da TASK-079
    (`tests/integration/test_collection_orchestration.py::_poll_idle_in_transaction`)
    -- mede a duração real de `idle in transaction` via `now() - state_change`,
    não a contagem de amostras."""
    with engine.connect() as connection:
        while not stop_event.is_set():
            rows = connection.execute(
                text(
                    "SELECT pid, query, "
                    "extract(epoch FROM (now() - state_change)) AS idle_seconds "
                    "FROM pg_stat_activity "
                    "WHERE state = 'idle in transaction' AND pid <> pg_backend_pid()"
                )
            ).all()
            connection.rollback()
            for row in rows:
                if row.idle_seconds >= _SUSTAINED_IDLE_THRESHOLD_SECONDS:
                    sustained.append((row.pid, row.idle_seconds, row.query))
            time.sleep(0.02)


def test_slow_send_message_never_holds_a_transaction_open_and_event_loop_stays_responsive(
    integration_database, monkeypatch
) -> None:
    """TASK-080: reproduz o cenário exigido -- `send_message` artificialmente
    lento, event loop monitorado por heartbeat, `pg_stat_activity` amostrado
    ao vivo durante o envio. Prova exigida: (1) nenhuma transação do
    `telegram_notifier` fica `idle in transaction` durante o envio; (2) o
    event loop nunca trava; (3) depois do envio liberar, a Fase C registra
    o resultado corretamente."""
    now = datetime.now(UTC).replace(microsecond=0)
    with integration_database.sessions.begin() as session:
        user = _seed_user(session, telegram_user_id=8_200_000_001)
        event = _publish_completion_event(session, user, now=now)
        event_id = event.id

    async_engine = create_telegram_async_database_engine(integration_database.settings)
    async_session_factory = create_async_session_factory(async_engine)

    heartbeat_ticks = 0

    async def _heartbeat() -> None:
        nonlocal heartbeat_ticks
        while True:
            await asyncio.sleep(0.01)
            heartbeat_ticks += 1

    stop_event = threading.Event()
    sustained_idle: list = []
    poller = threading.Thread(
        target=_poll_idle_in_transaction,
        args=(integration_database.engine, stop_event, sustained_idle),
        daemon=True,
    )
    poller.start()

    send_calls: list[int] = []

    async def _slow_send(chat_id: int, text: str, *, bot_token, **kwargs) -> None:
        send_calls.append(chat_id)
        await asyncio.sleep(0.3)

    monkeypatch.setattr("app.telegram.notifications.send_message", _slow_send)

    async def _run():
        heartbeat = asyncio.create_task(_heartbeat())
        started = asyncio.get_event_loop().time()
        try:
            result = await process_telegram_authentication_notifications(
                async_session_factory, bot_token=SecretStr("integration-token")
            )
            return result, asyncio.get_event_loop().time() - started
        finally:
            heartbeat.cancel()
            await async_engine.dispose()

    try:
        result, elapsed = asyncio.run(_run())
    finally:
        stop_event.set()
        poller.join(timeout=5)

    assert send_calls == [user.telegram_chat_id]
    assert result.claimed == 1
    assert result.succeeded == 1
    # Se alguma fase mantivesse uma transação aberta durante o `await
    # asyncio.sleep(0.3)` do envio (o mecanismo exato do autodeadlock da
    # TASK-079), o heartbeat pararia de bater -- limite bem abaixo do
    # esperado (~100 ticks/s) para tolerar jitter do CI.
    assert heartbeat_ticks >= elapsed / 0.05
    # Nenhuma conexão do telegram_notifier ficou parada em `idle in
    # transaction` além do jitter normal de uma transação curta e local --
    # a Fase A já commitou antes do envio, a Fase C só abre depois dele.
    assert sustained_idle == []

    with integration_database.sessions() as session:
        attempts = list(
            session.scalars(
                select(EventConsumptionAttempt).where(
                    EventConsumptionAttempt.event_id == event_id,
                    EventConsumptionAttempt.consumer_name
                    == TELEGRAM_AUTH_NOTIFICATION_CONSUMER,
                )
            )
        )
    assert len(attempts) == 1
    assert attempts[0].outcome is ConsumptionOutcome.SUCCEEDED
    assert attempts[0].failure_code is None


def test_batch_of_three_events_second_success_persists_despite_third_failure(
    integration_database, monkeypatch
) -> None:
    """TASK-080: evento 1 sucesso, evento 2 sucesso, evento 3 falha -- prova
    exigida: os registros dos eventos 1 e 2 permanecem confirmados
    (Fase C commita por evento, não em lote) e o evento 3 segue exatamente a
    política atual de retry (primeira falha -> FAILED com `next_retry_at`,
    nunca DEAD_LETTERED de imediato, dado `max_attempts` padrão)."""
    now = datetime.now(UTC).replace(microsecond=0)
    with integration_database.sessions.begin() as session:
        user_one = _seed_user(session, telegram_user_id=8_200_000_101)
        user_two = _seed_user(session, telegram_user_id=8_200_000_102)
        user_three = _seed_user(session, telegram_user_id=8_200_000_103)
        event_one = _publish_completion_event(session, user_one, now=now)
        event_two = _publish_completion_event(session, user_two, now=now)
        event_three = _publish_completion_event(session, user_three, now=now)
        event_one_id, event_two_id, event_three_id = (
            event_one.id,
            event_two.id,
            event_three.id,
        )

    async_engine = create_telegram_async_database_engine(integration_database.settings)
    async_session_factory = create_async_session_factory(async_engine)

    from app.telegram.bot_api import TelegramBotAPIError

    sent: list[int] = []

    async def _send(chat_id: int, text: str, *, bot_token, **kwargs) -> None:
        if chat_id == user_three.telegram_chat_id:
            raise TelegramBotAPIError(500)
        sent.append(chat_id)

    monkeypatch.setattr("app.telegram.notifications.send_message", _send)

    try:
        result = asyncio.run(
            process_telegram_authentication_notifications(
                async_session_factory, bot_token=SecretStr("integration-token")
            )
        )
    finally:
        asyncio.run(async_engine.dispose())

    assert result.claimed == 3
    assert result.succeeded == 2
    assert result.failed == 1
    assert sorted(sent) == sorted(
        (user_one.telegram_chat_id, user_two.telegram_chat_id)
    )

    with integration_database.sessions() as session:
        by_event = {
            attempt.event_id: attempt
            for attempt in session.scalars(
                select(EventConsumptionAttempt).where(
                    EventConsumptionAttempt.event_id.in_(
                        (event_one_id, event_two_id, event_three_id)
                    ),
                    EventConsumptionAttempt.consumer_name
                    == TELEGRAM_AUTH_NOTIFICATION_CONSUMER,
                )
            )
        }

    assert by_event[event_one_id].outcome is ConsumptionOutcome.SUCCEEDED
    assert by_event[event_two_id].outcome is ConsumptionOutcome.SUCCEEDED
    assert by_event[event_one_id].failure_code is None
    assert by_event[event_two_id].failure_code is None
    # Evento 3: política atual de retry (event_consumer_max_attempts=5 por
    # padrão) -- primeira falha nunca é dead-letter imediato.
    assert by_event[event_three_id].outcome is ConsumptionOutcome.FAILED
    assert by_event[event_three_id].failure_code == "telegram_api_rejected"
    assert by_event[event_three_id].next_retry_at is not None
