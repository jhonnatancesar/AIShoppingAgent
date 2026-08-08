import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from app.ai_provider import AIMessageRole, AIRequest, AIResponse
from app.intent import (
    Intent,
    IntentError,
    IntentInterpreter,
    IntentKind,
    parse_intent_response,
)
from app.intent.interpreter import _SYSTEM_PROMPT, PURPOSE
from app.missions.models import MissionCommand
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


@pytest.mark.anyio
async def test_interpret_builds_request_restricted_to_user_profile_and_purpose() -> (
    None
):
    manager = _FakeManager(_response())
    interpreter = IntentInterpreter(manager)

    await interpreter.interpret(
        "Quero um notebook gamer até R$ 5000 na Pichau ou Kabum"
    )

    request = manager.captured_request
    assert request is not None
    assert request.profile is UserRole.USER
    assert request.purpose == PURPOSE
    assert request.messages[0].role is AIMessageRole.SYSTEM
    assert request.messages[-1].role is AIMessageRole.USER
    assert request.messages[-1].content == (
        "Quero um notebook gamer até R$ 5000 na Pichau ou Kabum"
    )


@pytest.mark.anyio
@pytest.mark.parametrize("profile", [UserRole.ADMIN, UserRole.DEV])
async def test_interpret_accepts_explicit_profile_for_manual_validation_tooling(
    profile: UserRole,
) -> None:
    manager = _FakeManager(_response())
    interpreter = IntentInterpreter(manager)

    await interpreter.interpret("Quero um notebook gamer", profile=profile)

    request = manager.captured_request
    assert request is not None
    assert request.profile is profile


@pytest.mark.anyio
async def test_interpret_parses_create_mission_with_parameters() -> None:
    manager = _FakeManager(_response())
    interpreter = IntentInterpreter(manager)

    intent = await interpreter.interpret("Quero um notebook gamer até R$ 5000")

    assert intent.kind is IntentKind.CREATE_MISSION
    assert intent.command is None
    assert intent.parameters.search_query == "notebook gamer"
    assert intent.parameters.target_amount == Decimal("5000.00")
    assert intent.parameters.target_currency == "BRL"
    assert intent.parameters.sources == ("pichau", "kabum")
    assert intent.correlation_id == manager.captured_request.request_id  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_interpret_parses_query_mission() -> None:
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
    interpreter = IntentInterpreter(manager)

    intent = await interpreter.interpret("Como está minha missão do notebook?")

    assert intent.kind is IntentKind.QUERY_MISSION
    assert intent.parameters.mission_reference == "meu notebook"


@pytest.mark.anyio
@pytest.mark.parametrize("command", list(MissionCommand))
async def test_interpret_parses_every_existing_mission_command(
    command: MissionCommand,
) -> None:
    manager = _FakeManager(
        _response(
            kind="mission_command",
            command=command.value,
            parameters={
                "search_query": None,
                "target_amount": None,
                "target_currency": None,
                "sources": [],
                "mission_reference": "meu notebook",
            },
        )
    )
    interpreter = IntentInterpreter(manager)

    intent = await interpreter.interpret(f"Quero {command.value} a missão do notebook")

    assert intent.kind is IntentKind.MISSION_COMMAND
    assert intent.command is command


def test_system_prompt_instructs_robustness_to_informal_writing() -> None:
    assert "gírias" in _SYSTEM_PROMPT
    assert "ordem das" in _SYSTEM_PROMPT
    assert "erros de digitação" in _SYSTEM_PROMPT


@pytest.mark.anyio
async def test_interpret_rejects_blank_message_before_calling_provider() -> None:
    manager = _FakeManager(_response())
    interpreter = IntentInterpreter(manager)

    with pytest.raises(IntentError, match="message must not be blank"):
        await interpreter.interpret("   ")

    assert manager.captured_request is None


def _parsed(content: str) -> Intent:
    return parse_intent_response(
        content,
        correlation_id=uuid4(),
        raw_message="mensagem original",
        interpreted_at=datetime.now(UTC),
    )


def test_parse_falls_back_to_unknown_on_invalid_json() -> None:
    intent = _parsed("isto não é json")

    assert intent.kind is IntentKind.UNKNOWN
    assert intent.command is None
    assert intent.raw_message == "mensagem original"


def test_parse_falls_back_to_unknown_on_unexpected_top_level_keys() -> None:
    intent = _parsed(json.dumps({"kind": "unknown", "extra": True}))

    assert intent.kind is IntentKind.UNKNOWN


def test_parse_falls_back_to_unknown_on_invalid_kind_value() -> None:
    intent = _parsed(json.dumps({"kind": "delete_everything", "command": None}))

    assert intent.kind is IntentKind.UNKNOWN


def test_parse_falls_back_to_unknown_on_invalid_command_value() -> None:
    intent = _parsed(json.dumps({"kind": "mission_command", "command": "reopen"}))

    assert intent.kind is IntentKind.UNKNOWN


def test_parse_falls_back_to_unknown_when_kind_and_command_are_inconsistent() -> None:
    mismatched_kind = _parsed(
        json.dumps({"kind": "create_mission", "command": "activate"})
    )
    missing_command = _parsed(json.dumps({"kind": "mission_command", "command": None}))

    assert mismatched_kind.kind is IntentKind.UNKNOWN
    assert missing_command.kind is IntentKind.UNKNOWN


def test_parse_falls_back_to_unknown_on_source_outside_v1_selectable_set() -> None:
    intent = _parsed(
        json.dumps(
            {
                "kind": "create_mission",
                "command": None,
                "parameters": {"sources": ["shopee"]},
            }
        )
    )

    assert intent.kind is IntentKind.UNKNOWN


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-Infinity"])
def test_parse_falls_back_to_unknown_on_non_finite_decimal_amount(
    amount: str,
) -> None:
    intent = _parsed(
        json.dumps(
            {
                "kind": "create_mission",
                "command": None,
                "parameters": {
                    "target_amount": amount,
                    "target_currency": "BRL",
                },
            }
        )
    )

    assert intent.kind is IntentKind.UNKNOWN


def test_parse_falls_back_to_unknown_on_malformed_decimal_amount() -> None:
    intent = _parsed(
        json.dumps(
            {
                "kind": "create_mission",
                "command": None,
                "parameters": {
                    "target_amount": "não é número",
                    "target_currency": "BRL",
                },
            }
        )
    )

    assert intent.kind is IntentKind.UNKNOWN


def test_parse_accepts_explicit_unknown_response_from_the_model() -> None:
    intent = _parsed(json.dumps({"kind": "unknown", "command": None}))

    assert intent.kind is IntentKind.UNKNOWN
    assert intent.parameters.search_query is None
