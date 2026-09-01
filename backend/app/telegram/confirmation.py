"""Confirmação determinística antes de executar uma ação pendente.

Respostas claras de confirmação/cancelamento são classificadas localmente;
respostas desconhecidas são recusadas e solicitadas novamente, sem chamar
provedor de IA. Guarda só os dados mínimos necessários para executar uma
`create_mission`, `mission_command` ou `edit_mission` (TASK-069) já
validada -- `pause_for_edit` (TASK-069) reusa o mesmo par confirmar/
cancelar para pausar uma missão ativa antes de editar;
`await_create_mission_sources` (TASK-070) é o único estado pendente que
**não** passa por `resolve_answer` -- a resposta é uma lista numerada de
lojas, interpretada de forma determinística por
`parse_numbered_store_selection`, nunca por IA. O menu guiado de
`/editar-missao` (TASK-071) segue o mesmo princípio: toda a navegação e a
confirmação final são determinísticas. Nunca guarda o `Intent` bruto nem
texto livre da mensagem original.
"""

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from app.missions.models import Mission, MissionCommand
from app.telegram.formatting import (
    format_mission_status,
    format_money,
    format_store_list,
)

_COMMAND_VERBS: dict[MissionCommand, str] = {
    MissionCommand.ACTIVATE: "ativar",
    MissionCommand.PAUSE: "pausar",
    MissionCommand.RESUME: "retomar",
    MissionCommand.COMPLETE: "concluir",
    MissionCommand.CANCEL: "cancelar",
    MissionCommand.EXPIRE: "expirar",
}

_CONFIRMATION_SUFFIX = (
    '1 — Confirmar\n2 — Cancelar\n\nVocê também pode responder "sim" ou "não".'
)

_YES_NO_SUFFIX = '1 — Sim\n2 — Não\n\nVocê também pode responder "sim" ou "não".'

_CONFIRM_TOKENS = frozenset({"sim", "s", "1"})
_CANCEL_TOKENS = frozenset({"não", "nao", "n", "2"})


class ConfirmationError(ValueError):
    """A resposta não foi reconhecida como confirmação nem cancelamento."""


async def resolve_answer(answer: str) -> bool:
    """Resolve apenas o vocabulário fechado local, sem qualquer I/O ou IA."""
    normalized = answer.strip().casefold()
    if normalized in _CONFIRM_TOKENS:
        return True
    if normalized in _CANCEL_TOKENS:
        return False
    raise ConfirmationError(f"Não entendi.\n\n{_CONFIRMATION_SUFFIX}")


