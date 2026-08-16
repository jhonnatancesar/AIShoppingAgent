import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from app.ai_provider import (
    AIMessageRole,
    AIProviderCapabilityUnsupported,
    AIProviderQuotaExceeded,
    AIProviderUnavailable,
    AIRequest,
    AIResponse,
)
from app.intent import (
    Intent,
    IntentError,
    IntentInterpreter,
    IntentKind,
    IntentParameters,
    parse_intent_response,
)
from app.intent.interpreter import (
    _SYSTEM_PROMPT,
    _VERIFY_SYSTEM_PROMPT,
    PURPOSE,
    VERIFY_PURPOSE,
    _apply_safe_fallback,
    _build_verification_request,
    _identity_verification_reason,
    _parse_verified_search_query,
)
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
async def test_interpret_parses_model_when_present() -> None:
    """TASK-075: model estruturado, mesma chamada, preserva variante exata."""
    manager = _FakeManager(
        _response(
            parameters={
                "search_query": "Placa de Vídeo NVIDIA RTX 4070 Ti",
                "model": "RTX 4070 Ti",
                "target_amount": None,
                "target_currency": None,
                "sources": [],
                "mission_reference": None,
            }
        )
    )
    interpreter = IntentInterpreter(manager)

    intent = await interpreter.interpret("quero uma 4070 ti")

    assert intent.parameters.search_query == "Placa de Vídeo NVIDIA RTX 4070 Ti"
    assert intent.parameters.model == "RTX 4070 Ti"


@pytest.mark.anyio
async def test_interpret_model_defaults_to_none_when_absent() -> None:
    manager = _FakeManager(_response())
    interpreter = IntentInterpreter(manager)

    intent = await interpreter.interpret("quero um notebook gamer")

    assert intent.parameters.model is None


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


@pytest.mark.anyio
async def test_interpret_parses_edit_mission_with_target_and_sources() -> None:
    manager = _FakeManager(
        _response(
            kind="edit_mission",
            parameters={
                "search_query": None,
                "target_amount": "300.00",
                "target_currency": "BRL",
                "sources": ["kabum", "pichau"],
                "mission_reference": "teclado",
                "clear_target": False,
            },
        )
    )
    interpreter = IntentInterpreter(manager)

    intent = await interpreter.interpret("troca a missao do teclado pra kabum e pichau")

    assert intent.kind is IntentKind.EDIT_MISSION
    assert intent.command is None
    assert intent.parameters.mission_reference == "teclado"
    assert intent.parameters.target_amount == Decimal("300.00")
    assert intent.parameters.sources == ("kabum", "pichau")
    assert intent.parameters.clear_target is False


@pytest.mark.anyio
async def test_interpret_parses_edit_mission_clearing_the_target() -> None:
    manager = _FakeManager(
        _response(
            kind="edit_mission",
            parameters={
                "search_query": None,
                "target_amount": None,
                "target_currency": None,
                "sources": [],
                "mission_reference": "monitor",
                "clear_target": True,
            },
        )
    )
    interpreter = IntentInterpreter(manager)

    intent = await interpreter.interpret("tira o alvo da missao do monitor")

    assert intent.kind is IntentKind.EDIT_MISSION
    assert intent.parameters.clear_target is True
    assert intent.parameters.target_amount is None


def test_system_prompt_instructs_robustness_to_informal_writing() -> None:
    assert "gírias" in _SYSTEM_PROMPT
    assert "ordem das" in _SYSTEM_PROMPT
    assert "erros de digitação" in _SYSTEM_PROMPT


def test_system_prompt_documents_edit_mission_and_clear_target() -> None:
    assert "edit_mission" in _SYSTEM_PROMPT
    assert "clear_target" in _SYSTEM_PROMPT


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


def test_parse_falls_back_to_unknown_on_non_bool_clear_target() -> None:
    intent = _parsed(
        json.dumps(
            {
                "kind": "edit_mission",
                "command": None,
                "parameters": {
                    "mission_reference": "teclado",
                    "sources": ["kabum"],
                    "clear_target": "yes",
                },
            }
        )
    )

    assert intent.kind is IntentKind.UNKNOWN


