"""Managers dos perfis USER e ADMIN/DEV sobre provedores internos."""

from app.ai_provider.contracts import (
    AIProvider,
    AIProviderQuotaExceeded,
    AIProviderUnavailable,
    AIRequest,
    AIRequestError,
    AIResponse,
    validate_provider_response,
)
from app.ai_provider.gemini import GeminiProvider
from app.core.config import Settings, get_settings
from app.users.models import UserRole


class UserAIProviderManager:
    """Encaminha exclusivamente o perfil USER ao provider Gemini."""

    def __init__(self, provider: AIProvider) -> None:
        self._provider = provider

    async def generate(self, request: AIRequest) -> AIResponse:
        if request.profile is not UserRole.USER:
            raise AIRequestError("USER manager accepts only USER profile")
        response = await self._provider.generate(request)
        validate_provider_response(request, response)
        return response


class AdminDevAIProviderManager:
    """Política única de ADMIN/DEV com fallback seguro para o Gemini gratuito."""

    def __init__(self, premium: AIProvider, free: AIProvider) -> None:
        self._premium = premium
        self._free = free

    async def generate(self, request: AIRequest) -> AIResponse:
        if request.profile not in {UserRole.ADMIN, UserRole.DEV}:
            raise AIRequestError("ADMIN/DEV manager accepts only ADMIN or DEV profile")
        try:
            response = await self._premium.generate(request)
        except AIProviderQuotaExceeded, AIProviderUnavailable:
            response = await self._free.generate(request)
        validate_provider_response(request, response)
        return response


def build_user_ai_provider_manager(
    settings: Settings | None = None,
) -> UserAIProviderManager:
    """Monta o perfil USER somente quando a credencial segura está disponível."""
    current = settings or get_settings()
    if current.gemini_api_key is None:
        raise AIRequestError("AISHOPPING_GEMINI_API_KEY is required for USER profile")
    provider = GeminiProvider(current.gemini_api_key, current.gemini_model)
    return UserAIProviderManager(provider)


def build_admin_dev_ai_provider_manager(
    settings: Settings | None = None,
) -> AdminDevAIProviderManager:
    """Monta a política compartilhada de ADMIN/DEV com dois níveis Gemini."""
    current = settings or get_settings()
    if current.gemini_api_key is None:
        raise AIRequestError(
            "AISHOPPING_GEMINI_API_KEY is required for ADMIN/DEV profile"
        )
    premium = GeminiProvider(current.gemini_api_key, current.gemini_premium_model)
    free = GeminiProvider(current.gemini_api_key, current.gemini_model)
    return AdminDevAIProviderManager(premium, free)