def stage_create_mission(
    *,
    search_query: str,
    model: str | None = None,
    display_query: str | None = None,
    target_amount: object,
    target_currency: str | None,
    sources: tuple[str, ...],
) -> dict[str, Any]:
    """`display_query` (TASK-083, correção de regressão) é só
    apresentação -- vira `Mission.title` na criação; `search_query`
    continua a identidade operacional (o que é pesquisado nas lojas)."""
    return {
        "kind": "create_mission",
        "search_query": search_query,
        "model": model,
        "display_query": display_query,
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
    display = payload.get("display_query") or payload["search_query"]
    lines = ["🔎 Confirmar nova missão?", "", f'Produto: "{display}"']
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


_CREATE_MISSION_SOURCE_OPTIONS: dict[str, str] = {
    "1": "pichau",
    "2": "terabyte",
    "3": "amazon",
    "4": "kabum",
    "5": "magalu",
    "6": "mercadolivre",
}
"""TASK-070: ordem própria deste fluxo -- diferente da usada pelo
`/cadastro` (`app/users/registration.py`), que não é alterada por esta
TASK. Cada fluxo numerado define o próprio mapa."""

_CREATE_MISSION_SOURCE_ALL_TOKENS = frozenset({"7", "todo", "todos", "toda", "todas"})

_CREATE_MISSION_SOURCES_PROMPT = (
    "🏪 Em quais lojas você quer que eu procure?\n\n"
    "1 — Pichau\n"
    "2 — Terabyte\n"
    "3 — Amazon\n"
    "4 — Kabum\n"
    "5 — Magalu\n"
    "6 — Mercado Livre\n"
    "7 — Todas\n\n"
    "Digite os números separados por vírgula.\n"
    "Exemplo: 1,3\n\n"
    "Para escolher todas, envie 7."
)

_CREATE_MISSION_SOURCES_RETRY = (
    "Não entendi essa opção.\n\n"
    "Use os números de 1 a 6 separados por vírgula ou 7 para todas."
)


def parse_numbered_store_selection(
    raw: str,
    *,
    option_map: Mapping[str, str],
    all_tokens: frozenset[str],
) -> tuple[str, ...] | None:
    """Interpreta uma lista numerada de lojas de forma determinística, sem
    IA (TASK-070). Genérico/configurável -- `option_map`/`all_tokens`
    definem o vocabulário de cada fluxo; nenhuma ordem fica fixa aqui.

    A entrada precisa ser reconhecida por completo: qualquer token fora de
    `option_map`/`all_tokens` invalida a resposta inteira (nunca aceita só
    a parte reconhecida, ex.: "1,9" é inválido mesmo o "1" existindo).
    Repetição é deduplicada (ex.: "1,1" vira só a loja 1). Misturar "5"
    (ou sinônimo de "todas") com qualquer outro token ainda resulta em
    todas as opções (ex.: "5,1"). Retorna `None` quando a resposta é
    inválida.
    """
    tokens = [
        token.strip().lower() for token in re.split(r"[,\s]+", raw) if token.strip()
    ]
    if not tokens:
        return None
    if any(token in all_tokens for token in tokens):
        return tuple(sorted(set(option_map.values())))
    resolved: list[str] = []
    for token in tokens:
        code = option_map.get(token)
        if code is None:
            return None
        if code not in resolved:
            resolved.append(code)
    return tuple(resolved)


def stage_await_create_mission_sources(
    *,
    search_query: str,
    model: str | None = None,
    display_query: str | None = None,
    target_amount: object,
    target_currency: str | None,
) -> dict[str, Any]:
    """TASK-070: guarda os critérios já interpretados (produto e, se
    houver, preço-alvo) enquanto aguarda a escolha das lojas -- não cria a
    missão nem encena a confirmação normal ainda. `display_query`
    (TASK-083, correção de regressão) atravessa esse estado intermediário
    sem mudança -- só é usado quando `stage_create_mission` for chamado
    de fato, após a escolha das lojas."""
    return {
        "kind": "await_create_mission_sources",
        "search_query": search_query,
        "model": model,
        "display_query": display_query,
        "target_amount": str(target_amount) if target_amount is not None else None,
        "target_currency": target_currency,
    }


def describe_create_mission_sources_prompt() -> str:
    return _CREATE_MISSION_SOURCES_PROMPT


def describe_create_mission_sources_retry() -> str:
    return _CREATE_MISSION_SOURCES_RETRY


def resolve_create_mission_sources(raw: str) -> tuple[str, ...] | None:
    """TASK-070: única forma de interpretar a resposta à lista numerada de
    lojas na criação de missão -- determinística, nunca passa pela IA."""
    return parse_numbered_store_selection(
        raw,
        option_map=_CREATE_MISSION_SOURCE_OPTIONS,
        all_tokens=_CREATE_MISSION_SOURCE_ALL_TOKENS,
    )


def describe_mission_command(payload: dict[str, Any]) -> str:
    command = MissionCommand(payload["command"])
    title = payload["mission_title"]
    prompts = {
        MissionCommand.ACTIVATE: ("▶️", "ativar"),
        MissionCommand.PAUSE: ("⏸️", "pausar"),
        MissionCommand.RESUME: ("▶️", "retomar"),
        MissionCommand.COMPLETE: ("✅", "concluir"),
        MissionCommand.CANCEL: ("⚠️", "mesmo cancelar"),
        MissionCommand.EXPIRE: ("⌛", "expirar"),
    }
    icon, action = prompts[command]
    return f'{icon} Quer {action} a missão "{title}"?\n\n{_YES_NO_SUFFIX}'


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
    auto_paused: bool = False,
) -> dict[str, Any]:
    """TASK-069: monta o payload de edição -- só chamado quando a missão já
    está `PAUSED` no momento do *stage*; `expected_state_version` garante
    que a execução rejeita a confirmação se isso mudar antes de confirmar.

    `auto_paused` (TASK-090) distingue, para a mensagem final, se esta
    pausa foi provocada agora mesmo pelo próprio fluxo de edição (missão
    estava `ACTIVE` antes de `/editar_missao`) ou se a missão já estava
    `PAUSED` por escolha anterior do usuário -- nunca decide nem altera
    nenhuma transição, só a redação da confirmação."""
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
        "auto_paused": auto_paused,
    }


