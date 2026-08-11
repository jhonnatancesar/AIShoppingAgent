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
from sqlalchemy import select
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
from app.missions.models import (
    Mission,
    MissionCommand,
    MissionCriteria,
    MissionSource,
    MissionStatus,
)
from app.missions.query import (
    MissionReferenceError,
    find_missions_by_reference,
    list_missions_for_user,
    resolve_mission_for_command,
)
from app.missions.service import (
    InvalidMissionTransitionError,
    MissionEditConditionError,
    MissionNotFoundError,
    MissionTransitionConditionError,
    MissionVersionConflictError,
    create_mission_from_criteria,
    edit_mission_criteria,
    transition_mission,
)
from app.observability.metrics import observe_resilience_event
from app.privacy.notice import PRIVACY_COMMAND, privacy_notice
from app.stores.models import Store
from app.telegram.adapter import TelegramIntentAdapter
from app.telegram.authentication import (
    authenticate_telegram_user,
    webhook_secret_matches,
)
from app.telegram.bot_api import TelegramDeliveryError, send_message
from app.telegram.confirmation import (
    ConfirmationError,
    current_store_options,
    describe_create_mission,
    describe_create_mission_sources_prompt,
    describe_create_mission_sources_retry,
    describe_edit_add_sources_none_missing,
    describe_edit_add_sources_prompt,
    describe_edit_lojas_menu,
    describe_edit_lojas_menu_retry,
    describe_edit_menu,
    describe_edit_menu_retry,
    describe_edit_mission,
    describe_edit_remove_sources_prompt,
    describe_edit_remove_sources_too_few,
    describe_edit_remove_sources_would_empty,
    describe_edit_source_selection_retry,
    describe_edit_target_amount_prompt,
    describe_edit_target_amount_retry,
    describe_mission_choice_prompt,
    describe_mission_choice_retry,
    describe_mission_command,
    describe_no_editable_mission,
    describe_pause_for_edit,
    missing_store_options,
    parse_single_numbered_choice,
    parse_target_amount_entry,
    resolve_answer,
    resolve_create_mission_sources,
    resolve_edit_source_selection,
    stage_await_create_mission_sources,
    stage_create_mission,
    stage_edit_mission,
    stage_mission_command,
    stage_pause_for_edit,
)
from app.telegram.contracts import (
    TelegramChatType,
    TelegramContractError,
    TelegramMessage,
)
from app.telegram.formatting import (
    MISSION_STATUS_ICONS,
    format_mission_status,
    format_money,
    format_store_list,
)
from app.telegram.limits import reserve_telegram_update
from app.telegram.models import TelegramUpdateDisposition
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
    "• Dar um comando (pausar, retomar, concluir ou cancelar uma missão)\n"
    "• Editar lojas e/ou preço-alvo de uma missão pausada (/editar-missao)"
)

_CADASTRO_COMMAND = "/cadastro"
_CADASTRO_ALREADY_AUTHENTICATED_REPLY = (
    "✅ Você já está cadastrado e autenticado neste Telegram."
)
"""TASK-072: a checagem é sobre a sessão/identidade do Telegram
(`has_active_session`), não sobre o aparelho físico -- a mensagem evita
a palavra "dispositivo" por precisão. Bloquear aqui não altera
nenhum campo do cadastro nem `registration_step`."""
_UPGRADE_COMMAND = "/upgrade"
_UPGRADE_REPLY = "🔒 Mudar de usuário/perfil — em breve."
_START_COMMAND = "/start"
_HELP_COMMAND = "/ajuda"
_PASSWORD_COMMAND = "/senha"
_LOGIN_COMMAND = "/entrar"
_LOGOUT_COMMAND = "/sair"
_RECOVERY_COMMAND = "/recuperar"
_EDIT_MISSION_COMMAND = "/editar-missao"
_EDIT_MISSION_FREE_TEXT_REDIRECT = (
    "✏️ Editar lojas ou preço-alvo agora é sempre pelo menu guiado -- "
    f"envie {_EDIT_MISSION_COMMAND}."
)
"""TASK-071: o `IntentKind.EDIT_MISSION` continua existindo no
vocabulário do `IntentInterpreter`, mas deixou de ser executado -- o
risco identificado (a IA nunca sabe quais lojas a missão já tem, então
tratar `sources` como lista completa podia remover uma loja sem o
usuário perceber) só se resolve removendo esse caminho de entrada,
decisão explícita do usuário."""
_SESSION_REQUIRED_REPLY = (
    "🔒 Sua sessão não está ativa.\n\n"
    "Use /entrar para autenticar, ou /senha se ainda não criou uma senha."
)


