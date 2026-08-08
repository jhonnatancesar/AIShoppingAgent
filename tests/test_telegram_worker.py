"""Testes do processo contínuo de notificações Telegram."""

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

    async def _process(active_session: object, *, bot_token: object, limit: int):
        calls.append((active_session, limit))
        return TelegramNotificationBatch(claimed=1, succeeded=1, failed=0)

    monkeypatch.setattr("app.telegram.worker.process_telegram_notifications", _process)

    await run_worker(settings, once=True)

    assert calls == [(session, 25)]
    transaction.__exit__.assert_called_once()
    engine.dispose.assert_called_once()
