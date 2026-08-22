"""Fluxo real da sessão de navegador da aplicação web (TASK-091, item 1 da
V1.2) contra PostgreSQL real -- login, emissão/validação/revogação de
`WebSession`, bloqueio por tentativas e autorização de `/admin`."""

from datetime import UTC, datetime, timedelta

import pytest
from app.authentication.models import UserCredential, WebSession
from app.authentication.passwords import hash_password
from app.authentication.service import (
    AuthenticationError,
    AuthenticationRateLimited,
    authenticate_web_login,
    get_web_session_user,
    issue_web_session,
    revoke_web_session,
)
from app.authorization import AuthorizationDenied, Permission, authorize
from app.users.models import User, UserRole
from sqlalchemy import select

pytestmark = pytest.mark.integration

_PASSWORD = "Senha#Real123"
NOW = datetime(2026, 8, 21, 15, 0, tzinfo=UTC)


def _seed_user(sessions, *, role: UserRole, username: str) -> User:
    with sessions.begin() as session:
        user = User(display_name=username, role=role, username=username)
        session.add(user)
        session.flush()
        session.add(
            UserCredential(
                user_id=user.id,
                password_hash=hash_password(_PASSWORD),
                password_changed_at=NOW,
            )
        )
        session.flush()
        session.expunge(user)
    return user


def test_authenticate_web_login_and_issue_session_round_trips_to_the_same_user(
    integration_database,
) -> None:
    seeded = _seed_user(
        integration_database.sessions, role=UserRole.USER, username="webuser1"
    )

    with integration_database.sessions.begin() as session:
        user = authenticate_web_login(
            session, username="webuser1", password=_PASSWORD, now=NOW
        )
        raw_token = issue_web_session(session, user=user, now=NOW)
        assert user.id == seeded.id

    with integration_database.sessions.begin() as session:
        resolved = get_web_session_user(session, raw_token=raw_token, now=NOW)
        assert resolved is not None
        assert resolved.id == seeded.id


def test_authenticate_web_login_rejects_wrong_password_and_persists_the_failure(
    integration_database,
) -> None:
    seeded = _seed_user(
        integration_database.sessions, role=UserRole.USER, username="webuser2"
    )

    with integration_database.sessions.begin() as session:
        with pytest.raises(AuthenticationError):
            authenticate_web_login(
                session, username="webuser2", password="senha-errada", now=NOW
            )

    with integration_database.sessions.begin() as session:
        credential = session.get(UserCredential, seeded.id)
        assert credential.failed_login_attempts == 1


def test_five_failed_attempts_lock_the_account_temporarily(
    integration_database,
) -> None:
    _seed_user(integration_database.sessions, role=UserRole.USER, username="webuser3")

    for _ in range(5):
        with integration_database.sessions.begin() as session:
            with pytest.raises(AuthenticationError):
                authenticate_web_login(
                    session, username="webuser3", password="errada", now=NOW
                )

    with integration_database.sessions.begin() as session:
        with pytest.raises(AuthenticationRateLimited):
            authenticate_web_login(
                session,
                username="webuser3",
                password=_PASSWORD,
                now=NOW + timedelta(seconds=1),
            )


def test_issuing_a_new_session_revokes_the_previous_one(integration_database) -> None:
    seeded = _seed_user(
        integration_database.sessions, role=UserRole.USER, username="webuser4"
    )

    with integration_database.sessions.begin() as session:
        first_token = issue_web_session(session, user=seeded, now=NOW)

    with integration_database.sessions.begin() as session:
        second_token = issue_web_session(session, user=seeded, now=NOW)

    with integration_database.sessions.begin() as session:
        assert get_web_session_user(session, raw_token=first_token, now=NOW) is None
        assert (
            get_web_session_user(session, raw_token=second_token, now=NOW) is not None
        )


def test_revoke_web_session_invalidates_the_token(integration_database) -> None:
    seeded = _seed_user(
        integration_database.sessions, role=UserRole.USER, username="webuser5"
    )

    with integration_database.sessions.begin() as session:
        raw_token = issue_web_session(session, user=seeded, now=NOW)

    with integration_database.sessions.begin() as session:
        revoke_web_session(session, raw_token=raw_token, now=NOW)

    with integration_database.sessions.begin() as session:
        assert get_web_session_user(session, raw_token=raw_token, now=NOW) is None


def test_expired_session_is_not_resolved(integration_database) -> None:
    seeded = _seed_user(
        integration_database.sessions, role=UserRole.USER, username="webuser6"
    )

    with integration_database.sessions.begin() as session:
        raw_token = issue_web_session(session, user=seeded, now=NOW)

    far_future = NOW + timedelta(hours=13)
    with integration_database.sessions.begin() as session:
        assert (
            get_web_session_user(session, raw_token=raw_token, now=far_future) is None
        )


def test_admin_panel_access_denied_for_plain_user_allowed_for_dev(
    integration_database,
) -> None:
    plain_user = _seed_user(
        integration_database.sessions, role=UserRole.USER, username="webuser7"
    )
    dev_user = _seed_user(
        integration_database.sessions, role=UserRole.DEV, username="webuser8"
    )

    with integration_database.sessions.begin() as session:
        with pytest.raises(AuthorizationDenied):
            authorize(session, plain_user, Permission.ADMIN_PANEL_ACCESS)

    with integration_database.sessions.begin() as session:
        authorize(session, dev_user, Permission.ADMIN_PANEL_ACCESS)  # não levanta


def test_expires_at_matches_the_absolute_ttl_check_constraint(
    integration_database,
) -> None:
    """`ck_web_sessions_absolute_ttl` no banco reforça as 12h -- se a
    aplicação e o banco divergissem, este teste falharia no INSERT."""
    seeded = _seed_user(
        integration_database.sessions, role=UserRole.USER, username="webuser9"
    )

    with integration_database.sessions.begin() as session:
        issue_web_session(session, user=seeded, now=NOW)

    with integration_database.sessions.begin() as session:
        stored = session.scalar(
            select(WebSession).where(WebSession.user_id == seeded.id)
        )
        assert stored.expires_at == stored.authenticated_at + timedelta(hours=12)