class MissionIntentError(ValueError):
    """Um `Intent` de missão não trouxe os dados mínimos para agir."""


_KNOWN_DISPATCH_ERRORS = (
    MissionNotFoundError,
    MissionVersionConflictError,
    InvalidMissionTransitionError,
    MissionEditConditionError,
    MissionTransitionConditionError,
    MissionReferenceError,
    MissionIntentError,
)

_EDIT_MISSION_GUIDED_KINDS = (
    "await_edit_paused_choice",
    "await_edit_active_choice",
    "await_edit_menu_choice",
    "await_edit_lojas_choice",
    "await_edit_add_sources",
    "await_edit_remove_sources",
    "await_edit_target_amount",
)
"""TASK-071: passos de navegação do menu guiado de `/editar-missao` --
nenhum deles executa nada sozinho, só avança até o payload
`"kind": "edit_mission"` (ou `"pause_for_edit"`) já existente."""

_PENDING_INTENT_PERMISSIONS: dict[str, Permission] = {
    "create_mission": Permission.MISSION_CREATE,
    # TASK-070: mesma permissão de criar -- ainda não existe missão, só
    # falta escolher as lojas antes de seguir para a confirmação normal.
    "await_create_mission_sources": Permission.MISSION_CREATE,
    "edit_mission": Permission.MISSION_EDIT,
    # TASK-069: "pause_for_edit" executa um PAUSE de verdade -- mesma
    # permissão de qualquer outro comando de ciclo de vida.
    "pause_for_edit": Permission.MISSION_TRANSITION,
    **{kind: Permission.MISSION_EDIT for kind in _EDIT_MISSION_GUIDED_KINDS},
}
"""`"mission_command"` cai no default (`MISSION_TRANSITION`) do `.get`."""


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

    update_id: int = Field(ge=0)
    message: _TelegramIncomingMessage | None = None


