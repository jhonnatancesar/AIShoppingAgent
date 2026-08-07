from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from app.intent import Intent, IntentError, IntentKind, IntentParameters
from app.missions.models import MissionCommand


def _intent(**overrides: object) -> Intent:
    defaults: dict[str, object] = {
        "correlation_id": uuid4(),
        "kind": IntentKind.CREATE_MISSION,
        "raw_message": "Quero um notebook até R$ 5000",
        "interpreted_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return Intent(**defaults)  # type: ignore[arg-type]


def test_intent_accepts_create_and_query_without_command() -> None:
    create = _intent(kind=IntentKind.CREATE_MISSION)
    query = _intent(kind=IntentKind.QUERY_MISSION)
    unknown = _intent(kind=IntentKind.UNKNOWN)

    assert create.command is None
    assert query.command is None
    assert unknown.command is None


@pytest.mark.parametrize("command", list(MissionCommand))
def test_intent_accepts_every_existing_mission_command(command: MissionCommand) -> None:
    intent = _intent(kind=IntentKind.MISSION_COMMAND, command=command)

    assert intent.command is command


def test_intent_rejects_mission_command_without_command() -> None:
    with pytest.raises(IntentError, match="requires a MissionCommand"):
        _intent(kind=IntentKind.MISSION_COMMAND, command=None)


@pytest.mark.parametrize(
    "kind", [IntentKind.CREATE_MISSION, IntentKind.QUERY_MISSION, IntentKind.UNKNOWN]
)
def test_intent_rejects_command_outside_mission_command_kind(kind: IntentKind) -> None:
    with pytest.raises(IntentError, match="only valid for the mission_command"):
        _intent(kind=kind, command=MissionCommand.ACTIVATE)


def test_intent_rejects_blank_raw_message_and_naive_time() -> None:
    with pytest.raises(IntentError, match="raw_message"):
        _intent(raw_message="   ")
    with pytest.raises(IntentError, match="timezone"):
        _intent(interpreted_at=datetime.now())


def test_intent_rejects_non_uuid_correlation_and_wrong_kind_type() -> None:
    with pytest.raises(IntentError, match="correlation_id"):
        _intent(correlation_id="not-a-uuid")
    with pytest.raises(IntentError, match="kind must use IntentKind"):
        _intent(kind="create_mission")


def test_intent_parameters_default_to_empty() -> None:
    intent = _intent()

    assert intent.parameters == IntentParameters()


def test_parameters_require_paired_target_amount_and_currency() -> None:
    with pytest.raises(IntentError, match="paired"):
        IntentParameters(target_amount=Decimal("100"))
    with pytest.raises(IntentError, match="paired"):
        IntentParameters(target_currency="BRL")


def test_parameters_reject_negative_amount_and_invalid_currency() -> None:
    with pytest.raises(IntentError, match="non-negative"):
        IntentParameters(target_amount=Decimal("-1"), target_currency="BRL")
    with pytest.raises(IntentError, match="three uppercase"):
        IntentParameters(target_amount=Decimal("1"), target_currency="brl")
    with pytest.raises(IntentError, match="three uppercase"):
        IntentParameters(target_amount=Decimal("1"), target_currency="BR")


def test_parameters_accept_exact_v1_selectable_sources() -> None:
    parameters = IntentParameters(
        sources=("pichau", "terabyte", "amazon", "kabum"),
    )

    assert parameters.sources == ("pichau", "terabyte", "amazon", "kabum")


@pytest.mark.parametrize(
    "sources", [("mercado_livre",), ("shopee",), ("aliexpress",), ("unknown_store",)]
)
def test_parameters_reject_sources_outside_v1_selectable_set(
    sources: tuple[str, ...],
) -> None:
    with pytest.raises(IntentError, match="selectable V1 sources"):
        IntentParameters(sources=sources)


def test_parameters_reject_duplicated_sources() -> None:
    with pytest.raises(IntentError, match="not repeat"):
        IntentParameters(sources=("pichau", "pichau"))


def test_parameters_reject_blank_optional_text_fields() -> None:
    with pytest.raises(IntentError, match="search_query"):
        IntentParameters(search_query="   ")
    with pytest.raises(IntentError, match="mission_reference"):
        IntentParameters(mission_reference="")
