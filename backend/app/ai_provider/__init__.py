"""Porta única e neutra para todo uso de inteligência artificial."""

from app.ai_provider.contracts import (
    AIManagerError,
    AIMessage,
    AIMessageRole,
    AIProvider,
    AIProviderCapabilityUnsupported,
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
from app.ai_provider.groq import GroqProvider
from app.ai_provider.manager import (
    AdminDevAIProviderManager,
    UserAIProviderManager,
    build_admin_dev_ai_provider_manager,
    build_user_ai_provider_manager,
)
from app.ai_provider.openrouter import OpenRouterProvider
from app.ai_provider.telemetry import AIAttemptOutcome, AIQuotaNotice

__all__ = [
    "AdminDevAIProviderManager",
    "AIAttemptOutcome",
    "AIQuotaNotice",
    "AIManagerError",
    "AIMessage",
    "AIMessageRole",
    "AIProvider",
    "AIProviderCapabilityUnsupported",
    "AIProviderError",
    "AIProviderManager",
    "AIProviderQuotaExceeded",
    "AIProviderUnavailable",
    "AIRequest",
    "AIRequestError",
    "AIResponse",
    "GeminiProvider",
    "GroqProvider",
    "OpenRouterProvider",
    "UserAIProviderManager",
    "build_admin_dev_ai_provider_manager",
    "build_user_ai_provider_manager",
    "validate_provider_response",
]