@lru_cache
def get_telegram_intent_adapters() -> dict[UserRole, TelegramIntentAdapter]:
    """Monta um adaptador por perfil, uma única vez, reaproveitando os managers.

    `USER` fala exclusivamente com o Gemini gratuito, sem fallback;
    `ADMIN`/`DEV` compartilham a cascata do `AdminDevAIProviderManager`
    (Gemini Flash, depois Groq quando configurado — TASK-059/DEC-050). Qual
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
            reservation = reserve_telegram_update(
                session,
                update_id=update.update_id,
                user_id=user.id,
                accepted_per_minute=settings.telegram_rate_limit_per_minute,
                forced_disposition=TelegramUpdateDisposition.DISCARDED,
            )
            if reservation.replay:
                observe_resilience_event("webhook", "replay")
                return Response(status_code=status.HTTP_204_NO_CONTENT)
            _log_authorization_denial(error, user)
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        reservation = reserve_telegram_update(
            session,
            update_id=update.update_id,
            user_id=user.id,
            accepted_per_minute=settings.telegram_rate_limit_per_minute,
        )
        if reservation.replay:
            observe_resilience_event("webhook", "replay")
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        if reservation.disposition is TelegramUpdateDisposition.RATE_LIMITED:
            observe_resilience_event("webhook", "rate_limited")
            if reservation.warn_rate_limit and settings.telegram_bot_token is not None:
                await _send_reply_safely(
                    message.chat_id,
                    "Muitas mensagens em pouco tempo. Aguarde um minuto e tente novamente.",
                    settings=settings,
                )
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
            await _send_reply_safely(
                message.chat_id,
                reply,
                settings=settings,
            )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _send_reply_safely(chat_id: int, text: str, *, settings: Settings) -> None:
    """Não desfaz efeitos já aceitos por uma entrega externa ambígua/falha."""
    assert settings.telegram_bot_token is not None
    try:
        await send_message(
            chat_id,
            text,
            bot_token=settings.telegram_bot_token,
            timeout_seconds=settings.external_http_timeout_seconds,
            retry_after_cap_seconds=settings.retry_after_cap_seconds,
            circuit_failure_threshold=settings.circuit_failure_threshold,
            circuit_open_seconds=settings.circuit_open_seconds,
        )
    except TelegramDeliveryError as error:
        logger.warning(
            "telegram_reply_delivery_failed",
            extra={
                "telegram_delivery_code": error.code,
                "telegram_delivery_ambiguous": error.ambiguous,
            },
        )


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
    if lowered == PRIVACY_COMMAND:
        return privacy_notice()
    if lowered == _CADASTRO_COMMAND:
        authorize(session, user, Permission.PROFILE_MANAGE)
        if user.telegram_user_id is not None and has_active_session(
            session, user_id=user.id, telegram_user_id=user.telegram_user_id
        ):
            return _CADASTRO_ALREADY_AUTHENTICATED_REPLY
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
            registration_reply = advance_registration(
                user, answer=message.text, session=session
            )
        except RegistrationError as error:
            return str(error)
        if user.registration_step is not None:
            return registration_reply
        password_reply = _authentication_link_reply(
            _PASSWORD_COMMAND,
            user=user,
            session=session,
            public_base_url=auth_public_base_url,
        )
        return f"{registration_reply}\n\n{password_reply}"
    if user.telegram_user_id is None or not has_active_session(
        session,
        user_id=user.id,
        telegram_user_id=user.telegram_user_id,
    ):
        return _SESSION_REQUIRED_REPLY
    if lowered == _LOGOUT_COMMAND:
        logout(session, user=user)
        return "👋 Sessão encerrada.\n\nUse /entrar quando quiser acessar novamente."
    if lowered == _UPGRADE_COMMAND:
        authorize(session, user, Permission.PROFILE_MANAGE)
        return _UPGRADE_REPLY
    if lowered == PREFERENCES_COMMAND or lowered.startswith(f"{PREFERENCES_COMMAND} "):
        authorize(session, user, Permission.NOTIFICATION_PREFERENCES_MANAGE)
        return handle_preferences_command(user, lowered)
    if lowered == _EDIT_MISSION_COMMAND:
        authorize(session, user, Permission.MISSION_EDIT)
        return _start_edit_mission_flow(session=session, user=user)
    if user.pending_intent is not None:
        return await _resolve_pending_intent(
            message, adapters=adapters, session=session, user=user
        )

    authorize(session, user, Permission.AI_INTERPRET)
    profile = ai_profile_for_user(session, user)
    try:
        intent = await adapters[profile].interpret(message, profile=profile)
    except TelegramContractError, AIProviderError:
        logger.warning("telegram_webhook_intent_failed")
        return None

    try:
        return _dispatch_intent(intent, session=session, user=user)
    except _KNOWN_DISPATCH_ERRORS as error:
        logger.warning(
            "telegram_webhook_mission_failed",
            extra={"mission_error": type(error).__name__},
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
        CredentialAction.LOGIN: ("🔑", "Entrar"),
        CredentialAction.SET_PASSWORD: ("🔐", "Criar senha"),
        CredentialAction.CHANGE_PASSWORD: ("🔐", "Alterar senha"),
        CredentialAction.RECOVER_PASSWORD: ("🔐", "Recuperar senha"),
    }
    icon, label = labels[action]
    return (
        f"{icon} {label}\n{issued.url}\n\n"
        "O link é pessoal, de uso único e expira em 10 minutos."
    )


async def _resolve_pending_intent(
    message: TelegramMessage,
    *,
    adapters: dict[UserRole, TelegramIntentAdapter],
    session: Session,
    user: User,
) -> str:
    kind = user.pending_intent.get("kind")
    permission = _PENDING_INTENT_PERMISSIONS.get(kind, Permission.MISSION_TRANSITION)
    authorize(session, user, permission)
    if kind == "await_create_mission_sources":
        return _apply_create_mission_sources_answer(message.text, user=user)
    if kind in ("await_edit_paused_choice", "await_edit_active_choice"):
        return _apply_edit_mission_choice(message.text, user=user)
    if kind == "await_edit_menu_choice":
        return _apply_edit_menu_choice(message.text, session=session, user=user)
    if kind == "await_edit_lojas_choice":
        return _apply_edit_lojas_choice(message.text, user=user)
    if kind == "await_edit_add_sources":
        return _apply_edit_add_sources(message.text, user=user)
    if kind == "await_edit_remove_sources":
        return _apply_edit_remove_sources(message.text, user=user)
    if kind == "await_edit_target_amount":
        return _apply_edit_target_amount(message.text, user=user)

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
            extra={"mission_error": type(error).__name__},
        )
        return str(error)
    except Exception:
        user.pending_intent = None
        raise
    user.pending_intent = None
    return reply


def _apply_create_mission_sources_answer(text: str, *, user: User) -> str:
    """TASK-070: interpreta a resposta à lista numerada de lojas de forma
    determinística (sem IA); resposta inválida mantém o mesmo estado
    pendente e pede de novo -- nunca cria a missão nem volta a passar pelo
    `IntentInterpreter`."""
    payload = user.pending_intent
    sources = resolve_create_mission_sources(text)
    if sources is None:
        return describe_create_mission_sources_retry()
    create_payload = stage_create_mission(
        search_query=payload["search_query"],
        target_amount=payload["target_amount"],
        target_currency=payload["target_currency"],
        sources=sources,
    )
    user.pending_intent = create_payload
    return describe_create_mission(create_payload)


def _dispatch_intent(intent: Intent, *, session: Session, user: User) -> str:
    """Interpreta o `Intent` e decide a resposta.

    `create_mission` e `mission_command` mudam estado — em vez de
    executar direto, ficam "encenados" em `user.pending_intent` e só são
    executados após confirmação explícita do usuário (TASK-058). Uma
    `create_mission` sem loja nenhuma passa primeiro por um estado
    pendente à parte perguntando as lojas (TASK-070) antes de chegar a
    esse ponto de confirmação. `query_mission` é somente leitura e
    continua respondendo direto. `edit_mission` (TASK-069) não é mais
    executada a partir daqui -- desde a TASK-071, editar lojas/preço-alvo
    só acontece pelo menu guiado e determinístico do `/editar-missao`,
    nunca por texto livre interpretado pela IA.
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
    if intent.kind is IntentKind.EDIT_MISSION:
        return _EDIT_MISSION_FREE_TEXT_REDIRECT
    return _UNKNOWN_REPLY


