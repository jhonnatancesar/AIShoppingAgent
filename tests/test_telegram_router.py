import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.ai_provider import AIProviderQuotaExceeded, AIProviderUnavailable
from app.core.config import Settings
from app.intent import Intent, IntentKind, IntentParameters
from app.main import app
from app.missions.query import MissionReferenceError
from app.missions.service import MissionCreationError, MissionTransitionConditionError
from app.telegram.confirmation import ConfirmationError
from app.telegram.contracts import TelegramChatType, TelegramMessage
from app.telegram.router import (
    TelegramUpdate,
    _TelegramChat,
    _TelegramIncomingMessage,
    _TelegramSender,
    receive_telegram_webhook,
)
from app.users.models import UserRole


class _FakeAdapter:
    def __init__(self, outcome: Intent | Exception) -> None:
        self.outcome = outcome
        self.calls: list[tuple[TelegramMessage, UserRole]] = []
        self.manager = object()

    async def interpret(
        self, message: TelegramMessage, *, profile: UserRole = UserRole.USER
    ) -> Intent:
        self.calls.append((message, profile))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _patch_resolve_answer(
    monkeypatch: pytest.MonkeyPatch, outcome: bool | Exception
) -> None:
    async def _fake_resolve_answer(text: str, *, manager: object, profile: UserRole):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr("app.telegram.router.resolve_answer", _fake_resolve_answer)


def _fake_user(
    *,
    role: object = UserRole.USER,
    registration_step: str | None = None,
    pending_intent: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        role=role,
        telegram_user_id=222,
        telegram_chat_id=None,
        notify_price_decreases=True,
        notify_target_reached=True,
        is_active=True,
        registration_step=registration_step,
        pending_intent=pending_intent,
        username=None,
        email=None,
        favorite_stores=[],
        preferred_categories=[],
    )


def _adapters(adapter: _FakeAdapter, *, role: UserRole = UserRole.USER) -> dict:
    return {role: adapter}


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "_env_file": None,
        "telegram_webhook_secret": "correct-secret",
        "telegram_bot_token": "bot-token",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _update(**overrides: object) -> TelegramUpdate:
    defaults: dict[str, object] = {
        "update_id": 1,
        "message": _TelegramIncomingMessage(
            text="Quero um notebook até R$ 5000",
            date=1754586000,
            chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
            from_=_TelegramSender(id=222, first_name="Fulano"),
        ),
    }
    defaults.update(overrides)
    return TelegramUpdate(**defaults)  # type: ignore[arg-type]


def _intent(**overrides: object) -> Intent:
    defaults: dict[str, object] = {
        "correlation_id": uuid4(),
        "kind": IntentKind.CREATE_MISSION,
        "raw_message": "Quero um notebook até R$ 5000",
        "interpreted_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return Intent(**defaults)  # type: ignore[arg-type]


def _patch_user(
    monkeypatch: pytest.MonkeyPatch, user: SimpleNamespace
) -> list[tuple[int, str]]:
    resolve_calls: list[tuple[int, str]] = []

    def _fake_get_or_create(
        session: object, *, telegram_user_id: int, display_name: str
    ) -> SimpleNamespace:
        resolve_calls.append((telegram_user_id, display_name))
        return user

    monkeypatch.setattr(
        "app.telegram.authentication.get_or_create_telegram_user",
        _fake_get_or_create,
    )
    return resolve_calls


def _patch_send_message(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, str]]:
    send_calls: list[tuple[int, str]] = []

    async def _fake_send_message(chat_id: int, text: str, *, bot_token: object) -> None:
        send_calls.append((chat_id, text))

    monkeypatch.setattr("app.telegram.router.send_message", _fake_send_message)
    return send_calls


