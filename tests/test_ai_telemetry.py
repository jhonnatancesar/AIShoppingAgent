"""Testes da telemetria sanitizada do AI Provider Manager."""

from datetime import UTC, datetime

from app.ai_provider import (
    AIProviderQuotaExceeded,
    AIQuotaNotice,
)


def test_quota_notice_states_known_and_unknown_deadline() -> None:
    reset_at = datetime(2026, 8, 3, 3, 0, tzinfo=UTC)
    known = AIQuotaNotice.from_error(AIProviderQuotaExceeded(quota_reset_at=reset_at))
    unknown = AIQuotaNotice.from_error(AIProviderQuotaExceeded())

    assert known.reset_known is True
    assert reset_at.isoformat() in known.message
    assert unknown.reset_known is False
    assert "desconhecido" in unknown.message