def describe_edit_mission(payload: dict[str, Any]) -> str:
    title = payload["mission_title"]
    lines = ["✏️ Confirmar alterações?", "", f'Missão: "{title}"']
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
            "A missão continuará pausada depois da edição.",
            "",
            _CONFIRMATION_SUFFIX,
        ]
    )
    return "\n".join(lines)


# Subtask 7 da auditoria GG Oferta: fluxos determinísticos de /suporte e
# /sugerir_loja -- mesmo par confirmar/cancelar da TASK-058, nenhuma
# passagem por IA. A confirmação nunca promete prazo de resposta nem de
# implementação.

_SUPPORT_TYPE_OPTIONS: dict[str, str] = {"1": "bug", "2": "support"}

_SUPPORT_TYPE_PROMPT = (
    "🛠️ Qual é o motivo do contato?\n\n"
    "1 — Erro/Bug\n"
    "2 — Outro suporte\n\n"
    "Digite o número."
)

_SUPPORT_TYPE_RETRY = "Não entendi.\n\n1 — Erro/Bug\n2 — Outro suporte\n\nDigite 1 ou 2."

_SUPPORT_DESCRIPTION_PROMPT = "✍️ Descreva o que aconteceu, em poucas linhas."

_SUPPORT_DESCRIPTION_RETRY = "A descrição não pode ficar vazia. Descreva o que aconteceu."

_SUPPORT_TYPE_LABELS: dict[str, str] = {"bug": "Erro/Bug", "support": "Outro suporte"}


def describe_support_type_prompt() -> str:
    return _SUPPORT_TYPE_PROMPT


def describe_support_type_retry() -> str:
    return _SUPPORT_TYPE_RETRY


def resolve_support_type_choice(raw: str) -> str | None:
    return _SUPPORT_TYPE_OPTIONS.get(raw.strip())


def describe_support_description_prompt() -> str:
    return _SUPPORT_DESCRIPTION_PROMPT


def describe_support_description_retry() -> str:
    return _SUPPORT_DESCRIPTION_RETRY


def stage_support_feedback(*, kind: str, message: str) -> dict[str, Any]:
    return {"kind": "support_feedback", "feedback_kind": kind, "message": message}


def describe_support_feedback(payload: dict[str, Any]) -> str:
    label = _SUPPORT_TYPE_LABELS[payload["feedback_kind"]]
    lines = [
        "📩 Confirmar envio?",
        "",
        f"Tipo: {label}",
        f"Mensagem: {payload['message']}",
        "",
        _CONFIRMATION_SUFFIX,
    ]
    return "\n".join(lines)


_STORE_SUGGESTION_NAME_PROMPT = "🏪 Qual loja você quer sugerir?"
_STORE_SUGGESTION_NAME_RETRY = "O nome da loja não pode ficar vazio. Qual loja você quer sugerir?"
_STORE_SUGGESTION_URL_PROMPT = (
    "🔗 Se quiser, envie o link da loja.\n\nPara pular, envie 0."
)
_STORE_SUGGESTION_COMMENT_PROMPT = (
    "💬 Se quiser, deixe um comentário.\n\nPara pular, envie 0."
)
_SKIP_TOKENS = frozenset({"0", "pular"})