def _stage_create_mission(intent: Intent, *, user: User) -> str:
    """TASK-070: sem loja nenhuma informada, não assume mais as 4 fontes
    da V1 -- encena um estado pendente específico para perguntar por lista
    numerada, preservando os demais critérios já interpretados, e só
    segue para a confirmação normal depois de uma escolha válida."""
    search_query = intent.parameters.search_query
    if not search_query:
        raise MissionIntentError(
            "Não entendi o que você quer buscar. Pode detalhar o produto?"
        )
    if not intent.parameters.sources:
        payload = stage_await_create_mission_sources(
            search_query=search_query,
            target_amount=intent.parameters.target_amount,
            target_currency=intent.parameters.target_currency,
        )
        user.pending_intent = payload
        return describe_create_mission_sources_prompt()
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
    lines = ["📋 Suas missões:", ""]
    lines.extend(
        f"{MISSION_STATUS_ICONS[mission.status]} {mission.title} — "
        f"{format_mission_status(mission.status)}"
        for mission in missions
    )
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


def _query_missions_by_status(
    session: Session, *, user_id: Any, status_value: MissionStatus
) -> list[Mission]:
    return list(
        session.scalars(
            select(Mission)
            .where(Mission.user_id == user_id, Mission.status == status_value)
            .order_by(Mission.created_at)
        )
    )


def _query_mission_source_codes(session: Session, mission_id: UUID) -> tuple[str, ...]:
    return tuple(
        sorted(
            session.scalars(
                select(Store.code)
                .join(MissionSource, MissionSource.store_id == Store.id)
                .where(MissionSource.mission_id == mission_id)
            )
        )
    )


