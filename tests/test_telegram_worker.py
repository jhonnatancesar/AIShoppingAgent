"""Testes do processo contínuo de notificações Telegram."""

import asyncio
from unittest.mock import MagicMock

import pytest
from app.core.config import Settings
from app.telegram.notifications import TelegramNotificationBatch
from app.telegram.worker import run_worker


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
async def test_worker_once_processes_and_commits_one_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        database_password="password",
        telegram_bot_token="token",
        telegram_notification_batch_size=25,
        _env_file=None,
    )
    engine = MagicMock()
    session = MagicMock()
    transaction = MagicMock()
    transaction.__enter__.return_value = session
    session_factory = MagicMock()
    session_factory.begin.return_value = transaction
    monkeypatch.setattr(
        "app.telegram.worker.create_database_engine", lambda settings: engine
    )
    monkeypatch.setattr(
        "app.telegram.worker.create_session_factory",
        lambda configured_engine: session_factory,
    )
    calls: list[tuple[object, int]] = []

    async def _process(
        active_session: object, *, bot_token: object, limit: int, **kwargs: object
    ):
        calls.append((active_session, limit))
        return TelegramNotificationBatch(claimed=1, succeeded=1, failed=0, skipped=0)

    async def _process_auth(*args: object, **kwargs: object):
        return TelegramNotificationBatch(0, 0, 0, 0)

    monkeypatch.setattr("app.telegram.worker.process_telegram_notifications", _process)
    monkeypatch.setattr(
        "app.telegram.worker.process_telegram_authentication_notifications",
        _process_auth,
    )
    publish = MagicMock()
    monkeypatch.setattr(
        "app.telegram.worker.publish_due_authentication_notifications", publish
    )

    await run_worker(settings, once=True)

    assert calls == [(session, 25)]
    assert transaction.__exit__.call_count == 3
    publish.assert_called_once_with(session, limit=25)
    engine.dispose.assert_called_once()


@pytest.mark.anyio
async def test_worker_rolls_back_then_backs_off_outside_failed_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        database_password="password",
        telegram_bot_token="token",
        worker_failure_backoff_seconds=0.01,
        _env_file=None,
    )
    engine = MagicMock()
    sessions = MagicMock()
    transaction = MagicMock()
    transaction.__enter__.return_value = MagicMock()
    sessions.begin.return_value = transaction
    monkeypatch.setattr("app.telegram.worker.create_database_engine", lambda _: engine)
    monkeypatch.setattr(
        "app.telegram.worker.create_session_factory", lambda _: sessions
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
        "app.telegram.worker.publish_due_authentication_notifications", MagicMock()
    )
    monkeypatch.setattr("app.telegram.worker.asyncio.sleep", sleep)

    with pytest.raises(asyncio.CancelledError):
        await run_worker(settings, poll_seconds=0.01)

    assert calls == 2
    assert sleeps[0] == settings.worker_failure_backoff_seconds
    assert transaction.__exit__.call_count == 5
    engine.dispose.assert_called_once()
