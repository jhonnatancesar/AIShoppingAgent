"""Interpretador de intenção baseado exclusivamente no AIProviderManager.

O interpretador traduz uma mensagem livre do usuário em um `Intent`
estruturado. Ele não conhece Telegram nem qualquer outro canal, não decide
nem executa comandos de missão e nunca inventa um comando ou parâmetro que
não esteja claramente presente na mensagem: qualquer resposta fora do
contrato esperado cai em `IntentKind.UNKNOWN`.
"""

import dataclasses
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from app.ai_provider import (
    AIMessage,
    AIMessageRole,
    AIProviderError,
    AIProviderManager,
    AIRequest,
)
from app.intent.contracts import Intent, IntentError, IntentKind, IntentParameters
from app.intent.nomenclature import (
    check_known_family_contradiction,
    detect_unproven_enrichment,
)
from app.missions.models import MissionCommand
from app.users.models import UserRole

PURPOSE = "interpret_purchase_intent"
VERIFY_PURPOSE = "verify_product_identity"
"""TASK-083: propósito da segunda chamada, estritamente limitada a
verificar/corrigir a identidade de um produto já identificado -- nunca
uma nova interpretação completa da intenção original."""

_PARSING_ERRORS = (IntentError, ValueError, TypeError, KeyError, ArithmeticError)

