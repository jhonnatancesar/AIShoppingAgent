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
from app.telegram.contracts import TelegramMessage
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

    async def interpret(
        self, message: TelegramMessage, *, profile: UserRole = UserRole.USER
    ) -> Intent:
        self.calls.append((message, profile))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _fake_user(
    *, role: UserRole = UserRole.USER, registration_step: str | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        role=role,
        registration_step=registration_step,
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
            chat=_TelegramChat(id=111),
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
        "app.telegram.router.get_or_create_telegram_user", _fake_get_or_create
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
    assert message.chat_id == 111
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
    token: str | None,
) -> None:
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
    assert adapter.calls == []


@pytest.mark.anyio
async def test_unconfigured_secret_rejects_every_request() -> None:
    adapter = _FakeAdapter(_intent())

    response = await receive_telegram_webhook(
        update=_update(),
        x_telegram_bot_api_secret_token="anything",
        adapters=_adapters(adapter),  # type: ignore[arg-type]
        settings=_settings(telegram_webhook_secret=None),
        session=MagicMock(),
    )

    assert response.status_code == 401
    assert adapter.calls == []


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
            chat=_TelegramChat(id=111),
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
async def test_create_mission_intent_resolves_user_creates_and_replies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_user = _fake_user()
    fake_mission = SimpleNamespace(title="notebook gamer")
    create_calls: list[dict[str, object]] = []

    resolve_calls = _patch_user(monkeypatch, fake_user)

    def _fake_create_mission(session: object, **kwargs: object):
        create_calls.append(kwargs)
        return fake_mission, ("pichau", "kabum")

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
    assert create_calls[0]["search_query"] == "notebook gamer"
    assert create_calls[0]["source_codes"] == ("pichau", "kabum")
    assert send_calls[0][0] == 111
    assert "notebook gamer" in send_calls[0][1]


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
async def test_mission_command_intent_transitions_and_replies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.missions.models import MissionCommand, MissionStatus

    fake_mission = SimpleNamespace(id=uuid4(), title="notebook gamer", state_version=1)
    fake_transition = SimpleNamespace(to_status=MissionStatus.PAUSED)

    _patch_user(monkeypatch, _fake_user())
    monkeypatch.setattr(
        "app.telegram.router.resolve_mission_for_command",
        lambda session, **kwargs: fake_mission,
    )
    monkeypatch.setattr(
        "app.telegram.router.transition_mission",
        lambda session, **kwargs: fake_transition,
    )
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
    assert "paused" in send_calls[0][1]


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
@pytest.mark.parametrize(
    "error",
    [
        MissionTransitionConditionError("A missão precisa de fontes."),
        MissionReferenceError("Não encontrei nenhuma missão correspondente."),
    ],
)
async def test_known_mission_error_is_replied_and_returns_204(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    _patch_user(monkeypatch, _fake_user())

    def _raise(*args: object, **kwargs: object) -> object:
        raise error

    monkeypatch.setattr("app.telegram.router.create_mission_from_criteria", _raise)
    send_calls = _patch_send_message(monkeypatch)

    intent = _intent(
        kind=IntentKind.CREATE_MISSION,
        parameters=IntentParameters(search_query="notebook gamer"),
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
    assert send_calls[0][1] == str(error)


@pytest.mark.anyio
async def test_unexpected_error_is_not_masked_and_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_user(monkeypatch, _fake_user())

    def _raise(*args: object, **kwargs: object) -> object:
        raise MissionCreationError("stores not seeded for codes: pichau")

    monkeypatch.setattr("app.telegram.router.create_mission_from_criteria", _raise)

    intent = _intent(
        kind=IntentKind.CREATE_MISSION,
        parameters=IntentParameters(search_query="notebook gamer"),
    )
    adapter = _FakeAdapter(intent)

    with pytest.raises(MissionCreationError):
        await receive_telegram_webhook(
            update=_update(),
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
                chat=_TelegramChat(id=111),
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
                chat=_TelegramChat(id=111),
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
                    chat=_TelegramChat(id=111),
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
                chat=_TelegramChat(id=111),
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
