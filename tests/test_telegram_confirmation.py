"""Testes do fluxo de confirmação da intenção interpretada (TASK-058)."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from app.ai_provider import AIProviderUnavailable, AIRequest, AIResponse
from app.missions.models import MissionCommand
from app.telegram.confirmation import (
    ConfirmationError,
    describe_create_mission,
    describe_edit_mission,
    describe_mission_command,
    describe_pause_for_edit,
    resolve_answer,
    stage_create_mission,
    stage_edit_mission,
    stage_mission_command,
    stage_pause_for_edit,
)
from app.users.models import UserRole


class _FakeManager:
    """Substitui o AIProviderManager sem tocar em nenhum provedor real."""

    def __init__(
        self, content: str | None = None, error: Exception | None = None
    ) -> None:
        self.content = content
        self.error = error
        self.captured_request: AIRequest | None = None

    async def generate(self, request: AIRequest) -> AIResponse:
        self.captured_request = request
        if self.error is not None:
            raise self.error
        return AIResponse(
            request_id=request.request_id,
            provider="fake_provider",
            model="fake-model",
            content=self.content,
            finished_at=datetime.now(UTC),
        )


def _response(answer: str) -> str:
    return json.dumps({"answer": answer})


@pytest.mark.anyio
async def test_resolve_answer_builds_request_restricted_to_profile_and_purpose() -> (
    None
):
    manager = _FakeManager(_response("confirm"))

    await resolve_answer("sim", manager=manager, profile=UserRole.ADMIN)

    request = manager.captured_request
    assert request is not None
    assert request.profile is UserRole.ADMIN
    assert request.purpose == "interpret_confirmation_reply"
    assert request.messages[-1].content == "sim"


@pytest.mark.anyio
async def test_resolve_answer_returns_true_on_confirm_classification() -> None:
    manager = _FakeManager(_response("confirm"))

    assert (
        await resolve_answer("pod ser, bora", manager=manager, profile=UserRole.USER)
        is True
    )


@pytest.mark.anyio
async def test_resolve_answer_returns_false_on_cancel_classification() -> None:
    manager = _FakeManager(_response("cancel"))

    assert (
        await resolve_answer("deixa pra la", manager=manager, profile=UserRole.USER)
        is False
    )


@pytest.mark.anyio
async def test_resolve_answer_raises_on_unclear_classification() -> None:
    manager = _FakeManager(_response("unclear"))

    with pytest.raises(ConfirmationError):
        await resolve_answer("oi", manager=manager, profile=UserRole.USER)


@pytest.mark.anyio
async def test_resolve_answer_raises_on_malformed_json() -> None:
    manager = _FakeManager("isto não é json")

    with pytest.raises(ConfirmationError):
        await resolve_answer("sim", manager=manager, profile=UserRole.USER)


@pytest.mark.anyio
async def test_resolve_answer_raises_on_unexpected_response_shape() -> None:
    manager = _FakeManager(json.dumps({"answer": "confirm", "extra": True}))

    with pytest.raises(ConfirmationError):
        await resolve_answer("sim", manager=manager, profile=UserRole.USER)


@pytest.mark.anyio
async def test_resolve_answer_raises_on_invalid_answer_value() -> None:
    manager = _FakeManager(json.dumps({"answer": "maybe"}))

    with pytest.raises(ConfirmationError):
        await resolve_answer("sim", manager=manager, profile=UserRole.USER)


@pytest.mark.anyio
async def test_resolve_answer_raises_confirmation_error_on_provider_failure() -> None:
    manager = _FakeManager(error=AIProviderUnavailable())

    with pytest.raises(ConfirmationError):
        await resolve_answer("sim", manager=manager, profile=UserRole.USER)


def test_stage_and_describe_create_mission_with_target_and_sources() -> None:
    payload = stage_create_mission(
        search_query="notebook gamer",
        target_amount="5000.00",
        target_currency="BRL",
        sources=("pichau", "kabum"),
    )

    assert payload == {
        "kind": "create_mission",
        "search_query": "notebook gamer",
        "target_amount": "5000.00",
        "target_currency": "BRL",
        "sources": ["pichau", "kabum"],
    }
    description = describe_create_mission(payload)
    assert "notebook gamer" in description
    assert "R$ 5.000,00" in description
    assert "Pichau, Kabum" in description
    assert "sim" in description.lower()
    assert "não" in description.lower()


def test_stage_and_describe_create_mission_without_target_or_sources() -> None:
    payload = stage_create_mission(
        search_query="ssd nvme",
        target_amount=None,
        target_currency=None,
        sources=(),
    )

    description = describe_create_mission(payload)
    assert "ssd nvme" in description
    assert "todas as lojas disponíveis" in description
    assert "V1" not in description


def test_stage_and_describe_mission_command() -> None:
    mission_id = uuid4()

    payload = stage_mission_command(
        mission_id=mission_id,
        mission_title="notebook gamer",
        command=MissionCommand.PAUSE,
        expected_state_version=3,
    )

    assert payload == {
        "kind": "mission_command",
        "mission_id": str(mission_id),
        "mission_title": "notebook gamer",
        "command": "pause",
        "expected_state_version": 3,
    }
    description = describe_mission_command(payload)
    assert "pausar" in description
    assert "notebook gamer" in description


@pytest.mark.parametrize(
    ("command", "verb"),
    [
        (MissionCommand.ACTIVATE, "ativar"),
        (MissionCommand.PAUSE, "pausar"),
        (MissionCommand.RESUME, "retomar"),
        (MissionCommand.COMPLETE, "concluir"),
        (MissionCommand.CANCEL, "cancelar"),
        (MissionCommand.EXPIRE, "expirar"),
    ],
)
def test_describe_mission_command_covers_every_command(
    command: MissionCommand, verb: str
) -> None:
    payload = stage_mission_command(
        mission_id=uuid4(),
        mission_title="missão x",
        command=command,
        expected_state_version=1,
    )

    assert verb in describe_mission_command(payload)


def test_stage_and_describe_edit_mission_target_change_only() -> None:
    mission_id = uuid4()

    payload = stage_edit_mission(
        mission_id=mission_id,
        mission_title="teclado mecanico",
        expected_state_version=2,
        previous_target_amount=Decimal("500.00"),
        previous_target_currency="BRL",
        previous_sources=("kabum",),
        target_amount=Decimal("300.00"),
        target_currency="BRL",
        clear_target=False,
        sources=(),
    )

    assert payload == {
        "kind": "edit_mission",
        "mission_id": str(mission_id),
        "mission_title": "teclado mecanico",
        "expected_state_version": 2,
        "changes_target": True,
        "target_amount": "300.00",
        "target_currency": "BRL",
        "changes_sources": False,
        "sources": [],
        "previous_target_amount": "500.00",
        "previous_target_currency": "BRL",
        "previous_sources": ["kabum"],
    }
    description = describe_edit_mission(payload)
    assert "teclado mecanico" in description
    assert "R$ 500,00" in description
    assert "R$ 300,00" in description
    assert "continua pausada" in description
    assert "sim" in description.lower()


def test_stage_and_describe_edit_mission_clears_target() -> None:
    payload = stage_edit_mission(
        mission_id=uuid4(),
        mission_title="monitor curvo",
        expected_state_version=0,
        previous_target_amount=Decimal("1000.00"),
        previous_target_currency="BRL",
        previous_sources=("pichau",),
        target_amount=None,
        target_currency=None,
        clear_target=True,
        sources=(),
    )

    assert payload["changes_target"] is True
    assert payload["target_amount"] is None
    description = describe_edit_mission(payload)
    assert "R$ 1.000,00" in description
    assert "sem alvo" in description


def test_stage_and_describe_edit_mission_sources_change_only() -> None:
    payload = stage_edit_mission(
        mission_id=uuid4(),
        mission_title="notebook gamer",
        expected_state_version=1,
        previous_target_amount=None,
        previous_target_currency=None,
        previous_sources=(),
        target_amount=None,
        target_currency=None,
        clear_target=False,
        sources=("kabum", "pichau"),
    )

    assert payload["changes_target"] is False
    assert payload["changes_sources"] is True
    description = describe_edit_mission(payload)
    assert "🎯" not in description
    assert "nenhuma" in description
    assert "Kabum, Pichau" in description


def test_stage_and_describe_pause_for_edit() -> None:
    mission_id = uuid4()

    payload = stage_pause_for_edit(
        mission_id=mission_id,
        mission_title="ssd nvme",
        expected_state_version=4,
    )

    assert payload == {
        "kind": "pause_for_edit",
        "mission_id": str(mission_id),
        "mission_title": "ssd nvme",
        "expected_state_version": 4,
    }
    description = describe_pause_for_edit(payload)
    assert "ssd nvme" in description
    assert "ativa" in description
    assert "sim" in description.lower()
