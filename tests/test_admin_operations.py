"""Fluxos críticos e fronteiras de segurança da TASK-102."""

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.admin.service_ops import ManagedService, ServiceState
from app.core.errors import ApiError, register_api_error_handler
from app.database.dependency import get_session
from app.feedback.models import (
    FeedbackChannel,
    FeedbackKind,
    FeedbackStatus,
    UserFeedback,
)
from app.missions.models import Mission, MissionStatus
from app.ops_controller import Command
from app.users.models import User, UserLifecycleStatus, UserRole
from app.webapp.admin_router import (
    DeleteUserRequest,
    FeedbackStatusUpdateRequest,
    MissionCommandRequest,
    ServiceActionRequest,
    UpdateUserRequest,
    api_keys_status,
    delete_user,
    list_feedback_endpoint,
    mission_command,
    router,
    service_action,
    update_feedback_status_endpoint,
    update_user,
)
from app.webapp.dependency import WEB_SESSION_COOKIE_NAME
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError


class FakeSession:
    def __init__(self, user: User | None = None):
        self.user = user
        self.added = []
        self.executed = []

    def scalar(self, _statement):
        value, self.user = self.user, None
        return value

    def scalars(self, _statement):
        return []

    def execute(self, statement):
        self.executed.append(str(statement))

    def add(self, value):
        self.added.append(value)

    def commit(self):
        pass

    def get(self, _model, _id):
        return None


def _user(role: UserRole = UserRole.ADMIN) -> User:
    now = datetime(2026, 8, 22, tzinfo=UTC)
    return User(
        id=uuid4(),
        display_name="Admin",
        username="admin",
        role=role,
        is_active=True,
        lifecycle_status=UserLifecycleStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )


def test_user_is_blocked_from_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    register_api_error_handler(app)
    app.include_router(router)
    session = MagicMock()
    app.dependency_overrides[get_session] = lambda: session
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user",
        lambda *a, **k: _user(UserRole.USER),
    )
    response = TestClient(app).get(
        "/api/v1/admin/api-keys/status", cookies={WEB_SESSION_COOKIE_NAME: "token"}
    )
    assert response.status_code == 403


def test_api_key_authentication_and_issuance_remain_disabled() -> None:
    assert api_keys_status(_user()) == {
        "enabled": False,
        "authentication_enabled": False,
        "issuance_enabled": False,
        "message": "Em breve / desativado",
    }


def test_controller_and_admin_contract_reject_non_allowlisted_service() -> None:
    with pytest.raises(ValidationError):
        Command(service="database", operation="restart")
    with pytest.raises(ValidationError):
        ServiceActionRequest(
            service="api", operation="restart", confirmation=True, reason="teste seguro"
        )


def test_block_revokes_access_stops_schedules_and_audits() -> None:
    target = _user(UserRole.USER)
    actor = _user()
    session = FakeSession(target)
    result = update_user(
        target.id,
        UpdateUserRequest(
            lifecycle_status=UserLifecycleStatus.BLOCKED, reason="abuso confirmado"
        ),
        session,
        actor,
    )
    assert result.lifecycle_status is UserLifecycleStatus.BLOCKED
    assert target.is_active is False
    statements = " ".join(session.executed)
    assert "web_sessions" in statements and "mission_schedules" in statements
    assert any(entry.action == "admin.user.updated" for entry in session.added)


def test_removal_is_tombstone_and_never_deletes_mission_or_price_history() -> None:
    target = _user(UserRole.USER)
    actor = _user()
    session = FakeSession(target)
    delete_user(
        target.id,
        DeleteUserRequest(confirmation="admin", reason="pedido do proprietário"),
        session,
        actor,
    )
    assert target.lifecycle_status is UserLifecycleStatus.DELETED
    assert target.username is None and target.telegram_user_id is None
    statements = " ".join(session.executed).lower()
    assert "delete from missions" not in statements
    assert "delete from price_observations" not in statements
    assert any(entry.action == "admin.user.deleted" for entry in session.added)


def test_service_operation_is_logical_confirmed_and_audited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = _user()
    session = FakeSession()
    monkeypatch.setattr(
        "app.webapp.admin_router.ControllerServiceOps.execute",
        lambda self, service, operation: ServiceState(
            service=service, status="running"
        ),
    )
    settings = MagicMock()
    result = service_action(
        ServiceActionRequest(
            service=ManagedService.COLLECTION_WORKER,
            operation="restart",
            confirmation=True,
            reason="recuperação operacional",
        ),
        session,
        actor,
        settings,
    )
    assert result.status == "running"
    assert any(entry.action == "admin.service.restart" for entry in session.added)


