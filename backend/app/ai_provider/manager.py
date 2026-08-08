"""Managers dos perfis USER e ADMIN/DEV sobre provedores internos."""

from app.ai_provider.contracts import (
    AIProvider,
    AIProviderError,
    AIProviderQuotaExceeded,
    AIProviderUnavailable,
    AIRequest,
    AIRequestError,
    AIResponse,
    validate_provider_response,
)
from app.ai_provider.gemini import GeminiProvider
from app.ai_provider.groq import GroqProvider
from app.ai_provider.telemetry import AIAttemptOutcome, record_ai_attempt
from app.core.config import Settings, get_settings
from app.users.models import UserRole


class UserAIProviderManager:
    """Encaminha exclusivamente o perfil USER ao provider Gemini."""

    def __init__(self, provider: AIProvider) -> None:
        self._provider = provider

    async def generate(self, request: AIRequest) -> AIResponse:
        if request.profile is not UserRole.USER:
            raise AIRequestError("USER manager accepts only USER profile")
        try:
            response = await self._provider.generate(request)
            validate_provider_response(request, response)
        except AIProviderError as error:
            _record_provider_error(request, self._provider, error, fallback=False)
            raise
        record_ai_attempt(
            request,
            provider=response.provider,
            model=response.model,
            outcome=AIAttemptOutcome.SUCCEEDED,
            fallback=False,
        )
        return response


class AdminDevAIProviderManager:
    """Política única de ADMIN/DEV: premium, Groq opcional, depois Gemini gratuito."""

    def __init__(
        self,
        premium: AIProvider,
        free: AIProvider,
        *,
        groq: AIProvider | None = None,
    ) -> None:
        self._premium = premium
        self._free = free
        self._groq = groq

    async def generate(self, request: AIRequest) -> AIResponse:
        if request.profile not in {UserRole.ADMIN, UserRole.DEV}:
            raise AIRequestError("ADMIN/DEV manager accepts only ADMIN or DEV profile")

        tiers = [self._premium]
        if self._groq is not None:
            tiers.append(self._groq)
        tiers.append(self._free)

        last_error: AIProviderError | None = None
        for index, provider in enumerate(tiers):
            fallback = index > 0
            try:
                response = await provider.generate(request)
                validate_provider_response(request, response)
            except (AIProviderQuotaExceeded, AIProviderUnavailable) as error:
                _record_provider_error(request, provider, error, fallback=fallback)
                last_error = error
                continue
            except AIProviderError as error:
                _record_provider_error(request, provider, error, fallback=fallback)
                raise
            record_ai_attempt(
                request,
                provider=response.provider,
                model=response.model,
                outcome=AIAttemptOutcome.SUCCEEDED,
                fallback=fallback,
            )
            return response

        if last_error is not None:
            raise last_error
        raise AIProviderError("provider_error", retryable=False)


def build_user_ai_provider_manager(
    settings: Settings | None = None,
) -> UserAIProviderManager:
    """Monta o perfil USER somente quando a credencial segura está disponível."""
    current = settings or get_settings()
    if current.gemini_api_key_user is None:
        raise AIRequestError(
            "AISHOPPING_GEMINI_API_KEY_USER is required for USER profile"
        )
    provider = GeminiProvider(current.gemini_api_key_user, current.gemini_model)
    return UserAIProviderManager(provider)


def build_admin_dev_ai_provider_manager(
    settings: Settings | None = None,
) -> AdminDevAIProviderManager:
    """Monta a política de ADMIN/DEV: Gemini premium, Groq opcional, Gemini gratuito.

    Usa uma chave Gemini dedicada (`AISHOPPING_GEMINI_API_KEY_ADMIN_DEV`),
    separada da chave do perfil `USER`, para que a cota gratuita do
    ADMIN/DEV nunca compita com a cota compartilhada de usuários reais.
    """
    current = settings or get_settings()
    if current.gemini_api_key_admin_dev is None:
        raise AIRequestError(
            "AISHOPPING_GEMINI_API_KEY_ADMIN_DEV is required for ADMIN/DEV profile"
        )
    premium = GeminiProvider(
        current.gemini_api_key_admin_dev, current.gemini_premium_model
    )
    free = GeminiProvider(current.gemini_api_key_admin_dev, current.gemini_model)
    groq = (
        GroqProvider(current.groq_api_key, current.groq_model)
        if current.groq_api_key is not None
        else None
    )
    return AdminDevAIProviderManager(premium, free, groq=groq)


def _record_provider_error(
    request: AIRequest,
    provider: AIProvider,
    error: AIProviderError,
    *,
    fallback: bool,
) -> None:
    if isinstance(error, AIProviderQuotaExceeded):
        outcome = AIAttemptOutcome.QUOTA_EXCEEDED
    elif isinstance(error, AIProviderUnavailable):
        outcome = AIAttemptOutcome.UNAVAILABLE
    else:
        outcome = AIAttemptOutcome.FAILED
    record_ai_attempt(
        request,
        provider=provider.provider_id,
        model=provider.model,
        outcome=outcome,
        fallback=fallback,
        quota_reset_at=error.quota_reset_at,
    )