def test_parse_falls_back_to_unknown_when_clear_target_contradicts_target_amount() -> (
    None
):
    intent = _parsed(
        json.dumps(
            {
                "kind": "edit_mission",
                "command": None,
                "parameters": {
                    "mission_reference": "teclado",
                    "target_amount": "300.00",
                    "target_currency": "BRL",
                    "clear_target": True,
                },
            }
        )
    )

    assert intent.kind is IntentKind.UNKNOWN


def test_parse_falls_back_to_unknown_on_edit_mission_without_mission_reference() -> (
    None
):
    intent = _parsed(
        json.dumps(
            {
                "kind": "edit_mission",
                "command": None,
                "parameters": {"sources": ["kabum"]},
            }
        )
    )

    assert intent.kind is IntentKind.UNKNOWN


def test_parse_falls_back_to_unknown_on_edit_mission_with_no_actual_change() -> None:
    intent = _parsed(
        json.dumps(
            {
                "kind": "edit_mission",
                "command": None,
                "parameters": {"mission_reference": "teclado"},
            }
        )
    )

    assert intent.kind is IntentKind.UNKNOWN


# ---------------------------------------------------------------------------
# TASK-083 SUBETAPA 3: gatilho de verificação externa + fallback seguro.
# Só mocks -- nenhuma chamada real de IA.
# ---------------------------------------------------------------------------


class _SequencedManager:
    """Fake manager que responde chamadas em sequência -- usado para testar
    o fluxo de duas chamadas (interpretação + verificação de identidade).
    Cada item de `responders` é uma exceção (levantada) ou uma função
    `AIRequest -> AIResponse`. Levanta `IndexError` se houver mais chamadas
    do que responders configurados -- prova, por si só, que nenhuma
    chamada extra e não esperada aconteceu."""

    def __init__(self, *responders: BaseException | object) -> None:
        self._responders = list(responders)
        self.captured_requests: list[AIRequest] = []

    async def generate(self, request: AIRequest) -> AIResponse:
        self.captured_requests.append(request)
        responder = self._responders.pop(0)
        if isinstance(responder, BaseException):
            raise responder
        return responder(request)


def _plain(content: str):
    def _respond(request: AIRequest) -> AIResponse:
        return AIResponse(
            request_id=request.request_id,
            provider="fake_provider",
            model="fake-model",
            content=content,
            finished_at=datetime.now(UTC),
        )

    return _respond


def _grounded(
    content: str,
    *,
    performed: bool = True,
    sources: tuple[str, ...] | None = None,
):
    if sources is None:
        sources = ("https://example.invalid/fonte",) if performed else ()

    def _respond(request: AIRequest) -> AIResponse:
        return AIResponse(
            request_id=request.request_id,
            provider="fake_provider",
            model="fake-model",
            content=content,
            finished_at=datetime.now(UTC),
            grounding_requested=True,
            grounding_performed=performed,
            grounding_sources=sources,
        )

    return _respond


def _first_response(**parameters: object) -> str:
    payload: dict[str, object] = {
        "search_query": None,
        "model": None,
        "model_confidence": None,
        "target_amount": None,
        "target_currency": None,
        "sources": [],
        "mission_reference": None,
    }
    payload.update(parameters)
    return json.dumps(
        {"kind": "create_mission", "command": None, "parameters": payload}
    )


# --- A: model=null -> nenhuma chamada de verificação ---


@pytest.mark.anyio
async def test_scenario_a_no_model_never_triggers_verification() -> None:
    manager = _SequencedManager(_plain(_first_response(search_query="cadeira gamer")))
    interpreter = IntentInterpreter(manager)

    intent = await interpreter.interpret("quero uma cadeira gamer")

    assert intent.parameters.search_query == "cadeira gamer"
    assert intent.parameters.model is None
    assert len(manager.captured_requests) == 1


