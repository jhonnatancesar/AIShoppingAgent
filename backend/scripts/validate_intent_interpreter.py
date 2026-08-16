"""Validação manual do IntentInterpreter contra provedores de IA reais.

Sem argumentos, roda um conjunto amplo e diverso de mensagens reais —
escrita informal, gírias, erros de digitação e ordens de frase variadas —
cobrindo os quatro valores de `IntentKind`, para validar a robustez de
classificação exigida pela TASK-057. Com `--message`, valida apenas uma
mensagem pontual.

Por padrão usa o perfil `admin` (cascata Gemini Flash → Groq, TASK-059/
DEC-050), para não consumir a cota gratuita compartilhada do perfil `user`
real. Use `--profile user` só para a confirmação final antes de considerar a
robustez validada de verdade — é o único perfil que reflete exatamente o
caminho de produção do webhook do Telegram.
"""

import argparse
import asyncio
from datetime import UTC, datetime

from app.ai_provider import (
    AIProviderManager,
    AIProviderQuotaExceeded,
    AIRequest,
    AIResponse,
    build_admin_dev_ai_provider_manager,
    build_dev_ai_provider_manager,
    build_user_ai_provider_manager,
)
from app.intent import Intent, IntentInterpreter, parse_intent_response
from app.intent.interpreter import _needs_identity_verification
from app.users.models import UserRole


class _CapturingManager:
    """TASK-083: envolve o AIProviderManager real só para registrar cada
    chamada e sua resposta, sem alterar nenhum comportamento -- usado aqui
    para diagnosticar, na validação manual, quantas chamadas reais
    aconteceram e se/como o grounding de fato ocorreu em cada uma."""

    def __init__(self, manager: AIProviderManager) -> None:
        self._manager = manager
        self.calls: list[tuple[AIRequest, AIResponse | None]] = []

    async def generate(self, request: AIRequest) -> AIResponse:
        try:
            response = await self._manager.generate(request)
        except Exception:
            self.calls.append((request, None))
            raise
        self.calls.append((request, response))
        return response


_ADMIN_REQUEST_INTERVAL_SECONDS = 5.0
_USER_REQUEST_INTERVAL_SECONDS = 60.0
_QUOTA_RETRY_ATTEMPTS = 5
_QUOTA_FALLBACK_BACKOFF_SECONDS = 60.0

_PROFILE_ROLES: dict[str, UserRole] = {
    "admin": UserRole.ADMIN,
    "dev": UserRole.DEV,
    "user": UserRole.USER,
}

_DIVERSE_MESSAGES: tuple[str, ...] = (
    # create_mission: formal, gírias, sem valor-alvo, múltiplas fontes.
    "quero uma placa de video rtx 4070 ate 3200 na pichau ou terabyte",
    "bah to atras de um monitor gamer curvo, uns 1500 conto, pode ser em qualquer loja",
    "cria uma missao pra mim de fone bluetooth ate 300 reais",
    "preciso de um ssd nvme 1tb barato, sem valor definido mesmo",
    "bora comprar um teclado mecanico rgb la na amazon, add uns 450 pila",
    # create_mission: erro de digitação/modelo abreviado no search_query
    # (TASK-074) -- espera-se correção pra forma usual, não invenção de
    # especificação nova.
    "quero um 9950x3d ate 3500",
    "procura um mouse logitek barato",
    # create_mission: canonicalização completa + model estruturado
    # (TASK-075) -- search_query começa pelo tipo, model preserva a
    # variante exata (nunca reduzida).
    "quero uma 4070 ti",
    "procura um 9800x3d",
    # query_mission: formas variadas de perguntar, sem referência explícita.
    "e ai como ta indo a busca do meu ssd?",
    "mostra o status da missao do teclado",
    "tem alguma coisa nova na minha missao de fone bluetooth?",
    "list minhas missao",
    # mission_command: cobre os seis comandos com fraseado informal.
    "ativa de novo a missao do monitor",
    "pausa a busca do ssd por enquanto",
    "volta a rodar a missao do teclado",
    "finaliza a missao do fone, ja comprei",
    "cancela a missao do monitor curvo, desisti",
    "encerra a missao do ssd que ja venceu o prazo",
    # unknown: fora do domínio de compras ou sem sentido acionável.
    "oi bom dia",
    "qual a previsao do tempo pra amanha",
    "vc pode me ajudar com uma duvida de outro assunto",
    "kkkkkk mds",
)


def _build_manager(profile_name: str) -> _CapturingManager:
    base: AIProviderManager = (
        build_user_ai_provider_manager()
        if profile_name == "user"
        else (
            build_dev_ai_provider_manager()
            if profile_name == "dev"
            else build_admin_dev_ai_provider_manager()
        )
    )
    return _CapturingManager(base)


def _request_interval(profile_name: str) -> float:
    return (
        _USER_REQUEST_INTERVAL_SECONDS
        if profile_name == "user"
        else _ADMIN_REQUEST_INTERVAL_SECONDS
    )


