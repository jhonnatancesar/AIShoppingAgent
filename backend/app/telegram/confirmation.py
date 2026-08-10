"""Confirmação da intenção interpretada antes de executar (TASK-058).

Classifica sim/não via `AIProviderManager`, com um propósito e um prompt
próprios, isolados do `IntentInterpreter` — não altera o vocabulário
fechado de `IntentKind`/`MissionCommand`; o vocabulário de resposta desta
classificação (`confirm`/`cancel`/`unclear`) é novo e exclusivo deste
módulo. Guarda só os dados mínimos necessários para executar uma
`create_mission` ou `mission_command` já validada, nunca o `Intent` bruto
nem texto livre da mensagem original.
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