# --- B: identidade específica, alta confiança, sem contradição/enriquecimento ---


@pytest.mark.anyio
async def test_scenario_b_safe_identity_never_triggers_verification() -> None:
    manager = _SequencedManager(
        _plain(
            _first_response(
                search_query="Placa de Vídeo NVIDIA RTX 4070 Ti",
                model="RTX 4070 Ti",
                model_confidence="alta",
            )
        )
    )
    interpreter = IntentInterpreter(manager)

    intent = await interpreter.interpret("quero uma 4070 ti")

    assert intent.parameters.search_query == "Placa de Vídeo NVIDIA RTX 4070 Ti"
    assert intent.parameters.model == "RTX 4070 Ti"
    assert len(manager.captured_requests) == 1


# --- C: contradição detectada, grounding válido -> corrige a família ---


@pytest.mark.anyio
async def test_scenario_c_known_contradiction_corrected_by_valid_grounding() -> None:
    manager = _SequencedManager(
        _plain(
            _first_response(
                search_query="AMD Ryzen 9 9800X3D",
                model="9800X3D",
                model_confidence="baixa",
            )
        ),
        _grounded(
            json.dumps({"search_query": "AMD Ryzen 7 9800X3D"}),
            sources=("https://amd.example.invalid/9800x3d",),
        ),
    )
    interpreter = IntentInterpreter(manager)

    intent = await interpreter.interpret("me acha um 9800x3d pfvr")

    assert intent.parameters.search_query == "AMD Ryzen 7 9800X3D"
    assert intent.parameters.model == "9800X3D"
    assert intent.parameters.model_confidence is None
    assert len(manager.captured_requests) == 2
    verify_request = manager.captured_requests[1]
    assert verify_request.purpose == VERIFY_PURPOSE
    assert verify_request.require_search_grounding is True


# --- D: contradição detectada, grounding indisponível -> fallback seguro ---


@pytest.mark.anyio
async def test_scenario_d_grounding_unavailable_falls_back_to_bare_model() -> None:
    manager = _SequencedManager(
        _plain(
            _first_response(
                search_query="AMD Ryzen 9 9800X3D",
                model="9800X3D",
                model_confidence="baixa",
            )
        ),
        AIProviderUnavailable(),
    )
    interpreter = IntentInterpreter(manager)

    intent = await interpreter.interpret("me acha um 9800x3d pfvr")

    assert intent.parameters.search_query == "9800X3D"
    assert intent.parameters.model == "9800X3D"
    assert len(manager.captured_requests) == 2


@pytest.mark.anyio
async def test_scenario_d_variant_quota_and_capability_errors_also_fall_back() -> None:
    """A cascata inteira do AIProviderManager só pode devolver
    subclasses de AIProviderError -- todas tratadas do mesmo jeito pelo
    fallback seguro, nunca travando a criação da missão."""
    for error in (AIProviderQuotaExceeded(), AIProviderCapabilityUnsupported("x")):
        manager = _SequencedManager(
            _plain(
                _first_response(
                    search_query="AMD Ryzen 9 9800X3D",
                    model="9800X3D",
                    model_confidence="baixa",
                )
            ),
            error,
        )
        interpreter = IntentInterpreter(manager)

        intent = await interpreter.interpret("me acha um 9800x3d pfvr")

        assert intent.parameters.search_query == "9800X3D"


# --- E: grounding solicitado mas não executado -> fallback seguro ---


@pytest.mark.anyio
async def test_scenario_e_grounding_not_performed_falls_back() -> None:
    manager = _SequencedManager(
        _plain(
            _first_response(
                search_query="AMD Ryzen 9 9800X3D",
                model="9800X3D",
                model_confidence="baixa",
            )
        ),
        _grounded(json.dumps({"search_query": "AMD Ryzen 7 9800X3D"}), performed=False),
    )
    interpreter = IntentInterpreter(manager)

    intent = await interpreter.interpret("me acha um 9800x3d pfvr")

    assert intent.parameters.search_query == "9800X3D"


