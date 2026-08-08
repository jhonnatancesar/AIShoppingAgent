"""Webhook HTTP que recebe atualizações reais do Telegram.

A rota autentica a entrega e, para mensagens de texto, aceita somente a
identidade de uma pessoa ativa em seu chat privado direto (TASK-046). Depois
aplica a autorização por papel e ownership da TASK-047, traduz a mensagem em
`Intent`, executa comandos já implementados e responde ao Telegram.
"""

import logging
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
from app.authentication.models import CredentialAction
from app.authentication.service import (
    AuthenticationError,
    AuthenticationRateLimited,
    has_active_session,
    issue_action_link,
    logout,
)
from app.authorization import (
    AuthorizationDenied,
    Permission,
    ai_profile_for_user,
    authorize,
    deny_resource_unavailable,
)
from app.core.config import Settings, get_settings
from app.database.dependency import get_session
from app.intent import Intent, IntentInterpreter, IntentKind
from app.missions.models import Mission, MissionCommand
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
from app.telegram.authentication import (
    authenticate_telegram_user,
    webhook_secret_matches,
)
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
_START_COMMAND = "/start"
_HELP_COMMAND = "/ajuda"
_PASSWORD_COMMAND = "/senha"
_LOGIN_COMMAND = "/entrar"
_LOGOUT_COMMAND = "/sair"
_RECOVERY_COMMAND = "/recuperar"
_SESSION_REQUIRED_REPLY = (
    "Sua sessão por senha não está ativa. Use /entrar. "
    "Se ainda não criou uma senha, use /senha."
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
    if not webhook_secret_matches(
        x_telegram_bot_api_secret_token, settings.telegram_webhook_secret
    ):
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
        authentication = authenticate_telegram_user(
            session,
            message=message,
            display_name=update.message.from_.first_name,
        )
        if not authentication.authenticated:
            failure = authentication.failure
            if failure is None:
                raise RuntimeError("authentication failure reason is missing")
            logger.warning(
                "telegram_authentication_rejected",
                extra={"authentication_reason": failure.value},
            )
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        user = authentication.user
        if user is None:
            raise RuntimeError("authenticated user is missing")
        try:
            authorize(session, user, Permission.TELEGRAM_INTERACT)
        except AuthorizationDenied as error:
            _log_authorization_denial(error, user)
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        try:
            reply = await _handle_message(
                message,
                user=user,
                adapters=adapters,
                session=session,
                auth_public_base_url=settings.auth_public_base_url,
            )
        except AuthorizationDenied as error:
            _log_authorization_denial(error, user)
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        remember_private_notification_chat(user, message)
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
    auth_public_base_url: str,
) -> str | None:
    lowered = message.text.strip().lower()
    if lowered in {_START_COMMAND, _HELP_COMMAND}:
        return (
            "Eu acompanho suas missões de compra. Use /cadastro para completar "
            "seu perfil, /senha para criar sua senha e /entrar para autenticar."
        )
    if lowered == _CADASTRO_COMMAND:
        authorize(session, user, Permission.PROFILE_MANAGE)
        return start_registration(user)
    if lowered in {_PASSWORD_COMMAND, _LOGIN_COMMAND, _RECOVERY_COMMAND}:
        authorize(session, user, Permission.PROFILE_MANAGE)
        return _authentication_link_reply(
            lowered,
            user=user,
            session=session,
            public_base_url=auth_public_base_url,
        )
    if user.registration_step is not None:
        authorize(session, user, Permission.PROFILE_MANAGE)
        try:
            return advance_registration(user, answer=message.text)
        except RegistrationError as error:
            return str(error)
    if user.telegram_user_id is None or not has_active_session(
        session,
        user_id=user.id,
        telegram_user_id=user.telegram_user_id,
    ):
        return _SESSION_REQUIRED_REPLY
    if lowered == _LOGOUT_COMMAND:
        logout(session, user=user)
        return "Sessão encerrada. Use /entrar quando quiser acessar novamente."
    if lowered == _UPGRADE_COMMAND:
        authorize(session, user, Permission.PROFILE_MANAGE)
        return _UPGRADE_REPLY
    if lowered == PREFERENCES_COMMAND or lowered.startswith(f"{PREFERENCES_COMMAND} "):
        authorize(session, user, Permission.NOTIFICATION_PREFERENCES_MANAGE)
        return handle_preferences_command(user, lowered)
    if user.pending_intent is not None:
        return await _resolve_pending_intent(
            message, adapters=adapters, session=session, user=user
        )

    authorize(session, user, Permission.AI_INTERPRET)
    profile = ai_profile_for_user(session, user)
    try:
        intent = await adapters[profile].interpret(message, profile=profile)
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