def test_admin_pauses_and_resumes_user_mission_with_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = _user()
    mission = Mission(
        id=uuid4(),
        user_id=uuid4(),
        title="Notebook",
        status=MissionStatus.ACTIVE,
        state_version=2,
        created_at=datetime(2026, 8, 22, tzinfo=UTC),
        updated_at=datetime(2026, 8, 22, tzinfo=UTC),
    )
    session = FakeSession()
    session.get = lambda *_: mission

    def transition(_session, **values):
        mission.status = (
            MissionStatus.PAUSED
            if values["command"].value == "pause"
            else MissionStatus.ACTIVE
        )
        mission.state_version += 1

    monkeypatch.setattr("app.webapp.admin_router.transition_mission", transition)
    paused = mission_command(
        mission.id,
        MissionCommandRequest(
            command="pause", expected_state_version=2, reason="manutenção"
        ),
        session,
        actor,
    )
    assert paused.status is MissionStatus.PAUSED
    resumed = mission_command(
        mission.id,
        MissionCommandRequest(
            command="resume", expected_state_version=3, reason="retomada"
        ),
        session,
        actor,
    )
    assert resumed.status is MissionStatus.ACTIVE
    assert sum(entry.action == "admin.mission.command" for entry in session.added) == 2


def test_mission_history_has_restrictive_owner_reference() -> None:
    foreign_key = next(iter(Mission.__table__.c.user_id.foreign_keys))
    assert foreign_key.ondelete == "RESTRICT"


def _feedback(
    *,
    kind: FeedbackKind = FeedbackKind.BUG,
    channel: FeedbackChannel = FeedbackChannel.WEB,
    status: FeedbackStatus = FeedbackStatus.NEW,
    user_id=None,
) -> UserFeedback:
    return UserFeedback(
        id=uuid4(),
        user_id=user_id,
        kind=kind,
        channel=channel,
        message="Mensagem de teste",
        store_name=None,
        store_url=None,
        status=status,
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )


def test_user_is_blocked_from_admin_feedback_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    register_api_error_handler(app)
    app.include_router(router)
    session = MagicMock()
    app.dependency_overrides[get_session] = lambda: session
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user",
        lambda *a, **k: _user(UserRole.USER),
    )
    response = TestClient(app).get(
        "/api/v1/admin/feedback", cookies={WEB_SESSION_COOKIE_NAME: "token"}
    )
    assert response.status_code == 403


def test_admin_feedback_list_rejects_limit_above_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nunca listagem ilimitada: `limit` acima do teto (100) é rejeitado
    pelo próprio contrato HTTP, antes de qualquer consulta ao banco."""
    app = FastAPI()
    register_api_error_handler(app)
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: MagicMock()
    monkeypatch.setattr(
        "app.webapp.dependency.get_web_session_user",
        lambda *a, **k: _user(UserRole.ADMIN),
    )
    response = TestClient(app).get(
        "/api/v1/admin/feedback?limit=500",
        cookies={WEB_SESSION_COOKIE_NAME: "token"},
    )
    assert response.status_code == 422


def test_list_feedback_endpoint_maps_items_and_resolves_user_display_name() -> None:
    actor = _user()
    reporter = _user(UserRole.USER)
    feedback = _feedback(user_id=reporter.id)
    session = FakeSession()
    session.scalars = lambda _statement: [feedback]
    session.scalar = lambda _statement: 1
    session.get = lambda _model, _id: reporter

    result = list_feedback_endpoint(
        status_filter=None,
        kind_filter=None,
        limit=20,
        offset=0,
        session=session,
        _=actor,
    )

    assert len(result.items) == 1
    assert result.limit == 20
    assert result.offset == 0
    assert result.total == 1
    item = result.items[0]
    assert item.id == feedback.id
    assert item.kind is FeedbackKind.BUG
    assert item.channel is FeedbackChannel.WEB
    assert item.user_display_name == reporter.display_name


def test_update_feedback_status_endpoint_updates_and_audits() -> None:
    actor = _user()
    feedback = _feedback()
    session = FakeSession()
    session.get = lambda _model, _id: feedback

    result = update_feedback_status_endpoint(
        feedback.id,
        FeedbackStatusUpdateRequest(status=FeedbackStatus.REVIEWED),
        session,
        actor,
    )

    assert result.status is FeedbackStatus.REVIEWED
    assert feedback.status is FeedbackStatus.REVIEWED
    assert any(
        entry.action == "admin.feedback.status_updated" for entry in session.added
    )


def test_update_feedback_status_endpoint_404_for_missing_feedback() -> None:
    actor = _user()
    session = FakeSession()
    session.get = lambda _model, _id: None

    with pytest.raises(ApiError) as exc_info:
        update_feedback_status_endpoint(
            uuid4(),
            FeedbackStatusUpdateRequest(status=FeedbackStatus.CLOSED),
            session,
            actor,
        )

    assert exc_info.value.status_code == 404
