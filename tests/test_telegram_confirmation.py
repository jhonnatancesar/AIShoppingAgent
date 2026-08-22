"""Testes do fluxo de confirmação da intenção interpretada (TASK-058)."""

from decimal import Decimal
from uuid import uuid4

import pytest
from app.missions.models import MissionCommand, MissionStatus
from app.telegram.confirmation import (
    ConfirmationError,
    current_store_options,
    describe_create_mission,
    describe_create_mission_sources_prompt,
    describe_create_mission_sources_retry,
    describe_edit_add_sources_none_missing,
    describe_edit_add_sources_prompt,
    describe_edit_lojas_menu,
    describe_edit_lojas_menu_retry,
    describe_edit_menu,
    describe_edit_menu_retry,
    describe_edit_mission,
    describe_edit_remove_sources_prompt,
    describe_edit_remove_sources_too_few,
    describe_edit_remove_sources_would_empty,
    describe_edit_source_selection_retry,
    describe_edit_target_amount_prompt,
    describe_edit_target_amount_retry,
    describe_mission_choice_prompt,
    describe_mission_choice_retry,
    describe_mission_command,
    describe_mission_command_choice_prompt,
    describe_mission_command_choice_retry,
    describe_no_editable_mission,
    describe_pause_for_edit,
    missing_store_options,
    parse_multi_numbered_choice,
    parse_numbered_store_selection,
    parse_single_numbered_choice,
    parse_target_amount_entry,
    resolve_answer,
    resolve_create_mission_sources,
    resolve_edit_source_selection,
    stage_await_create_mission_sources,
    stage_create_mission,
    stage_edit_mission,
    stage_mission_command,
    stage_mission_command_choice,
    stage_pause_for_edit,
)


@pytest.mark.anyio
@pytest.mark.parametrize("answer", ["sim", "SIM", "s", "1", "  sim  "])
async def test_resolve_answer_accepts_closed_confirm_vocabulary(answer: str) -> None:
    assert await resolve_answer(answer) is True


@pytest.mark.anyio
@pytest.mark.parametrize("answer", ["não", "nao", "N", "2", "  não  "])
async def test_resolve_answer_accepts_closed_cancel_vocabulary(answer: str) -> None:
    assert await resolve_answer(answer) is False


@pytest.mark.anyio
@pytest.mark.parametrize("answer", ["oi", "pode ser", "1 - sim", "3", ""])
async def test_resolve_answer_rejects_every_ambiguous_answer_locally(
    answer: str,
) -> None:
    with pytest.raises(ConfirmationError, match="Não entendi"):
        await resolve_answer(answer)


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
        "model": None,
        "display_query": None,
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


def test_describe_create_mission_prefers_display_query_when_present() -> None:
    """TASK-083 (correção de regressão): identidade provisória plausível
    aparece na confirmação, mesmo com `search_query` operacional ainda
    reduzida ao código cru."""
    payload = stage_create_mission(
        search_query="9950X3D",
        model="9950X3D",
        display_query="AMD Ryzen 9 9950X3D",
        target_amount=None,
        target_currency=None,
        sources=(),
    )

    description = describe_create_mission(payload)
    assert "AMD Ryzen 9 9950X3D" in description
    assert '"9950X3D"' not in description


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


_STORE_OPTIONS = {"1": "pichau", "2": "terabyte", "3": "amazon", "4": "kabum"}
_STORE_ALL_TOKENS = frozenset({"5", "todo", "todos", "toda", "todas"})


def test_parse_numbered_store_selection_single_option() -> None:
    assert parse_numbered_store_selection(
        "1", option_map=_STORE_OPTIONS, all_tokens=_STORE_ALL_TOKENS
    ) == ("pichau",)


def test_parse_numbered_store_selection_multiple_options() -> None:
    assert parse_numbered_store_selection(
        "1,3", option_map=_STORE_OPTIONS, all_tokens=_STORE_ALL_TOKENS
    ) == ("pichau", "amazon")
    assert parse_numbered_store_selection(
        "1,2,4", option_map=_STORE_OPTIONS, all_tokens=_STORE_ALL_TOKENS
    ) == ("pichau", "terabyte", "kabum")


