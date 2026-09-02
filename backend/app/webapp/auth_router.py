"""Recuperação de senha e verificação de e-mail (Subtask 9, auditoria GG
Oferta) -- fluxo compartilhado Web + Telegram sobre `VerificationChallenge`.

Recuperação nunca concede acesso só por saber username/e-mail: a
identidade é resolvida internamente, mas a resposta pública nunca
distingue "conta não existe" de "conta existe sem esse canal disponível"
-- os dois casos devolvem o mesmo formato neutro (`channels: []` na
consulta; um `challenge_id` decorativo, nunca persistido, na
solicitação -- que só falha depois, no confirm, com o mesmo texto de
"código inválido" que um código digitado errado teria).

Verificação de e-mail é autenticada (só o próprio dono pode pedir/
confirmar a verificação do próprio e-mail) e só fica ativa quando existe
provider real configurado (`Settings.email_delivery_provider`) -- nunca
finge envio."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authentication.delivery import (
    DeliveryUnavailable,
    EmailDeliveryProvider,
    TelegramDeliveryProvider,
    VerificationDeliveryProvider,
    available_channels,
    email_delivery_available,
)
from app.authentication.models import VerificationChannel, VerificationPurpose
from app.authentication.passwords import PasswordPolicyError
from app.authentication.service import set_new_password_async
from app.authentication.verification import (
    CHALLENGE_TTL,
    ChallengeInvalid,
    ChallengeRateLimited,
    confirm_challenge_async,
    create_challenge_async,
)
from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.database.dependency import get_web_async_session
from app.users.models import User
from app.webapp.dependency import require_web_session

router = APIRouter(prefix="/api/v1/auth", tags=["webapp-auth"])

_RECOVERY_PURPOSES = frozenset(
    {VerificationPurpose.PASSWORD_RESET, VerificationPurpose.PASSWORD_CHANGE}
)


class RecoveryChannelsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identifier: str = Field(min_length=1, max_length=254)


class RecoveryChannelsResponse(BaseModel):
    channels: list[VerificationChannel]


class RecoveryRequestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identifier: str = Field(min_length=1, max_length=254)
    channel: VerificationChannel


class ChallengeIssuedResponse(BaseModel):
    challenge_id: UUID
    expires_at: str


class RecoveryConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge_id: UUID
    code: str = Field(min_length=1, max_length=16)
    new_password: str = Field(min_length=1, max_length=128)
    new_password_confirmation: str = Field(min_length=1, max_length=128)


class VerificationConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge_id: UUID
    code: str = Field(min_length=1, max_length=16)


class OkResponse(BaseModel):
    ok: bool = True


async def _resolve_identifier(session: AsyncSession, identifier: str) -> User | None:
    normalized = identifier.strip()
    if not normalized:
        return None
    return await session.scalar(
        select(User).where(
            or_(User.username == normalized, User.email == normalized),
            User.is_active.is_(True),
        )
    )


def _delivery_provider(
    channel: VerificationChannel, *, settings: Settings
) -> VerificationDeliveryProvider:
    if channel is VerificationChannel.TELEGRAM:
        if settings.telegram_bot_token is None:
            raise DeliveryUnavailable("bot do Telegram não configurado")
        return TelegramDeliveryProvider(
            bot_token=settings.telegram_bot_token,
            timeout_seconds=settings.external_http_timeout_seconds,
        )
    return EmailDeliveryProvider()


def _require_pepper(settings: Settings) -> str:
    """`Settings.verification_code_pepper` ausente nunca vira um fallback
    silencioso para hash sem segredo -- bloqueia com um erro claro."""
    if settings.verification_code_pepper is None:
        raise ApiError(
            status_code=503,
            code="verification_unavailable",
            message="Recuperação/verificação temporariamente indisponível.",
        )
    return settings.verification_code_pepper.get_secret_value()


def _decoy_challenge() -> ChallengeIssuedResponse:
    """Resposta com o mesmo formato de uma solicitação real, mas nunca
    persistida -- confirmar este id sempre falha como "código inválido",
    indistinguível de um código real digitado errado."""
    return ChallengeIssuedResponse(
        challenge_id=uuid4(),
        expires_at=(datetime.now(UTC) + CHALLENGE_TTL).isoformat(),
    )


# --- Recuperação de senha (Web e Telegram, fluxo compartilhado) --------


@router.post("/password-recovery/channels", response_model=RecoveryChannelsResponse)
async def recovery_channels(
    payload: RecoveryChannelsRequest,
    session: AsyncSession = Depends(get_web_async_session),
    settings: Settings = Depends(get_settings),
) -> RecoveryChannelsResponse:
    user = await _resolve_identifier(session, payload.identifier)
    if user is None:
        return RecoveryChannelsResponse(channels=[])
    channels = available_channels(user, settings=settings)
    return RecoveryChannelsResponse(
        channels=sorted(channels, key=lambda channel: channel.value)
    )


@router.post("/password-recovery/request", response_model=ChallengeIssuedResponse)
async def request_password_recovery(
    payload: RecoveryRequestRequest,
    session: AsyncSession = Depends(get_web_async_session),
    settings: Settings = Depends(get_settings),
) -> ChallengeIssuedResponse:
    # Checa o pepper ANTES de resolver o identificador: se estivesse
    # depois do branch de decoy, uma má configuração faria requisições
    # reais responderem 503 e requisições para conta inexistente
    # responderem 200 (decoy) -- um oráculo de enumeração por status code
    # que o design de "resposta idêntica" existe justamente para evitar.
    pepper = _require_pepper(settings)
    user = await _resolve_identifier(session, payload.identifier)
    if user is None or payload.channel not in available_channels(user, settings=settings):
        return _decoy_challenge()
    try:
        challenge, code = await create_challenge_async(
            session,
            user_id=user.id,
            purpose=VerificationPurpose.PASSWORD_RESET,
            channel=payload.channel,
            pepper=pepper,
        )
    except ChallengeRateLimited as error:
        raise ApiError(
            status_code=429,
            code="recovery_rate_limited",
            message="Muitas tentativas. Tente novamente mais tarde.",
        ) from error
    provider = _delivery_provider(payload.channel, settings=settings)
    try:
        await provider.deliver(
            user=user, code=code, purpose=VerificationPurpose.PASSWORD_RESET
        )
    except DeliveryUnavailable as error:
        # Sem commit explícito: `get_web_async_session` desfaz o challenge
        # junto com a exceção -- nunca deixa um código "confirmável" que
        # não foi de fato entregue.
        raise ApiError(
            status_code=503,
            code="delivery_unavailable",
            message="Não foi possível enviar o código agora. Tente novamente.",
        ) from error
    return ChallengeIssuedResponse(
        challenge_id=challenge.id, expires_at=challenge.expires_at.isoformat()
    )


@router.post("/password-recovery/confirm", response_model=OkResponse)
async def confirm_password_recovery(
    payload: RecoveryConfirmRequest,
    session: AsyncSession = Depends(get_web_async_session),
    settings: Settings = Depends(get_settings),
) -> OkResponse:
    if payload.new_password != payload.new_password_confirmation:
        raise ApiError(
            status_code=422,
            code="password_confirmation_mismatch",
            message="As senhas informadas não conferem.",
        )
    pepper = _require_pepper(settings)
    try:
        challenge = await confirm_challenge_async(
            session,
            challenge_id=payload.challenge_id,
            code=payload.code,
            purposes=_RECOVERY_PURPOSES,
            pepper=pepper,
        )
    except ChallengeInvalid as error:
        await session.commit()  # persiste o incremento de `attempts`
        raise ApiError(
            status_code=422, code="challenge_invalid", message=str(error)
        ) from error
    user = await session.get(User, challenge.user_id)
    if user is None or not user.is_active:
        await session.commit()
        raise ApiError(
            status_code=422,
            code="challenge_invalid",
            message="Código inválido ou expirado.",
        )
    try:
        await set_new_password_async(session, user=user, new_password=payload.new_password)
    except PasswordPolicyError as error:
        # Sem commit: o challenge continua não-usado (não foi "queimado"
        # por uma senha fraca) -- o usuário pode tentar de novo com o
        # mesmo código, sem pedir um novo.
        raise ApiError(status_code=422, code="weak_password", message=str(error)) from error
    return OkResponse()


# --- Verificação de e-mail (autenticada) --------------------------------


@router.post("/verification/request", response_model=ChallengeIssuedResponse)
async def request_email_verification(
    session: AsyncSession = Depends(get_web_async_session),
    settings: Settings = Depends(get_settings),
    user: User = Depends(require_web_session),
) -> ChallengeIssuedResponse:
    if user.email is None:
        raise ApiError(
            status_code=422,
            code="email_missing",
            message="Cadastre um e-mail antes de verificar.",
        )
    if not email_delivery_available(settings):
        raise ApiError(
            status_code=503,
            code="email_delivery_unavailable",
            message="A verificação de e-mail ainda não está disponível.",
        )
    pepper = _require_pepper(settings)
    try:
        challenge, code = await create_challenge_async(
            session,
            user_id=user.id,
            purpose=VerificationPurpose.EMAIL_VERIFICATION,
            channel=VerificationChannel.EMAIL,
            pepper=pepper,
        )
    except ChallengeRateLimited as error:
        raise ApiError(
            status_code=429,
            code="verification_rate_limited",
            message="Muitas tentativas. Tente novamente mais tarde.",
        ) from error
    provider = EmailDeliveryProvider()
    try:
        await provider.deliver(
            user=user, code=code, purpose=VerificationPurpose.EMAIL_VERIFICATION
        )
    except DeliveryUnavailable as error:
        raise ApiError(
            status_code=503,
            code="delivery_unavailable",
            message="Não foi possível enviar o código agora. Tente novamente.",
        ) from error
    return ChallengeIssuedResponse(
        challenge_id=challenge.id, expires_at=challenge.expires_at.isoformat()
    )


@router.post("/verification/confirm", response_model=OkResponse)
async def confirm_email_verification(
    payload: VerificationConfirmRequest,
    session: AsyncSession = Depends(get_web_async_session),
    settings: Settings = Depends(get_settings),
    user: User = Depends(require_web_session),
) -> OkResponse:
    pepper = _require_pepper(settings)
    try:
        challenge = await confirm_challenge_async(
            session,
            challenge_id=payload.challenge_id,
            code=payload.code,
            purposes=frozenset({VerificationPurpose.EMAIL_VERIFICATION}),
            pepper=pepper,
        )
    except ChallengeInvalid as error:
        await session.commit()
        raise ApiError(
            status_code=422, code="challenge_invalid", message=str(error)
        ) from error
    if challenge.user_id != user.id or challenge.channel is not VerificationChannel.EMAIL:
        await session.commit()
        raise ApiError(
            status_code=422,
            code="challenge_invalid",
            message="Código inválido ou expirado.",
        )
    user.email_verified_at = challenge.used_at
    return OkResponse()
