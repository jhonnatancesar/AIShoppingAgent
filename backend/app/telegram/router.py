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
from decimal import Decimal
from functools import lru_cache
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.ai_provider import (
    AIProviderError,
    build_admin_dev_ai_provider_manager,
    build_user_ai_provider_manager,
)
from app.core.config import Settings, get_settings
from app.database.dependency import get_session
from app.intent import Intent, IntentInterpreter, IntentKind
from app.missions.models import MissionCommand
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
from app.telegram.confirmation import (
    ConfirmationError,
    describe_create_mission,
    describe_mission_command,
    resolve_answer,
    stage_create_mission,
    stage_mission_command,
)
from app.telegram.contracts import (
    TelegramChatType,
    TelegramContractError,
    TelegramMessage,
)
from app.telegram.notifications import remember_private_notification_chat
from app.telegram.preferences import PREFERENCES_COMMAND, handle_preferences_command
from app.users.models import User, UserRole
from app.users.registration import (
    RegistrationError,
    advance_registration,
    start_registration,
)
from app.users.service import get_or_create_telegram_user

logger = logging.getLogger("app.telegram")

router = APIRouter(tags=["telegram"])

_UNKNOWN_REPLY = (
    "Não entendi seu pedido. Não converso sobre outros assuntos — só ajudo "
    "com suas missões de compra. Você pode:\n\n"
    '• Criar uma missão (ex.: "quero uma RTX 4060 até R$ 2500 na Kabum")\n'
    "• Consultar suas missões\n"
    "• Dar um comando (pausar, retomar, concluir ou cancelar uma missão)"
)

_CADASTRO_COMMAND = "/cadastro"
_UPGRADE_COMMAND = "/upgrade"
_UPGRADE_REPLY = "🔒 Mudar de usuário/perfil — em breve."


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
    type: TelegramChatType


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
def get_telegram_intent_adapters() -> dict[UserRole, TelegramIntentAdapter]:
    """Monta um adaptador por perfil, uma única vez, reaproveitando os managers.

    `USER` fala exclusivamente com o Gemini gratuito, sem fallback;
    `ADMIN`/`DEV` compartilham a cascata do `AdminDevAIProviderManager`
    (Gemini premium, Groq opcional, Gemini gratuito — TASK-059). Qual
    adaptador é usado numa interação real depende de `User.role`
    (TASK-060), nunca de escolha do próprio usuário.
    """
    user_interpreter = IntentInterpreter(build_user_ai_provider_manager())
    admin_dev_interpreter = IntentInterpreter(build_admin_dev_ai_provider_manager())
    return {
        UserRole.USER: TelegramIntentAdapter(user_interpreter),
        UserRole.ADMIN: TelegramIntentAdapter(admin_dev_interpreter),
        UserRole.DEV: TelegramIntentAdapter(admin_dev_interpreter),
    }


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
    adapters: dict[UserRole, TelegramIntentAdapter] = Depends(
        get_telegram_intent_adapters
    ),
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
        user = get_or_create_telegram_user(
            session,
            telegram_user_id=message.user_id,
            display_name=update.message.from_.first_name,
        )
        remember_private_notification_chat(user, message)
        reply = await _handle_message(
            message, user=user, adapters=adapters, session=session
        )
        if reply is not None and settings.telegram_bot_token is not None:
            await send_message(
                message.chat_id, reply, bot_token=settings.telegram_bot_token
            )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _handle_message(
    message: TelegramMessage,
    *,
    user: User,
    adapters: dict[UserRole, TelegramIntentAdapter],
    session: Session,
) -> str | None:
    lowered = message.text.strip().lower()
    if lowered == _CADASTRO_COMMAND:
        return start_registration(user)
    if lowered == _UPGRADE_COMMAND:
        return _UPGRADE_REPLY
    if lowered == PREFERENCES_COMMAND or lowered.startswith(f"{PREFERENCES_COMMAND} "):
        return handle_preferences_command(user, lowered)
    if user.registration_step is not None:
        try:
            return advance_registration(user, answer=message.text)
        except RegistrationError as error:
            return str(error)

    if user.pending_intent is not None:
        return await _resolve_pending_intent(
            message, adapters=adapters, session=session, user=user
        )

    try:
        intent = await adapters[user.role].interpret(message, profile=user.role)
    except TelegramContractError, AIProviderError:
        logger.warning(
            "telegram_webhook_intent_failed",
            extra={"telegram_chat_id": message.chat_id},
        )
        return None

    try:
        return _dispatch_intent(intent, session=session, user=user)
    except _KNOWN_DISPATCH_ERRORS as error:
        logger.warning(
            "telegram_webhook_mission_failed",
            extra={
                "telegram_chat_id": message.chat_id,
                "mission_error": type(error).__name__,
            },
        )
        return str(error)


