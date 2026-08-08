"""Validação real e isolada da TASK-061 em PostgreSQL 18."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.audit.models import AuditEntry
from app.authentication.models import (
    CredentialAction,
    CredentialActionToken,
    UserAuthSession,
    UserCredential,
)
from app.authentication.passwords import verify_password
from app.authentication.service import (
    AuthenticationError,
    AuthenticationRateLimited,
    complete_action,
    has_active_session,
    issue_action_link,
    logout,
)
from app.database.session import create_database_engine, create_session_factory
from app.users.models import User, UserRole
from sqlalchemy import select

PASSWORD_A = "frase secreta real segura 061 A"
PASSWORD_B = "frase secreta real segura 061 B"
PASSWORD_C = "frase secreta real segura 061 C"


def _raw_token(url: str) -> str:
    return url.rsplit(":", 1)[1]


def main() -> None:
    engine = create_database_engine()
    factory = create_session_factory(engine)
    user_id = uuid4()
    telegram_id = 8_000_000_000 + (user_id.int % 999_999_999)
    started = datetime.now(UTC)

    with factory.begin() as session:
        user = User(
            id=user_id,
            display_name="TASK-061 validation",
            role=UserRole.USER,
            is_active=True,
            telegram_user_id=telegram_id,
            username=f"task061_{user_id.hex[:12]}",
        )
        session.add(user)
        session.flush()
        setup = issue_action_link(
            session,
            user=user,
            action=CredentialAction.SET_PASSWORD,
            public_base_url="http://localhost:8000",
            now=started,
        )
        setup_raw = _raw_token(setup.url)

    with factory.begin() as session:
        action = complete_action(
            session,
            raw_token=setup_raw,
            password=PASSWORD_A,
            password_confirmation=PASSWORD_A,
            now=started + timedelta(seconds=1),
        )
        assert action is CredentialAction.SET_PASSWORD
        session.flush()
        credential = session.get(UserCredential, user_id)
        assert credential is not None
        assert PASSWORD_A not in credential.password_hash
        assert verify_password(credential.password_hash, PASSWORD_A)

    with factory.begin() as session:
        user = session.get(User, user_id)
        assert user is not None
        login = issue_action_link(
            session,
            user=user,
            action=CredentialAction.LOGIN,
            public_base_url="http://localhost:8000",
            now=started + timedelta(minutes=1),
        )
        login_raw = _raw_token(login.url)

    def resolve_same_token() -> str:
        try:
            with factory.begin() as concurrent_session:
                complete_action(
                    concurrent_session,
                    raw_token=login_raw,
                    password=PASSWORD_A,
                    password_confirmation=None,
                    now=started + timedelta(minutes=1, seconds=1),
                )
            return "succeeded"
        except AuthenticationError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        concurrent_results = sorted(
            executor.map(lambda _: resolve_same_token(), range(2))
        )
    assert concurrent_results == ["rejected", "succeeded"]

    with factory.begin() as session:
        auth_session = session.execute(
            select(UserAuthSession).where(UserAuthSession.user_id == user_id)
        ).scalar_one()
        original_expiry = auth_session.expires_at
        assert original_expiry == auth_session.authenticated_at + timedelta(hours=12)
        assert has_active_session(
            session,
            user_id=user_id,
            telegram_user_id=telegram_id,
            now=started + timedelta(hours=2),
        )
        assert auth_session.expires_at == original_expiry

    # Nova conexão representa recuperação do estado após reinício do processo.
    with factory.begin() as session:
        assert has_active_session(
            session,
            user_id=user_id,
            telegram_user_id=telegram_id,
            now=started + timedelta(hours=3),
        )
        user = session.get(User, user_id)
        assert user is not None
        logout(session, user=user, now=started + timedelta(hours=3))
    with factory.begin() as session:
        assert not has_active_session(
            session,
            user_id=user_id,
            telegram_user_id=telegram_id,
            now=started + timedelta(hours=3, seconds=1),
        )

    _login(factory, user_id, PASSWORD_A, started + timedelta(hours=4))
    _change_password(
        factory,
        user_id,
        CredentialAction.CHANGE_PASSWORD,
        PASSWORD_B,
        started + timedelta(hours=5),
    )
    _assert_all_sessions_revoked(factory, user_id)
    _login(factory, user_id, PASSWORD_B, started + timedelta(hours=6))
    _change_password(
        factory,
        user_id,
        CredentialAction.RECOVER_PASSWORD,
        PASSWORD_C,
        started + timedelta(hours=7),
    )
    _assert_all_sessions_revoked(factory, user_id)

    with factory.begin() as session:
        user = session.get(User, user_id)
        assert user is not None
        expired = issue_action_link(
            session,
            user=user,
            action=CredentialAction.LOGIN,
            public_base_url="http://localhost:8000",
            now=started - timedelta(minutes=11),
        )
        expired_raw = _raw_token(expired.url)
    with factory.begin() as session:
        try:
            complete_action(
                session,
                raw_token=expired_raw,
                password=PASSWORD_C,
                password_confirmation=None,
                now=started,
            )
        except AuthenticationError:
            pass
        else:
            raise AssertionError("expired token was accepted")

    with factory.begin() as session:
        user = session.get(User, user_id)
        assert user is not None
        recovery = issue_action_link(
            session,
            user=user,
            action=CredentialAction.RECOVER_PASSWORD,
            public_base_url="http://localhost:8000",
            now=started + timedelta(hours=8),
        )
        assert recovery.expires_at == started + timedelta(hours=8, minutes=10)
    with factory.begin() as session:
        user = session.get(User, user_id)
        assert user is not None
        try:
            issue_action_link(
                session,
                user=user,
                action=CredentialAction.RECOVER_PASSWORD,
                public_base_url="http://localhost:8000",
                now=started + timedelta(hours=8, minutes=1),
            )
        except AuthenticationRateLimited:
            pass
        else:
            raise AssertionError("recovery rate limit was not persistent")

    failed_at = started + timedelta(hours=9)
    with factory.begin() as session:
        user = session.get(User, user_id)
        assert user is not None
        limited_login = issue_action_link(
            session,
            user=user,
            action=CredentialAction.LOGIN,
            public_base_url="http://localhost:8000",
            now=failed_at,
        )
        limited_raw = _raw_token(limited_login.url)
    for attempt in range(5):
        with factory.begin() as session:
            try:
                complete_action(
                    session,
                    raw_token=limited_raw,
                    password="senha incorreta persistente 061",
                    password_confirmation=None,
                    now=failed_at + timedelta(seconds=attempt + 1),
                )
            except AuthenticationError:
                pass
            else:
                raise AssertionError("incorrect password was accepted")
    with factory.begin() as session:
        credential = session.get(UserCredential, user_id)
        assert credential is not None
        assert credential.failed_login_attempts == 5
        assert credential.login_locked_until is not None
        assert credential.login_locked_until > failed_at

    # O cooldown expira; não existe bloqueio permanente.
    _login(factory, user_id, PASSWORD_C, failed_at + timedelta(minutes=16))
    with factory.begin() as session:
        credential = session.get(UserCredential, user_id)
        assert credential is not None
        assert credential.failed_login_attempts == 0
        assert credential.login_locked_until is None

    with factory.begin() as session:
        audits = session.scalars(
            select(AuditEntry).where(AuditEntry.actor_id == user_id)
        ).all()
        serialized = json.dumps(
            [entry.entry_metadata for entry in audits], sort_keys=True, default=str
        )
        for secret in (setup_raw, login_raw, PASSWORD_A, PASSWORD_B, PASSWORD_C):
            assert secret not in serialized
        assert (
            session.scalar(
                select(CredentialActionToken).where(
                    CredentialActionToken.token_hash == setup_raw
                )
            )
            is None
        )

    print(
        json.dumps(
            {
                "postgresql": "passed",
                "concurrent_single_use": concurrent_results,
                "session_absolute_ttl": "passed",
                "restart_recovery": "passed",
                "logout_revocation": "passed",
                "password_change_revocation": "passed",
                "password_recovery_revocation": "passed",
                "token_expiry": "passed",
                "persistent_rate_limit": "passed",
                "persistent_login_cooldown": "passed",
                "audit_secret_canary": "passed",
            }
        )
    )


def _login(factory, user_id, password: str, now: datetime) -> None:
    with factory.begin() as session:
        user = session.get(User, user_id)
        assert user is not None
        link = issue_action_link(
            session,
            user=user,
            action=CredentialAction.LOGIN,
            public_base_url="http://localhost:8000",
            now=now,
        )
        raw = _raw_token(link.url)
    with factory.begin() as session:
        complete_action(
            session,
            raw_token=raw,
            password=password,
            password_confirmation=None,
            now=now + timedelta(seconds=1),
        )


def _change_password(factory, user_id, action, password: str, now: datetime) -> None:
    with factory.begin() as session:
        user = session.get(User, user_id)
        assert user is not None
        link = issue_action_link(
            session,
            user=user,
            action=action,
            public_base_url="http://localhost:8000",
            now=now,
        )
        raw = _raw_token(link.url)
    with factory.begin() as session:
        complete_action(
            session,
            raw_token=raw,
            password=password,
            password_confirmation=password,
            now=now + timedelta(seconds=1),
        )


def _assert_all_sessions_revoked(factory, user_id) -> None:
    with factory.begin() as session:
        sessions = session.scalars(
            select(UserAuthSession).where(UserAuthSession.user_id == user_id)
        ).all()
        assert sessions
        assert all(item.revoked_at is not None for item in sessions)


if __name__ == "__main__":
    main()