def describe_store_suggestion_name_prompt() -> str:
    return _STORE_SUGGESTION_NAME_PROMPT


def describe_store_suggestion_name_retry() -> str:
    return _STORE_SUGGESTION_NAME_RETRY


def describe_store_suggestion_url_prompt() -> str:
    return _STORE_SUGGESTION_URL_PROMPT


def describe_store_suggestion_comment_prompt() -> str:
    return _STORE_SUGGESTION_COMMENT_PROMPT


def resolve_optional_step(raw: str) -> str | None:
    """`None` quando o usuário pulou a etapa opcional (URL/comentário)."""
    text = raw.strip()
    if not text or text.casefold() in _SKIP_TOKENS:
        return None
    return text


def stage_store_suggestion(
    *, store_name: str, store_url: str | None = None, comment: str | None = None
) -> dict[str, Any]:
    return {
        "kind": "store_suggestion",
        "store_name": store_name,
        "store_url": store_url,
        "comment": comment,
    }


def describe_store_suggestion(payload: dict[str, Any]) -> str:
    lines = ["📩 Confirmar sugestão?", "", f"Loja: {payload['store_name']}"]
    if payload.get("store_url"):
        lines.append(f"Link: {payload['store_url']}")
    if payload.get("comment"):
        lines.append(f"Comentário: {payload['comment']}")
    lines.extend(["", _CONFIRMATION_SUFFIX])
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
        f'⏸️ A missão "{title}" está ativa.\n\n'
        "Preciso pausá-la antes de editar.\n\n"
        "1 — Pausar e continuar\n"
        "2 — Cancelar\n\n"
        'Você também pode responder "sim" ou "não".'
    )


# TASK-071: menu guiado e determinístico de `/editar-missao` -- nenhuma
# função abaixo chama IA. A navegação termina sempre convergindo para o
# mesmo payload `stage_edit_mission`/`describe_edit_mission` (acima), que
# já é a confirmação final sim/não reaproveitada da TASK-069.

_MISSION_CHOICE_RETRY = "Não entendi.\n\nDigite apenas o número da missão."


def parse_single_numbered_choice(raw: str, *, count: int) -> int | None:
    """Índice zero-based (`0`..`count - 1`) de uma escolha numérica única
    (TASK-071) -- `None` se não for um único número dentro do intervalo."""
    token = raw.strip()
    if not token.isdigit():
        return None
    value = int(token)
    if not (1 <= value <= count):
        return None
    return value - 1


def describe_mission_choice_prompt(titles: Sequence[str], *, header: str) -> str:
    lines = [header, ""]
    lines.extend(f"{index + 1} — {title}" for index, title in enumerate(titles))
    lines.extend(["", "Digite o número."])
    return "\n".join(lines)


def describe_mission_choice_retry() -> str:
    return _MISSION_CHOICE_RETRY


# TASK-085: seleção numérica (única ou múltipla) de missão para comandos
# ambíguos (`cancel`/`pause`/etc. com mais de uma candidata). A resposta
# numérica já É a confirmação -- escolher números específicos é um ato
# deliberado, diferente de uma frase livre ambígua; não intercala o par
# confirmar/cancelar da TASK-058 (decisão registrada em
# `docs/tasks/TASK-085.md`, "Ponto de decisão em aberto", opção B).

_MISSION_COMMAND_CHOICE_RETRY = (
    "Não entendi.\n\nDigite o número de uma ou mais missões da lista, "
    "separados por vírgula.\n\nExemplo: 1 ou 1,3"
)


