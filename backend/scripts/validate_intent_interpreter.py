"""Validação manual do IntentInterpreter contra o Gemini real do perfil USER."""

import argparse
import asyncio

from app.ai_provider import build_user_ai_provider_manager
from app.intent import IntentInterpreter


async def validate(message: str) -> None:
    manager = build_user_ai_provider_manager()
    interpreter = IntentInterpreter(manager)

    intent = await interpreter.interpret(message)

    print(f"mensagem: {message!r}")
    print(f"kind: {intent.kind.value}")
    print(f"command: {intent.command.value if intent.command else None}")
    print(f"search_query: {intent.parameters.search_query}")
    print(
        f"target: {intent.parameters.target_amount} {intent.parameters.target_currency}"
    )
    print(f"sources: {intent.parameters.sources}")
    print(f"mission_reference: {intent.parameters.mission_reference}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--message",
        default="Quero comprar uma placa de vídeo RTX 4060 até R$ 2500 na Kabum",
    )
    args = parser.parse_args()
    asyncio.run(validate(args.message))


if __name__ == "__main__":
    main()
