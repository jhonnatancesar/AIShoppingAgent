"""Webhook HTTP que recebe atualizações reais do Telegram.

A rota autentica a entrega, extrai a mensagem de texto quando existir e a
traduz em um `Intent` via `TelegramIntentAdapter` (TASK-033). Ela não decide
nem executa nenhuma ação de domínio, e não responde ao usuário: isso pertence
às TASKs 035 e 036.
"""

import logging
import secrets
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Header, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from app.ai_provider import AIProviderError, build_user_ai_provider_manager
from app.core.config import Settings, get_settings
from app.intent import IntentInterpreter
from app.telegram.adapter import TelegramIntentAdapter
from app.telegram.contracts import TelegramContractError, TelegramMessage

logger = logging.getLogger("app.telegram")

router = APIRouter(tags=["telegram"])


class _TelegramChat(BaseModel):
    id: int


class _TelegramSender(BaseModel):
    id: int


class _TelegramIncomingMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    text: str | None = None
    date: int
    chat: _TelegramChat
    from_: _TelegramSender = Field(alias="from")


class TelegramUpdate(BaseModel):
    """Fatia mínima do `Update` do Telegram usada por este webhook."""

    model_config = ConfigDict(extra="ignore")

    update_id: int
    message: _TelegramIncomingMessage | None = None


@lru_cache
def get_telegram_intent_adapter() -> TelegramIntentAdapter:
    """Monta o adaptador uma única vez, reaproveitando o IntentInterpreter."""
    manager = build_user_ai_provider_manager()
    return TelegramIntentAdapter(IntentInterpreter(manager))


@router.post(
    "/telegram/webhook",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="receive_telegram_webhook",
    summary="Receber atualização do Telegram",
    description=(
        "Autentica uma atualização real do Telegram e traduz sua mensagem de "
        "texto em uma intenção estruturada, sem executar nem responder nada."
    ),
    response_description="Atualização autenticada e processada.",
)
async def receive_telegram_webhook(
    update: TelegramUpdate,
    x_telegram_bot_api_secret_token: Annotated[str | None, Header()] = None,
    adapter: TelegramIntentAdapter = Depends(get_telegram_intent_adapter),
    settings: Settings = Depends(get_settings),
) -> Response:
    if not _secret_matches(x_telegram_bot_api_secret_token, settings):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "error": {
                    "code": "telegram_webhook_unauthorized",
                    "message": "Assinatura do webhook inválida.",
                    "details": None,
                }
            },
        )

    message = _extract_message(update)
    if message is not None:
        try:
            await adapter.interpret(message)
        except TelegramContractError, AIProviderError:
            logger.warning(
                "telegram_webhook_intent_failed",
                extra={"telegram_chat_id": message.chat_id},
            )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _secret_matches(provided: str | None, settings: Settings) -> bool:
    expected = settings.telegram_webhook_secret
    if expected is None or provided is None:
        return False
    return secrets.compare_digest(provided, expected.get_secret_value())


def _extract_message(update: TelegramUpdate) -> TelegramMessage | None:
    incoming = update.message
    if incoming is None or not incoming.text:
        return None
    return TelegramMessage(
        chat_id=incoming.chat.id,
        user_id=incoming.from_.id,
        text=incoming.text,
        received_at=datetime.fromtimestamp(incoming.date, tz=UTC),
    )