# --- F: grounding executado mas sem fontes -> inconclusiva -> fallback seguro ---


@pytest.mark.anyio
async def test_scenario_f_grounding_performed_without_sources_falls_back() -> None:
    manager = _SequencedManager(
        _plain(
            _first_response(
                search_query="AMD Ryzen 9 9800X3D",
                model="9800X3D",
                model_confidence="baixa",
            )
        ),
        _grounded(
            json.dumps({"search_query": "AMD Ryzen 7 9800X3D"}),
            performed=True,
            sources=(),
        ),
    )
    interpreter = IntentInterpreter(manager)

    intent = await interpreter.interpret("me acha um 9800x3d pfvr")

    assert intent.parameters.search_query == "9800X3D"


# --- G: model_confidence baixa aciona verificação sem tabela guardrail ---


@pytest.mark.anyio
async def test_scenario_g_low_confidence_triggers_even_without_guardrail_table() -> (
    None
):
    manager = _SequencedManager(
        _plain(
            _first_response(
                search_query="Placa de Vídeo NVIDIA RTX 9090",
                model="RTX 9090",
                model_confidence="baixa",
            )
        ),
        _grounded(json.dumps({"search_query": "Placa de Vídeo NVIDIA RTX 9090"})),
    )
    interpreter = IntentInterpreter(manager)

    intent = await interpreter.interpret("quero uma rtx9090 ate 3000")

    assert len(manager.captured_requests) == 2
    assert intent.parameters.search_query == "Placa de Vídeo NVIDIA RTX 9090"


# --- H: enriquecimento não comprovado em modelo desconhecido isolado ---


@pytest.mark.anyio
async def test_scenario_h_unproven_enrichment_triggers_for_unknown_model() -> None:
    manager = _SequencedManager(
        _plain(
            _first_response(
                search_query="Processador Desconhecido ZX9999KX",
                model="ZX9999KX",
                model_confidence="alta",  # propositalmente "alta": prova que o
                # gatilho não depende do autorrelato nem de tabela manual.
            )
        ),
        _grounded(json.dumps({"search_query": "ZX9999KX"}), performed=False),
    )
    interpreter = IntentInterpreter(manager)

    intent = await interpreter.interpret("quero um zx9999kx")

    assert len(manager.captured_requests) == 2
    assert intent.parameters.search_query == "ZX9999KX"


# --- I: segunda resposta tenta alterar outros critérios -> só identidade muda ---


@pytest.mark.anyio
async def test_scenario_i_second_response_cannot_alter_other_parameters() -> None:
    manager = _SequencedManager(
        _plain(
            _first_response(
                search_query="AMD Ryzen 9 9800X3D",
                model="9800X3D",
                model_confidence="baixa",
                target_amount="3500.00",
                target_currency="BRL",
                sources=["kabum"],
            )
        ),
        _grounded(
            json.dumps(
                {
                    "search_query": "AMD Ryzen 7 9800X3D",
                    "target_amount": "1.00",
                }
            )
        ),
    )
    interpreter = IntentInterpreter(manager)

    intent = await interpreter.interpret("me acha um 9800x3d pfvr ate 3500")

    # Chave extra invalida o parsing da verificação -> tratado como
    # inconclusivo -> fallback seguro, nunca a injeção maliciosa.
    assert intent.parameters.search_query == "9800X3D"
    assert intent.parameters.target_amount == Decimal("3500.00")
    assert intent.parameters.target_currency == "BRL"
    assert intent.parameters.sources == ("kabum",)


