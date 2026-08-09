"""Políticas determinísticas de retry e circuit breaker da TASK-049."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.ai_provider import (
    AdminDevAIProviderManager,
    AIMessage,
    AIMessageRole,
    AIProviderUnavailable,
    AIRequest,
    AIResponse,
)
from app.collection.providers.base import PlaywrightStoreProvider
from app.core.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    OperationSafety,
    RetryPolicy,
    parse_retry_after,
    retry_operation,
)
from app.users.models import UserRole


@pytest.mark.anyio
async def test_retry_repeats_only_safe_operation() -> None:
    calls = 0
    sleeps: list[float] = []

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TimeoutError
        return "ok"

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    result = await retry_operation(
        operation,
        safety=OperationSafety.SAFE,
        policy=RetryPolicy(max_attempts=3),
        is_transient=lambda error: isinstance(error, TimeoutError),
        sleep=sleep,
    )

    assert result == "ok"
    assert calls == 3
    assert len(sleeps) == 2


@pytest.mark.anyio
async def test_non_idempotent_ambiguous_operation_is_never_retried() -> None:
    calls = 0

    async def operation() -> None:
        nonlocal calls
        calls += 1
        raise TimeoutError

    with pytest.raises(TimeoutError):
        await retry_operation(
            operation,
            safety=OperationSafety.POTENTIALLY_NON_IDEMPOTENT,
            policy=RetryPolicy(max_attempts=3),
            is_transient=lambda error: True,
        )

    assert calls == 1


def test_retry_after_accepts_delta_and_http_date_with_cap() -> None:
    now = datetime(2026, 8, 8, 12, tzinfo=UTC)
    future = now + timedelta(minutes=5)

    assert parse_retry_after("12", cap_seconds=30, now=now) == 12
    assert parse_retry_after("999", cap_seconds=30, now=now) == 30
    assert (
        parse_retry_after(
            future.strftime("%a, %d %b %Y %H:%M:%S GMT"), cap_seconds=30, now=now
        )
        == 30
    )
    assert parse_retry_after("invalid", cap_seconds=30, now=now) is None


def test_circuit_opens_and_allows_only_one_half_open_probe() -> None:
    current = [0.0]
    circuit = CircuitBreaker(
        failure_threshold=2, open_seconds=30, clock=lambda: current[0]
    )
    circuit.before_call()
    circuit.record_failure(transient=True)
    circuit.before_call()
    circuit.record_failure(transient=True)

    assert circuit.state is CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        circuit.before_call()

    current[0] = 30.0
    assert circuit.state is CircuitState.HALF_OPEN
    circuit.before_call()
    with pytest.raises(CircuitOpenError):
        circuit.before_call()
    circuit.record_success()
    assert circuit.state is CircuitState.CLOSED


def test_permanent_failure_does_not_open_circuit() -> None:
    circuit = CircuitBreaker(failure_threshold=1)
    circuit.before_call()
    circuit.record_failure(transient=False)
    assert circuit.state is CircuitState.CLOSED


class _AIProvider:
    def __init__(self, provider_id: str, model: str, *, fails: bool = False) -> None:
        self.provider_id = provider_id
        self.model = model
        self.fails = fails
        self.calls = 0

    async def generate(self, request: AIRequest) -> AIResponse:
        self.calls += 1
        if self.fails:
            raise AIProviderUnavailable()
        return AIResponse(
            request.request_id,
            self.provider_id,
            self.model,
            "ok",
            datetime.now(UTC),
        )


@pytest.mark.anyio
async def test_ai_provider_circuits_are_independent() -> None:
    suffix = uuid4().hex
    premium = _AIProvider("gemini", f"premium-{suffix}", fails=True)
    groq = _AIProvider("groq", f"groq-{suffix}")
    free = _AIProvider("gemini", f"free-{suffix}")
    manager = AdminDevAIProviderManager(
        premium,
        free,
        groq=groq,
        circuit_failure_threshold=1,
    )
    request = AIRequest(
        uuid4(),
        UserRole.DEV,
        "resilience_test",
        (AIMessage(AIMessageRole.USER, "teste"),),
        datetime.now(UTC),
    )

    assert (await manager.generate(request)).provider == "groq"
    assert (await manager.generate(request)).provider == "groq"
    assert premium.calls == 1
    assert groq.calls == 2
    assert free.calls == 0


class _StoreA(PlaywrightStoreProvider):
    source_code = "resilience_store_a"
    result_selector = "main"


class _StoreB(PlaywrightStoreProvider):
    source_code = "resilience_store_b"
    result_selector = "main"


def test_store_provider_circuits_are_independent() -> None:
    first = _StoreA(circuit_failure_threshold=1)
    second = _StoreB(circuit_failure_threshold=1)
    first._circuit.before_call()
    first._circuit.record_failure(transient=True)

    with pytest.raises(CircuitOpenError):
        first._circuit.before_call()
    second._circuit.before_call()
    second._circuit.record_success()