async def _resolve_pending_intent(
    message: TelegramMessage,
    *,
    adapters: dict[UserRole, TelegramIntentAdapter],
    session: Session,
    user: User,
) -> str:
    try:
        confirmed = await resolve_answer(
            message.text, manager=adapters[user.role].manager, profile=user.role
        )
    except ConfirmationError as error:
        return str(error)

    payload = user.pending_intent
    user.pending_intent = None
    if not confirmed:
        return "Combinado, cancelei."

    try:
        return _execute_pending_intent(payload, session=session, user=user)
    except _KNOWN_DISPATCH_ERRORS as error:
        logger.warning(
            "telegram_webhook_mission_failed",
            extra={
                "telegram_chat_id": message.chat_id,
                "mission_error": type(error).__name__,
            },
        )
        return str(error)


def _dispatch_intent(intent: Intent, *, session: Session, user: User) -> str:
    """Interpreta o `Intent` e decide a resposta.

    `create_mission` e `mission_command` mudam estado — em vez de executar
    direto, ficam "encenados" em `user.pending_intent` e só são executados
    após confirmação explícita do usuário (TASK-058). `query_mission` é
    somente leitura e continua respondendo direto.
    """
    if intent.kind is IntentKind.CREATE_MISSION:
        return _stage_create_mission(intent, user=user)
    if intent.kind is IntentKind.QUERY_MISSION:
        return _handle_query_mission(intent, session=session, user=user)
    if intent.kind is IntentKind.MISSION_COMMAND:
        return _stage_mission_command(intent, session=session, user=user)
    return _UNKNOWN_REPLY


def _stage_create_mission(intent: Intent, *, user: User) -> str:
    search_query = intent.parameters.search_query
    if not search_query:
        raise MissionIntentError(
            "Não entendi o que você quer buscar. Pode detalhar o produto?"
        )
    payload = stage_create_mission(
        search_query=search_query,
        target_amount=intent.parameters.target_amount,
        target_currency=intent.parameters.target_currency,
        sources=intent.parameters.sources,
    )
    user.pending_intent = payload
    return describe_create_mission(payload)


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


def _stage_mission_command(intent: Intent, *, session: Session, user: User) -> str:
    mission = resolve_mission_for_command(
        session,
        user_id=user.id,
        reference=intent.parameters.mission_reference,
    )
    payload = stage_mission_command(
        mission_id=mission.id,
        mission_title=mission.title,
        command=intent.command,
        expected_state_version=mission.state_version,
    )
    user.pending_intent = payload
    return describe_mission_command(payload)


def _execute_pending_intent(
    payload: dict[str, Any], *, session: Session, user: User
) -> str:
    if payload["kind"] == "create_mission":
        return _execute_create_mission(payload, session=session, user=user)
    return _execute_mission_command(payload, session=session, user=user)


def _execute_create_mission(
    payload: dict[str, Any], *, session: Session, user: User
) -> str:
    target_amount = (
        Decimal(payload["target_amount"])
        if payload["target_amount"] is not None
        else None
    )
    mission, sources = create_mission_from_criteria(
        session,
        user_id=user.id,
        search_query=payload["search_query"],
        target_amount=target_amount,
        target_currency=payload["target_currency"],
        source_codes=tuple(payload["sources"]),
        requested_at=datetime.now(UTC),
    )
    return (
        f'Missão "{mission.title}" criada e ativa! Buscando em: {", ".join(sources)}.'
    )


def _execute_mission_command(
    payload: dict[str, Any], *, session: Session, user: User
) -> str:
    transition = transition_mission(
        session,
        mission_id=UUID(payload["mission_id"]),
        command=MissionCommand(payload["command"]),
        expected_state_version=payload["expected_state_version"],
        actor_type="telegram",
        actor_id=user.id,
    )
    return f'"{payload["mission_title"]}" agora está {transition.to_status.value}.'


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
        chat_type=incoming.chat.type,
        user_id=incoming.from_.id,
        text=incoming.text,
        received_at=datetime.fromtimestamp(incoming.date, tz=UTC),
    )