def _start_edit_mission_flow(*, session: Session, user: User) -> str:
    """TASK-071: única porta de entrada do menu guiado -- resolve qual
    missão editar sem IA, priorizando missões `PAUSED`; sem nenhuma
    pausada, reaproveita o pedido de pausa já existente (TASK-069) para
    a(s) missão(ões) `ACTIVE`."""
    paused = _query_missions_by_status(
        session, user_id=user.id, status_value=MissionStatus.PAUSED
    )
    if len(paused) == 1:
        return _stage_edit_menu(
            mission_id=str(paused[0].id),
            mission_title=paused[0].title,
            state_version=paused[0].state_version,
            user=user,
        )
    if len(paused) > 1:
        return _stage_edit_mission_choice(
            paused, user=user, kind="await_edit_paused_choice"
        )

    active = _query_missions_by_status(
        session, user_id=user.id, status_value=MissionStatus.ACTIVE
    )
    if len(active) == 1:
        return _stage_pause_offer_for_edit(
            mission_id=str(active[0].id),
            mission_title=active[0].title,
            state_version=active[0].state_version,
            user=user,
        )
    if len(active) > 1:
        return _stage_edit_mission_choice(
            active, user=user, kind="await_edit_active_choice"
        )

    user.pending_intent = None
    return describe_no_editable_mission()


def _stage_edit_mission_choice(
    missions: list[Mission], *, user: User, kind: str
) -> str:
    header = (
        "Você tem mais de uma missão pausada. Qual você quer editar?"
        if kind == "await_edit_paused_choice"
        else "Você tem mais de uma missão ativa. Qual você quer pausar para editar?"
    )
    user.pending_intent = {
        "kind": kind,
        "mission_ids": [str(mission.id) for mission in missions],
        "mission_titles": [mission.title for mission in missions],
        "mission_state_versions": [mission.state_version for mission in missions],
    }
    return describe_mission_choice_prompt(
        [mission.title for mission in missions], header=header
    )


def _apply_edit_mission_choice(text: str, *, user: User) -> str:
    payload = user.pending_intent
    index = parse_single_numbered_choice(text, count=len(payload["mission_ids"]))
    if index is None:
        return describe_mission_choice_retry()
    mission_id = payload["mission_ids"][index]
    mission_title = payload["mission_titles"][index]
    state_version = payload["mission_state_versions"][index]
    if payload["kind"] == "await_edit_paused_choice":
        return _stage_edit_menu(
            mission_id=mission_id,
            mission_title=mission_title,
            state_version=state_version,
            user=user,
        )
    return _stage_pause_offer_for_edit(
        mission_id=mission_id,
        mission_title=mission_title,
        state_version=state_version,
        user=user,
    )


def _stage_pause_offer_for_edit(
    *, mission_id: str, mission_title: str, state_version: int, user: User
) -> str:
    payload = stage_pause_for_edit(
        mission_id=UUID(mission_id),
        mission_title=mission_title,
        expected_state_version=state_version,
    )
    user.pending_intent = payload
    return describe_pause_for_edit(payload)


def _stage_edit_menu(
    *, mission_id: str, mission_title: str, state_version: int, user: User
) -> str:
    user.pending_intent = {
        "kind": "await_edit_menu_choice",
        "mission_id": mission_id,
        "mission_title": mission_title,
        "expected_state_version": state_version,
    }
    return describe_edit_menu(mission_title)


def _apply_edit_menu_choice(text: str, *, session: Session, user: User) -> str:
    payload = user.pending_intent
    choice = parse_single_numbered_choice(text, count=2)
    if choice is None:
        return describe_edit_menu_retry()
    mission_id = payload["mission_id"]
    if choice == 0:  # "1 - Lojas"
        current_sources = _query_mission_source_codes(session, UUID(mission_id))
        user.pending_intent = {
            "kind": "await_edit_lojas_choice",
            "mission_id": mission_id,
            "mission_title": payload["mission_title"],
            "expected_state_version": payload["expected_state_version"],
            "current_sources": list(current_sources),
        }
        return describe_edit_lojas_menu()
    # choice == 1: "2 - Preço-alvo"
    criteria = session.scalar(
        select(MissionCriteria).where(MissionCriteria.mission_id == UUID(mission_id))
    )
    user.pending_intent = {
        "kind": "await_edit_target_amount",
        "mission_id": mission_id,
        "mission_title": payload["mission_title"],
        "expected_state_version": payload["expected_state_version"],
        "previous_target_amount": (
            str(criteria.target_amount)
            if criteria and criteria.target_amount is not None
            else None
        ),
        "previous_target_currency": criteria.target_currency if criteria else None,
    }
    return describe_edit_target_amount_prompt()


