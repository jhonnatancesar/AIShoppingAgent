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
    "validate_provider_response",
]