@pytest.mark.anyio
async def test_valid_secret_and_text_message_returns_204_and_calls_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_user(monkeypatch, _fake_user())
    adapter = _FakeAdapter(_intent(kind=IntentKind.UNKNOWN))

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(telegram_bot_token=None),
        session=MagicMock(),
    )

    assert response.status_code == 204
    assert len(adapter.calls) == 1
    message, profile = adapter.calls[0]
    assert message.chat_id == 222
    assert message.user_id == 222
    assert message.text == "Quero um notebook até R$ 5000"
    assert message.received_at == datetime.fromtimestamp(1754586000, tz=UTC)
    assert profile is UserRole.USER


@pytest.mark.anyio
async def test_admin_user_uses_admin_dev_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_user(monkeypatch, _fake_user(role=UserRole.ADMIN))
    _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent(kind=IntentKind.UNKNOWN))

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter, role=UserRole.ADMIN),  # type: ignore[arg-type]
        settings=_settings(),
        session=MagicMock(),
    )

    assert response.status_code == 204
    assert len(adapter.calls) == 1
    assert adapter.calls[0][1] is UserRole.ADMIN


@pytest.mark.anyio
@pytest.mark.parametrize("token", [None, "wrong-secret"])
async def test_missing_or_wrong_secret_returns_401_without_calling_adapter(
    monkeypatch: pytest.MonkeyPatch,
    token: str | None,
) -> None:
    resolve_calls = _patch_user(monkeypatch, _fake_user())
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token=token,
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=MagicMock(),
    )

    assert response.status_code == 401
    payload = json.loads(response.body)
    assert payload["error"]["code"] == "telegram_webhook_unauthorized"
    assert resolve_calls == []
    assert adapter.calls == []


@pytest.mark.anyio
async def test_unconfigured_secret_rejects_every_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolve_calls = _patch_user(monkeypatch, _fake_user())
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="anything",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(telegram_webhook_secret=None),
        session=MagicMock(),
    )

    assert response.status_code == 401
    assert resolve_calls == []
    assert adapter.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("chat_type", "reason"),
    [
        (TelegramChatType.GROUP, "non_private_chat"),
        (TelegramChatType.SUPERGROUP, "non_private_chat"),
        (TelegramChatType.CHANNEL, "non_private_chat"),
        (TelegramChatType.PRIVATE, "identity_mismatch"),
    ],
)
async def test_rejected_telegram_identity_is_sanitized_no_op(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    chat_type: TelegramChatType,
    reason: str,
) -> None:
    user = _fake_user()
    resolve_calls = _patch_user(monkeypatch, user)
    send_calls = _patch_send_message(monkeypatch)
    remember = MagicMock(side_effect=AssertionError("must not remember chat"))
    monkeypatch.setattr(
        "app.telegram.router.remember_private_notification_chat", remember
    )
    adapter = _FakeAdapter(_intent())

    with caplog.at_level("WARNING", logger="app.telegram"):
        response = await receive_telegram_webhook(
            update=_update(
                message=_TelegramIncomingMessage(
                    text="segredo-canario",
                    date=1754586000,
                    chat=_TelegramChat(id=111, type=chat_type),
                    from_=_TelegramSender(id=222, first_name="Fulano"),
                )
            ),
            x_telegram_bot_api_secret_token="correct-secret",
            adapters=_adapters(adapter),  # type: ignore[arg-type]
            settings=_settings(),
            session=MagicMock(),
        )

    assert response.status_code == 204
    assert resolve_calls == []
    assert adapter.calls == []
    assert send_calls == []
    remember.assert_not_called()
    record = caplog.records[-1]
    assert record.message == "telegram_authentication_rejected"
    assert record.authentication_reason == reason  # type: ignore[attr-defined]
    assert "111" not in record.getMessage()
    assert "222" not in record.getMessage()
    assert "segredo-canario" not in record.getMessage()