# ---------------------------------------------------------------------------
# TASK-083 (correção de regressão): Caso A (não confirmado, sem
# contradição -- preserva display_query) vs Caso B (contradição
# confirmada -- nunca preserva nada como provisório). Exemplos literais
# do pedido do usuário.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_regression_case_a_unconfirmed_identity_preserves_display_query() -> None:
    manager = _SequencedManager(
        _plain(
            _first_response(
                search_query="AMD Ryzen 9 9950X3D",
                model="9950X3D",
                model_confidence="baixa",
            )
        ),
        AIProviderUnavailable(),  # grounding indisponível
    )
    interpreter = IntentInterpreter(manager)

    intent = await interpreter.interpret("me acha um 9950x3d pfvr")

    assert intent.parameters.search_query == "9950X3D"
    assert intent.parameters.model == "9950X3D"
    assert intent.parameters.display_query == "AMD Ryzen 9 9950X3D"


@pytest.mark.anyio
async def test_regression_case_b_contradiction_never_leaks_wrong_identity() -> None:
    manager = _SequencedManager(
        _plain(
            _first_response(
                search_query="AMD Ryzen 9 9800X3D",
                model="9800X3D",
                model_confidence="baixa",
            )
        ),
        AIProviderUnavailable(),  # grounding indisponível
    )
    interpreter = IntentInterpreter(manager)

    intent = await interpreter.interpret("me acha um 9800x3d pfvr")

    assert intent.parameters.search_query == "9800X3D"
    assert intent.parameters.model == "9800X3D"
    assert intent.parameters.display_query is None


# --- model_confidence nunca escapa do IntentInterpreter ---


@pytest.mark.anyio
async def test_model_confidence_is_always_stripped_from_final_parameters() -> None:
    manager = _SequencedManager(
        _plain(
            _first_response(
                search_query="Placa de Vídeo NVIDIA RTX 4070 Ti",
                model="RTX 4070 Ti",
                model_confidence="alta",
            )
        )
    )
    interpreter = IntentInterpreter(manager)

    intent = await interpreter.interpret("quero uma 4070 ti")

    assert intent.parameters.model_confidence is None


# --- unidade pura: _identity_verification_reason ---


def _params(**overrides: object) -> IntentParameters:
    defaults: dict[str, object] = {"search_query": None, "model": None}
    defaults.update(overrides)
    return IntentParameters(**defaults)


def test_verification_reason_none_when_model_is_none() -> None:
    assert _identity_verification_reason("qualquer coisa", _params()) is None


def test_verification_reason_unconfirmed_on_low_confidence_regardless_of_rest() -> None:
    parameters = _params(
        model="RTX 9090",
        search_query="Placa de Vídeo NVIDIA RTX 9090",
        model_confidence="baixa",
    )
    assert _identity_verification_reason("qualquer coisa", parameters) == "unconfirmed"


def test_verification_reason_contradiction_on_known_family_contradiction() -> None:
    parameters = _params(model="9800X3D", search_query="AMD Ryzen 9 9800X3D")
    assert _identity_verification_reason("9800x3d", parameters) == "contradiction"


def test_verification_reason_contradiction_takes_priority_over_low_confidence() -> None:
    """TASK-083 (correção): quando os dois sinais disparam ao mesmo tempo,
    o motivo reportado precisa ser a contradição -- é o sinal mais forte,
    e é ele quem decide se `_apply_safe_fallback` pode preservar
    `display_query` ou não."""
    parameters = _params(
        model="9800X3D",
        search_query="AMD Ryzen 9 9800X3D",
        model_confidence="baixa",
    )
    assert _identity_verification_reason("9800x3d", parameters) == "contradiction"


def test_verification_reason_unconfirmed_on_unproven_enrichment_for_bare_input() -> (
    None
):
    parameters = _params(model="ZX9999KX", search_query="Processador ZX9999KX")
    assert (
        _identity_verification_reason("quero um zx9999kx", parameters) == "unconfirmed"
    )


def test_verification_reason_none_when_nothing_suspicious() -> None:
    parameters = _params(
        model="RTX 4070 Ti",
        search_query="Placa de Vídeo NVIDIA RTX 4070 Ti",
        model_confidence="alta",
    )
    assert _identity_verification_reason("quero uma 4070 ti", parameters) is None


