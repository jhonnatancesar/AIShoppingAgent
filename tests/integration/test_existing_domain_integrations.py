"""Validações permanentes dos serviços que exigem PostgreSQL real."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.missions.models import (
    Mission,
    MissionCommand,
    MissionSchedule,
    MissionStatus,
)
from app.missions.service import transition_mission
from app.users.models import User, UserRole

from backend.scripts.validate_authorization import validate as validate_authorization
from backend.scripts.validate_limits_resilience import main as validate_resilience
from backend.scripts.validate_password_authentication import (
    main as validate_authentication,
)
from backend.scripts.validate_privacy import main as validate_privacy
from backend.scripts.validate_purchase_trail import validate as validate_purchase_trail
from backend.scripts.validate_recommendation_flow import (
    validate as validate_recommendation_flow,
)

pytestmark = pytest.mark.integration


def test_recommendation_comparison_and_confirmation(integration_database) -> None:
    validate_recommendation_flow()


def test_purchase_trail_and_concurrent_resolution(integration_database) -> None:
    validate_purchase_trail()


def test_password_authentication_and_single_use_concurrency(
    integration_database,
) -> None:
    validate_authentication()


def test_authorization_hierarchy_and_ownership(integration_database) -> None:
    validate_authorization()


def test_persistent_replay_rate_limit_and_event_resilience(
    integration_database,
) -> None:
    validate_resilience()


def test_privacy_cleanup_and_fail_closed_deidentification(
    integration_database,
) -> None:
    validate_privacy()


def test_cancel_mission_transition_disables_schedule_in_same_transaction(
    integration_database,
) -> None:
    now = datetime.now(UTC)
    with integration_database.sessions.begin() as session:
        user = User(
            id=uuid4(),
            display_name="Integração cancelamento determinístico",
            role=UserRole.USER,
            is_active=True,
            telegram_user_id=8_150_000_001,
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        session.flush()
        mission = Mission(
            id=uuid4(),
            user_id=user.id,
            title="RTX 5070",
            status=MissionStatus.ACTIVE,
            state_version=3,
            created_at=now - timedelta(hours=1),
            updated_at=now - timedelta(hours=1),
        )
        schedule = MissionSchedule(
            mission_id=mission.id,
            interval_minutes=60,
            next_run_at=now,
            is_enabled=True,
            created_at=now - timedelta(hours=1),
            updated_at=now - timedelta(hours=1),
        )
        session.add_all([mission, schedule])
        session.flush()

        transition = transition_mission(
            session,
            mission_id=mission.id,
            command=MissionCommand.CANCEL,
            expected_state_version=3,
            actor_type="telegram",
            actor_id=user.id,
            transitioned_at=now,
        )

        assert transition.to_status is MissionStatus.CANCELLED
        assert mission.status is MissionStatus.CANCELLED
        assert mission.state_version == 4
        assert schedule.is_enabled is False
        assert schedule.updated_at == now
