"""TASK-128 etapa 3 -- a descrição de uma missão nova passa pela IA antes
de virar missão (Telegram e site): entendida, vaga ("não entendi,
descreva melhor") ou IA sem resposta depois da cascata ("tente abrir a
missão mais tarde") -- os dois últimos nunca se confundem."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.ai_provider import (
    AIProviderError,
    AIProviderQuotaExceeded,
    AIProviderUnavailable,
)
from app.intent import (
    Intent,
    IntentError,
    IntentKind,
    IntentParameters,
    MissionDescriptionOutcome,
    check_mission_description,
    mission_description_outcome,
)
from app.intent.interpreter import _SYSTEM_PROMPT
from app.users.models import UserRole


def _intent(kind: IntentKind, search_query: str | None = None) -> Intent:
    return Intent(
        correlation_id=uuid4(),
        kind=kind,
        raw_message="descrição",
        interpreted_at=datetime.now(UTC),
        parameters=IntentParameters(search_query=search_query),
    )


class _Interpreter:
    def __init__(self, outcome) -> None:
        self._outcome = outcome
        self.calls: list[tuple[str, UserRole]] = []

    async def interpret(self, message, *, profile):
        self.calls.append((message, profile))
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome


def _check(outcome, description="cadeira gamer", profile=UserRole.USER):
    interpreter = _Interpreter(outcome)
    result = asyncio.run(
        check_mission_description(interpreter, description, profile=profile)
    )
    return result, interpreter


def test_request_with_something_to_search_is_understood() -> None:
    intent = _intent(IntentKind.CREATE_MISSION, "cadeira gamer")

    result, interpreter = _check(intent, profile=UserRole.DEV)

    assert result.outcome is MissionDescriptionOutcome.UNDERSTOOD
    assert result.intent is intent
    assert interpreter.calls == [("cadeira gamer", UserRole.DEV)], (
        "a descrição vai crua -- a detecção de código de modelo isolado "
        "depende do texto original"
    )


@pytest.mark.parametrize(
    "intent",
    [
        _intent(IntentKind.UNKNOWN),
        _intent(IntentKind.CREATE_MISSION),
        _intent(IntentKind.QUERY_MISSION, "notebook"),
    ],
)
def test_vague_or_other_intents_are_unclear(intent) -> None:
    assert mission_description_outcome(intent) is MissionDescriptionOutcome.UNCLEAR
    result, _ = _check(intent)
    assert result.outcome is MissionDescriptionOutcome.UNCLEAR
    assert result.intent is None


@pytest.mark.parametrize(
    "error",
    [
        AIProviderQuotaExceeded(),
        AIProviderUnavailable(),
        AIProviderError("cesar_core_request_failed", retryable=False),
    ],
)
def test_ai_without_answer_after_the_cascade_is_never_called_vague(error) -> None:
    result, _ = _check(error)
    assert result.outcome is MissionDescriptionOutcome.AI_UNAVAILABLE


def test_blank_description_is_unclear() -> None:
    result, _ = _check(IntentError("message must not be blank"), description=" ")
    assert result.outcome is MissionDescriptionOutcome.UNCLEAR


def test_interpreter_prompt_defines_what_a_vague_request_is() -> None:
    assert 'use "kind": "unknown" -- o GG Oferta vai pedir' in _SYSTEM_PROMPT
    assert "quero comprar alguma coisa boa e barata" in _SYSTEM_PROMPT
    assert 'já é um pedido de "create_mission"' in _SYSTEM_PROMPT
