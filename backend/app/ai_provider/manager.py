"""Implementação inicial do manager restrita ao perfil USER."""

from app.ai_provider.contracts import (
    AIProvider,
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


def build_user_ai_provider_manager(
    settings: Settings | None = None,
) -> UserAIProviderManager:
    """Monta o perfil USER somente quando a credencial segura está disponível."""
    current = settings or get_settings()
    if current.gemini_api_key is None:
        raise AIRequestError("AISHOPPING_GEMINI_API_KEY is required for USER profile")
    provider = GeminiProvider(current.gemini_api_key, current.gemini_model)
    return UserAIProviderManager(provider)