def parse_multi_numbered_choice(raw: str, *, count: int) -> tuple[int, ...] | None:
    """Índices zero-based de uma seleção múltipla (TASK-085) -- vírgula
    como separador, espaços opcionais (mesmo princípio de
    `parse_numbered_store_selection`: qualquer token não numérico ou
    fora do intervalo `1..count` invalida a resposta inteira, nunca
    aceita parcialmente). Repetição é deduplicada, preservando a ordem
    da primeira ocorrência -- aceita `"1"` (seleção única) e `"1,3"`/
    `"2, 4"` (múltipla) com a mesma função."""
    tokens = [token.strip() for token in re.split(r"[,\s]+", raw) if token.strip()]
    if not tokens:
        return None
    resolved: list[int] = []
    for token in tokens:
        if not token.isdigit():
            return None
        value = int(token)
        if not (1 <= value <= count):
            return None
        index = value - 1
        if index not in resolved:
            resolved.append(index)
    return tuple(resolved)


def stage_mission_command_choice(
    *, missions: Sequence[Mission], command: MissionCommand
) -> dict[str, Any]:
    """Grava o mapeamento número→missão exatamente como listado -- a
    seleção do usuário nunca é resolvida contra uma nova consulta
    reordenada no banco, só contra este payload."""
    return {
        "kind": "mission_command_choice",
        "command": command.value,
        "missions": [
            {
                "mission_id": str(mission.id),
                "mission_title": mission.title,
                "expected_state_version": mission.state_version,
            }
            for mission in missions
        ],
    }


def describe_mission_command_choice_prompt(
    missions: Sequence[Mission], *, command: MissionCommand
) -> str:
    verb = _COMMAND_VERBS[command]
    lines = [f"Encontrei mais de uma missão para {verb}:", ""]
    lines.extend(
        f"{index + 1} — {mission.title} — {format_mission_status(mission.status)}"
        for index, mission in enumerate(missions)
    )
    lines.extend(
        [
            "",
            "Digite o número da missão.\n"
            "Para selecionar mais de uma, separe os números por vírgula.",
            "",
            "Exemplo: 1,3",
        ]
    )
    return "\n".join(lines)


def describe_mission_command_choice_retry() -> str:
    return _MISSION_COMMAND_CHOICE_RETRY


_NO_EDITABLE_MISSION_REPLY = (
    "Você não tem nenhuma missão pausada ou ativa disponível para edição agora."
)


def describe_no_editable_mission() -> str:
    return _NO_EDITABLE_MISSION_REPLY


_EDIT_MENU_PROMPT_TEMPLATE = (
    '✏️ O que você quer editar na missão "{title}"?\n\n'
    "1 — Lojas\n"
    "2 — Preço-alvo\n\n"
    "Digite o número."
)

_EDIT_MENU_RETRY = "Não entendi.\n\n1 — Lojas\n2 — Preço-alvo\n\nDigite 1 ou 2."


def describe_edit_menu(mission_title: str) -> str:
    return _EDIT_MENU_PROMPT_TEMPLATE.format(title=mission_title)


def describe_edit_menu_retry() -> str:
    return _EDIT_MENU_RETRY


_EDIT_LOJAS_MENU_PROMPT = (
    "🏪 O que você quer fazer?\n\n"
    "1 — Adicionar lojas\n"
    "2 — Remover lojas\n\n"
    "Digite o número."
)

_EDIT_LOJAS_MENU_RETRY = (
    "Não entendi.\n\n1 — Adicionar lojas\n2 — Remover lojas\n\nDigite 1 ou 2."
)


def describe_edit_lojas_menu() -> str:
    return _EDIT_LOJAS_MENU_PROMPT


def describe_edit_lojas_menu_retry() -> str:
    return _EDIT_LOJAS_MENU_RETRY


def missing_store_options(current_sources: Sequence[str]) -> dict[str, str]:
    """TASK-071: lojas que a missão ainda não tem, numeradas a partir de 1
    na mesma ordem canônica da TASK-070."""
    missing = [
        code
        for code in _CREATE_MISSION_SOURCE_OPTIONS.values()
        if code not in current_sources
    ]
    return {str(index + 1): code for index, code in enumerate(missing)}