async def _interpret_with_quota_retry(
    interpreter: IntentInterpreter, message: str, *, role: UserRole
) -> Intent:
    for attempt in range(1, _QUOTA_RETRY_ATTEMPTS + 1):
        try:
            return await interpreter.interpret(message, profile=role)
        except AIProviderQuotaExceeded as error:
            if attempt == _QUOTA_RETRY_ATTEMPTS:
                raise
            backoff = _QUOTA_FALLBACK_BACKOFF_SECONDS
            if error.quota_reset_at is not None:
                remaining = (error.quota_reset_at - datetime.now(UTC)).total_seconds()
                backoff = max(backoff, remaining)
            print(
                f"cota excedida (tentativa {attempt}/{_QUOTA_RETRY_ATTEMPTS}); "
                f"aguardando {backoff:.0f}s antes de repetir a mesma mensagem"
            )
            await asyncio.sleep(backoff)
    raise AssertionError("unreachable")


async def validate(
    message: str,
    *,
    profile_name: str = "admin",
    interpreter: IntentInterpreter | None = None,
) -> None:
    role = _PROFILE_ROLES[profile_name]
    if interpreter is None:
        interpreter = IntentInterpreter(_build_manager(profile_name))

    manager = interpreter.manager
    calls_before = len(manager.calls) if isinstance(manager, _CapturingManager) else 0

    intent = await _interpret_with_quota_retry(interpreter, message, role=role)

    print(f"mensagem: {message!r}")
    print(f"kind: {intent.kind.value}")
    print(f"command: {intent.command.value if intent.command else None}")
    print(f"search_query (final): {intent.parameters.search_query}")
    print(f"model (final): {intent.parameters.model}")
    print(
        f"target: {intent.parameters.target_amount} {intent.parameters.target_currency}"
    )
    print(f"sources: {intent.parameters.sources}")
    print(f"mission_reference: {intent.parameters.mission_reference}")

    if isinstance(manager, _CapturingManager):
        _print_grounding_diagnostics(manager.calls[calls_before:], message)


def _print_grounding_diagnostics(
    calls: list[tuple[AIRequest, AIResponse | None]], raw_message: str
) -> None:
    """TASK-083: relata, sem alterar o comportamento de produção, quantas
    chamadas reais aconteceram e o que cada uma revela sobre a verificação
    de identidade -- primeira interpretação, motivo do gatilho (se houve
    segunda chamada) e evidência estrutural de grounding."""
    print(f"chamadas reais de IA: {len(calls)}")

    first_request, first_response = calls[0]
    if first_response is None:
        print("  [1] falhou antes de responder (ver exceção acima)")
        return

    first_intent = parse_intent_response(
        first_response.content,
        correlation_id=first_request.request_id,
        raw_message=raw_message,
        interpreted_at=first_response.finished_at,
    )
    triggered = _needs_identity_verification(raw_message, first_intent.parameters)
    print(f"  [1] purpose={first_request.purpose}")
    print(
        f"      primeira interpretação -- search_query: "
        f"{first_intent.parameters.search_query!r}"
    )
    print(f"      primeira interpretação -- model: {first_intent.parameters.model!r}")
    print(
        "      primeira interpretação -- model_confidence: "
        f"{first_intent.parameters.model_confidence!r}"
    )
    print(f"      gatilho de verificação necessário: {triggered}")

    for index, (call_request, call_response) in enumerate(calls[1:], start=2):
        if call_response is None:
            print(
                f"  [{index}] purpose={call_request.purpose} -- falhou antes de responder"
            )
            continue
        print(
            f"  [{index}] purpose={call_request.purpose} "
            f"grounding_requested={call_response.grounding_requested} "
            f"grounding_performed={call_response.grounding_performed} "
            f"fontes={len(call_response.grounding_sources)}"
        )


async def validate_all(messages: tuple[str, ...], *, profile_name: str) -> None:
    interpreter = IntentInterpreter(_build_manager(profile_name))
    interval = _request_interval(profile_name)
    for index, message in enumerate(messages, start=1):
        print(f"--- [{index}/{len(messages)}] ---")
        await validate(message, profile_name=profile_name, interpreter=interpreter)
        print()
        if index < len(messages):
            await asyncio.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--message",
        default=None,
        help=(
            "Valida uma única mensagem pontual. Sem esta opção, roda o "
            "conjunto diverso padrão cobrindo os quatro IntentKind."
        ),
    )
    parser.add_argument(
        "--profile",
        choices=sorted(_PROFILE_ROLES),
        default="admin",
        help=(
            "Perfil usado para validar (default: admin, cascata Gemini "
            "Flash/Groq). Use 'user' só para a confirmação final contra o "
            "caminho real de produção."
        ),
    )
    args = parser.parse_args()

    if args.message is not None:
        asyncio.run(validate(args.message, profile_name=args.profile))
    else:
        asyncio.run(validate_all(_DIVERSE_MESSAGES, profile_name=args.profile))


if __name__ == "__main__":
    main()
