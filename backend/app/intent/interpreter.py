"""Interpretador de intenção baseado exclusivamente no AIProviderManager.

O interpretador traduz uma mensagem livre do usuário em um `Intent`
estruturado. Ele não conhece Telegram nem qualquer outro canal, não decide
nem executa comandos de missão e nunca inventa um comando ou parâmetro que
não esteja claramente presente na mensagem: qualquer resposta fora do
contrato esperado cai em `IntentKind.UNKNOWN`.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from app.ai_provider import (
    AIMessage,
    AIMessageRole,
    AIProviderManager,
    AIRequest,
)
from app.intent.contracts import Intent, IntentError, IntentKind, IntentParameters
from app.missions.models import MissionCommand
from app.users.models import UserRole

PURPOSE = "interpret_purchase_intent"

_PARSING_ERRORS = (IntentError, ValueError, TypeError, KeyError, ArithmeticError)

_ALLOWED_RESPONSE_KEYS = frozenset({"kind", "command", "parameters"})
_ALLOWED_PARAMETER_KEYS = frozenset(
    {
        "search_query",
        "target_amount",
        "target_currency",
        "sources",
        "mission_reference",
        "clear_target",
    }
)

_SYSTEM_PROMPT = (
    "Você traduz uma única mensagem de um usuário do AIShoppingAgent em uma "
    "intenção estruturada. Você nunca executa nem confirma nenhuma ação; "
    "apenas classifica a mensagem.\n\n"
    "Responda somente com um objeto JSON válido, sem texto adicional, "
    "comentários ou blocos de código, exatamente neste formato:\n"
    '{"kind": "create_mission" | "query_mission" | "mission_command" | '
    '"edit_mission" | "unknown", '
    '"command": "activate" | "pause" | "resume" | "complete" | "cancel" | "expire" | null, '
    '"parameters": {'
    '"search_query": string ou null, '
    '"target_amount": string decimal (ex.: "1500.00") ou null, '
    '"target_currency": string ISO 4217 de 3 letras maiúsculas (ex.: "BRL") ou null, '
    '"sources": lista com zero ou mais valores entre "pichau", "terabyte", "amazon", "kabum", '
    '"mission_reference": string ou null, '
    '"clear_target": true ou false}}\n\n'
    'Use "kind": "mission_command" somente com um "command" entre os seis '
    "listados, que representam comandos já existentes do ciclo de vida da "
    'missão. Use "kind": "create_mission" para pedidos de criar ou iniciar '
    'uma nova missão de compra. Use "kind": "query_mission" para pedidos de '
    "consultar, listar ou acompanhar missões existentes. Use "
    '"kind": "edit_mission" para pedidos de mudar as lojas e/ou o '
    "preço-alvo de uma missão que já existe, sem criar uma nova — sempre "
    'preencha "mission_reference" e ao menos um de "sources" '
    "(a nova lista completa de lojas desejadas, não só a diferença), "
    '"target_amount"/"target_currency" (o novo alvo) ou '
    '"clear_target": true (quando a pessoa pede para remover o alvo e só '
    'acompanhar preços). Nunca use "true" em "clear_target" ao mesmo tempo '
    'que preenche "target_amount" — são pedidos contraditórios; prefira '
    '"unknown" nesse caso. Use '
    '"kind": "unknown" sempre que a mensagem não corresponder com segurança '
    "a nenhuma dessas opções. Nunca invente um comando, fonte, valor ou "
    "moeda que não esteja claramente presente na mensagem; nesse caso, "
    "prefira null, uma lista vazia ou false.\n\n"
    "O usuário escreve como fala: erros de digitação, abreviações, gírias "
    "regionais, falta de acentuação ou de pontuação e qualquer ordem das "
    "informações na frase nunca impedem a classificação. Interprete o "
    "sentido da mensagem, não sua forma exata ou uma ordem fixa de palavras. "
    "Nunca exija que o usuário siga um padrão de escrita; classifique "
    '"unknown" apenas quando o sentido da mensagem, e não apenas sua forma, '
    "for realmente ambíguo ou fora do domínio de compras.\n\n"
    'Ao preencher "search_query", corrija erro de digitação óbvio e '
    "complete nome de marca/modelo reconhecível para a forma usual "
    '(ex.: "logitek" -> "logitech", "9950x3d" -> "ryzen 9 9950x3d"). Essa '
    "correção é só do nome do produto/marca já citado — nunca adicione "
    "especificação, cor, variante, quantidade ou característica que a "
    "pessoa não mencionou; isso continua proibido pela regra de nunca "
    "inventar.\n\n"
    "Exemplos de mensagens reais e a resposta esperada, apenas para ilustrar "
    "o padrão — generalize o critério, nunca copie um exemplo literalmente:\n\n"
    'Mensagem: "eu qria uma rtx 4060 ate uns 2500 pila na kabum, bora"\n'
    'Resposta: {"kind": "create_mission", "command": null, "parameters": '
    '{"search_query": "rtx 4060", "target_amount": "2500.00", '
    '"target_currency": "BRL", "sources": ["kabum"], "mission_reference": null, '
    '"clear_target": false}}\n\n'
    'Mensagem: "ate 3000 reais me acha um notebook gamer, comeca a procurar ai"\n'
    'Resposta: {"kind": "create_mission", "command": null, "parameters": '
    '{"search_query": "notebook gamer", "target_amount": "3000.00", '
    '"target_currency": "BRL", "sources": [], "mission_reference": null, '
    '"clear_target": false}}\n\n'
    'Mensagem: "quero um 9950x3d ate 3500"\n'
    'Resposta: {"kind": "create_mission", "command": null, "parameters": '
    '{"search_query": "ryzen 9 9950x3d", "target_amount": "3500.00", '
    '"target_currency": "BRL", "sources": [], "mission_reference": null, '
    '"clear_target": false}}\n\n'
    'Mensagem: "procura um mouse logitek barato"\n'
    'Resposta: {"kind": "create_mission", "command": null, "parameters": '
    '{"search_query": "mouse logitech", "target_amount": null, '
    '"target_currency": null, "sources": [], "mission_reference": null, '
    '"clear_target": false}}\n\n'
    'Mensagem: "e ai cade minha missao do notebook, achou algo?"\n'
    'Resposta: {"kind": "query_mission", "command": null, "parameters": '
    '{"search_query": null, "target_amount": null, "target_currency": null, '
    '"sources": [], "mission_reference": "notebook", "clear_target": false}}\n\n'
    'Mensagem: "pausa ai a missao do teclado mecanico pfvr"\n'
    'Resposta: {"kind": "mission_command", "command": "pause", "parameters": '
    '{"search_query": null, "target_amount": null, "target_currency": null, '
    '"sources": [], "mission_reference": "teclado mecanico", "clear_target": false}}\n\n'
    'Mensagem: "cancela essa busca do monitor curvo, nao quero mais nao"\n'
    'Resposta: {"kind": "mission_command", "command": "cancel", "parameters": '
    '{"search_query": null, "target_amount": null, "target_currency": null, '
    '"sources": [], "mission_reference": "monitor curvo", "clear_target": false}}\n\n'
    'Mensagem: "troca a missao do teclado pra kabum e pichau, deixa o alvo em 300"\n'
    'Resposta: {"kind": "edit_mission", "command": null, "parameters": '
    '{"search_query": null, "target_amount": "300.00", "target_currency": "BRL", '
    '"sources": ["kabum", "pichau"], "mission_reference": "teclado", '
    '"clear_target": false}}\n\n'
    'Mensagem: "tira o preco alvo da missao do monitor, so quero acompanhar os precos"\n'
    'Resposta: {"kind": "edit_mission", "command": null, "parameters": '
    '{"search_query": null, "target_amount": null, "target_currency": null, '
    '"sources": [], "mission_reference": "monitor", "clear_target": true}}\n\n'
    'Mensagem: "bom dia, tudo certo?"\n'
    'Resposta: {"kind": "unknown", "command": null, "parameters": '
    '{"search_query": null, "target_amount": null, "target_currency": null, '
    '"sources": [], "mission_reference": null, "clear_target": false}}'
)


class IntentInterpreter:
    """Traduz mensagens livres em intenções estruturadas, sem lógica de domínio."""

    def __init__(self, manager: AIProviderManager) -> None:
        self._manager = manager

    @property
    def manager(self) -> AIProviderManager:
        return self._manager

    async def interpret(
        self,
        message: str,
        *,
        requested_at: datetime | None = None,
        profile: UserRole = UserRole.USER,
    ) -> Intent:
        """Interpreta uma mensagem livre via `AIProviderManager`.

        `profile` sempre é `USER` no caminho de produção (webhook do
        Telegram); só ferramentas de validação manual passam `ADMIN`/`DEV`,
        para não consumir a cota gratuita compartilhada do perfil `USER`.
        """
        if not isinstance(message, str) or not message.strip():
            raise IntentError("message must not be blank")

        moment = requested_at or datetime.now(UTC)
        request = AIRequest(
            request_id=uuid4(),
            profile=profile,
            purpose=PURPOSE,
            messages=(
                AIMessage(AIMessageRole.SYSTEM, _SYSTEM_PROMPT),
                AIMessage(AIMessageRole.USER, message),
            ),
            requested_at=moment,
        )
        response = await self._manager.generate(request)
        return parse_intent_response(
            response.content,
            correlation_id=request.request_id,
            raw_message=message,
            interpreted_at=response.finished_at,
        )


def parse_intent_response(
    content: str,
    *,
    correlation_id: Any,
    raw_message: str,
    interpreted_at: datetime,
) -> Intent:
    """Faz parsing estrito da resposta do provedor.

    Qualquer resposta que não siga exatamente o contrato esperado — JSON
    inválido, campos desconhecidos, valores fora do vocabulário fechado ou
    combinação inconsistente entre `kind` e `command` — resulta em uma
    intenção `UNKNOWN`, nunca em um comando inventado.
    """
    try:
        return _parse_intent_response(
            content,
            correlation_id=correlation_id,
            raw_message=raw_message,
            interpreted_at=interpreted_at,
        )
    except _PARSING_ERRORS:
        return Intent(
            correlation_id=correlation_id,
            kind=IntentKind.UNKNOWN,
            raw_message=raw_message,
            interpreted_at=interpreted_at,
        )


def _parse_intent_response(
    content: str,
    *,
    correlation_id: Any,
    raw_message: str,
    interpreted_at: datetime,
) -> Intent:
    payload = json.loads(content)
    if not isinstance(payload, dict) or set(payload) - _ALLOWED_RESPONSE_KEYS:
        raise IntentError("unexpected response shape")

    kind = IntentKind(payload.get("kind"))

    command_value = payload.get("command")
    command = MissionCommand(command_value) if command_value is not None else None

    parameters = _parse_parameters(payload.get("parameters"))

    return Intent(
        correlation_id=correlation_id,
        kind=kind,
        raw_message=raw_message,
        interpreted_at=interpreted_at,
        command=command,
        parameters=parameters,
    )


def _parse_parameters(raw: Any) -> IntentParameters:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or set(raw) - _ALLOWED_PARAMETER_KEYS:
        raise IntentError("unexpected parameters shape")

    search_query = _optional_str(raw.get("search_query"))
    mission_reference = _optional_str(raw.get("mission_reference"))
    target_currency = _optional_str(raw.get("target_currency"))

    target_amount_raw = raw.get("target_amount")
    target_amount: Decimal | None = None
    if target_amount_raw is not None:
        if not isinstance(target_amount_raw, str):
            raise IntentError("target_amount must be a decimal string")
        try:
            target_amount = Decimal(target_amount_raw)
        except InvalidOperation as error:
            raise IntentError("target_amount must be a valid decimal") from error
        if not target_amount.is_finite():
            raise IntentError("target_amount must be a finite decimal")

    sources_raw = raw.get("sources") if raw.get("sources") is not None else []
    if not isinstance(sources_raw, list) or any(
        not isinstance(source, str) for source in sources_raw
    ):
        raise IntentError("sources must be a list of strings")

    clear_target_raw = raw.get("clear_target", False)
    if not isinstance(clear_target_raw, bool):
        raise IntentError("clear_target must be a bool")

    return IntentParameters(
        search_query=search_query,
        target_amount=target_amount,
        target_currency=target_currency,
        sources=tuple(sources_raw),
        mission_reference=mission_reference,
        clear_target=clear_target_raw,
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise IntentError("expected a string or null")
    return value