def current_store_options(current_sources: Sequence[str]) -> dict[str, str]:
    """TASK-071: lojas já vinculadas à missão, numeradas a partir de 1 na
    mesma ordem canônica."""
    linked = [
        code
        for code in _CREATE_MISSION_SOURCE_OPTIONS.values()
        if code in current_sources
    ]
    return {str(index + 1): code for index, code in enumerate(linked)}


def describe_store_selection_prompt(
    option_map: Mapping[str, str], *, header: str
) -> str:
    lines = [header, ""]
    lines.extend(
        f"{number} — {code.capitalize()}" for number, code in option_map.items()
    )
    lines.extend(["", "Digite os números separados por vírgula."])
    return "\n".join(lines)


_EDIT_ADD_SOURCES_HEADER = "🏪 Lojas ainda não vinculadas à missão:"
_EDIT_REMOVE_SOURCES_HEADER = "🏪 Lojas atualmente vinculadas à missão:"
_EDIT_SOURCE_SELECTION_RETRY = (
    "Não entendi essa opção.\n\nUse apenas os números mostrados, separados por vírgula."
)
_EDIT_ADD_SOURCES_NONE_MISSING = (
    "Essa missão já está vinculada a todas as lojas disponíveis."
)
_EDIT_REMOVE_SOURCES_TOO_FEW = (
    "Essa missão só tem uma loja vinculada.\n\n"
    "Não dá para remover a última loja, porque a missão precisa ter pelo menos uma."
)
_EDIT_REMOVE_SOURCES_WOULD_EMPTY = (
    "Não dá para remover todas as lojas.\n\n"
    "A missão precisa manter pelo menos uma loja vinculada.\n"
    "Escolha menos opções."
)


def describe_edit_add_sources_prompt(option_map: Mapping[str, str]) -> str:
    return describe_store_selection_prompt(option_map, header=_EDIT_ADD_SOURCES_HEADER)


def describe_edit_remove_sources_prompt(option_map: Mapping[str, str]) -> str:
    return describe_store_selection_prompt(
        option_map, header=_EDIT_REMOVE_SOURCES_HEADER
    )


def describe_edit_source_selection_retry() -> str:
    return _EDIT_SOURCE_SELECTION_RETRY


def describe_edit_add_sources_none_missing() -> str:
    return _EDIT_ADD_SOURCES_NONE_MISSING


def describe_edit_remove_sources_too_few() -> str:
    return _EDIT_REMOVE_SOURCES_TOO_FEW


def describe_edit_remove_sources_would_empty() -> str:
    return _EDIT_REMOVE_SOURCES_WOULD_EMPTY


def resolve_edit_source_selection(
    raw: str, *, option_map: Mapping[str, str]
) -> tuple[str, ...] | None:
    """TASK-071: mesma validação estrita da TASK-070 (`parse_numbered_store_selection`),
    sem atalho de "todas" -- o conjunto de opções já é dinâmico (só o que
    falta ou só o que está vinculado), então cada resposta precisa citar
    os números mostrados."""
    return parse_numbered_store_selection(
        raw, option_map=option_map, all_tokens=frozenset()
    )


_EDIT_TARGET_AMOUNT_PROMPT = (
    "🎯 Digite o novo preço-alvo em reais.\n\n"
    "Exemplos:\n300\n300,50\n\n"
    "Para remover o preço-alvo, envie 0."
)
_EDIT_TARGET_AMOUNT_RETRY = (
    "Não entendi esse valor.\n\n"
    "Digite um número, como 300 ou 300,50.\n\n"
    "Para remover o preço-alvo, envie 0."
)


def describe_edit_target_amount_prompt() -> str:
    return _EDIT_TARGET_AMOUNT_PROMPT


def describe_edit_target_amount_retry() -> str:
    return _EDIT_TARGET_AMOUNT_RETRY


def parse_target_amount_entry(raw: str) -> Decimal | None:
    """TASK-071: valor digitado diretamente, sem IA -- aceita vírgula ou
    ponto como separador decimal. `None` se não for um número finito e
    não negativo."""
    text = raw.strip().replace(",", ".")
    if not text:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return amount