_ALLOWED_RESPONSE_KEYS = frozenset({"kind", "command", "parameters"})
_ALLOWED_PARAMETER_KEYS = frozenset(
    {
        "search_query",
        "model",
        "model_confidence",
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
    '"model": string ou null, '
    '"model_confidence": "alta" | "baixa" | null, '
    '"target_amount": string decimal (ex.: "1500.00") ou null, '
    '"target_currency": string ISO 4217 de 3 letras maiúsculas (ex.: "BRL") ou null, '
    '"sources": lista com zero ou mais valores entre "pichau", "terabyte", "amazon", "kabum", "magalu", "mercadolivre", '
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
    "complete nome de marca/modelo reconhecível para a forma usual. "
    "Quando o produto tiver um modelo específico e você conseguir "
    "identificar com segurança o tipo do produto (o que ele é, ex.: "
    "processador, placa de vídeo, mouse), monte uma descrição canônica "
    "completa, na ordem Tipo Marca Linha/Família Modelo, do jeito que o "
    "produto aparece de verdade nas lojas -- comece pelo TIPO (ex.: "
    '"9950x3d" -> "Processador AMD Ryzen 9 9950X3D", nunca "AMD '
    'Processador Ryzen 9 9950X3D"). Erro de digitação de marca também é '
    'corrigido mesmo sem modelo específico (ex.: "logitek" -> '
    '"logitech"). Essa correção é só do nome do produto/marca/tipo já '
    "identificável — nunca adicione especificação, cor, variante, "
    "quantidade ou característica que a pessoa não mencionou; isso "
    "continua proibido pela regra de nunca inventar.\n\n"
    'Preencha "model" com o modelo/variante completo do produto quando '
    "houver um identificável com segurança, preservando exatamente "
    'qualquer sufixo de variante que mudar o produto (ex.: "Ti", '
    '"SUPER", "XT", "XTX", "GRE") -- nunca reduza "RTX 4070 Ti" para '
    '"4070", nunca invente ou complete uma variante que a pessoa não '
    'mencionou. Sem modelo específico identificável (ex.: "notebook '
    'gamer", "mouse sem fio"), use null -- não force um modelo.\n\n'
    'Preencha "model_confidence" sempre que "model" for preenchido, e '
    'use null quando "model" for null. Use "alta" quando a marca/linha/'
    "família do produto for certa -- porque o usuário já escreveu isso "
    "na mensagem, ou porque o padrão do código não deixa dúvida (só "
    'existe uma marca/linha possível para aquele código). Use "baixa" '
    "quando você estiver completando marca, linha ou família só a "
    "partir do próprio código, sem confirmação direta na mensagem, e "
    "existir risco real de confundir com uma variante semelhante da "
    "mesma geração (ex.: linhas irmãs como Ryzen 7 e Ryzen 9 dentro da "
    'mesma família X3D). Continue preenchendo "search_query" com sua '
    'melhor estimativa mesmo quando marcar "baixa" -- nunca deixe de '
    "responder por incerteza.\n\n"
    "Exemplos de mensagens reais e a resposta esperada, apenas para ilustrar "
    "o padrão — generalize o critério, nunca copie um exemplo literalmente:\n\n"
    'Mensagem: "eu qria uma rtx 4060 ate uns 2500 pila na kabum, bora"\n'
    'Resposta: {"kind": "create_mission", "command": null, "parameters": '
    '{"search_query": "Placa de Vídeo NVIDIA RTX 4060", "model": "RTX 4060", '
    '"model_confidence": "alta", '
    '"target_amount": "2500.00", '
    '"target_currency": "BRL", "sources": ["kabum"], "mission_reference": null, '
    '"clear_target": false}}\n\n'
    'Mensagem: "ate 3000 reais me acha um notebook gamer, comeca a procurar ai"\n'
    'Resposta: {"kind": "create_mission", "command": null, "parameters": '
    '{"search_query": "notebook gamer", "model": null, "model_confidence": null, '
    '"target_amount": "3000.00", '
    '"target_currency": "BRL", "sources": [], "mission_reference": null, '
    '"clear_target": false}}\n\n'
    'Mensagem: "quero um 9950x3d ate 3500"\n'
    'Resposta: {"kind": "create_mission", "command": null, "parameters": '
    '{"search_query": "Processador AMD Ryzen 9 9950X3D", "model": "9950X3D", '
    '"model_confidence": "baixa", '
    '"target_amount": "3500.00", '
    '"target_currency": "BRL", "sources": [], "mission_reference": null, '
    '"clear_target": false}}\n\n'
    'Mensagem: "me acha um 9800x3d pfvr"\n'
    'Resposta: {"kind": "create_mission", "command": null, "parameters": '
    '{"search_query": "AMD Ryzen 7 9800X3D", "model": "9800X3D", '
    '"model_confidence": "baixa", '
    '"target_amount": null, '
    '"target_currency": null, "sources": [], "mission_reference": null, '
    '"clear_target": false}}\n\n'
    'Mensagem: "quero uma 4070 ti"\n'
    'Resposta: {"kind": "create_mission", "command": null, "parameters": '
    '{"search_query": "Placa de Vídeo NVIDIA RTX 4070 Ti", "model": "RTX 4070 Ti", '
    '"model_confidence": "alta", '
    '"target_amount": null, '
    '"target_currency": null, "sources": [], "mission_reference": null, '
    '"clear_target": false}}\n\n'
    'Mensagem: "procura um mouse logitek barato"\n'
    'Resposta: {"kind": "create_mission", "command": null, "parameters": '
    '{"search_query": "mouse logitech", "model": null, "model_confidence": null, '
    '"target_amount": null, '
    '"target_currency": null, "sources": [], "mission_reference": null, '
    '"clear_target": false}}\n\n'
    'Mensagem: "e ai cade minha missao do notebook, achou algo?"\n'
    'Resposta: {"kind": "query_mission", "command": null, "parameters": '
    '{"search_query": null, "model": null, "model_confidence": null, '
    '"target_amount": null, "target_currency": null, '
    '"sources": [], "mission_reference": "notebook", "clear_target": false}}\n\n'
    'Mensagem: "pausa ai a missao do teclado mecanico pfvr"\n'
    'Resposta: {"kind": "mission_command", "command": "pause", "parameters": '
    '{"search_query": null, "model": null, "model_confidence": null, '
    '"target_amount": null, "target_currency": null, '
    '"sources": [], "mission_reference": "teclado mecanico", "clear_target": false}}\n\n'
    'Mensagem: "cancela essa busca do monitor curvo, nao quero mais nao"\n'
    'Resposta: {"kind": "mission_command", "command": "cancel", "parameters": '
    '{"search_query": null, "model": null, "model_confidence": null, '
    '"target_amount": null, "target_currency": null, '
    '"sources": [], "mission_reference": "monitor curvo", "clear_target": false}}\n\n'
    'Mensagem: "troca a missao do teclado pra kabum e pichau, deixa o alvo em 300"\n'
    'Resposta: {"kind": "edit_mission", "command": null, "parameters": '
    '{"search_query": null, "model": null, "model_confidence": null, '
    '"target_amount": "300.00", "target_currency": "BRL", '
    '"sources": ["kabum", "pichau"], "mission_reference": "teclado", '
    '"clear_target": false}}\n\n'
    'Mensagem: "tira o preco alvo da missao do monitor, so quero acompanhar os precos"\n'
    'Resposta: {"kind": "edit_mission", "command": null, "parameters": '
    '{"search_query": null, "model": null, "model_confidence": null, '
    '"target_amount": null, "target_currency": null, '
    '"sources": [], "mission_reference": "monitor", "clear_target": true}}\n\n'
    'Mensagem: "bom dia, tudo certo?"\n'
    'Resposta: {"kind": "unknown", "command": null, "parameters": '
    '{"search_query": null, "model": null, "model_confidence": null, '
    '"target_amount": null, "target_currency": null, '
    '"sources": [], "mission_reference": null, "clear_target": false}}'
)

_VERIFY_SYSTEM_PROMPT = (
    "Você verifica a identidade de um único produto de hardware, usando "
    "exclusivamente a ferramenta de busca disponível para confirmar fatos "
    "reais -- nunca complete com o seu próprio conhecimento sem "
    "confirmação. Você recebe o código/modelo já identificado de um "
    "produto e uma descrição candidata, que pode estar errada quanto à "
    "marca, linha ou família. Pesquise e confirme a que marca/linha/"
    "família esse código realmente pertence.\n\n"
    "Responda somente com um objeto JSON válido, sem texto adicional, "
    "comentários ou blocos de código, exatamente neste formato: "
    '{"search_query": string}.\n\n'
    'O valor de "search_query" deve ser a descrição canônica completa do '
    "produto, na ordem Tipo Marca Linha/Família Modelo, exatamente como "
    "ele aparece de verdade nas lojas, sempre preservando o código do "
    "modelo recebido sem alterá-lo. Nunca mantenha nem invente marca, "
    "linha ou família que a busca não confirmar com segurança -- se a "
    "busca não confirmar, responda só com o próprio código do modelo, "
    "sem acrescentar nenhuma marca, linha, família ou categoria. Você "
    "nunca decide sobre orçamento, lojas, quantidade ou qualquer outro "
    "critério -- responda apenas sobre a identidade do produto."
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
        intent = parse_intent_response(
            response.content,
            correlation_id=request.request_id,
            raw_message=message,
            interpreted_at=response.finished_at,
        )

        parameters = intent.parameters
        if parameters.model is not None:
            reason = _identity_verification_reason(message, parameters)
            if reason is not None:
                parameters = await self._verify_identity(
                    raw_message=message,
                    parameters=parameters,
                    requested_at=moment,
                    profile=profile,
                    reason=reason,
                )
            if parameters.model_confidence is not None:
                # TASK-083: model_confidence é só um sinal de acionamento
                # interno -- já foi usado acima para decidir a verificação,
                # não persiste além do IntentInterpreter (não espelha
                # nenhuma coluna de domínio).
                parameters = dataclasses.replace(parameters, model_confidence=None)

        if parameters is intent.parameters:
            return intent
        return dataclasses.replace(intent, parameters=parameters)

    async def _verify_identity(
        self,
        *,
        raw_message: str,
        parameters: IntentParameters,
        requested_at: datetime,
        profile: UserRole,
        reason: str,
    ) -> IntentParameters:
        """Uma única chamada adicional, com objetivo estritamente limitado
        a verificar/corrigir a identidade do produto -- nunca uma nova
        interpretação completa da intenção. Qualquer falha, indisponibilidade
        ou evidência insuficiente aplica o fallback seguro em vez de manter
        o `search_query` original, já classificado como suspeito. `reason`
        (TASK-083, correção de regressão) decide o que o fallback preserva
        como `display_query` -- ver `_apply_safe_fallback`."""
        model = parameters.model
        if model is None:
            raise IntentError("_verify_identity requires a model")
        candidate = parameters.search_query
        verify_request = _build_verification_request(
            raw_message=raw_message,
            model=model,
            search_query=candidate,
            requested_at=requested_at,
            profile=profile,
        )
        try:
            verify_response = await self._manager.generate(verify_request)
        except AIProviderError:
            return _apply_safe_fallback(parameters, reason=reason, candidate=candidate)

        if not (
            verify_response.grounding_requested
            and verify_response.grounding_performed
            and verify_response.grounding_sources
        ):
            return _apply_safe_fallback(parameters, reason=reason, candidate=candidate)

        verified_query = _parse_verified_search_query(verify_response.content)
        if verified_query is None:
            return _apply_safe_fallback(parameters, reason=reason, candidate=candidate)

        return dataclasses.replace(
            parameters,
            search_query=verified_query,
            display_query=None,
        )


def _identity_verification_reason(
    raw_message: str, parameters: IntentParameters
) -> str | None:
    """Gatilho econômico (TASK-083) + motivo (correção de regressão): só
    aciona verificação externa nos casos documentados -- nunca por
    padrão, nunca para busca genérica sem `model`. `None` = não precisa
    verificar. `"contradiction"` = o guardrail determinístico já provou
    que a interpretação está errada (checado primeiro -- é o sinal mais
    forte, tem prioridade mesmo se `model_confidence` também tivesse
    disparado). `"unconfirmed"` = só falta confirmação externa, a
    interpretação pode estar certa (autorrelato de baixa confiança ou
    enriquecimento não comprovado, TASK-083 original)."""
    model = parameters.model
    if model is None:
        return None
    search_query = parameters.search_query
    if (
        search_query is not None
        and check_known_family_contradiction(model, search_query) is not None
    ):
        return "contradiction"
    if parameters.model_confidence == "baixa":
        return "unconfirmed"
    if detect_unproven_enrichment(raw_message, model, search_query):
        return "unconfirmed"
    return None


def _apply_safe_fallback(
    parameters: IntentParameters, *, reason: str, candidate: str | None
) -> IntentParameters:
    """TASK-083 (correção de regressão): a identidade OPERACIONAL
    (`search_query`, usada nas lojas e por `_needs_identity_resolution`
    no `collection_worker`) nunca preserva o `search_query` original
    quando a verificação era necessária e não pôde confirmar -- reduz
    sempre ao próprio código/modelo, o único valor que o usuário
    efetivamente forneceu e que o sistema pode considerar seguro pra
    pesquisar. Isso é intencional e continua igual à TASK-083 original.

    A regressão estava em jogar fora, junto, o enriquecimento plausível
    da interpretação normal (ex.: "AMD Ryzen 9 9950X3D") mesmo quando ele
    nunca foi contradito por nenhum guardrail -- só não confirmado ainda.
    `display_query` (TASK-083, correção) preserva esse candidato só para
    apresentação/contexto (`Mission.title`), e só quando `reason ==
    "unconfirmed"`: uma contradição confirmada (`reason ==
    "contradiction"`) não tem nada de útil para preservar, nem como
    provisório -- já provou estar errada."""
    display_query = candidate if reason == "unconfirmed" else None
    return dataclasses.replace(
        parameters, search_query=parameters.model, display_query=display_query
    )


_ALLOWED_VERIFICATION_KEYS = frozenset({"search_query"})


def _build_verification_request(
    *,
    raw_message: str,
    model: str,
    search_query: str | None,
    requested_at: datetime,
    profile: UserRole,
) -> AIRequest:
    candidate = search_query or model
    user_content = (
        f'Mensagem original do usuário: "{raw_message}"\n'
        f'Código/modelo já identificado: "{model}"\n'
        f'Descrição candidata, que pode estar errada: "{candidate}"'
    )
    return AIRequest(
        request_id=uuid4(),
        profile=profile,
        purpose=VERIFY_PURPOSE,
        messages=(
            AIMessage(AIMessageRole.SYSTEM, _VERIFY_SYSTEM_PROMPT),
            AIMessage(AIMessageRole.USER, user_content),
        ),
        requested_at=requested_at,
        require_search_grounding=True,
    )


def _parse_verified_search_query(content: str) -> str | None:
    """Parsing estrito e de escopo mínimo: só aceita a chave esperada, e
    trata qualquer desvio -- JSON inválido, chave extra, valor vazio ou de
    tipo errado -- como verificação inconclusiva, nunca como erro fatal."""
    try:
        payload = json.loads(content)
    except ValueError, TypeError:
        return None
    if not isinstance(payload, dict) or set(payload) - _ALLOWED_VERIFICATION_KEYS:
        return None
    value = payload.get("search_query")
    if not isinstance(value, str) or not value.strip():
        return None
    return value


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
    model = _optional_str(raw.get("model"))
    model_confidence = _optional_str(raw.get("model_confidence"))
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
        model=model,
        model_confidence=model_confidence,
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