def test_parse_numbered_store_selection_all_token() -> None:
    assert parse_numbered_store_selection(
        "5", option_map=_STORE_OPTIONS, all_tokens=_STORE_ALL_TOKENS
    ) == ("amazon", "kabum", "pichau", "terabyte")


def test_parse_numbered_store_selection_all_mixed_with_another_number() -> None:
    # TASK-070: misturar "5" com qualquer outro número ainda vira "todas".
    assert parse_numbered_store_selection(
        "5,1", option_map=_STORE_OPTIONS, all_tokens=_STORE_ALL_TOKENS
    ) == ("amazon", "kabum", "pichau", "terabyte")


def test_parse_numbered_store_selection_rejects_unknown_option() -> None:
    assert (
        parse_numbered_store_selection(
            "9", option_map=_STORE_OPTIONS, all_tokens=_STORE_ALL_TOKENS
        )
        is None
    )


def test_parse_numbered_store_selection_rejects_valid_mixed_with_invalid() -> None:
    # TASK-070: nunca aceita parcialmente -- "1,9" é inválido mesmo o "1" existindo.
    assert (
        parse_numbered_store_selection(
            "1,9", option_map=_STORE_OPTIONS, all_tokens=_STORE_ALL_TOKENS
        )
        is None
    )


def test_parse_numbered_store_selection_rejects_unrecognizable_text() -> None:
    assert (
        parse_numbered_store_selection(
            "não sei", option_map=_STORE_OPTIONS, all_tokens=_STORE_ALL_TOKENS
        )
        is None
    )


def test_parse_numbered_store_selection_deduplicates_repeated_option() -> None:
    assert parse_numbered_store_selection(
        "1,1", option_map=_STORE_OPTIONS, all_tokens=_STORE_ALL_TOKENS
    ) == ("pichau",)


def test_resolve_create_mission_sources_uses_the_task_070_order() -> None:
    # Ordem própria deste fluxo, diferente da usada pelo /cadastro.
    # diferente da usada pelo /cadastro.
    assert resolve_create_mission_sources("1,4") == ("pichau", "kabum")
    assert resolve_create_mission_sources("5") == ("magalu",)
    assert resolve_create_mission_sources("6") == (
        "amazon",
        "kabum",
        "magalu",
        "pichau",
        "terabyte",
    )
    assert resolve_create_mission_sources("9") is None


def test_stage_await_create_mission_sources_preserves_other_criteria() -> None:
    payload = stage_await_create_mission_sources(
        search_query="notebook gamer",
        target_amount=Decimal("5000.00"),
        target_currency="BRL",
    )

    assert payload == {
        "kind": "await_create_mission_sources",
        "search_query": "notebook gamer",
        "model": None,
        "display_query": None,
        "target_amount": "5000.00",
        "target_currency": "BRL",
    }


def test_stage_await_create_mission_sources_without_target() -> None:
    payload = stage_await_create_mission_sources(
        search_query="ssd nvme", target_amount=None, target_currency=None
    )

    assert payload["target_amount"] is None
    assert payload["target_currency"] is None


def test_describe_create_mission_sources_prompt_lists_task_070_order() -> None:
    prompt = describe_create_mission_sources_prompt()

    assert "1 — Pichau" in prompt
    assert "2 — Terabyte" in prompt
    assert "3 — Amazon" in prompt
    assert "4 — Kabum" in prompt
    assert "5 — Magalu" in prompt
    assert "6 — Todas" in prompt


def test_describe_create_mission_sources_retry_asks_again() -> None:
    assert "Não entendi essa opção" in describe_create_mission_sources_retry()


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


