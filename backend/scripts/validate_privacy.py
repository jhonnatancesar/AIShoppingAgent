"""Valida retenção e desidentificação em PostgreSQL real sem deixar resíduos."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from app.audit.models import AuditEntry
from app.authentication.models import (
    CredentialAction,
    CredentialActionToken,
    UserAuthSession,
    UserCredential,
)
from app.core.config import Settings
from app.database.session import create_database_engine
from app.missions.models import (
    Mission,
    MissionCommand,
    MissionCriteria,
    MissionSchedule,
    MissionStatus,
    MissionTransition,
)
from app.privacy.service import (
    DEIDENTIFIED_DISPLAY_NAME,
    HistoricalPersonalDataConflict,
    cleanup_expired_authentication_artifacts,
    deidentify_account,
)
from app.users.models import User, UserRole
from sqlalchemy import select


def main() -> None:
    engine = create_database_engine(Settings(observability_enabled=False))
    connection = engine.connect()
    transaction = connection.begin()
    from sqlalchemy.orm import Session

    session = Session(bind=connection, expire_on_commit=False)
    now = datetime.now(UTC)
    marker = uuid4().hex
    try:
        user = User(
            display_name=f"Privacy Canary {marker}",
            role=UserRole.USER,
            telegram_user_id=8_000_000_000 + int(marker[:7], 16),
            telegram_chat_id=8_000_000_000 + int(marker[:7], 16),
            username=f"p{marker[:20]}",
            email=f"{marker}@example.invalid",
            favorite_stores=["kabum"],
            preferred_categories=["hardware"],
            pending_intent={"kind": "create_mission", "search_query": marker},
        )
        session.add(user)
        session.flush()
        mission = Mission(
            user_id=user.id,
            title=f"Notebook {marker}",
            status=MissionStatus.ACTIVE,
            state_version=1,
        )
        session.add(mission)
        session.flush()
        session.add_all(
            (
                MissionCriteria(
                    mission_id=mission.id,
                    search_query=f"Notebook {marker}",
                    target_amount=None,
                    target_currency=None,
                ),
                MissionSchedule(
                    mission_id=mission.id,
                    interval_minutes=60,
                    next_run_at=now + timedelta(hours=1),
                    is_enabled=True,
                ),
                UserCredential(user_id=user.id, password_hash=f"hash-{marker}"),
            )
        )
        token_created = now - timedelta(days=2)
        token = CredentialActionToken(
            token_hash=sha256(marker.encode()).hexdigest(),
            action=CredentialAction.LOGIN,
            user_id=user.id,
            telegram_user_id=user.telegram_user_id,
            failed_attempts=0,
            created_at=token_created,
            expires_at=token_created + timedelta(minutes=10),
        )
        authenticated_at = now - timedelta(days=45)
        auth_session = UserAuthSession(
            user_id=user.id,
            telegram_user_id=user.telegram_user_id,
            authenticated_at=authenticated_at,
            expires_at=authenticated_at + timedelta(hours=12),
        )
        session.add_all((token, auth_session))
        session.flush()

        cleanup = cleanup_expired_authentication_artifacts(session, now=now)
        assert cleanup.action_tokens_deleted >= 1
        assert cleanup.auth_sessions_deleted >= 1
        assert session.get(CredentialActionToken, token.id) is None
        assert session.get(UserAuthSession, auth_session.id) is None

        expected_telegram_id = user.telegram_user_id
        assert expected_telegram_id is not None
        result = deidentify_account(
            session,
            user_id=user.id,
            expected_telegram_user_id=expected_telegram_id,
        )
        assert result.missions_scrubbed == 1
        assert result.credentials_deleted == 1
        assert user.display_name == DEIDENTIFIED_DISPLAY_NAME
        assert user.telegram_user_id is None
        assert user.telegram_chat_id is None
        assert user.pending_intent is None
        assert user.is_active is False
        audit = session.scalar(
            select(AuditEntry).where(
                AuditEntry.resource_id == user.id,
                AuditEntry.action == "privacy.account_deidentified",
            )
        )
        assert audit is not None
        assert marker not in str(audit.entry_metadata)

        conflict_user = User(
            display_name=f"Conflict {marker}",
            role=UserRole.USER,
            telegram_user_id=9_000_000_000 + int(marker[:7], 16),
        )
        session.add(conflict_user)
        session.flush()
        conflict_mission = Mission(
            user_id=conflict_user.id,
            title="Conflict mission",
            status=MissionStatus.CANCELLED,
            state_version=1,
        )
        session.add(conflict_mission)
        session.flush()
        session.add(
            MissionTransition(
                mission_id=conflict_mission.id,
                from_status=MissionStatus.DRAFT,
                to_status=MissionStatus.CANCELLED,
                command=MissionCommand.CANCEL,
                actor_type="telegram",
                actor_id=conflict_user.id,
                reason=f"free text {marker}",
                transitioned_at=now,
            )
        )
        session.flush()
        try:
            deidentify_account(
                session,
                user_id=conflict_user.id,
                expected_telegram_user_id=conflict_user.telegram_user_id,
            )
        except HistoricalPersonalDataConflict as error:
            assert error.areas == ("mission_transitions.reason",)
        else:
            raise AssertionError("immutable free text conflict was not detected")
        assert conflict_user.is_active is True
        print("PostgreSQL privacy validation passed with transactional rollback")
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


if __name__ == "__main__":
    main()
