"""Controles da coleta: autorização, rollback de configuração e agenda real.

Somente PostgreSQL descartável; status operacional controlado, sem iniciar
workers ou contêineres pelos endpoints administrativos.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.admin.service_ops import ServiceOpsUnavailable
from app.authentication.service import issue_web_session
from app.collection.models import CollectionQueueConfig, StoreThrottleState
from app.core.config import get_settings
from app.core.errors import register_api_error_handler
from app.database.dependency import get_session
from app.missions.models import Mission, MissionSchedule, MissionSource, MissionStatus
from app.stores.models import Store
from app.users.models import User, UserRole
from app.webapp import admin_router
from app.webapp.csrf import CSRF_COOKIE_NAME
from app.webapp.dependency import WEB_SESSION_COOKIE_NAME
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

pytestmark = pytest.mark.integration


@pytest.fixture
def admin_client(integration_database, monkeypatch):
    database = integration_database
    app = FastAPI()
    register_api_error_handler(app)
    app.include_router(admin_router.router)

    def db_session():
        with database.sessions() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    class Operations:
        def status(self, service):
            if service.value == "collection_worker":
                raise ServiceOpsUnavailable("offline no teste")
            return SimpleNamespace(status="healthy", detail="controlado")

    monkeypatch.setattr(
        admin_router, "ControllerServiceOps", lambda settings: Operations()
    )
    app.dependency_overrides[get_session] = db_session
    app.dependency_overrides[get_settings] = lambda: database.settings
    with database.sessions.begin() as session:
        user = User(
            username="collection_admin",
            display_name="Admin de teste",
            role=UserRole.ADMIN,
        )
        session.add(user)
        session.flush()
        token = issue_web_session(session, user=user)
        owner = user.id
    with TestClient(
        app, cookies={WEB_SESSION_COOKIE_NAME: token, CSRF_COOKIE_NAME: "test-csrf"}
    ) as client:
        client.headers["X-CSRF-Token"] = "test-csrf"
        yield client, database, owner


def test_queue_overrides_preserve_omitted_fields_and_rollback_invalid_range(
    admin_client,
):
    client, database, _ = admin_client
    path = "/api/v1/admin/queue/config"
    response = client.patch(
        path,
        json={
            "max_concurrent_user_batches_override": 2,
            "user_cooldown_min_seconds_override": 20,
            "user_cooldown_max_seconds_override": 40,
            "store_min_interval_seconds_override": 3,
            "reason": "teste de configuração",
        },
    )
    assert response.status_code == 200
    assert response.json()["max_concurrent_user_batches"] == 2
    invalid = client.patch(
        path,
        json={"user_cooldown_max_seconds_override": 10, "reason": "intervalo inválido"},
    )
    assert invalid.status_code == 422
    with database.sessions() as session:
        row = session.get(CollectionQueueConfig, 1)
        assert row.user_cooldown_max_seconds_override == 40
        assert row.user_cooldown_min_seconds_override == 20
    cleared = client.patch(
        path,
        json={
            "max_concurrent_user_batches_override": None,
            "reason": "restaurar default",
        },
    )
    assert cleared.status_code == 200
    data = cleared.json()
    assert data["max_concurrent_user_batches_override"] is None
    assert (
        data["max_concurrent_user_batches"]
        == database.settings.max_concurrent_user_batches
    )
    assert data["user_cooldown_min_seconds"] == 20
    assert data["user_cooldown_max_seconds"] == 40


def test_collection_trigger_updates_source_schedule_and_provider_state(admin_client):
    client, database, owner = admin_client
    future = datetime.now(UTC) + timedelta(days=1)
    with database.sessions.begin() as session:
        store = session.scalar(select(Store).where(Store.code == "terabyte"))
        store_id = store.id
        mission = Mission(
            user_id=owner, title="Coleta de teste", status=MissionStatus.ACTIVE
        )
        session.add(mission)
        session.flush()
        mission_id = mission.id
        session.add(
            MissionSchedule(
                mission_id=mission_id, next_run_at=future, interval_minutes=60
            )
        )
        session.add(
            MissionSource(mission_id=mission_id, store_id=store_id, next_run_at=future)
        )
    payload = {
        "mission_id": str(mission_id),
        "reason": "disparo controlado",
        "confirmation": False,
    }
    assert (
        client.post("/api/v1/admin/collections/trigger", json=payload).status_code
        == 409
    )
    with database.sessions() as session:
        schedule = session.scalar(
            select(MissionSchedule).where(MissionSchedule.mission_id == mission_id)
        )
        assert schedule.next_run_at == future
    payload["confirmation"] = True
    response = client.post("/api/v1/admin/collections/trigger", json=payload)
    assert response.status_code == 202 and response.json() == {"status": "scheduled"}
    with database.sessions() as session:
        source = session.scalar(
            select(MissionSource).where(MissionSource.mission_id == mission_id)
        )
        schedule = session.scalar(
            select(MissionSchedule).where(MissionSchedule.mission_id == mission_id)
        )
        assert source.next_run_at == schedule.next_run_at < future
        assert schedule.is_enabled
    for enabled in (False, True):
        response = client.patch(
            f"/api/v1/admin/providers/{store_id}",
            json={"enabled": enabled, "reason": "teste da loja"},
        )
        assert response.status_code == 200 and response.json()["is_active"] is enabled
    assert (
        client.patch(
            f"/api/v1/admin/providers/{uuid4()}",
            json={"enabled": True, "reason": "loja ausente"},
        ).status_code
        == 404
    )
    payload["mission_id"] = str(uuid4())
    assert (
        client.post("/api/v1/admin/collections/trigger", json=payload).status_code
        == 404
    )


def test_dashboard_reports_real_counts_and_store_throttle(admin_client):
    client, database, _ = admin_client
    with database.sessions.begin() as session:
        store = session.scalar(select(Store).where(Store.code == "terabyte"))
        session.add(
            StoreThrottleState(
                store_id=store.id,
                next_allowed_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
    response = client.get("/api/v1/admin/dashboard")
    assert response.status_code == 200
    data = response.json()
    assert data["users"]["total"] == 1
    assert data["missions"]["total"] == 0
    assert any(item["status"] == "unavailable" for item in data["workers"])
    assert any(item["status"] == "healthy" for item in data["workers"])
    queue = client.get("/api/v1/admin/queue")
    assert queue.status_code == 200
    assert queue.json()["users"] == []
    assert next(item for item in queue.json()["stores"] if item["code"] == "terabyte")[
        "throttled"
    ]
    users = client.get("/api/v1/admin/users", params={"q": "collection_admin"})
    assert users.status_code == 200 and len(users.json()["items"]) == 1
