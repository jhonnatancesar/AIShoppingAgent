"""Testes dos controles técnicos de privacidade da V1."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.audit.models import AuditEntry
from app.missions.models import MissionStatus
from app.privacy.notice import PRIVACY_COMMAND, privacy_notice
from app.privacy.service import (
    DEIDENTIFIED_DISPLAY_NAME,
    DEIDENTIFIED_MISSION_TITLE,
    DEIDENTIFIED_SEARCH_QUERY,
    HistoricalPersonalDataConflict,
    PrivacyIdentityMismatch,
    cleanup_expired_authentication_artifacts,
    deidentify_account,
)
from app.users.models import UserRole

from backend.scripts.register_telegram_commands import _COMMANDS


class _ScalarRows:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def all(self) -> list[object]:
        return self._rows


class _PrivacySession:
    def __init__(self, user: object, scalar_rows: list[list[object]]) -> None:
        self.user = user
        self.scalar_rows = list(scalar_rows)
        self.added: list[object] = []
        self.execute_results = [
            SimpleNamespace(rowcount=1),
            SimpleNamespace(rowcount=2),
            SimpleNamespace(rowcount=3),
        ]
        self.flushed = False

    def scalar(self, statement: object) -> object:
        return self.user

    def scalars(self, statement: object) -> _ScalarRows:
        return _ScalarRows(self.scalar_rows.pop(0))

    def execute(self, statement: object) -> object:
        return self.execute_results.pop(0)

    def add(self, value: object) -> None:
        self.added.append(value)

    def flush(self) -> None:
        self.flushed = True


def _user() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        display_name="Pessoa Canary",
        role=UserRole.ADMIN,
        is_active=True,
        telegram_user_id=123456789,
        telegram_chat_id=123456789,
        notify_price_decreases=True,
        notify_target_reached=True,
        username="pessoa_canary",
        email="privacy-canary@example.invalid",
        favorite_stores=["kabum"],
        preferred_categories=["hardware"],
        registration_step="email",
        pending_intent={"query": "privacy-canary"},
    )


def test_privacy_command_is_registered_and_does_not_overpromise() -> None:
    assert PRIVACY_COMMAND == "/privacidade"
    assert any(item["command"] == "privacidade" for item in _COMMANDS)
    notice = privacy_notice()
    assert "pseudônimo" in notice
    assert "não é uma garantia de anonimização irreversível" in notice
    assert "conformidade" not in notice.casefold()


def test_deidentify_account_scrubs_mutable_data_and_preserves_safe_audit() -> None:
    user = _user()
    mission = SimpleNamespace(id=uuid4(), title="Busca da Pessoa Canary")
    criteria = SimpleNamespace(search_query="privacy-canary notebook")
    schedule = SimpleNamespace(is_enabled=True)
    session = _PrivacySession(
        user,
        [
            [mission.id],
            [],  # audit metadata
            [],  # purchase snapshots
            [],  # transition reasons
            [],  # event payloads
            [],  # raw evidence
            [mission],
            [criteria],
            [schedule],
        ],
    )

    result = deidentify_account(
        session,  # type: ignore[arg-type]
        user_id=user.id,
        expected_telegram_user_id=123456789,
    )

    assert user.display_name == DEIDENTIFIED_DISPLAY_NAME
    assert user.role is UserRole.USER
    assert user.is_active is False
    assert user.telegram_user_id is None
    assert user.telegram_chat_id is None
    assert user.username is None
    assert user.email is None
    assert user.favorite_stores == []
    assert user.preferred_categories == []
    assert user.pending_intent is None
    assert mission.title == DEIDENTIFIED_MISSION_TITLE
    assert criteria.search_query == DEIDENTIFIED_SEARCH_QUERY
    assert schedule.is_enabled is False
    assert result.missions_scrubbed == 1
    assert result.schedules_disabled == 1
    assert result.credentials_deleted == 1
    assert result.action_tokens_deleted == 2
    assert result.auth_sessions_deleted == 3
    audit = session.added[0]
    assert isinstance(audit, AuditEntry)
    assert audit.actor_id is None
    assert audit.entry_metadata == {
        "direct_identifiers": "removed",
        "historical_linkage": "internal_uuid_preserved",
    }
    assert "privacy-canary" not in str(audit.entry_metadata)
    assert session.flushed is True


def test_deidentify_account_fails_before_mutation_on_identity_mismatch() -> None:
    user = _user()
    session = _PrivacySession(user, [])

    with pytest.raises(PrivacyIdentityMismatch):
        deidentify_account(
            session,  # type: ignore[arg-type]
            user_id=user.id,
            expected_telegram_user_id=987654321,
        )

    assert user.is_active is True
    assert session.added == []


def test_deidentify_account_fails_closed_for_pii_in_append_only_history() -> None:
    user = _user()
    mission_id = uuid4()
    unsafe_audit = SimpleNamespace(
        entry_metadata={"copied_email": "privacy-canary@example.invalid"}
    )
    session = _PrivacySession(
        user,
        [
            [mission_id],
            [unsafe_audit],
            [],
            [],
            [],
            [],
        ],
    )

    with pytest.raises(HistoricalPersonalDataConflict) as captured:
        deidentify_account(
            session,  # type: ignore[arg-type]
            user_id=user.id,
            expected_telegram_user_id=123456789,
        )

    assert captured.value.areas == ("audit_entries.metadata",)
    assert user.is_active is True
    assert session.execute_results[0].rowcount == 1
    assert session.added == []


def test_deidentify_account_is_idempotent_after_completion() -> None:
    user = _user()
    user.display_name = DEIDENTIFIED_DISPLAY_NAME
    user.is_active = False
    user.telegram_user_id = None
    user.telegram_chat_id = None
    user.username = None
    user.email = None
    session = _PrivacySession(user, [])

    result = deidentify_account(
        session,  # type: ignore[arg-type]
        user_id=user.id,
        expected_telegram_user_id=123456789,
    )

    assert result.already_deidentified is True
    assert session.added == []


def test_cleanup_uses_fixed_operational_windows() -> None:
    session = MagicMock()
    session.execute.side_effect = [
        SimpleNamespace(rowcount=4),
        SimpleNamespace(rowcount=5),
    ]

    result = cleanup_expired_authentication_artifacts(
        session, now=datetime(2026, 8, 8, 12, tzinfo=UTC)
    )

    assert result.action_tokens_deleted == 4
    assert result.auth_sessions_deleted == 5
    assert session.execute.call_count == 2


def test_cleanup_rejects_naive_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        cleanup_expired_authentication_artifacts(
            MagicMock(), now=datetime(2026, 8, 8, 12)
        )


def test_user_role_remains_single_value_after_privacy_work() -> None:
    assert tuple(role.value for role in UserRole) == ("USER", "ADMIN", "DEV")
    assert MissionStatus.ACTIVE.value == "active"
