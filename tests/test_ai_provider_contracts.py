from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.ai_provider import (
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
from app.users.models import UserRole


def _request() -> AIRequest:
    return AIRequest(
        request_id=uuid4(),
        profile=UserRole.USER,
        purpose="interpret_purchase_intent",
        messages=(AIMessage(AIMessageRole.USER, "Quero um notebook"),),
        requested_at=datetime.now(UTC),
    )


class _FakeProvider:
    provider_id = "fake_provider"
    model = "fake-model"

    async def generate(self, request: AIRequest) -> AIResponse:
        return AIResponse(
            request.request_id,
            self.provider_id,
            "fake-model",
            "resposta",
            request.requested_at,
        )


class _FakeManager:
    async def generate(self, request: AIRequest) -> AIResponse:
        return await _FakeProvider().generate(request)


def test_protocols_accept_structural_async_implementations() -> None:
    assert isinstance(_FakeProvider(), AIProvider)
    assert isinstance(_FakeManager(), AIProviderManager)


@pytest.mark.anyio
async def test_request_flows_through_manager_contract() -> None:
    request = _request()

    response = await _FakeManager().generate(request)
    validate_provider_response(request, response)

    assert response.content == "resposta"
    assert response.provider == "fake_provider"


def test_request_supports_only_existing_user_profiles() -> None:
    assert {profile.value for profile in UserRole} == {"USER", "ADMIN", "DEV"}
    with pytest.raises(AIRequestError, match="profile"):
        AIRequest(
            uuid4(),
            "PLUS",  # type: ignore[arg-type]
            "test_request",
            (AIMessage(AIMessageRole.USER, "hello"),),
            datetime.now(UTC),
        )


@pytest.mark.parametrize("purpose", ["", "Invalid Purpose", "UPPER_CASE"])
def test_request_rejects_invalid_purpose(purpose: str) -> None:
    with pytest.raises(AIRequestError, match="purpose"):
        AIRequest(
            uuid4(),
            UserRole.USER,
            purpose,
            (AIMessage(AIMessageRole.USER, "hello"),),
            datetime.now(UTC),
        )


def test_request_rejects_empty_messages_and_naive_time() -> None:
    with pytest.raises(AIRequestError, match="messages"):
        AIRequest(uuid4(), UserRole.USER, "test_request", (), datetime.now(UTC))
    with pytest.raises(AIRequestError, match="timezone"):
        AIRequest(
            uuid4(),
            UserRole.USER,
            "test_request",
            (AIMessage(AIMessageRole.USER, "hello"),),
            datetime.now(),
        )


def test_request_rejects_mutable_message_collection() -> None:
    with pytest.raises(AIRequestError, match="tuple"):
        AIRequest(
            uuid4(),
            UserRole.USER,
            "test_request",
            [AIMessage(AIMessageRole.USER, "hello")],  # type: ignore[arg-type]
            datetime.now(UTC),
        )


def test_messages_and_responses_reject_blank_content() -> None:
    with pytest.raises(AIRequestError, match="message content"):
        AIMessage(AIMessageRole.USER, "  ")
    request = _request()
    with pytest.raises(AIRequestError, match="response content"):
        AIResponse(
            request.request_id,
            "fake_provider",
            "fake-model",
            "",
            request.requested_at,
        )


def test_response_must_match_request_identity_and_time() -> None:
    request = _request()
    mismatch = AIResponse(
        uuid4(), "fake_provider", "fake-model", "answer", request.requested_at
    )
    with pytest.raises(AIProviderError, match="provider_request_mismatch"):
        validate_provider_response(request, mismatch)

    early = AIResponse(
        request.request_id,
        "fake_provider",
        "fake-model",
        "answer",
        request.requested_at - timedelta(seconds=1),
    )
    with pytest.raises(AIProviderError, match="provider_time_mismatch"):
        validate_provider_response(request, early)


def test_provider_errors_expose_only_stable_code_and_retryability() -> None:
    unavailable = AIProviderUnavailable()
    quota = AIProviderQuotaExceeded()

    assert unavailable.retryable is True
    assert quota.retryable is False
    assert str(unavailable) == "provider_unavailable"
    assert quota.quota_reset_at is None
    with pytest.raises(ValueError, match="snake_case"):
        AIProviderError("secret: token=abc", retryable=False)


def test_quota_reset_must_include_timezone() -> None:
    with pytest.raises(AIRequestError, match="timezone"):
        AIProviderQuotaExceeded(quota_reset_at=datetime.now())


# TASK-083: contrato de grounding -- request continua funcionando sem
# declarar nada (compatibilidade), e a resposta distingue estruturalmente
# "solicitado" de "realmente executado" de "evidência disponível".


def test_request_grounding_defaults_to_false_and_existing_calls_are_unaffected() -> (
    None
):
    request = _request()
    assert request.require_search_grounding is False


def test_request_rejects_non_bool_grounding_flag() -> None:
    with pytest.raises(AIRequestError, match="require_search_grounding"):
        AIRequest(
            request_id=uuid4(),
            profile=UserRole.USER,
            purpose="interpret_purchase_intent",
            messages=(AIMessage(AIMessageRole.USER, "oi"),),
            requested_at=datetime.now(UTC),
            require_search_grounding="yes",  # type: ignore[arg-type]
        )


def test_response_grounding_fields_default_to_false_and_empty() -> None:
    response = AIResponse(
        uuid4(), "fake_provider", "fake-model", "resposta", datetime.now(UTC)
    )
    assert response.grounding_requested is False
    assert response.grounding_performed is False
    assert response.grounding_sources == ()


def test_response_rejects_performed_without_requested() -> None:
    with pytest.raises(AIRequestError, match="grounding_performed"):
        AIResponse(
            uuid4(),
            "fake_provider",
            "fake-model",
            "resposta",
            datetime.now(UTC),
            grounding_requested=False,
            grounding_performed=True,
        )


def test_response_rejects_sources_without_performed() -> None:
    with pytest.raises(AIRequestError, match="grounding_sources"):
        AIResponse(
            uuid4(),
            "fake_provider",
            "fake-model",
            "resposta",
            datetime.now(UTC),
            grounding_requested=True,
            grounding_performed=False,
            grounding_sources=("https://example.invalid/fonte",),
        )


def test_response_rejects_blank_grounding_source() -> None:
    with pytest.raises(AIRequestError, match="grounding_sources"):
        AIResponse(
            uuid4(),
            "fake_provider",
            "fake-model",
            "resposta",
            datetime.now(UTC),
            grounding_requested=True,
            grounding_performed=True,
            grounding_sources=("  ",),
        )


def test_response_accepts_fully_grounded_shape() -> None:
    response = AIResponse(
        uuid4(),
        "gemini",
        "gemini-3.6-flash",
        "resposta",
        datetime.now(UTC),
        grounding_requested=True,
        grounding_performed=True,
        grounding_sources=("https://example.invalid/fonte",),
    )
    assert response.grounding_performed is True
    assert response.grounding_sources == ("https://example.invalid/fonte",)


def test_capability_unsupported_error_exposes_capability_and_stable_code() -> None:
    error = AIProviderCapabilityUnsupported("search_grounding")
    assert error.capability == "search_grounding"
    assert error.retryable is False
    assert str(error) == "capability_unsupported_search_grounding"