@pytest.mark.anyio
async def test_inactive_user_is_rejected_before_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _fake_user()
    user.is_active = False
    resolve_calls = _patch_user(monkeypatch, user)
    send_calls = _patch_send_message(monkeypatch)
    remember = MagicMock(side_effect=AssertionError("must not remember chat"))
    monkeypatch.setattr(
        "app.telegram.router.remember_private_notification_chat", remember
    )
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=MagicMock(),
    )

    assert response.status_code == 204
    assert resolve_calls == [(222, "Fulano")]
    assert adapter.calls == []
    assert send_calls == []
    remember.assert_not_called()
    assert user.telegram_chat_id is None


@pytest.mark.anyio
async def test_update_without_message_is_a_no_op_204() -> None:
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(message=None),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=MagicMock(),
    )

    assert response.status_code == 204
    assert adapter.calls == []


@pytest.mark.anyio
async def test_message_without_text_is_a_no_op_204() -> None:
    adapter = _FakeAdapter(_intent())
    update = _update(
        message=_TelegramIncomingMessage(
            text=None,
            date=1754586000,
            chat=_TelegramChat(id=111, type=TelegramChatType.GROUP),
            from_=_TelegramSender(id=222, first_name="Fulano"),
        )
    )

    response = await receive_telegram_webhook(
        update=update,
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=MagicMock(),
    )

    assert response.status_code == 204
    assert adapter.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("error", [AIProviderQuotaExceeded(), AIProviderUnavailable()])
async def test_ai_provider_failure_still_returns_204(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    _patch_user(monkeypatch, _fake_user())
    adapter = _FakeAdapter(error)

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=MagicMock(),
    )

    assert response.status_code == 204
    assert len(adapter.calls) == 1


def test_telegram_webhook_route_is_exposed_in_openapi() -> None:
    operation = app.openapi()["paths"]["/telegram/webhook"]["post"]

    assert operation["tags"] == ["telegram"]
    assert operation["operationId"] == "receive_telegram_webhook"
    assert operation["summary"] == "Receber atualização do Telegram"
    assert (
        operation["responses"]["204"]["description"]
        == "Atualização autenticada e processada."
    )


@pytest.mark.anyio
async def test_create_mission_intent_stages_confirmation_without_creating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user()
    create_calls: list[dict[str, object]] = []

    resolve_calls = _patch_user(monkeypatch, fake_user)

    def _fake_create_mission(session: object, **kwargs: object):
        create_calls.append(kwargs)
        raise AssertionError("create_mission_from_criteria should not run yet")

    monkeypatch.setattr(
        "app.telegram.router.create_mission_from_criteria", _fake_create_mission
    )
    send_calls = _patch_send_message(monkeypatch)

    intent = _intent(
        kind=IntentKind.CREATE_MISSION,
        parameters=IntentParameters(
            search_query="notebook gamer",
            target_amount=Decimal("5000.00"),
            target_currency="BRL",
            sources=("pichau", "kabum"),
        ),
    )
    adapter = _FakeAdapter(intent)

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=MagicMock(),
    )

    assert response.status_code == 204
    assert resolve_calls == [(222, "Fulano")]
    assert create_calls == []
    assert fake_user.pending_intent == {
        "kind": "create_mission",
        "search_query": "notebook gamer",
        "target_amount": "5000.00",
        "target_currency": "BRL",
        "sources": ["pichau", "kabum"],
    }
    assert send_calls[0][0] == 222
    assert "notebook gamer" in send_calls[0][1]
    assert "sim" in send_calls[0][1].lower()


@pytest.mark.anyio
async def test_confirmed_pending_create_mission_executes_and_clears_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user(
        pending_intent={
            "kind": "create_mission",
            "search_query": "notebook gamer",
            "target_amount": "5000.00",
            "target_currency": "BRL",
            "sources": ["pichau", "kabum"],
        }
    )
    fake_mission = SimpleNamespace(title="notebook gamer")
    create_calls: list[dict[str, object]] = []

    _patch_user(monkeypatch, fake_user)
    _patch_resolve_answer(monkeypatch, True)

    def _fake_create_mission(session: object, **kwargs: object):
        create_calls.append(kwargs)
        return fake_mission, ("pichau", "kabum")

    monkeypatch.setattr(
        "app.telegram.router.create_mission_from_criteria", _fake_create_mission
    )
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="sim",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=MagicMock(),
    )

    assert response.status_code == 204
    # confirmação usa um classificador de IA próprio, não o IntentInterpreter
    assert adapter.calls == []
    assert create_calls[0]["search_query"] == "notebook gamer"
    assert create_calls[0]["source_codes"] == ("pichau", "kabum")
    assert fake_user.pending_intent is None
    assert "notebook gamer" in send_calls[0][1]


