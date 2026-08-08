import json
from datetime import UTC, datetime

import pytest
from app.ai_provider import AIRequest, AIResponse
from app.intent import IntentInterpreter, IntentKind
from app.missions.models import MissionCommand
from app.telegram import TelegramIntentAdapter, TelegramMessage
from app.users.models import UserRole


class _FakeManager:
    """Substitui o AIProviderManager sem tocar em nenhum provedor real."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.captured_request: AIRequest | None = None

    async def generate(self, request: AIRequest) -> AIResponse:
        self.captured_request = request
        return AIResponse(
            request_id=request.request_id,
            provider="fake_provider",
            model="fake-model",
            content=self.content,
            finished_at=datetime.now(UTC),
        )


def _response(**overrides: object) -> str:
    payload: dict[str, object] = {
        "kind": "create_mission",
        "command": None,
        "parameters": {
            "search_query": "notebook gamer",
            "target_amount": "5000.00",
            "target_currency": "BRL",
            "sources": ["pichau", "kabum"],
            "mission_reference": None,
        },
    }
    payload.update(overrides)
    return json.dumps(payload)


def _message(**overrides: object) -> TelegramMessage:
    defaults: dict[str, object] = {
        "chat_id": 123,
        "user_id": 456,
        "text": "Quero um notebook gamer até R$ 5000 na Pichau ou Kabum",
        "received_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return TelegramMessage(**defaults)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_interpret_forwards_text_and_received_at_to_the_interpreter() -> None:
    manager = _FakeManager(_response())
    adapter = TelegramIntentAdapter(IntentInterpreter(manager))
    message = _message()

    await adapter.interpret(message)

    request = manager.captured_request
    assert request is not None
    assert request.messages[-1].content == message.text
    assert request.requested_at == message.received_at


@pytest.mark.anyio
async def test_interpret_returns_the_intent_unchanged_for_create_mission() -> None:
    manager = _FakeManager(_response())
    adapter = TelegramIntentAdapter(IntentInterpreter(manager))

    intent = await adapter.interpret(_message())

    assert intent.kind is IntentKind.CREATE_MISSION
    assert intent.raw_message == _message().text
    assert intent.parameters.search_query == "notebook gamer"


@pytest.mark.anyio
async def test_interpret_returns_the_intent_unchanged_for_query_mission() -> None:
    manager = _FakeManager(
        _response(
            kind="query_mission",
            parameters={
                "search_query": None,
                "target_amount": None,
                "target_currency": None,
                "sources": [],
                "mission_reference": "meu notebook",
            },
        )
    )
    adapter = TelegramIntentAdapter(IntentInterpreter(manager))

    intent = await adapter.interpret(_message(text="Como está minha missão?"))

    assert intent.kind is IntentKind.QUERY_MISSION
    assert intent.parameters.mission_reference == "meu notebook"


@pytest.mark.anyio
async def test_interpret_returns_the_intent_unchanged_for_mission_command() -> None:
    manager = _FakeManager(
        _response(
            kind="mission_command",
            command=MissionCommand.PAUSE.value,
            parameters={
                "search_query": None,
                "target_amount": None,
                "target_currency": None,
                "sources": [],
                "mission_reference": "notebook",
            },
        )
    )
    adapter = TelegramIntentAdapter(IntentInterpreter(manager))

    intent = await adapter.interpret(_message(text="pausa minha missão do notebook"))

    assert intent.kind is IntentKind.MISSION_COMMAND
    assert intent.command is MissionCommand.PAUSE


@pytest.mark.anyio
async def test_interpret_forwards_explicit_profile_to_the_interpreter() -> None:
    manager = _FakeManager(_response())
    adapter = TelegramIntentAdapter(IntentInterpreter(manager))

    await adapter.interpret(_message(), profile=UserRole.ADMIN)

    request = manager.captured_request
    assert request is not None
    assert request.profile is UserRole.ADMIN


@pytest.mark.anyio
async def test_interpret_returns_unknown_on_unrelated_message() -> None:
    manager = _FakeManager(json.dumps({"kind": "unknown", "command": None}))
    adapter = TelegramIntentAdapter(IntentInterpreter(manager))

    intent = await adapter.interpret(_message(text="Qual é a previsão do tempo?"))

    assert intent.kind is IntentKind.UNKNOWN
    assert intent.command is None