def test_stage_and_describe_mission_command_choice() -> None:
    from types import SimpleNamespace

    missions = [
        SimpleNamespace(
            id=uuid4(),
            title="Ryzen 7 9800X3D",
            status=MissionStatus.ACTIVE,
            state_version=1,
        ),
        SimpleNamespace(
            id=uuid4(),
            title="Mouse Logitech",
            status=MissionStatus.PAUSED,
            state_version=2,
        ),
    ]

    payload = stage_mission_command_choice(
        missions=missions, command=MissionCommand.CANCEL
    )

    assert payload["kind"] == "mission_command_choice"
    assert payload["command"] == "cancel"
    assert payload["missions"] == [
        {
            "mission_id": str(missions[0].id),
            "mission_title": "Ryzen 7 9800X3D",
            "expected_state_version": 1,
        },
        {
            "mission_id": str(missions[1].id),
            "mission_title": "Mouse Logitech",
            "expected_state_version": 2,
        },
    ]
    prompt = describe_mission_command_choice_prompt(
        missions, command=MissionCommand.CANCEL
    )
    assert "1 — Ryzen 7 9800X3D — ativa" in prompt
    assert "2 — Mouse Logitech — pausada" in prompt
    assert "cancelar" in prompt
    assert describe_mission_command_choice_retry()  # não vazio


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
        "auto_paused": False,
    }
    description = describe_edit_mission(payload)
    assert "teclado mecanico" in description
    assert "R$ 500,00" in description
    assert "R$ 300,00" in description
    assert "continuará pausada" in description
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


# TASK-071: menu guiado e determinístico de /editar-missao.


def test_parse_single_numbered_choice_valid() -> None:
    assert parse_single_numbered_choice("1", count=2) == 0
    assert parse_single_numbered_choice("2", count=2) == 1


def test_parse_single_numbered_choice_rejects_out_of_range() -> None:
    assert parse_single_numbered_choice("3", count=2) is None
    assert parse_single_numbered_choice("0", count=2) is None


def test_parse_single_numbered_choice_rejects_non_numeric_or_multiple() -> None:
    assert parse_single_numbered_choice("abc", count=2) is None
    assert parse_single_numbered_choice("1,2", count=2) is None
    assert parse_single_numbered_choice("", count=2) is None


# --- TASK-085: parse_multi_numbered_choice ---


def test_parse_multi_numbered_choice_single_value() -> None:
    assert parse_multi_numbered_choice("1", count=3) == (0,)


def test_parse_multi_numbered_choice_multiple_with_and_without_spaces() -> None:
    assert parse_multi_numbered_choice("1,3", count=4) == (0, 2)
    assert parse_multi_numbered_choice("2, 4", count=4) == (1, 3)


def test_parse_multi_numbered_choice_rejects_out_of_range_index() -> None:
    assert parse_multi_numbered_choice("1,9", count=3) is None


def test_parse_multi_numbered_choice_rejects_invalid_token() -> None:
    assert parse_multi_numbered_choice("1,abc", count=3) is None


def test_parse_multi_numbered_choice_rejects_empty() -> None:
    assert parse_multi_numbered_choice("", count=3) is None
    assert parse_multi_numbered_choice("   ", count=3) is None


def test_parse_multi_numbered_choice_dedupes_preserving_first_order() -> None:
    assert parse_multi_numbered_choice("2,1,2", count=3) == (1, 0)


def test_describe_mission_choice_prompt_lists_titles_numbered() -> None:
    prompt = describe_mission_choice_prompt(
        ["teclado mecanico", "monitor curvo"], header="Qual missão?"
    )

    assert "Qual missão?" in prompt
    assert "1 — teclado mecanico" in prompt
    assert "2 — monitor curvo" in prompt


def test_describe_mission_choice_retry_asks_again() -> None:
    assert "número" in describe_mission_choice_retry()


def test_describe_no_editable_mission_mentions_paused_and_active() -> None:
    reply = describe_no_editable_mission()
    assert "pausada" in reply
    assert "ativa" in reply


def test_describe_edit_menu_lists_lojas_and_preco() -> None:
    menu = describe_edit_menu("teclado mecanico")
    assert "teclado mecanico" in menu
    assert "1 — Lojas" in menu
    assert "2 — Preço-alvo" in menu


