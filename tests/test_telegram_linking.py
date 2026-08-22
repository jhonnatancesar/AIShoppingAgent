"""Prova temporária e desvinculação Telegram da TASK-101."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.authentication.models import TelegramLinkToken
from app.authentication.service import token_digest
from app.authentication.telegram_linking import (
    TelegramLinkError,
    complete_telegram_link,
    issue_telegram_link,
    unlink_telegram,
)
from app.telegram.router import _parse_telegram_link_command
from app.users.models import User, UserRole

NOW = datetime(2026, 8, 22, 20, 0, tzinfo=UTC)


def _web_user(*, linked: bool = False) -> User:
    return User(
        id=uuid4(),
        display_name="Conta Web",
        username="web",
        role=UserRole.USER,
        is_active=True,
        telegram_user_id=456 if linked else None,
        telegram_chat_id=456 if linked else None,
        favorite_stores=[],
        preferred_categories=[],
        notify_price_decreases=True,
        notify_target_reached=True,
        created_at=NOW,
        updated_at=NOW,
    )


class _SyncSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.executed: list[object] = []

    def execute(self, statement):
        self.executed.append(statement)

    def scalar(self, _statement):
        return 0

    def add(self, value: object) -> None:
        self.added.append(value)


class _AsyncSession:
    def __init__(self, *, scalars: list[object], target: User | None = None) -> None:
        self.scalars = scalars
        self.target = target
        self.added: list[object] = []
        self.executed: list[object] = []

    async def scalar(self, _statement):
        return self.scalars.pop(0)

    async def get(self, _model, _identity, **_kwargs):
        return self.target

    async def execute(self, statement):
        self.executed.append(statement)

    def add(self, value: object) -> None:
        self.added.append(value)


def _token(*, user: User, raw: str = "valid-proof") -> TelegramLinkToken:
    return TelegramLinkToken(
        id=uuid4(),
        token_hash=token_digest(raw),
        user_id=user.id,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )


def test_issued_token_is_stored_only_as_hash() -> None:
    session = _SyncSession()
    challenge = issue_telegram_link(session, user=_web_user(), now=NOW)
    stored = next(item for item in session.added if isinstance(item, TelegramLinkToken))

    assert challenge.raw_token not in stored.token_hash
    assert stored.token_hash == token_digest(challenge.raw_token)
    assert challenge.command == f"/vincular {challenge.raw_token}"


def test_link_without_valid_proof_is_rejected() -> None:
    target = _web_user()
    session = _AsyncSession(scalars=[None], target=target)

    with pytest.raises(TelegramLinkError, match="inválido"):
        asyncio.run(
            complete_telegram_link(
                session,
                raw_token="not-a-proof",
                telegram_user_id=123,
                telegram_chat_id=123,
                now=NOW,
            )
        )
    assert target.telegram_user_id is None


def test_proof_must_come_from_own_private_chat_identity() -> None:
    session = _AsyncSession(scalars=[])

    with pytest.raises(TelegramLinkError, match="inválido"):
        asyncio.run(
            complete_telegram_link(
                session,
                raw_token="valid-proof",
                telegram_user_id=123,
                telegram_chat_id=999,
                now=NOW,
            )
        )


def test_router_recognizes_link_before_generic_telegram_user_creation() -> None:
    recognized, token = _parse_telegram_link_command("/vincular AbC_123")

    assert recognized is True
    assert token == "AbC_123"


def test_valid_private_proof_links_the_correct_web_account() -> None:
    target = _web_user()
    token = _token(user=target)
    session = _AsyncSession(scalars=[token, None], target=target)

    linked = asyncio.run(
        complete_telegram_link(
            session,
            raw_token="valid-proof",
            telegram_user_id=123,
            telegram_chat_id=123,
            now=NOW,
        )
    )

    assert linked.id == target.id
    assert target.telegram_user_id == 123
    assert target.telegram_chat_id == 123
    assert token.consumed_at == NOW


def test_consumed_token_cannot_be_reused() -> None:
    target = _web_user()
    token = _token(user=target)
    first = _AsyncSession(scalars=[token, None], target=target)
    asyncio.run(
        complete_telegram_link(
            first,
            raw_token="valid-proof",
            telegram_user_id=123,
            telegram_chat_id=123,
            now=NOW,
        )
    )

    second = _AsyncSession(scalars=[token], target=target)
    with pytest.raises(TelegramLinkError, match="inválido"):
        asyncio.run(
            complete_telegram_link(
                second,
                raw_token="valid-proof",
                telegram_user_id=123,
                telegram_chat_id=123,
                now=NOW + timedelta(seconds=1),
            )
        )


def test_telegram_identity_already_owned_by_another_account_is_rejected() -> None:
    target = _web_user()
    other = _web_user(linked=True)
    token = _token(user=target)
    session = _AsyncSession(scalars=[token, other], target=target)

    with pytest.raises(TelegramLinkError, match="Não foi possível"):
        asyncio.run(
            complete_telegram_link(
                session,
                raw_token="valid-proof",
                telegram_user_id=456,
                telegram_chat_id=456,
                now=NOW,
            )
        )
    assert target.telegram_user_id is None
    assert token.invalidated_at == NOW


def test_unlink_preserves_web_account_and_missions() -> None:
    user = _web_user(linked=True)
    mission = SimpleNamespace(id=uuid4(), user_id=user.id, title="Notebook")
    session = _SyncSession()

    unlink_telegram(session, user=user, now=NOW)

    assert user.id == mission.user_id
    assert mission.title == "Notebook"
    assert user.username == "web"
    assert user.telegram_user_id is None
    assert user.telegram_chat_id is None
    assert "web_sessions" not in " ".join(str(statement) for statement in session.executed)
