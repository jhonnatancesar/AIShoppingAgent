"""Testes da dependência de sessão de banco por requisição."""

from unittest.mock import MagicMock, patch

import pytest
from app.database.dependency import get_session


def test_get_session_commits_and_closes_on_success() -> None:
    fake_session = MagicMock()
    with patch(
        "app.database.dependency._get_session_factory",
        return_value=lambda: fake_session,
    ):
        generator = get_session()
        session = next(generator)
        assert session is fake_session

        with pytest.raises(StopIteration):
            next(generator)

    fake_session.commit.assert_called_once()
    fake_session.rollback.assert_not_called()
    fake_session.close.assert_called_once()


def test_get_session_rolls_back_and_closes_on_exception() -> None:
    fake_session = MagicMock()
    with patch(
        "app.database.dependency._get_session_factory",
        return_value=lambda: fake_session,
    ):
        generator = get_session()
        next(generator)

        with pytest.raises(RuntimeError, match="boom"):
            generator.throw(RuntimeError("boom"))

    fake_session.commit.assert_not_called()
    fake_session.rollback.assert_called_once()
    fake_session.close.assert_called_once()
