"""Telemetria sanitizada e aviso de cota do AI Provider Manager."""

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.ai_provider.contracts import AIProviderError, AIRequest

logger = logging.getLogger("app.ai_provider")


class AIAttemptOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    QUOTA_EXCEEDED = "quota_exceeded"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AIQuotaNotice:
    """Dados seguros para um futuro canal comunicar bloqueio por cota."""

    quota_reset_at: datetime | None

    @property
    def reset_known(self) -> bool:
        return self.quota_reset_at is not None

    @property
    def message(self) -> str:
        if self.quota_reset_at is None:
            return "A cota de IA acabou. O prazo para usar o chat novamente é desconhecido."
        reset = self.quota_reset_at.isoformat()
        return f"A cota de IA acabou. O chat poderá ser usado novamente após {reset}."

    @classmethod
    def from_error(cls, error: AIProviderError) -> AIQuotaNotice:
        return cls(quota_reset_at=error.quota_reset_at)


def record_ai_attempt(
    request: AIRequest,
    *,
    provider: str,
    model: str,
    outcome: AIAttemptOutcome,
    fallback: bool,
    quota_reset_at: datetime | None = None,
) -> None:
    """Registra somente metadados operacionais permitidos pela TASK-031."""
    logger.info(
        "ai_provider_attempt",
        extra={
            "ai_request_id": str(request.request_id),
            "ai_profile": request.profile.value,
            "ai_purpose": request.purpose,
            "ai_provider": provider,
            "ai_model": model,
            "ai_outcome": outcome.value,
            "ai_fallback": fallback,
            "ai_quota_reset_at": (
                quota_reset_at.isoformat() if quota_reset_at is not None else None
            ),
        },
    )