def _authentication_link_reply(
    command: str,
    *,
    user: User,
    session: Session,
    public_base_url: str,
) -> str:
    if not user.username:
        return "Complete primeiro seu nome de usuário com /cadastro."
    action = {
        _LOGIN_COMMAND: CredentialAction.LOGIN,
        _RECOVERY_COMMAND: CredentialAction.RECOVER_PASSWORD,
    }.get(command)
    if action is None:
        from app.authentication.models import UserCredential

        action = (
            CredentialAction.CHANGE_PASSWORD
            if session.get(UserCredential, user.id) is not None
            else CredentialAction.SET_PASSWORD
        )
    try:
        issued = issue_action_link(
            session,
            user=user,
            action=action,
            public_base_url=public_base_url,
        )
    except AuthenticationRateLimited:
        return "Muitas solicitações. Tente novamente mais tarde."
    except AuthenticationError:
        return "Não foi possível gerar o link. Verifique seu cadastro."
    labels = {
        CredentialAction.LOGIN: "Entrar",
        CredentialAction.SET_PASSWORD: "Criar senha",
        CredentialAction.CHANGE_PASSWORD: "Alterar senha",
        CredentialAction.RECOVER_PASSWORD: "Recuperar senha",
    }
    return (
        f"{labels[action]}: {issued.url}\n\n"
        "O link é pessoal, de uso único e expira em 10 minutos."
    )


async def _resolve_pending_intent(
    message: TelegramMessage,
    *,
    adapters: dict[UserRole, TelegramIntentAdapter],
    session: Session,
    user: User,
) -> str:
    permission = (
        Permission.MISSION_CREATE
        if user.pending_intent.get("kind") == "create_mission"
        else Permission.MISSION_TRANSITION
    )
    authorize(session, user, permission)
    profile = ai_profile_for_user(session, user)
    try:
        confirmed = await resolve_answer(
            message.text, manager=adapters[profile].manager, profile=profile
        )
    except ConfirmationError as error:
        return str(error)

    payload = user.pending_intent
    if not confirmed:
        user.pending_intent = None
        return "Combinado, cancelei."

    try:
        reply = _execute_pending_intent(payload, session=session, user=user)
    except AuthorizationDenied:
        raise
    except _KNOWN_DISPATCH_ERRORS as error:
        user.pending_intent = None
        logger.warning(
            "telegram_webhook_mission_failed",
            extra={
                "telegram_chat_id": message.chat_id,
                "mission_error": type(error).__name__,
            },
        )
        return str(error)
    except Exception:
        user.pending_intent = None
        raise
    user.pending_intent = None
    return reply


def _dispatch_intent(intent: Intent, *, session: Session, user: User) -> str:
    """Interpreta o `Intent` e decide a resposta.

    `create_mission` e `mission_command` mudam estado — em vez de executar
    direto, ficam "encenados" em `user.pending_intent` e só são executados
    após confirmação explícita do usuário (TASK-058). `query_mission` é
    somente leitura e continua respondendo direto.
    """
    if intent.kind is IntentKind.CREATE_MISSION:
        authorize(session, user, Permission.MISSION_CREATE)
        return _stage_create_mission(intent, user=user)
    if intent.kind is IntentKind.QUERY_MISSION:
        authorize(session, user, Permission.MISSION_READ)
        return _handle_query_mission(intent, session=session, user=user)
    if intent.kind is IntentKind.MISSION_COMMAND:
        authorize(session, user, Permission.MISSION_TRANSITION)
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
    authorize(session, user, Permission.MISSION_CREATE)
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
    authorize(
        session,
        user,
        Permission.MISSION_TRANSITION,
        resource_type="mission",
        resource_id=UUID(payload["mission_id"]),
    )
    mission_id = UUID(payload["mission_id"])
    mission = session.get(Mission, mission_id)
    if mission is None or mission.user_id != user.id:
        deny_resource_unavailable(
            session,
            user,
            Permission.MISSION_TRANSITION,
            resource_type="mission",
            resource_id=mission_id,
        )
    transition = transition_mission(
        session,
        mission_id=mission_id,
        command=MissionCommand(payload["command"]),
        expected_state_version=payload["expected_state_version"],
        actor_type="telegram",
        actor_id=user.id,
    )
    return f'"{payload["mission_title"]}" agora está {transition.to_status.value}.'


def _log_authorization_denial(error: AuthorizationDenied, user: User) -> None:
    role = user.role.value if isinstance(user.role, UserRole) else "unknown"
    logger.warning(
        "telegram_authorization_denied",
        extra={
            "authorization_permission": error.permission.value,
            "authorization_reason": error.reason.value,
            "authorization_role": role,
        },
    )


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