@pytest.mark.anyio
async def test_cancelled_pending_create_mission_does_not_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user(
        pending_intent={
            "kind": "create_mission",
            "search_query": "notebook gamer",
            "target_amount": None,
            "target_currency": None,
            "sources": [],
        }
    )
    _patch_user(monkeypatch, fake_user)
    _patch_resolve_answer(monkeypatch, False)

    def _fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("create_mission_from_criteria should not run")

    monkeypatch.setattr("app.telegram.router.create_mission_from_criteria", _fail)
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="não",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=MagicMock(),
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert fake_user.pending_intent is None
    assert "cancel" in send_calls[0][1].lower()


@pytest.mark.anyio
async def test_unrecognized_answer_to_pending_intent_keeps_it_staged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = {
        "kind": "create_mission",
        "search_query": "notebook gamer",
        "target_amount": None,
        "target_currency": None,
        "sources": [],
    }
    fake_user = _fake_user(pending_intent=pending)
    _patch_user(monkeypatch, fake_user)
    _patch_resolve_answer(monkeypatch, ConfirmationError("Não entendi. Tente de novo."))
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="talvez",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=MagicMock(),
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert fake_user.pending_intent == pending
    assert "não entendi" in send_calls[0][1].lower()


@pytest.mark.anyio
async def test_create_mission_intent_without_search_query_is_a_known_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_user(monkeypatch, _fake_user())
    send_calls = _patch_send_message(monkeypatch)

    intent = _intent(kind=IntentKind.CREATE_MISSION)  # sem parameters.search_query
    adapter = _FakeAdapter(intent)

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=MagicMock(),
    )

    assert response.status_code == 204
    assert send_calls  # respondeu explicando o problema, sem propagar exceção


@pytest.mark.anyio
async def test_query_mission_intent_lists_missions_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_mission = SimpleNamespace(
        title="notebook gamer", status=SimpleNamespace(value="active")
    )

    _patch_user(monkeypatch, _fake_user())
    monkeypatch.setattr(
        "app.telegram.router.list_missions_for_user",
        lambda session, **kwargs: [fake_mission],
    )
    send_calls = _patch_send_message(monkeypatch)

    intent = _intent(kind=IntentKind.QUERY_MISSION)
    adapter = _FakeAdapter(intent)

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=MagicMock(),
    )

    assert response.status_code == 204
    assert "notebook gamer" in send_calls[0][1]


@pytest.mark.anyio
async def test_mission_command_intent_stages_confirmation_without_transitioning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.missions.models import MissionCommand

    fake_user = _fake_user()
    fake_mission = SimpleNamespace(id=uuid4(), title="notebook gamer", state_version=1)

    _patch_user(monkeypatch, fake_user)
    monkeypatch.setattr(
        "app.telegram.router.resolve_mission_for_command",
        lambda session, **kwargs: fake_mission,
    )

    def _fail_transition(*args: object, **kwargs: object) -> object:
        raise AssertionError("transition_mission should not run yet")

    monkeypatch.setattr("app.telegram.router.transition_mission", _fail_transition)
    send_calls = _patch_send_message(monkeypatch)

    intent = _intent(
        kind=IntentKind.MISSION_COMMAND,
        command=MissionCommand.PAUSE,
    )
    adapter = _FakeAdapter(intent)

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=MagicMock(),
    )

    assert response.status_code == 204
    assert fake_user.pending_intent == {
        "kind": "mission_command",
        "mission_id": str(fake_mission.id),
        "mission_title": "notebook gamer",
        "command": "pause",
        "expected_state_version": 1,
    }
    assert "pausar" in send_calls[0][1]
    assert "notebook gamer" in send_calls[0][1]


