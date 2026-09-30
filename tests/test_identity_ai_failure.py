"""TASK-133 (etapa 1) -- regras puras do registro de falhas de IA de identidade:
classificação da exceção, espera crescente, teto só para falha de conteúdo e
disjuntor. O efeito no banco fica em `tests/integration/test_identity_ai_failure.py`."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from app.ai_provider import (
    AIProviderError,
    AIProviderQuotaExceeded,
    AIProviderUnavailable,
    AIRequestError,
)
from app.products.identity_ai import (
    INFRA_FAILURE_KINDS,
    AIExtractionFailure,
    classify_ai_failure,
)
from app.products.identity_ai_failure import (
    AIBreaker,
    is_retry_due,
    retry_delay,
)

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def test_exceptions_are_classified_without_leaking_messages() -> None:
    reset = NOW + timedelta(hours=3)
    assert classify_ai_failure(
        AIProviderQuotaExceeded(quota_reset_at=reset)
    ) == AIExtractionFailure("quota", reset)
    assert classify_ai_failure(AIProviderUnavailable()).kind == "provider_unavailable"
    assert classify_ai_failure(TimeoutError()).kind == "timeout"
    assert (
        classify_ai_failure(AIProviderError("upstream_timeout", retryable=True)).kind
        == "timeout"
    )
    assert (
        classify_ai_failure(AIProviderError("bad_gateway", retryable=True)).kind
        == "provider_error"
    )
    assert classify_ai_failure(AIRequestError("x")).kind == "request_rejected"
    assert classify_ai_failure(RuntimeError("segredo")).kind == "provider_error"


def test_infra_delay_grows_and_is_capped_at_six_hours() -> None:
    delays = [retry_delay("quota", attempts) for attempts in (1, 2, 3, 4, 5, 50)]
    assert delays[0] == timedelta(minutes=15)
    assert delays[1] == timedelta(minutes=30)
    assert delays == sorted(delays)
    assert delays[-1] == timedelta(hours=6)


def test_content_delay_follows_the_schedule_and_saturates() -> None:
    assert retry_delay("invalid_response", 1) == timedelta(minutes=15)
    assert retry_delay("invalid_response", 3) == timedelta(hours=6)
    assert retry_delay("invalid_response", 99) == timedelta(hours=24)


def test_only_infrastructure_kinds_are_exempt_from_the_cap() -> None:
    assert {"quota", "provider_unavailable", "timeout", "provider_error"} <= (
        INFRA_FAILURE_KINDS
    )
    assert "invalid_response" not in INFRA_FAILURE_KINDS
    assert "request_rejected" not in INFRA_FAILURE_KINDS


def test_breaker_opens_on_infrastructure_and_closes_on_time() -> None:
    breaker = AIBreaker()
    assert breaker.open_until(NOW) is None
    breaker.trip(AIExtractionFailure("invalid_response"), NOW)
    assert breaker.open_until(NOW) is None, "falha de conteúdo nunca abre"
    breaker.trip(AIExtractionFailure("provider_unavailable"), NOW)
    assert breaker.open_until(NOW) == NOW + timedelta(minutes=5)
    assert breaker.open_until(NOW + timedelta(minutes=6)) is None
    breaker.reset()
    assert breaker.open_until(NOW) is None


def test_breaker_honors_quota_reset_but_never_beyond_one_hour() -> None:
    breaker = AIBreaker()
    breaker.trip(AIExtractionFailure("quota", NOW + timedelta(minutes=20)), NOW)
    assert breaker.open_until(NOW) == NOW + timedelta(minutes=20)
    breaker.trip(AIExtractionFailure("quota", NOW + timedelta(days=1)), NOW)
    assert breaker.open_until(NOW) == NOW + timedelta(hours=1)
    breaker.trip(AIExtractionFailure("quota", NOW - timedelta(minutes=1)), NOW)
    assert breaker.open_until(NOW) == NOW + timedelta(hours=1), "nunca encurta"


@pytest.mark.parametrize(
    ("next_retry_at", "due"),
    [
        (None, True),
        (NOW - timedelta(seconds=1), True),
        (NOW, True),
        (NOW + timedelta(seconds=1), False),
    ],
)
def test_retry_is_due_only_after_next_retry_at(next_retry_at, due) -> None:
    candidate = SimpleNamespace(next_retry_at=next_retry_at)
    assert is_retry_due(candidate, NOW) is due


# --- record_ai_failure / clear_ai_failed com sessão simulada ---------------

import asyncio  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402
from uuid import uuid4  # noqa: E402

from app.products.identity_ai_failure import (  # noqa: E402
    MAX_CONTENT_ATTEMPTS,
    clear_ai_failed,
    record_ai_failure,
)
from app.products.identity_candidates import ProductIdentityCandidate  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


def _session(existing=None):
    session = MagicMock()
    session.begin_nested = MagicMock(side_effect=lambda: _Nested())
    session.scalar = AsyncMock(return_value=existing)
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.execute = AsyncMock()
    return session


def _record(session, failure, **kwargs):
    asyncio.run(
        record_ai_failure(
            session,
            raw_title="título",
            title_hash="hash",
            failure=failure,
            now=NOW,
            **kwargs,
        )
    )


def test_new_failure_creates_the_candidate_with_the_first_attempt() -> None:
    session = _session()
    _record(session, AIExtractionFailure("quota"))
    row = session.add.call_args.args[0]
    assert (row.status, row.ai_error_kind, row.ai_attempts) == ("ai_failed", "quota", 1)
    assert row.next_retry_at == NOW + timedelta(minutes=15)


def test_circuit_open_does_not_spend_an_attempt_and_waits_for_the_breaker() -> None:
    session = _session()
    until = NOW + timedelta(minutes=4)
    _record(
        session,
        AIExtractionFailure("circuit_open"),
        count_attempt=False,
        retry_at=until,
    )
    row = session.add.call_args.args[0]
    assert (row.ai_attempts, row.next_retry_at) == (0, until)


def test_existing_failure_row_is_updated_not_duplicated() -> None:
    existing = ProductIdentityCandidate(status="ai_failed", ai_attempts=2)
    session = _session(existing)
    source = uuid4()
    _record(session, AIExtractionFailure("timeout"), source_product_id=source)
    session.add.assert_not_called()
    assert (existing.ai_attempts, existing.ai_error_kind) == (3, "timeout")
    assert existing.source_product_id == source
    assert existing.next_retry_at == NOW + timedelta(hours=1)


def test_a_real_decision_is_never_overwritten_by_a_failure() -> None:
    existing = ProductIdentityCandidate(status="approved", ai_attempts=0)
    session = _session(existing)
    _record(session, AIExtractionFailure("quota"))
    session.add.assert_not_called()
    assert existing.status == "approved"


def test_content_cap_moves_to_page_reading_or_terminal() -> None:
    with_product = ProductIdentityCandidate(
        status="ai_failed", ai_attempts=MAX_CONTENT_ATTEMPTS - 1
    )
    _record(
        _session(with_product),
        AIExtractionFailure("invalid_response"),
        source_product_id=uuid4(),
    )
    assert with_product.status == "awaiting_page"
    assert with_product.next_retry_at is None

    session = _session()
    session.scalar = AsyncMock(return_value=None)
    fresh = ProductIdentityCandidate(
        status="ai_failed", ai_attempts=MAX_CONTENT_ATTEMPTS - 1
    )
    _record(_session(fresh), AIExtractionFailure("request_rejected"))
    assert fresh.status == "unrecognized"

    # sem linha anterior e no teto (uma única falha nunca chega lá, mas o
    # código não pode quebrar): cria a linha já terminal
    brand_new = _session()
    import app.products.identity_ai_failure as module

    module.MAX_CONTENT_ATTEMPTS, saved = 1, module.MAX_CONTENT_ATTEMPTS
    try:
        _record(brand_new, AIExtractionFailure("invalid_response"))
    finally:
        module.MAX_CONTENT_ATTEMPTS = saved
    assert brand_new.add.call_args.args[0].status == "unrecognized"


def test_race_with_another_writer_is_swallowed() -> None:
    session = _session()
    session.flush = AsyncMock(side_effect=IntegrityError("x", {}, Exception()))
    _record(session, AIExtractionFailure("quota"))


def test_clear_deletes_only_the_failure_row() -> None:
    session = _session()
    asyncio.run(clear_ai_failed(session, "hash"))
    session.execute.assert_awaited_once()