def _apply_edit_lojas_choice(text: str, *, user: User) -> str:
    payload = user.pending_intent
    choice = parse_single_numbered_choice(text, count=2)
    if choice is None:
        return describe_edit_lojas_menu_retry()
    current_sources = tuple(payload["current_sources"])
    if choice == 0:
        return _start_add_sources(payload, current_sources=current_sources, user=user)
    return _start_remove_sources(payload, current_sources=current_sources, user=user)


def _start_add_sources(
    payload: dict[str, Any], *, current_sources: tuple[str, ...], user: User
) -> str:
    option_map = missing_store_options(current_sources)
    if not option_map:
        user.pending_intent = None
        return describe_edit_add_sources_none_missing()
    user.pending_intent = {
        "kind": "await_edit_add_sources",
        "mission_id": payload["mission_id"],
        "mission_title": payload["mission_title"],
        "expected_state_version": payload["expected_state_version"],
        "current_sources": list(current_sources),
        "option_map": option_map,
    }
    return describe_edit_add_sources_prompt(option_map)


def _start_remove_sources(
    payload: dict[str, Any], *, current_sources: tuple[str, ...], user: User
) -> str:
    if len(current_sources) <= 1:
        user.pending_intent = None
        return describe_edit_remove_sources_too_few()
    option_map = current_store_options(current_sources)
    user.pending_intent = {
        "kind": "await_edit_remove_sources",
        "mission_id": payload["mission_id"],
        "mission_title": payload["mission_title"],
        "expected_state_version": payload["expected_state_version"],
        "current_sources": list(current_sources),
        "option_map": option_map,
    }
    return describe_edit_remove_sources_prompt(option_map)


def _apply_edit_add_sources(text: str, *, user: User) -> str:
    payload = user.pending_intent
    selected = resolve_edit_source_selection(text, option_map=payload["option_map"])
    if selected is None:
        return describe_edit_source_selection_retry()
    current_sources = tuple(payload["current_sources"])
    combined = current_sources + tuple(
        code for code in selected if code not in current_sources
    )
    create_payload = stage_edit_mission(
        mission_id=UUID(payload["mission_id"]),
        mission_title=payload["mission_title"],
        expected_state_version=payload["expected_state_version"],
        previous_target_amount=None,
        previous_target_currency=None,
        previous_sources=current_sources,
        target_amount=None,
        target_currency=None,
        clear_target=False,
        sources=combined,
    )
    user.pending_intent = create_payload
    return describe_edit_mission(create_payload)


def _apply_edit_remove_sources(text: str, *, user: User) -> str:
    payload = user.pending_intent
    selected = resolve_edit_source_selection(text, option_map=payload["option_map"])
    if selected is None:
        return describe_edit_source_selection_retry()
    current_sources = tuple(payload["current_sources"])
    remaining = tuple(code for code in current_sources if code not in selected)
    if not remaining:
        return describe_edit_remove_sources_would_empty()
    create_payload = stage_edit_mission(
        mission_id=UUID(payload["mission_id"]),
        mission_title=payload["mission_title"],
        expected_state_version=payload["expected_state_version"],
        previous_target_amount=None,
        previous_target_currency=None,
        previous_sources=current_sources,
        target_amount=None,
        target_currency=None,
        clear_target=False,
        sources=remaining,
    )
    user.pending_intent = create_payload
    return describe_edit_mission(create_payload)


def _apply_edit_target_amount(text: str, *, user: User) -> str:
    payload = user.pending_intent
    amount = parse_target_amount_entry(text)
    if amount is None:
        return describe_edit_target_amount_retry()
    clear_target = amount == 0
    create_payload = stage_edit_mission(
        mission_id=UUID(payload["mission_id"]),
        mission_title=payload["mission_title"],
        expected_state_version=payload["expected_state_version"],
        previous_target_amount=payload["previous_target_amount"],
        previous_target_currency=payload["previous_target_currency"],
        previous_sources=(),
        target_amount=None if clear_target else amount,
        target_currency=None if clear_target else "BRL",
        clear_target=clear_target,
        sources=(),
    )
    user.pending_intent = create_payload
    return describe_edit_mission(create_payload)


