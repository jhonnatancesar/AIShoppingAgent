"""Testes do processo contínuo de notificações Telegram."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.core.config import Settings
from app.telegram.notifications import TelegramNotificationBatch
from app.telegram.worker import run_worker


def _fake_session_factory() -> tuple[MagicMock, MagicMock]:
    """Fábrica assíncrona falsa (TASK-080): `factory()` é um gerenciador de
    contexto assíncrono que abre uma sessão falsa, cujo `.begin()` também é
    um gerenciador de contexto assíncrono -- mesmo protocolo real usado por
    `async with session_factory() as session, session.begin():`. Suficiente
    para os testes deste módulo, que mockam os `processor`s inteiros e não
    exercitam as três fases de verdade (cobertas em
    `tests/test_telegram_notifications.py`)."""
    session = MagicMock()
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    transaction_cm = MagicMock()
    transaction_cm.__aenter__ = AsyncMock(return_value=None)
    transaction_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=transaction_cm)
    factory = MagicMock(return_value=session_cm)
    return factory, session


@pytest.mark.anyio
async def test_worker_requires_bot_token_before_opening_database() -> None:
    settings = Settings(
        database_password="password",
        telegram_bot_token=None,
        _env_file=None,
    )

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        await run_worker(settings, once=True)


@pytest.mark.anyio
async def test_worker_once_processes_one_batch_and_disposes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        database_password="password",
        telegram_bot_token="token",
        telegram_notification_batch_size=25,
        _env_file=None,
    )
    engine = MagicMock()
    engine.dispose = AsyncMock()
    session_factory, session = _fake_session_factory()
    monkeypatch.setattr(
        "app.telegram.worker.create_telegram_async_database_engine",
        lambda settings: engine,
    )
    monkeypatch.setattr(
        "app.telegram.worker.create_async_session_factory",
        lambda configured_engine: session_factory,
    )
    calls: list[tuple[object, int]] = []

    async def _process(
        active_session_factory: object,
        *,
        bot_token: object,
        limit: int,
        **kwargs: object,
    ):
        calls.append((active_session_factory, limit))
        return TelegramNotificationBatch(claimed=1, succeeded=1, failed=0, skipped=0)

    async def _process_auth(*args: object, **kwargs: object):
        return TelegramNotificationBatch(0, 0, 0, 0)

    monkeypatch.setattr("app.telegram.worker.process_telegram_notifications", _process)
    monkeypatch.setattr(
        "app.telegram.worker.process_telegram_authentication_notifications",
        _process_auth,
    )
    monkeypatch.setattr(
        "app.telegram.worker.process_telegram_prelist_notifications", _process_auth
    )
    publish = AsyncMock()
    monkeypatch.setattr(
        "app.telegram.worker.publish_due_authentication_notifications_async", publish
    )

    await run_worker(settings, once=True)

    # TASK-080: o worker passa a session_factory direto para o processor --
    # cada fase (A/B/C) abre/fecha sua própria transação internamente, não
    # mais uma transação por lote mantida pelo worker.
    assert calls == [(session_factory, 25)]
    publish.assert_called_once_with(session, limit=25)
    engine.dispose.assert_called_once()


@pytest.mark.anyio
async def test_worker_backs_off_after_failure_then_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        database_password="password",
        telegram_bot_token="token",
        worker_failure_backoff_seconds=0.01,
        _env_file=None,
    )
    engine = MagicMock()
    engine.dispose = AsyncMock()
    session_factory, _session = _fake_session_factory()
    monkeypatch.setattr(
        "app.telegram.worker.create_telegram_async_database_engine", lambda _: engine
    )
    monkeypatch.setattr(
        "app.telegram.worker.create_async_session_factory", lambda _: session_factory
    )
    calls = 0

    async def process(*args: object, **kwargs: object) -> TelegramNotificationBatch:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("controlled failure")
        return TelegramNotificationBatch(0, 0, 0, 0)

    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr("app.telegram.worker.process_telegram_notifications", process)

    async def process_auth(*args: object, **kwargs: object):
        return TelegramNotificationBatch(0, 0, 0, 0)

    monkeypatch.setattr(
        "app.telegram.worker.process_telegram_authentication_notifications",
        process_auth,
    )
    monkeypatch.setattr(
        "app.telegram.worker.process_telegram_prelist_notifications", process_auth
    )
    monkeypatch.setattr(
        "app.telegram.worker.publish_due_authentication_notifications_async",
        AsyncMock(),
    )
    monkeypatch.setattr("app.telegram.worker.asyncio.sleep", sleep)

    with pytest.raises(asyncio.CancelledError):
        await run_worker(settings, poll_seconds=0.01)

    assert calls == 2
    assert sleeps[0] == settings.worker_failure_backoff_seconds
    engine.dispose.assert_called_once()