@pytest.mark.anyio
async def test_confirmed_pending_mission_command_executes_and_clears_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.missions.models import MissionStatus

    mission_id = uuid4()
    fake_user = _fake_user(
        pending_intent={
            "kind": "mission_command",
            "mission_id": str(mission_id),
            "mission_title": "notebook gamer",
            "command": "pause",
            "expected_state_version": 1,
        }
    )
    fake_transition = SimpleNamespace(to_status=MissionStatus.PAUSED)

    _patch_user(monkeypatch, fake_user)
    _patch_resolve_answer(monkeypatch, True)
    monkeypatch.setattr(
        "app.telegram.router.transition_mission",
        lambda session, **kwargs: fake_transition,
    )
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())
    session = MagicMock()
    session.get.return_value = SimpleNamespace(user_id=fake_user.id)

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="confirmo",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=session,
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert fake_user.pending_intent is None
    assert "paused" in send_calls[0][1]


@pytest.mark.anyio
async def test_unknown_role_is_denied_without_functional_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user(role="OWNER")
    _patch_user(monkeypatch, fake_user)
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent(kind=IntentKind.QUERY_MISSION))
    session = MagicMock()

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=session,
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert send_calls == []
    assert fake_user.telegram_chat_id is None
    audit = session.add.call_args.args[0]
    assert audit.action == "authorization.denied"
    assert audit.entry_metadata == {
        "permission": "telegram.interact",
        "reason": "unknown_role",
        "role": "unknown",
    }


@pytest.mark.anyio
async def test_forged_pending_mission_keeps_state_and_is_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mission_id = uuid4()
    pending = {
        "kind": "mission_command",
        "mission_id": str(mission_id),
        "mission_title": "missao de outro usuario",
        "command": "pause",
        "expected_state_version": 1,
    }
    fake_user = _fake_user(pending_intent=pending)
    _patch_user(monkeypatch, fake_user)
    _patch_resolve_answer(monkeypatch, True)
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())
    session = MagicMock()
    session.get.return_value = SimpleNamespace(user_id=uuid4())

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=session,
    )

    assert response.status_code == 204
    assert fake_user.pending_intent == pending
    assert fake_user.telegram_chat_id is None
    assert send_calls == []
    audit = session.add.call_args.args[0]
    assert audit.resource_id == mission_id
    assert audit.entry_metadata["reason"] == "resource_unavailable"


@pytest.mark.anyio
async def test_unknown_intent_replies_asking_to_rephrase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_user(monkeypatch, _fake_user())
    send_calls = _patch_send_message(monkeypatch)

    intent = _intent(kind=IntentKind.UNKNOWN)
    adapter = _FakeAdapter(intent)

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=MagicMock(),
    )

    assert response.status_code == 204
    assert "Não entendi" in send_calls[0][1]


@pytest.mark.anyio
async def test_mission_reference_error_at_staging_is_replied_without_pending_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.missions.models import MissionCommand

    fake_user = _fake_user()
    error = MissionReferenceError("Não encontrei nenhuma missão correspondente.")

    _patch_user(monkeypatch, fake_user)

    def _raise(*args: object, **kwargs: object) -> object:
        raise error

    monkeypatch.setattr("app.telegram.router.resolve_mission_for_command", _raise)
    send_calls = _patch_send_message(monkeypatch)

    intent = _intent(kind=IntentKind.MISSION_COMMAND, command=MissionCommand.PAUSE)
    adapter = _FakeAdapter(intent)

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=MagicMock(),
    )

    assert response.status_code == 204
    assert fake_user.pending_intent is None
    assert send_calls[0][1] == str(error)