def _execute_pending_intent(
    payload: dict[str, Any], *, session: Session, user: User
) -> str:
    kind = payload["kind"]
    if kind == "create_mission":
        return _execute_create_mission(payload, session=session, user=user)
    if kind == "edit_mission":
        return _execute_edit_mission(payload, session=session, user=user)
    if kind == "pause_for_edit":
        return _execute_pause_for_edit(payload, session=session, user=user)
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
        schedule_interval_minutes=get_settings().collection_schedule_interval_minutes,
        schedule_stagger_seconds=get_settings().collection_schedule_stagger_seconds,
    )
    lines = ["✅ Missão criada!", "", f"🔎 Produto: {mission.title}"]
    if target_amount is not None and payload["target_currency"] is not None:
        lines.append(
            f"🎯 Alvo: {format_money(target_amount, payload['target_currency'])}"
        )
    lines.append(f"🏪 Lojas: {format_store_list(sources)}")
    lines.extend(["", "Vou começar a monitorar os preços para você."])
    return "\n".join(lines)


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
    icon = MISSION_STATUS_ICONS[transition.to_status]
    status = format_mission_status(transition.to_status)
    return f'{icon} "{payload["mission_title"]}" agora está {status}.'


def _execute_edit_mission(
    payload: dict[str, Any], *, session: Session, user: User
) -> str:
    mission_id = UUID(payload["mission_id"])
    authorize(
        session,
        user,
        Permission.MISSION_EDIT,
        resource_type="mission",
        resource_id=mission_id,
    )
    mission = session.get(Mission, mission_id)
    if mission is None or mission.user_id != user.id:
        deny_resource_unavailable(
            session,
            user,
            Permission.MISSION_EDIT,
            resource_type="mission",
            resource_id=mission_id,
        )
    target_update = None
    if payload["changes_target"]:
        amount = payload["target_amount"]
        target_update = (
            Decimal(amount) if amount is not None else None,
            payload["target_currency"],
        )
    source_codes = tuple(payload["sources"]) if payload["changes_sources"] else None
    _mission, effective_codes = edit_mission_criteria(
        session,
        mission_id=mission_id,
        expected_state_version=payload["expected_state_version"],
        target_update=target_update,
        source_codes=source_codes,
    )
    lines = ["✅ Missão atualizada!", "", f'🔎 Missão: "{payload["mission_title"]}"']
    if payload["changes_target"]:
        if payload["target_amount"] is not None:
            lines.append(
                "🎯 Alvo: "
                f"{format_money(Decimal(payload['target_amount']), payload['target_currency'])}"
            )
        else:
            lines.append("🎯 Alvo: removido — agora só acompanhando preços.")
    if payload["changes_sources"]:
        lines.append(f"🏪 Lojas: {format_store_list(effective_codes)}")
    lines.extend(
        ["", "A missão continua pausada — use /retomar quando quiser voltar a coletar."]
    )
    return "\n".join(lines)


def _execute_pause_for_edit(
    payload: dict[str, Any], *, session: Session, user: User
) -> str:
    mission_id = UUID(payload["mission_id"])
    authorize(
        session,
        user,
        Permission.MISSION_TRANSITION,
        resource_type="mission",
        resource_id=mission_id,
    )
    mission = session.get(Mission, mission_id)
    if mission is None or mission.user_id != user.id:
        deny_resource_unavailable(
            session,
            user,
            Permission.MISSION_TRANSITION,
            resource_type="mission",
            resource_id=mission_id,
        )
    transition_mission(
        session,
        mission_id=mission_id,
        command=MissionCommand.PAUSE,
        expected_state_version=payload["expected_state_version"],
        actor_type="telegram",
        actor_id=user.id,
    )
    title = payload["mission_title"]
    return (
        f'⏸️ "{title}" está pausada agora.\n\n'
        f"Para editar, envie {_EDIT_MISSION_COMMAND}."
    )


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