# --- unidade pura: _apply_safe_fallback ---


def test_apply_safe_fallback_reduces_search_query_to_bare_model() -> None:
    parameters = _params(
        model="9800X3D",
        search_query="AMD Ryzen 9 9800X3D",
        target_amount=Decimal("3500.00"),
        target_currency="BRL",
        sources=("kabum",),
    )
    fallback = _apply_safe_fallback(
        parameters, reason="contradiction", candidate=parameters.search_query
    )

    assert fallback.search_query == "9800X3D"
    assert fallback.model == "9800X3D"
    assert fallback.target_amount == Decimal("3500.00")
    assert fallback.sources == ("kabum",)


def test_apply_safe_fallback_never_preserves_display_query_on_contradiction() -> None:
    """Caso B do pedido do usuário: 9800X3D interpretado como Ryzen 9 é
    contradição confirmada -- não pode sobrar em `display_query` nem como
    provisório."""
    parameters = _params(model="9800X3D", search_query="AMD Ryzen 9 9800X3D")
    fallback = _apply_safe_fallback(
        parameters, reason="contradiction", candidate=parameters.search_query
    )

    assert fallback.search_query == "9800X3D"
    assert fallback.display_query is None


def test_apply_safe_fallback_preserves_display_query_when_unconfirmed() -> None:
    """Caso A do pedido do usuário: 9950X3D interpretado como Ryzen 9 não
    tem contradição conhecida, só falta confirmação -- `search_query`
    operacional reduz ao código cru, mas `display_query` preserva o
    candidato plausível para UX/contexto."""
    parameters = _params(model="9950X3D", search_query="AMD Ryzen 9 9950X3D")
    fallback = _apply_safe_fallback(
        parameters, reason="unconfirmed", candidate=parameters.search_query
    )

    assert fallback.search_query == "9950X3D"
    assert fallback.model == "9950X3D"
    assert fallback.display_query == "AMD Ryzen 9 9950X3D"


# --- unidade pura: _parse_verified_search_query ---


def test_parse_verified_query_accepts_minimal_valid_shape() -> None:
    content = json.dumps({"search_query": "AMD Ryzen 7 9800X3D"})
    assert _parse_verified_search_query(content) == "AMD Ryzen 7 9800X3D"


@pytest.mark.parametrize(
    "content",
    [
        "isto não é json",
        json.dumps({"search_query": "ok", "target_amount": "1.00"}),
        json.dumps({"model": "9800X3D"}),
        json.dumps({"search_query": ""}),
        json.dumps({"search_query": "   "}),
        json.dumps({"search_query": 123}),
        json.dumps(["9800X3D"]),
    ],
)
def test_parse_verified_query_rejects_any_deviation(content: str) -> None:
    assert _parse_verified_search_query(content) is None


# --- unidade pura: _build_verification_request ---


def test_build_verification_request_is_scoped_to_identity_only() -> None:
    request = _build_verification_request(
        raw_message="me acha um 9800x3d pfvr",
        model="9800X3D",
        search_query="AMD Ryzen 9 9800X3D",
        requested_at=datetime.now(UTC),
        profile=UserRole.ADMIN,
    )

    assert request.purpose == VERIFY_PURPOSE
    assert request.purpose != PURPOSE
    assert request.require_search_grounding is True
    assert request.profile is UserRole.ADMIN
    assert "9800x3d" in request.messages[-1].content.lower()
    assert "9800X3D" in request.messages[-1].content


def test_verify_system_prompt_states_scope_is_identity_only() -> None:
    assert "nunca decide" in _VERIFY_SYSTEM_PROMPT.lower()
    assert "search_query" in _VERIFY_SYSTEM_PROMPT


def test_system_prompt_documents_model_confidence_contract() -> None:
    assert "model_confidence" in _SYSTEM_PROMPT
    assert '"alta"' in _SYSTEM_PROMPT
    assert '"baixa"' in _SYSTEM_PROMPT