def test_describe_edit_menu_retry() -> None:
    assert "lojas" in describe_edit_menu_retry().lower()


def test_describe_edit_lojas_menu_lists_add_and_remove() -> None:
    menu = describe_edit_lojas_menu()
    assert "1 — Adicionar lojas" in menu
    assert "2 — Remover lojas" in menu


def test_describe_edit_lojas_menu_retry() -> None:
    assert "adicionar" in describe_edit_lojas_menu_retry().lower()


def test_missing_store_options_excludes_current_and_uses_task_070_order() -> None:
    assert missing_store_options(("pichau",)) == {
        "1": "terabyte",
        "2": "amazon",
        "3": "kabum",
        "4": "magalu",
    }
    assert missing_store_options(()) == {
        "1": "pichau",
        "2": "terabyte",
        "3": "amazon",
        "4": "kabum",
        "5": "magalu",
    }
    assert (
        missing_store_options(("pichau", "terabyte", "amazon", "kabum", "magalu")) == {}
    )


def test_current_store_options_includes_only_linked_in_canonical_order() -> None:
    assert current_store_options(("kabum", "pichau")) == {"1": "pichau", "2": "kabum"}
    assert current_store_options(("pichau",)) == {"1": "pichau"}


def test_describe_edit_add_sources_prompt_lists_option_map() -> None:
    prompt = describe_edit_add_sources_prompt({"1": "terabyte", "2": "kabum"})
    assert "ainda não vinculadas" in prompt
    assert "1 — Terabyte" in prompt
    assert "2 — Kabum" in prompt


def test_describe_edit_remove_sources_prompt_lists_option_map() -> None:
    prompt = describe_edit_remove_sources_prompt({"1": "pichau"})
    assert "atualmente vinculadas" in prompt
    assert "1 — Pichau" in prompt


def test_describe_edit_source_selection_retry() -> None:
    assert "Não entendi essa opção" in describe_edit_source_selection_retry()


def test_describe_edit_add_sources_none_missing() -> None:
    assert "todas" in describe_edit_add_sources_none_missing().lower()


def test_describe_edit_remove_sources_too_few() -> None:
    assert "pelo menos uma" in describe_edit_remove_sources_too_few()


def test_describe_edit_remove_sources_would_empty() -> None:
    assert "pelo menos uma" in describe_edit_remove_sources_would_empty()


def test_resolve_edit_source_selection_validates_completely_no_all_shortcut() -> None:
    option_map = {"1": "terabyte", "2": "amazon", "3": "kabum"}
    assert resolve_edit_source_selection("1,3", option_map=option_map) == (
        "terabyte",
        "kabum",
    )
    assert resolve_edit_source_selection("1,9", option_map=option_map) is None
    # sem atalho de "todas" -- não há um número fixo que sempre signifique isso
    assert resolve_edit_source_selection("todas", option_map=option_map) is None


def test_describe_edit_target_amount_prompt_mentions_zero_removes_target() -> None:
    prompt = describe_edit_target_amount_prompt()
    assert "reais" in prompt
    assert "0" in prompt
    assert "remover o preço-alvo" in prompt


def test_describe_edit_target_amount_retry() -> None:
    assert "Não entendi" in describe_edit_target_amount_retry()


def test_parse_target_amount_entry_accepts_dot_and_comma() -> None:
    assert parse_target_amount_entry("300") == Decimal("300")
    assert parse_target_amount_entry("300.50") == Decimal("300.50")
    assert parse_target_amount_entry("300,50") == Decimal("300.50")


def test_parse_target_amount_entry_accepts_zero_to_clear() -> None:
    assert parse_target_amount_entry("0") == Decimal("0")


def test_parse_target_amount_entry_rejects_negative_and_non_numeric() -> None:
    assert parse_target_amount_entry("-5") is None
    assert parse_target_amount_entry("não sei") is None
    assert parse_target_amount_entry("") is None
