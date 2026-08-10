"""Confirmação da intenção interpretada antes de executar (TASK-058).

Classifica sim/não via `AIProviderManager`, com um propósito e um prompt
próprios, isolados do `IntentInterpreter` — não altera o vocabulário
fechado de `IntentKind`/`MissionCommand`; o vocabulário de resposta desta
classificação (`confirm`/`cancel`/`unclear`) é novo e exclusivo deste
módulo. Guarda só os dados mínimos necessários para executar uma
`create_mission`, `mission_command` ou `edit_mission` (TASK-069) já
validada -- `pause_for_edit` (TASK-069) reusa o mesmo par confirmar/
cancelar para pausar uma missão ativa antes de editar. Nunca guarda o
`Intent` bruto nem texto livre da mensagem original.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from app.ai_provider import (
    AIMessage,
    AIMessageRole,
    AIProviderError,
    AIProviderManager,
    AIRequest,
)
from app.missions.models import MissionCommand
from app.telegram.formatting import format_money, format_store_list
from app.users.models import UserRole

PURPOSE = "interpret_confirmation_reply"

_COMMAND_VERBS: dict[MissionCommand, str] = {
    MissionCommand.ACTIVATE: "ativar",
    MissionCommand.PAUSE: "pausar",
    MissionCommand.RESUME: "retomar",
    MissionCommand.COMPLETE: "concluir",
    MissionCommand.CANCEL: "cancelar",
    MissionCommand.EXPIRE: "expirar",
}

_CONFIRMATION_SUFFIX = (
    'Responda "1" para confirmar ou "2" para cancelar (também aceito "sim"/"não").'
)

_SYSTEM_PROMPT = (
    "Você classifica se uma resposta curta do usuário confirma ou cancela "
    "uma ação pendente. Você nunca decide nem executa a ação; apenas "
    "classifica a resposta.\n\n"
    "Responda somente com um objeto JSON válido, sem texto adicional, "
    "comentários ou blocos de código, exatamente neste formato:\n"
    '{"answer": "confirm" | "cancel" | "unclear"}\n\n'
    'Use "confirm" quando a pessoa concorda, aprova ou confirma, mesmo com '
    "erros de português, gírias, abreviações ou frases informais (ex.: "
    '"sim", "pode ser", "bora", "isso mesmo", "é isso msm", "confirmado", '
    '"1", "1 - sim"). Use "cancel" quando a pessoa recusa, nega ou desiste '
    '(ex.: "não", "nao quero", "deixa pra la", "cancela", "esquece", "2", '
    '"2 - não"). A pergunta sempre oferece "1" para confirmar e "2" para '
    "cancelar, então um número isolado deve ser classificado por essa "
    'correspondência. Use "unclear" sempre que a resposta não expressar '
    "claramente nem confirmação nem cancelamento."
)


class ConfirmationError(ValueError):
    """A resposta não foi reconhecida como confirmação nem cancelamento."""


async def resolve_answer(
    answer: str, *, manager: AIProviderManager, profile: UserRole
) -> bool:
    """`True` se confirmado, `False` se cancelado; levanta se não reconhecido."""
    request = AIRequest(
        request_id=uuid4(),
        profile=profile,
        purpose=PURPOSE,
        messages=(
            AIMessage(AIMessageRole.SYSTEM, _SYSTEM_PROMPT),
            AIMessage(AIMessageRole.USER, answer),
        ),
        requested_at=datetime.now(UTC),
    )
    try:
        response = await manager.generate(request)
    except AIProviderError:
        raise ConfirmationError(
            f"Não consegui confirmar agora. {_CONFIRMATION_SUFFIX}"
        ) from None

    classification = _parse_classification(response.content)
    if classification == "confirm":
        return True
    if classification == "cancel":
        return False
    raise ConfirmationError(f"Não entendi. {_CONFIRMATION_SUFFIX}")


def _parse_classification(content: str) -> str | None:
    try:
        payload = json.loads(content)
    except ValueError:
        return None
    if not isinstance(payload, dict) or set(payload) != {"answer"}:
        return None
    value = payload["answer"]
    if value not in {"confirm", "cancel", "unclear"}:
        return None
    return value


def stage_create_mission(
    *,
    search_query: str,
    target_amount: object,
    target_currency: str | None,
    sources: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "kind": "create_mission",
        "search_query": search_query,
        "target_amount": str(target_amount) if target_amount is not None else None,
        "target_currency": target_currency,
        "sources": list(sources),
    }


def stage_mission_command(
    *,
    mission_id: UUID,
    mission_title: str,
    command: MissionCommand,
    expected_state_version: int,
) -> dict[str, Any]:
    return {
        "kind": "mission_command",
        "mission_id": str(mission_id),
        "mission_title": mission_title,
        "command": command.value,
        "expected_state_version": expected_state_version,
    }


def describe_create_mission(payload: dict[str, Any]) -> str:
    lines = ["🔎 Confirmar nova missão?", "", f'Produto: "{payload["search_query"]}"']
    amount, currency = payload.get("target_amount"), payload.get("target_currency")
    if amount and currency:
        lines.append(f"🎯 Alvo: {format_money(Decimal(amount), currency)}")
    sources = payload.get("sources") or []
    lines.append(
        f"🏪 Lojas: {format_store_list(sources)}"
        if sources
        else "🏪 Lojas: todas as lojas disponíveis"
    )
    lines.extend(["", _CONFIRMATION_SUFFIX])
    return "\n".join(lines)


def describe_mission_command(payload: dict[str, Any]) -> str:
    verb = _COMMAND_VERBS[MissionCommand(payload["command"])]
    title = payload["mission_title"]
    return f'Confirmar: {verb} a missão "{title}"?\n\n{_CONFIRMATION_SUFFIX}'


def stage_edit_mission(
    *,
    mission_id: UUID,
    mission_title: str,
    expected_state_version: int,
    previous_target_amount: object,
    previous_target_currency: str | None,
    previous_sources: tuple[str, ...],
    target_amount: object,
    target_currency: str | None,
    clear_target: bool,
    sources: tuple[str, ...],
) -> dict[str, Any]:
    """TASK-069: monta o payload de edição -- só chamado quando a missão já
    está `PAUSED` no momento do *stage*; `expected_state_version` garante
    que a execução rejeita a confirmação se isso mudar antes de confirmar."""
    changes_target = clear_target or target_amount is not None
    changes_sources = bool(sources)
    return {
        "kind": "edit_mission",
        "mission_id": str(mission_id),
        "mission_title": mission_title,
        "expected_state_version": expected_state_version,
        "changes_target": changes_target,
        "target_amount": str(target_amount) if target_amount is not None else None,
        "target_currency": target_currency,
        "changes_sources": changes_sources,
        "sources": list(sources) if changes_sources else [],
        "previous_target_amount": (
            str(previous_target_amount) if previous_target_amount is not None else None
        ),
        "previous_target_currency": previous_target_currency,
        "previous_sources": list(previous_sources),
    }


def describe_edit_mission(payload: dict[str, Any]) -> str:
    title = payload["mission_title"]
    lines = ["✏️ Confirmar edição da missão?", "", f'Missão: "{title}"']
    if payload["changes_target"]:
        before = _format_target(
            payload["previous_target_amount"], payload["previous_target_currency"]
        )
        after = _format_target(payload["target_amount"], payload["target_currency"])
        lines.append(f"🎯 Alvo: {before} → {after}")
    if payload["changes_sources"]:
        before_stores = (
            format_store_list(payload["previous_sources"])
            if payload["previous_sources"]
            else "nenhuma"
        )
        after_stores = format_store_list(payload["sources"])
        lines.append(f"🏪 Lojas: {before_stores} → {after_stores}")
    lines.extend(
        [
            "",
            "A missão continua pausada depois da edição — use /retomar quando "
            "quiser voltar a coletar.",
            "",
            _CONFIRMATION_SUFFIX,
        ]
    )
    return "\n".join(lines)


def _format_target(amount: str | None, currency: str | None) -> str:
    if amount is None or currency is None:
        return "sem alvo"
    return format_money(Decimal(amount), currency)


def stage_pause_for_edit(
    *, mission_id: UUID, mission_title: str, expected_state_version: int
) -> dict[str, Any]:
    """TASK-069: pausa é pré-requisito para editar uma missão `ACTIVE` --
    encenada com o mesmo par confirmar/cancelar, sem vocabulário de IA
    novo."""
    return {
        "kind": "pause_for_edit",
        "mission_id": str(mission_id),
        "mission_title": mission_title,
        "expected_state_version": expected_state_version,
    }


def describe_pause_for_edit(payload: dict[str, Any]) -> str:
    title = payload["mission_title"]
    return (
        f'⏸️ A missão "{title}" está ativa -- preciso pausá-la antes de '
        "editar. Quer que eu pause agora?\n\n"
        f"{_CONFIRMATION_SUFFIX}"
    )
