"""Webhook HTTP que recebe atualizações reais do Telegram.

A rota autentica a entrega, extrai a mensagem de texto quando existir e a
traduz em um `Intent` via `TelegramIntentAdapter` (TASK-033). A partir da
TASK-035, ela também resolve a identidade do usuário (TASK-056), executa a
ação de missão correspondente ao `Intent` e responde ao Telegram — sem
teclado interativo e sem as notificações proativas orientadas a evento, que
continuam reservadas à TASK-036.
"""

import logging
import secrets
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Header, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.ai_provider import AIProviderError, build_user_ai_provider_manager
from app.core.config import Settings, get_settings
from app.database.dependency import get_session
from app.intent import Intent, IntentInterpreter, IntentKind
from app.missions.query import (
    MissionReferenceError,
    find_missions_by_reference,
    list_missions_for_user,
    resolve_mission_for_command,
)
from app.missions.service import (
    InvalidMissionTransitionError,
    MissionNotFoundError,
    MissionTransitionConditionError,
    MissionVersionConflictError,
    create_mission_from_criteria,
    transition_mission,
)
from app.telegram.adapter import TelegramIntentAdapter
from app.telegram.bot_api import send_message
from app.telegram.contracts import TelegramContractError, TelegramMessage
from app.users.models import User
from app.users.service import get_or_create_telegram_user

logger = logging.getLogger("app.telegram")

router = APIRouter(tags=["telegram"])

_UNKNOWN_REPLY = (
    "Não entendi seu pedido. Você pode:\n\n"
    '• Criar uma missão (ex.: "quero uma RTX 4060 até R$ 2500 na Kabum")\n'
    "• Consultar suas missões\n"
    "• Dar um comando (pausar, retomar, concluir ou cancelar uma missão)"
)


class MissionIntentError(ValueError):
    """Um `Intent` de missão não trouxe os dados mínimos para agir."""


_KNOWN_DISPATCH_ERRORS = (
    MissionNotFoundError,
    MissionVersionConflictError,
    InvalidMissionTransitionError,
    MissionTransitionConditionError,
    MissionReferenceError,
    MissionIntentError,
)


class _TelegramChat(BaseModel):
    id: int


class _TelegramSender(BaseModel):
    id: int
    first_name: str


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
        "Autentica uma atualização real do Telegram, traduz sua mensagem de "
        "texto em uma intenção estruturada, executa a ação de missão "
        "correspondente e responde ao usuário."
    ),
    response_description="Atualização autenticada e processada.",
)
async def receive_telegram_webhook(
    update: TelegramUpdate,
    x_telegram_bot_api_secret_token: Annotated[str | None, Header()] = None,
    adapter: TelegramIntentAdapter = Depends(get_telegram_intent_adapter),
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_session),
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
            intent = await adapter.interpret(message)
        except TelegramContractError, AIProviderError:
            logger.warning(
                "telegram_webhook_intent_failed",
                extra={"telegram_chat_id": message.chat_id},
            )
        else:
            await _handle_intent(
                intent,
                message=message,
                first_name=update.message.from_.first_name,
                session=session,
                settings=settings,
            )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _handle_intent(
    intent: Intent,
    *,
    message: TelegramMessage,
    first_name: str,
    session: Session,
    settings: Settings,
) -> None:
    user = get_or_create_telegram_user(
        session,
        telegram_user_id=message.user_id,
        display_name=first_name,
    )
    try:
        reply = _dispatch_intent(intent, session=session, user=user)
    except _KNOWN_DISPATCH_ERRORS as error:
        logger.warning(
            "telegram_webhook_mission_failed",
            extra={
                "telegram_chat_id": message.chat_id,
                "mission_error": type(error).__name__,
            },
        )
        reply = str(error)

    if settings.telegram_bot_token is not None:
        await send_message(
            message.chat_id, reply, bot_token=settings.telegram_bot_token
        )


def _dispatch_intent(intent: Intent, *, session: Session, user: User) -> str:
    if intent.kind is IntentKind.CREATE_MISSION:
        return _handle_create_mission(intent, session=session, user=user)
    if intent.kind is IntentKind.QUERY_MISSION:
        return _handle_query_mission(intent, session=session, user=user)
    if intent.kind is IntentKind.MISSION_COMMAND:
        return _handle_mission_command(intent, session=session, user=user)
    return _UNKNOWN_REPLY


def _handle_create_mission(intent: Intent, *, session: Session, user: User) -> str:
    search_query = intent.parameters.search_query
    if not search_query:
        raise MissionIntentError(
            "Não entendi o que você quer buscar. Pode detalhar o produto?"
        )
    mission, sources = create_mission_from_criteria(
        session,
        user_id=user.id,
        search_query=search_query,
        target_amount=intent.parameters.target_amount,
        target_currency=intent.parameters.target_currency,
        source_codes=intent.parameters.sources,
        requested_at=intent.interpreted_at,
    )
    return (
        f'Missão "{mission.title}" criada e ativa! Buscando em: {", ".join(sources)}.'
    )


def _handle_query_mission(intent: Intent, *, session: Session, user: User) -> str:
    reference = intent.parameters.mission_reference
    if reference:
        missions = find_missions_by_reference(
            session, user_id=user.id, reference=reference
        )
    else:
        missions = list_missions_for_user(session, user_id=user.id)

    if not missions:
        return "Você ainda não tem nenhuma missão registrada."
    lines = [f"• {mission.title} — {mission.status.value}" for mission in missions]
    return "\n".join(lines)


def _handle_mission_command(intent: Intent, *, session: Session, user: User) -> str:
    mission = resolve_mission_for_command(
        session,
        user_id=user.id,
        reference=intent.parameters.mission_reference,
    )
    transition = transition_mission(
        session,
        mission_id=mission.id,
        command=intent.command,
        expected_state_version=mission.state_version,
        actor_type="telegram",
        actor_id=user.id,
    )
    return f'"{mission.title}" agora está {transition.to_status.value}.'


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
