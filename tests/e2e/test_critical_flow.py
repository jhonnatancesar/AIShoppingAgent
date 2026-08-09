"""Regressão E2E da cadeia crítica sem depender da Internet pública."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from app.ai_provider import AIResponse
from app.authentication.models import (
    CredentialAction,
    CredentialActionToken,
    UserAuthSession,
    UserCredential,
)
from app.collection import (
    CollectionAdapter,
    CollectionRequest,
    CollectionResult,
    RawCollectedOffer,
)
from app.collection.models import CollectionRun, CollectionRunStatus, PriceObservation
from app.collection.worker import run_worker as run_collection_worker
from app.core.config import get_settings
from app.database.dependency import _get_session_factory
from app.events import ConsumptionOutcome, Event, EventConsumptionAttempt, EventType
from app.intent import Intent, IntentKind, IntentParameters
from app.main import app
from app.missions.models import Mission, MissionCommand, MissionSchedule, MissionStatus
from app.telegram.notifications import (
    TELEGRAM_AUTH_NOTIFICATION_CONSUMER,
    TELEGRAM_NOTIFICATION_CONSUMER,
)
from app.telegram.router import get_telegram_intent_adapters
from app.telegram.worker import run_worker as run_telegram_worker
from app.users.models import User, UserRole
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tests.integration.conftest import IntegrationDatabase

pytestmark = [pytest.mark.integration, pytest.mark.e2e]

_WEBHOOK_SECRET = "integration-local-boundary"
_BOT_TOKEN = "integration-local-boundary"
_PRIMARY_TELEGRAM_ID = 8_100_000_001
_OTHER_TELEGRAM_ID = 8_100_000_002


class _ControlledManager:
    async def generate(self, request):
        answer = {"answer": "confirm"}
        return AIResponse(
            request_id=request.request_id,
            provider="controlled_boundary",
            model="e2e-local",
            content=json.dumps(answer),
            finished_at=datetime.now(UTC),
        )


class _ControlledAdapter:
    def __init__(self) -> None:
        self.manager = _ControlledManager()

    async def interpret(self, message, *, profile=UserRole.USER):
        text = message.text.lower()
        if "consultar" in text:
            kind = IntentKind.QUERY_MISSION
            parameters = IntentParameters(mission_reference="e2e principal")
            command = None
        elif "pausar" in text:
            kind = IntentKind.MISSION_COMMAND
            parameters = IntentParameters(mission_reference="e2e pausada")
            command = MissionCommand.PAUSE
        else:
            kind = IntentKind.CREATE_MISSION
            query = (
                "e2e pausada"
                if "pausada" in text
                else "e2e skipped"
                if "skipped" in text
                else "e2e principal"
            )
            parameters = IntentParameters(
                search_query=query,
                target_amount=Decimal("999999.00"),
                target_currency="BRL",
                sources=("pichau", "terabyte", "amazon", "kabum"),
            )
            command = None
        return Intent(
            correlation_id=uuid4(),
            kind=kind,
            raw_message=message.text,
            interpreted_at=datetime.now(UTC),
            command=command,
            parameters=parameters,
        )


class _ControlledProvider:
    def __init__(self, source_code: str, *, fail: bool = False) -> None:
        self.source_code = source_code
        self._fail = fail

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        if self._fail:
            raise ConnectionError("controlled external boundary unavailable")
        now = datetime.now(UTC)
        identity = f"{request.mission_id.hex}-{self.source_code}"
        return CollectionResult(
            source_code=self.source_code,
            started_at=max(now, request.requested_at),
            completed_at=max(now, request.requested_at),
            offers=(
                RawCollectedOffer(
                    source_code=self.source_code,
                    url=f"https://example.test/{self.source_code}/{identity}",
                    title=f"Controlled {self.source_code} offer",
                    collected_at=max(now, request.requested_at),
                    external_id=identity,
                    raw_price="R$ 1.000,00",
                    raw_currency="BRL",
                    raw_shipping="Frete grátis",
                    raw_availability="Em estoque",
                    evidence={"source": "controlled_e2e_boundary"},
                ),
            ),
        )


def _adapter() -> CollectionAdapter:
    return CollectionAdapter(
        (
            _ControlledProvider("pichau"),
            _ControlledProvider("terabyte"),
            _ControlledProvider("amazon", fail=True),
            _ControlledProvider("kabum"),
        )
    )


def _seed_authenticated_user(database: IntegrationDatabase, telegram_id: int) -> User:
    now = datetime.now(UTC)
    with database.sessions.begin() as session:
        user = User(
            display_name="E2E synthetic user",
            role=UserRole.USER,
            telegram_user_id=telegram_id,
            telegram_chat_id=telegram_id,
            username=f"e2e_{telegram_id}",
        )
        session.add(user)
        session.flush()
        session.add(
            UserAuthSession(
                user_id=user.id,
                telegram_user_id=telegram_id,
                authenticated_at=now,
                expires_at=now + timedelta(hours=12),
            )
        )
        return user


def _payload(update_id: int, telegram_id: int, text: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "text": text,
            "date": int(datetime.now(UTC).timestamp()),
            "chat": {"id": telegram_id, "type": "private"},
            "from": {"id": telegram_id, "first_name": "Pessoa E2E"},
        },
    }


def _post(client: TestClient, update_id: int, telegram_id: int, text: str) -> None:
    response = client.post(
        "/telegram/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": _WEBHOOK_SECRET},
        json=_payload(update_id, telegram_id, text),
    )
    assert response.status_code == 204


@pytest.mark.anyio
async def test_registration_numbered_stores_password_and_login_are_one_onboarding(
    integration_database: IntegrationDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = integration_database
    replies: list[tuple[int, str]] = []
    auth_notifications: list[tuple[int, str]] = []

    async def capture_reply(chat_id, text, **kwargs):
        replies.append((chat_id, text))

    async def capture_notification(chat_id, text, **kwargs):
        auth_notifications.append((chat_id, text))

    monkeypatch.setattr("app.telegram.router.send_message", capture_reply)
    monkeypatch.setattr("app.telegram.notifications.send_message", capture_notification)
    _get_session_factory.cache_clear()
    get_settings.cache_clear()
    app.dependency_overrides[get_settings] = lambda: database.settings
    app.dependency_overrides[get_telegram_intent_adapters] = lambda: {
        role: _ControlledAdapter() for role in UserRole
    }

    try:
        with TestClient(app) as client:
            _post(client, 200, _PRIMARY_TELEGRAM_ID, "/cadastro")
            _post(client, 201, _PRIMARY_TELEGRAM_ID, "pessoa_e2e")
            _post(client, 202, _PRIMARY_TELEGRAM_ID, "pular")
            assert "1 - Kabum" in replies[-1][1]
            assert "5 - Todas" in replies[-1][1]
            _post(client, 203, _PRIMARY_TELEGRAM_ID, "5")
            _post(client, 204, _PRIMARY_TELEGRAM_ID, "informatica")
            registration_reply = replies[-1][1]
            assert "Cadastro confirmado" in registration_reply
            setup_token = registration_reply.rsplit("#password:", 1)[1].split()[0]

            setup = client.post(
                "/auth/actions",
                json={
                    "token": setup_token,
                    "password": "marAzul9",
                    "password_confirmation": "marAzul9",
                },
            )
            assert setup.status_code == 200
            assert setup.json()["ok"] is True
            assert "/entrar" in setup.json()["message"]

            _post(client, 205, _PRIMARY_TELEGRAM_ID, "/entrar")
            login_token = replies[-1][1].rsplit("#login:", 1)[1].split()[0]
            login = client.post(
                "/auth/actions",
                json={"token": login_token, "password": "marAzul9"},
            )
            assert login.status_code == 200
            assert login.json()["ok"] is True

            await run_telegram_worker(database.settings, once=True)
            assert [chat_id for chat_id, _ in auth_notifications] == [
                _PRIMARY_TELEGRAM_ID,
                _PRIMARY_TELEGRAM_ID,
            ]
            assert "Senha criada" in auth_notifications[0][1]
            assert "Login realizado" in auth_notifications[1][1]

        with database.sessions.begin() as session:
            user = session.scalar(
                select(User).where(User.telegram_user_id == _PRIMARY_TELEGRAM_ID)
            )
            assert user is not None
            assert user.favorite_stores == ["amazon", "kabum", "pichau", "terabyte"]
            assert session.get(UserCredential, user.id) is not None
            assert (
                session.scalar(
                    select(func.count(UserAuthSession.id)).where(
                        UserAuthSession.user_id == user.id,
                        UserAuthSession.revoked_at.is_(None),
                    )
                )
                == 1
            )
            actions = set(
                session.scalars(
                    select(CredentialActionToken.action).where(
                        CredentialActionToken.user_id == user.id
                    )
                )
            )
            assert actions == {CredentialAction.SET_PASSWORD, CredentialAction.LOGIN}
            assert (
                session.scalar(
                    select(func.count(EventConsumptionAttempt.id)).where(
                        EventConsumptionAttempt.consumer_name
                        == TELEGRAM_AUTH_NOTIFICATION_CONSUMER,
                        EventConsumptionAttempt.outcome == ConsumptionOutcome.SUCCEEDED,
                    )
                )
                == 2
            )

        with database.sessions.begin() as session:
            auth_session = session.scalar(
                select(UserAuthSession).where(
                    UserAuthSession.user_id == user.id,
                    UserAuthSession.revoked_at.is_(None),
                )
            )
            assert auth_session is not None
            warning_now = datetime.now(UTC)
            auth_session.authenticated_at = warning_now - timedelta(
                hours=11, minutes=40
            )
            auth_session.expires_at = auth_session.authenticated_at + timedelta(
                hours=12
            )
            auth_session.expiry_warning_event_published = False
            auth_session.expiry_event_published = False
            auth_session_id = auth_session.id

        await run_telegram_worker(database.settings, once=True)
        assert "expira em breve" in auth_notifications[-1][1]
        warning_count = len(auth_notifications)
        await run_telegram_worker(database.settings, once=True)
        assert len(auth_notifications) == warning_count

        with database.sessions.begin() as session:
            auth_session = session.get(UserAuthSession, auth_session_id)
            assert auth_session is not None
            expired_now = datetime.now(UTC)
            auth_session.authenticated_at = expired_now - timedelta(hours=12, minutes=1)
            auth_session.expires_at = auth_session.authenticated_at + timedelta(
                hours=12
            )
            auth_session.expiry_event_published = False

        await run_telegram_worker(database.settings, once=True)
        assert "sessão expirou" in auth_notifications[-1][1]
        expired_count = len(auth_notifications)
        await run_telegram_worker(database.settings, once=True)
        assert len(auth_notifications) == expired_count
    finally:
        app.dependency_overrides.clear()
        _get_session_factory.cache_clear()
        get_settings.cache_clear()


@pytest.mark.anyio
async def test_critical_chain_replay_restart_skipped_and_ownership(
    integration_database: IntegrationDatabase,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    database = integration_database
    primary = _seed_authenticated_user(database, _PRIMARY_TELEGRAM_ID)
    _seed_authenticated_user(database, _OTHER_TELEGRAM_ID)
    adapter = _ControlledAdapter()
    adapters = {role: adapter for role in UserRole}
    replies: list[tuple[int, str]] = []
    notifications: list[tuple[int, str]] = []

    async def capture_reply(chat_id, text, **kwargs):
        replies.append((chat_id, text))

    async def capture_notification(chat_id, text, **kwargs):
        notifications.append((chat_id, text))

    monkeypatch.setattr("app.telegram.router.send_message", capture_reply)
    monkeypatch.setattr("app.telegram.notifications.send_message", capture_notification)
    monkeypatch.setattr(
        "app.collection.worker.build_collection_adapter", lambda _: _adapter()
    )
    monkeypatch.setattr("app.missions.schedule.random.uniform", lambda a, b: 0.0)
    _get_session_factory.cache_clear()
    get_settings.cache_clear()
    app.dependency_overrides[get_settings] = lambda: database.settings
    app.dependency_overrides[get_telegram_intent_adapters] = lambda: adapters

    try:
        with TestClient(app) as client:
            _post(client, 100, _PRIMARY_TELEGRAM_ID, "criar e2e principal")
            assert "Responda" in replies[-1][1]
            _post(client, 101, _PRIMARY_TELEGRAM_ID, "sim")
            assert "criada" in replies[-1][1].lower()
            _post(client, 101, _PRIMARY_TELEGRAM_ID, "sim")

            with database.sessions.begin() as session:
                missions = list(
                    session.scalars(
                        select(Mission).where(Mission.user_id == primary.id)
                    )
                )
                assert len(missions) == 1
                principal_id = missions[0].id
                assert missions[0].status is MissionStatus.ACTIVE
                assert (
                    session.scalar(
                        select(func.count(MissionSchedule.id)).where(
                            MissionSchedule.mission_id == principal_id
                        )
                    )
                    == 1
                )

            await run_collection_worker(database.settings, once=True)

            with database.sessions.begin() as session:
                runs = list(
                    session.scalars(
                        select(CollectionRun).where(
                            CollectionRun.mission_id == principal_id
                        )
                    )
                )
                run_ids = {run.id for run in runs}
                assert len(runs) == 4
                assert (
                    sum(run.status is CollectionRunStatus.SUCCEEDED for run in runs)
                    == 3
                )
                assert (
                    sum(run.status is CollectionRunStatus.FAILED for run in runs) == 1
                )
                observation_ids = set(
                    session.scalars(
                        select(PriceObservation.id).where(
                            PriceObservation.collection_run_id.in_(run_ids)
                        )
                    )
                )
                assert len(observation_ids) == 3
                target_events = list(
                    session.scalars(
                        select(Event).where(
                            Event.mission_id == principal_id,
                            Event.event_type == EventType.PRICE_TARGET_REACHED_V1.value,
                        )
                    )
                )
                assert len(target_events) == 3
                target_event_ids = {event.id for event in target_events}

            await run_telegram_worker(database.settings, once=True)
            assert len(notifications) == 3
            assert all(chat_id == _PRIMARY_TELEGRAM_ID for chat_id, _ in notifications)

            with database.sessions.begin() as session:
                terminals = list(
                    session.scalars(
                        select(EventConsumptionAttempt).where(
                            EventConsumptionAttempt.event_id.in_(target_event_ids),
                            EventConsumptionAttempt.consumer_name
                            == TELEGRAM_NOTIFICATION_CONSUMER,
                        )
                    )
                )
                assert len(terminals) == 3
                assert all(
                    item.outcome is ConsumptionOutcome.SUCCEEDED for item in terminals
                )

            await run_collection_worker(database.settings, once=True)
            await run_telegram_worker(database.settings, once=True)
            with database.sessions.begin() as session:
                assert (
                    set(
                        session.scalars(
                            select(CollectionRun.id).where(
                                CollectionRun.mission_id == principal_id
                            )
                        )
                    )
                    == run_ids
                )
                assert (
                    set(
                        session.scalars(
                            select(PriceObservation.id).where(
                                PriceObservation.collection_run_id.in_(run_ids)
                            )
                        )
                    )
                    == observation_ids
                )
                assert (
                    session.scalar(
                        select(func.count(EventConsumptionAttempt.id)).where(
                            EventConsumptionAttempt.event_id.in_(target_event_ids),
                            EventConsumptionAttempt.consumer_name
                            == TELEGRAM_NOTIFICATION_CONSUMER,
                        )
                    )
                    == 3
                )
            assert len(notifications) == 3

            _post(client, 110, _PRIMARY_TELEGRAM_ID, "/preferencias alvo desativar")
            _post(client, 111, _PRIMARY_TELEGRAM_ID, "criar e2e skipped")
            _post(client, 112, _PRIMARY_TELEGRAM_ID, "sim")
            with database.sessions.begin() as session:
                skipped_mission = session.scalar(
                    select(Mission).where(
                        Mission.user_id == primary.id,
                        Mission.title == "e2e skipped",
                    )
                )
                assert skipped_mission is not None
                skipped_mission_id = skipped_mission.id

            await run_collection_worker(database.settings, once=True)
            await run_telegram_worker(database.settings, once=True)
            with database.sessions.begin() as session:
                skipped_events = list(
                    session.scalars(
                        select(Event.id).where(
                            Event.mission_id == skipped_mission_id,
                            Event.event_type == EventType.PRICE_TARGET_REACHED_V1.value,
                        )
                    )
                )
                assert skipped_events
                assert session.scalar(
                    select(func.count(EventConsumptionAttempt.id)).where(
                        EventConsumptionAttempt.event_id.in_(skipped_events),
                        EventConsumptionAttempt.outcome == ConsumptionOutcome.SKIPPED,
                    )
                ) == len(skipped_events)
            sent_before_reactivation = len(notifications)
            _post(client, 113, _PRIMARY_TELEGRAM_ID, "/preferencias alvo ativar")
            await run_telegram_worker(database.settings, once=True)
            assert len(notifications) == sent_before_reactivation

            _post(client, 120, _PRIMARY_TELEGRAM_ID, "criar e2e pausada")
            _post(client, 121, _PRIMARY_TELEGRAM_ID, "sim")
            _post(client, 122, _PRIMARY_TELEGRAM_ID, "pausar e2e pausada")
            _post(client, 123, _PRIMARY_TELEGRAM_ID, "sim")
            with database.sessions.begin() as session:
                paused = session.scalar(
                    select(Mission).where(
                        Mission.user_id == primary.id,
                        Mission.title == "e2e pausada",
                    )
                )
                assert paused is not None and paused.status is MissionStatus.PAUSED
                paused_id = paused.id
            await run_collection_worker(database.settings, once=True)
            with database.sessions.begin() as session:
                assert (
                    session.scalar(
                        select(func.count(CollectionRun.id)).where(
                            CollectionRun.mission_id == paused_id
                        )
                    )
                    == 0
                )

            _post(client, 130, _OTHER_TELEGRAM_ID, "consultar e2e principal")
            assert "nenhuma missão" in replies[-1][1].lower()
    finally:
        app.dependency_overrides.clear()
        _get_session_factory.cache_clear()
        get_settings.cache_clear()

    rendered_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "e2e principal" not in rendered_logs
    assert str(_PRIMARY_TELEGRAM_ID) not in rendered_logs