@pytest.mark.anyio
async def test_known_error_at_confirmed_execution_is_replied_and_clears_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = MissionTransitionConditionError("A missão precisa de fontes.")
    fake_user = _fake_user(
        pending_intent={
            "kind": "create_mission",
            "search_query": "notebook gamer",
            "target_amount": None,
            "target_currency": None,
            "sources": [],
        }
    )

    _patch_user(monkeypatch, fake_user)
    _patch_resolve_answer(monkeypatch, True)

    def _raise(*args: object, **kwargs: object) -> object:
        raise error

    monkeypatch.setattr("app.telegram.router.create_mission_from_criteria", _raise)
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="sim",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=MagicMock(),
    )

    assert response.status_code == 204
    assert fake_user.pending_intent is None
    assert send_calls[0][1] == str(error)


@pytest.mark.anyio
async def test_unexpected_error_at_confirmed_execution_is_not_masked_and_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user(
        pending_intent={
            "kind": "create_mission",
            "search_query": "notebook gamer",
            "target_amount": None,
            "target_currency": None,
            "sources": [],
        }
    )
    _patch_user(monkeypatch, fake_user)
    _patch_resolve_answer(monkeypatch, True)

    def _raise(*args: object, **kwargs: object) -> object:
        raise MissionCreationError("stores not seeded for codes: pichau")

    monkeypatch.setattr("app.telegram.router.create_mission_from_criteria", _raise)
    adapter = _FakeAdapter(_intent())

    with pytest.raises(MissionCreationError):
        await receive_telegram_webhook(
            update=_update(
                message=_TelegramIncomingMessage(
                    text="sim",
                    date=1754586000,
                    chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                    from_=_TelegramSender(id=222, first_name="Fulano"),
                )
            ),
            x_telegram_bot_api_secret_token="correct-secret",
            adapters=_adapters(adapter),  # type: ignore[arg-type]
            settings=_settings(),
            session=MagicMock(),
        )


@pytest.mark.anyio
async def test_cadastro_command_starts_registration_without_calling_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user()
    _patch_user(monkeypatch, fake_user)
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/cadastro",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=MagicMock(),
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert fake_user.registration_step == "username"
    assert "usuário" in send_calls[0][1].lower()


@pytest.mark.anyio
async def test_registration_in_progress_consumes_reply_without_calling_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user(registration_step="username")
    _patch_user(monkeypatch, fake_user)
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="joaosilva",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=MagicMock(),
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert fake_user.username == "joaosilva"
    assert fake_user.registration_step == "email"
    assert send_calls  # perguntou o próximo passo


@pytest.mark.anyio
async def test_registration_full_flow_completes_and_clears_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user()
    _patch_user(monkeypatch, fake_user)
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())
    adapters = _adapters(adapter)

    answers = ["joaosilva", "pular", "pichau, kabum", "games, moveis"]
    text = "/cadastro"
    for answer in [None, *answers]:
        if answer is not None:
            text = answer
        await receive_telegram_webhook(
            update=_update(
                message=_TelegramIncomingMessage(
                    text=text,
                    date=1754586000,
                    chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                    from_=_TelegramSender(id=222, first_name="Fulano"),
                )
            ),
            x_telegram_bot_api_secret_token="correct-secret",
            adapters=adapters,  # type: ignore[arg-type]
            settings=_settings(),
            session=MagicMock(),
        )

    assert fake_user.username == "joaosilva"
    assert fake_user.email is None
    assert fake_user.favorite_stores == ["kabum", "pichau"]
    assert fake_user.preferred_categories == ["games", "moveis"]
    assert fake_user.registration_step is None
    assert adapter.calls == []
    assert "concluído" in send_calls[-1][1].lower()


@pytest.mark.anyio
async def test_upgrade_command_replies_statically_without_calling_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_user(monkeypatch, _fake_user())
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/upgrade",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=MagicMock(),
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert "em breve" in send_calls[0][1].lower()


