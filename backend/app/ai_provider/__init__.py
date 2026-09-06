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
from app.ai_provider.manager import (
    build_admin_dev_ai_provider_manager,
    build_user_ai_provider_manager,
)
from app.ai_provider.telemetry import AIAttemptOutcome, AIQuotaNotice

__all__ = [
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
    "build_admin_dev_ai_provider_manager",
    "build_user_ai_provider_manager",
    "validate_provider_response",
]
