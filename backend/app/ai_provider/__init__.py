"""Porta única e neutra para todo uso de inteligência artificial."""

from app.ai_provider.contracts import (
    AIManagerError,
    AIMessage,
    AIMessageRole,
    AIProvider,
    AIProviderError,
    AIProviderManager,
    AIProviderQuotaExceeded,
    AIProviderUnavailable,
    AIRequest,
    AIRequestError,
    AIResponse,
    validate_provider_response,
)
from app.ai_provider.gemini import GeminiProvider
from app.ai_provider.manager import (
    UserAIProviderManager,
    build_user_ai_provider_manager,
)

__all__ = [
    "AIManagerError",
    "AIMessage",
    "AIMessageRole",
    "AIProvider",
    "AIProviderError",
    "AIProviderManager",
    "AIProviderQuotaExceeded",
    "AIProviderUnavailable",
    "AIRequest",
    "AIRequestError",
    "AIResponse",
    "GeminiProvider",
    "UserAIProviderManager",
    "build_user_ai_provider_manager",
    "validate_provider_response",
]
