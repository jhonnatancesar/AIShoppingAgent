"""Validação manual do IntentInterpreter contra o Gemini real do perfil USER.

Sem argumentos, roda um conjunto amplo e diverso de mensagens reais —
escrita informal, gírias, erros de digitação e ordens de frase variadas —
cobrindo os quatro valores de `IntentKind`, para validar a robustez de
classificação exigida pela TASK-057. Com `--message`, valida apenas uma
mensagem pontual.
"""

import argparse
import asyncio
from datetime import UTC, datetime

from app.ai_provider import AIProviderQuotaExceeded, build_user_ai_provider_manager
from app.intent import Intent, IntentInterpreter

_REQUEST_INTERVAL_SECONDS = 60.0
_QUOTA_RETRY_ATTEMPTS = 5
_QUOTA_FALLBACK_BACKOFF_SECONDS = 60.0

_DIVERSE_MESSAGES: tuple[str, ...] = (
    # create_mission: formal, gírias, sem valor-alvo, múltiplas fontes.
    "quero uma placa de video rtx 4070 ate 3200 na pichau ou terabyte",
    "bah to atras de um monitor gamer curvo, uns 1500 conto, pode ser em qualquer loja",
    "cria uma missao pra mim de fone bluetooth ate 300 reais",
    "preciso de um ssd nvme 1tb barato, sem valor definido mesmo",
    "bora comprar um teclado mecanico rgb la na amazon, add uns 450 pila",
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


async def _interpret_with_quota_retry(
    interpreter: IntentInterpreter, message: str
) -> Intent:
    for attempt in range(1, _QUOTA_RETRY_ATTEMPTS + 1):
        try:
            return await interpreter.interpret(message)
        except AIProviderQuotaExceeded as error:
            if attempt == _QUOTA_RETRY_ATTEMPTS:
                raise
            backoff = _QUOTA_FALLBACK_BACKOFF_SECONDS
            if error.quota_reset_at is not None:
                remaining = (error.quota_reset_at - datetime.now(UTC)).total_seconds()
                backoff = max(backoff, remaining)
            print(
                f"cota do Gemini excedida (tentativa {attempt}/{_QUOTA_RETRY_ATTEMPTS}); "
                f"aguardando {backoff:.0f}s antes de repetir a mesma mensagem"
            )
            await asyncio.sleep(backoff)
    raise AssertionError("unreachable")


async def validate(
    message: str, *, interpreter: IntentInterpreter | None = None
) -> None:
    if interpreter is None:
        interpreter = IntentInterpreter(build_user_ai_provider_manager())

    intent = await _interpret_with_quota_retry(interpreter, message)

    print(f"mensagem: {message!r}")
    print(f"kind: {intent.kind.value}")
    print(f"command: {intent.command.value if intent.command else None}")
    print(f"search_query: {intent.parameters.search_query}")
    print(
        f"target: {intent.parameters.target_amount} {intent.parameters.target_currency}"
    )
    print(f"sources: {intent.parameters.sources}")
    print(f"mission_reference: {intent.parameters.mission_reference}")


async def validate_all(messages: tuple[str, ...]) -> None:
    interpreter = IntentInterpreter(build_user_ai_provider_manager())
    for index, message in enumerate(messages, start=1):
        print(f"--- [{index}/{len(messages)}] ---")
        await validate(message, interpreter=interpreter)
        print()
        if index < len(messages):
            await asyncio.sleep(_REQUEST_INTERVAL_SECONDS)


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
    args = parser.parse_args()

    if args.message is not None:
        asyncio.run(validate(args.message))
    else:
        asyncio.run(validate_all(_DIVERSE_MESSAGES))


if __name__ == "__main__":
    main()
