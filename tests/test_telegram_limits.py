"""Contrato dos recibos transacionais de replay/rate limit."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.database.base import Base
from app.database.model_registry import REGISTERED_MODELS
from app.telegram.limits import reserve_telegram_update
from app.telegram.models import TelegramUpdateDisposition, TelegramUpdateReceipt
from sqlalchemy import CheckConstraint, Index, UniqueConstraint
from sqlalchemy.exc import IntegrityError


def test_receipt_table_is_minimal_append_only_contract() -> None:
    table = TelegramUpdateReceipt.__table__
    assert [column.name for column in table.columns] == [
        "id",
        "update_id",
        "user_id",
        "disposition",
        "recorded_at",
    ]
    assert any(
        isinstance(item, UniqueConstraint)
        and item.name == "uq_telegram_update_receipts_update_id"
        for item in table.constraints
    )
    assert any(
        isinstance(item, CheckConstraint)
        and item.name == "ck_telegram_update_receipts_disposition"
        for item in table.constraints
    )
    assert any(
        isinstance(item, Index)
        and item.name == "ix_telegram_update_receipts_user_window"
        for item in table.indexes
    )
    assert TelegramUpdateReceipt in REGISTERED_MODELS
    assert Base.metadata.tables[table.name] is table


def test_existing_receipt_is_terminal_replay_without_new_write() -> None:
    session = MagicMock()
    user_id = uuid4()
    session.execute.return_value.scalar_one_or_none.return_value = user_id
    session.scalar.return_value = SimpleNamespace(
        disposition=TelegramUpdateDisposition.RATE_LIMITED.value
    )

    result = reserve_telegram_update(
        session,
        update_id=99,
        user_id=user_id,
        accepted_per_minute=20,
    )

    assert result.replay is True
    assert result.disposition is TelegramUpdateDisposition.RATE_LIMITED
    session.add.assert_not_called()


def test_rate_limited_update_is_terminal_and_warns_only_first_in_window() -> None:
    session = MagicMock()
    user_id = uuid4()
    session.execute.return_value.scalar_one_or_none.return_value = user_id
    session.scalar.side_effect = [None, 20, 0]
    nested = MagicMock()
    session.begin_nested.return_value = nested

    result = reserve_telegram_update(
        session,
        update_id=100,
        user_id=user_id,
        accepted_per_minute=20,
    )

    assert result.replay is False
    assert result.disposition is TelegramUpdateDisposition.RATE_LIMITED
    assert result.warn_rate_limit is True
    receipt = session.add.call_args.args[0]
    assert receipt.disposition == TelegramUpdateDisposition.RATE_LIMITED.value


def test_forced_discard_is_persisted_as_terminal_receipt() -> None:
    session = MagicMock()
    user_id = uuid4()
    session.execute.return_value.scalar_one_or_none.return_value = user_id
    session.scalar.return_value = None

    result = reserve_telegram_update(
        session,
        update_id=101,
        user_id=user_id,
        accepted_per_minute=20,
        forced_disposition=TelegramUpdateDisposition.DISCARDED,
    )

    assert result.replay is False
    assert result.disposition is TelegramUpdateDisposition.DISCARDED
    receipt = session.add.call_args.args[0]
    assert receipt.disposition == TelegramUpdateDisposition.DISCARDED.value


def test_unexpected_integrity_error_is_not_masked() -> None:
    class Diagnostic:
        constraint_name = "fk_telegram_update_receipts_user_id_users"

    class OriginalError(Exception):
        diag = Diagnostic()

    session = MagicMock()
    user_id = uuid4()
    session.execute.return_value.scalar_one_or_none.return_value = user_id
    session.scalar.side_effect = [None, 0]
    session.flush.side_effect = IntegrityError("INSERT", {}, OriginalError())

    with pytest.raises(IntegrityError):
        reserve_telegram_update(
            session,
            update_id=102,
            user_id=user_id,
            accepted_per_minute=20,
        )
