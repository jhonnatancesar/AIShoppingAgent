"""Primitivas locais e limitadas de resiliência para integrações externas."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from threading import Lock
from time import monotonic


class OperationSafety(StrEnum):
    SAFE = "safe"
    POTENTIALLY_NON_IDEMPOTENT = "potentially_non_idempotent"


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """O circuito recusou uma chamada antes de tocar a dependência."""


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 5.0
    retry_after_cap_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")
        if self.base_delay_seconds <= 0:
            raise ValueError("base_delay_seconds must be positive")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("max_delay_seconds must not be smaller than base delay")
        if self.retry_after_cap_seconds <= 0:
            raise ValueError("retry_after_cap_seconds must be positive")

    def delay(self, failed_attempt: int, *, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return min(max(0.0, retry_after), self.retry_after_cap_seconds)
        ceiling = min(
            self.max_delay_seconds,
            self.base_delay_seconds * (2 ** max(0, failed_attempt - 1)),
        )
        return random.uniform(0, ceiling)


class CircuitBreaker:
    """Circuit breaker por processo com uma única sonda em half-open."""

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        open_seconds: float = 30.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if not 1 <= failure_threshold <= 100:
            raise ValueError("failure_threshold must be between 1 and 100")
        if open_seconds <= 0:
            raise ValueError("open_seconds must be positive")
        self.failure_threshold = failure_threshold
        self.open_seconds = open_seconds
        self._clock = clock
        self._lock = Lock()
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at: float | None = None
        self._half_open_probe = False

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if (
                self._state is CircuitState.OPEN
                and self._opened_at is not None
                and self._clock() - self._opened_at >= self.open_seconds
            ):
                return CircuitState.HALF_OPEN
            return self._state

    def before_call(self) -> None:
        with self._lock:
            if self._state is CircuitState.OPEN:
                assert self._opened_at is not None
                if self._clock() - self._opened_at < self.open_seconds:
                    raise CircuitOpenError("dependency circuit is open")
                self._state = CircuitState.HALF_OPEN
            if self._state is CircuitState.HALF_OPEN:
                if self._half_open_probe:
                    raise CircuitOpenError("dependency circuit half-open probe is busy")
                self._half_open_probe = True

    def record_success(self) -> None:
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failures = 0
            self._opened_at = None
            self._half_open_probe = False

    def record_failure(self, *, transient: bool) -> None:
        with self._lock:
            self._half_open_probe = False
            if not transient:
                self._state = CircuitState.CLOSED
                self._failures = 0
                self._opened_at = None
                return
            self._failures += 1
            if self._state is CircuitState.HALF_OPEN or (
                self._failures >= self.failure_threshold
            ):
                self._state = CircuitState.OPEN
                self._opened_at = self._clock()


class CircuitRegistry:
    """Mantém circuitos independentes por chave estável de dependência/operação."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._circuits: dict[str, CircuitBreaker] = {}

    def get(
        self, key: str, *, failure_threshold: int, open_seconds: float
    ) -> CircuitBreaker:
        if not key or len(key) > 160:
            raise ValueError("circuit key must be non-blank and bounded")
        with self._lock:
            circuit = self._circuits.get(key)
            if circuit is None:
                circuit = CircuitBreaker(
                    failure_threshold=failure_threshold,
                    open_seconds=open_seconds,
                )
                self._circuits[key] = circuit
            return circuit


CIRCUITS = CircuitRegistry()


async def retry_operation[ResultT](
    operation: Callable[[], Awaitable[ResultT]],
    *,
    safety: OperationSafety,
    policy: RetryPolicy,
    is_transient: Callable[[Exception], bool],
    retry_after: Callable[[Exception], float | None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    on_retry: Callable[[], None] | None = None,
) -> ResultT:
    """Repete somente operação previamente classificada como segura."""
    attempts = policy.max_attempts if safety is OperationSafety.SAFE else 1
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except Exception as error:
            if attempt >= attempts or not is_transient(error):
                raise
            if on_retry is not None:
                on_retry()
            advised = retry_after(error) if retry_after is not None else None
            await sleep(policy.delay(attempt, retry_after=advised))
    raise RuntimeError("retry loop exhausted unexpectedly")


def parse_retry_after(
    value: str | None,
    *,
    cap_seconds: float,
    now: datetime | None = None,
) -> float | None:
    """Aceita delta-seconds ou HTTP-date e sempre aplica um teto local."""
    if value is None or cap_seconds <= 0:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except TypeError, ValueError, OverflowError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        seconds = (parsed - (now or datetime.now(UTC))).total_seconds()
    if seconds < 0:
        return None
    return min(seconds, cap_seconds)