@pytest.mark.anyio
async def test_preferences_command_queries_without_calling_ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user()
    _patch_user(monkeypatch, fake_user)
    send_calls = _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/preferencias",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=MagicMock(),
    )

    assert response.status_code == 204
    assert adapter.calls == []
    assert "quedas de preço: ativadas" in send_calls[0][1].lower()
    assert "preço-alvo atingido: ativadas" in send_calls[0][1].lower()


@pytest.mark.anyio
async def test_preferences_command_disables_only_requested_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user()
    _patch_user(monkeypatch, fake_user)
    _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent())

    await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/preferencias quedas desativar",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=MagicMock(),
    )

    assert adapter.calls == []
    assert fake_user.notify_price_decreases is False
    assert fake_user.notify_target_reached is True


@pytest.mark.anyio
async def test_private_webhook_message_remembers_notification_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user()
    _patch_user(monkeypatch, fake_user)
    _patch_send_message(monkeypatch)
    adapter = _FakeAdapter(_intent(kind=IntentKind.UNKNOWN))

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="oi",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=MagicMock(),
    )

    assert response.status_code == 204
    assert fake_user.telegram_chat_id == 222


@pytest.mark.anyio
async def test_start_remains_available_without_password_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user()
    _patch_user(monkeypatch, fake_user)
    sends = _patch_send_message(monkeypatch)
    monkeypatch.setattr("app.telegram.router.has_active_session", lambda *a, **k: False)
    adapter = _FakeAdapter(_intent(kind=IntentKind.UNKNOWN))

    response = await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/start",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=MagicMock(),
    )

    assert response.status_code == 204
    assert "/entrar" in sends[0][1]
    assert adapter.calls == []


@pytest.mark.anyio
async def test_sensitive_message_is_blocked_without_password_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user()
    _patch_user(monkeypatch, fake_user)
    sends = _patch_send_message(monkeypatch)
    monkeypatch.setattr("app.telegram.router.has_active_session", lambda *a, **k: False)
    adapter = _FakeAdapter(_intent())

    await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=MagicMock(),
    )

    assert "sessão por senha" in sends[0][1]
    assert adapter.calls == []


@pytest.mark.anyio
async def test_login_command_issues_server_bound_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user()
    fake_user.username = "cliente"
    _patch_user(monkeypatch, fake_user)
    sends = _patch_send_message(monkeypatch)
    calls: list[dict] = []

    def issue(*args: object, **kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(
            url="https://auth.example.test/auth#login:opaque",
            expires_at=datetime.now(UTC),
        )

    monkeypatch.setattr("app.telegram.router.issue_action_link", issue)
    adapter = _FakeAdapter(_intent(kind=IntentKind.UNKNOWN))

    await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/entrar",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(auth_public_base_url="https://auth.example.test"),
        session=MagicMock(),
    )

    assert calls[0]["user"] is fake_user
    assert calls[0]["action"].value == "login"
    assert "#login:opaque" in sends[0][1]
    assert adapter.calls == []


@pytest.mark.anyio
async def test_logout_revokes_active_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user()
    _patch_user(monkeypatch, fake_user)
    sends = _patch_send_message(monkeypatch)
    monkeypatch.setattr("app.telegram.router.has_active_session", lambda *a, **k: True)
    logout_calls: list[object] = []
    monkeypatch.setattr(
        "app.telegram.router.logout",
        lambda session, *, user: logout_calls.append(user),
    )
    adapter = _FakeAdapter(_intent(kind=IntentKind.UNKNOWN))

    await receive_telegram_webhook(
        update=_update(
            message=_TelegramIncomingMessage(
                text="/sair",
                date=1754586000,
                chat=_TelegramChat(id=222, type=TelegramChatType.PRIVATE),
                from_=_TelegramSender(id=222, first_name="Fulano"),
            )
        ),
        x_telegram_bot_api_secret_token="correct-secret",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(),
        session=MagicMock(),
    )

    assert logout_calls == [fake_user]
    assert "Sessão encerrada" in sends[0][1]
