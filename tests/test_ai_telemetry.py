"""Testes da telemetria sanitizada do AI Provider Manager."""

import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.ai_provider import (
    AdminDevAIProviderManager,
    AIMessage,
    AIMessageRole,
    AIProviderQuotaExceeded,
    AIQuotaNotice,
    AIRequest,
    AIResponse,
)
from app.ai_provider.gemini import _translate_api_error
from app.users.models import UserRole
from google.genai import errors


def _request() -> AIRequest:
    return AIRequest(
        uuid4(),
        UserRole.ADMIN,
        "user_assistance",
        (AIMessage(AIMessageRole.USER, "prompt ultrassecreto"),),
        datetime.now(UTC),
    )


class _Provider:
    def __init__(
        self, model: str, result: str | Exception, *, provider_id: str = "gemini"
    ) -> None:
        self.provider_id = provider_id
        self.model = model
        self.result = result

    async def generate(self, request: AIRequest) -> AIResponse:
        if isinstance(self.result, Exception):
            raise self.result
        return AIResponse(
            request.request_id,
            self.provider_id,
            self.model,
            self.result,
            datetime.now(UTC),
        )


@pytest.mark.anyio
async def test_fallback_records_gemini_failure_and_groq_success(
    caplog: pytest.LogCaptureFixture,
) -> None:
    reset_at = datetime.now(UTC) + timedelta(seconds=30)
    manager = AdminDevAIProviderManager(
        _Provider(
            "gemini-3.6-flash",
            AIProviderQuotaExceeded(quota_reset_at=reset_at),
        ),
        groq=_Provider(
            "openai/gpt-oss-120b",
            "resposta que também não deve ir ao log",
            provider_id="groq",
        ),
    )
    request = _request()

    with caplog.at_level(logging.INFO, logger="app.ai_provider"):
        response = await manager.generate(request)

    assert response.model == "openai/gpt-oss-120b"
    records = [record for record in caplog.records if record.name == "app.ai_provider"]
    assert [record.ai_outcome for record in records] == [  # type: ignore[attr-defined]
        "quota_exceeded",
        "succeeded",
    ]
    assert [record.ai_fallback for record in records] == [False, True]  # type: ignore[attr-defined]
    assert records[0].ai_quota_reset_at == reset_at.isoformat()  # type: ignore[attr-defined]
    serialized = " ".join(str(record.__dict__) for record in records)
    assert "ultrassecreto" not in serialized
    assert "resposta que também" not in serialized


def test_gemini_preserves_only_valid_retry_info() -> None:
    before = datetime.now(UTC) + timedelta(seconds=20)
    upstream = errors.APIError(
        429,
        {
            "error": {
                "message": "secret token=abc",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "20.5s",
                    }
                ],
            }
        },
    )

    translated = _translate_api_error(upstream)

    assert isinstance(translated, AIProviderQuotaExceeded)
    assert translated.quota_reset_at is not None
    assert translated.quota_reset_at >= before
    assert "secret" not in str(translated)


@pytest.mark.parametrize("retry_delay", ["invalid", "-1s", "NaNs", None])
def test_gemini_does_not_invent_invalid_quota_reset(retry_delay: object) -> None:
    upstream = errors.APIError(
        429,
        {
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.RetryInfo",
                    "retryDelay": retry_delay,
                }
            ]
        },
    )

    translated = _translate_api_error(upstream)

    assert translated.quota_reset_at is None


def test_quota_notice_states_known_and_unknown_deadline() -> None:
    reset_at = datetime(2026, 8, 3, 3, 0, tzinfo=UTC)
    known = AIQuotaNotice.from_error(AIProviderQuotaExceeded(quota_reset_at=reset_at))
    unknown = AIQuotaNotice.from_error(AIProviderQuotaExceeded())

    assert known.reset_known is True
    assert reset_at.isoformat() in known.message
    assert unknown.reset_known is False
    assert "desconhecido" in unknown.message
