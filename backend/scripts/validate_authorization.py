"""Valida a TASK-047 em PostgreSQL real e desfaz os dados temporarios."""

from app.audit.models import AuditEntry
from app.authorization import (
    ROLE_PERMISSIONS,
    AuthorizationDenialReason,
    AuthorizationDenied,
    Permission,
    authorize,
)
from app.core.config import Settings
from app.database.session import create_database_engine, create_session_factory
from app.missions.models import Mission, MissionCriteria, MissionStatus
from app.purchase import (
    MissionNotFoundForRecommendationError,
    RecommendationReason,
    RecommendationStatus,
    recommend_for_mission,
)
from app.users.models import User, UserRole
from sqlalchemy import select


def validate() -> None:
    settings = Settings()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    session = session_factory()
    transaction = session.begin()
    try:
        users = {
            role: User(display_name=f"TASK-047 {role.value}", role=role)
            for role in UserRole
        }
        session.add_all(users.values())
        session.flush()

        if not (
            ROLE_PERMISSIONS[UserRole.USER]
            < ROLE_PERMISSIONS[UserRole.ADMIN]
            < ROLE_PERMISSIONS[UserRole.DEV]
        ):
            raise RuntimeError("role permissions do not form a strict hierarchy")

        mission = Mission(
            user_id=users[UserRole.USER].id,
            title="validacao ownership TASK-047",
            status=MissionStatus.ACTIVE,
            state_version=1,
        )
        session.add(mission)
        session.flush()
        session.add(
            MissionCriteria(
                mission_id=mission.id,
                search_query="produto temporario",
            )
        )
        session.flush()

        own_result = recommend_for_mission(
            session,
            mission.id,
            owner_user_id=users[UserRole.USER].id,
        )
        if (
            own_result.status is not RecommendationStatus.INSUFFICIENT_DATA
            or own_result.reason is not RecommendationReason.MISSION_CURRENCY_MISSING
        ):
            raise RuntimeError("owner could not read its own mission consistently")

        for privileged_role in (UserRole.ADMIN, UserRole.DEV):
            try:
                recommend_for_mission(
                    session,
                    mission.id,
                    owner_user_id=users[privileged_role].id,
                )
            except MissionNotFoundForRecommendationError:
                pass
            else:
                raise RuntimeError(f"{privileged_role.value} bypassed ownership")

        try:
            authorize(
                session,
                users[UserRole.USER],
                Permission.AI_PROFILE_ADMIN,
            )
        except AuthorizationDenied as error:
            if error.reason is not AuthorizationDenialReason.PERMISSION_DENIED:
                raise RuntimeError("unexpected authorization denial reason") from error
        else:
            raise RuntimeError("USER obtained an ADMIN-only permission")
        session.flush()

        audit = session.scalar(
            select(AuditEntry).where(
                AuditEntry.actor_id == users[UserRole.USER].id,
                AuditEntry.action == "authorization.denied",
            )
        )
        if audit is None or audit.entry_metadata != {
            "permission": "ai_profile.admin",
            "reason": "permission_denied",
            "role": "USER",
        }:
            raise RuntimeError("authorization denial was not audited safely")

        print(
            "TASK-047 PostgreSQL validation passed: "
            "hierarchy=strict, ownership=isolated, denial=audit_only"
        )
    finally:
        transaction.rollback()
        session.close()
        engine.dispose()


if __name__ == "__main__":
    validate()
