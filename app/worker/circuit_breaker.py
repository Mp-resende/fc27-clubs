from __future__ import annotations

import time
from enum import Enum

import structlog

log = structlog.get_logger(__name__)


class _State(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half-open"


class CircuitBreaker:
    """Simple three-state circuit breaker.

    closed   → normal operation
    open     → requests blocked after threshold consecutive failures
    half-open → a single probe is allowed after the backoff period
    """

    def __init__(self, threshold: int, backoff_seconds: int) -> None:
        self._threshold = threshold
        self._backoff_seconds = backoff_seconds
        self._state = _State.CLOSED
        self._failure_count = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        """Return True if the circuit is open and requests should be blocked."""
        if self._state == _State.CLOSED:
            return False
        if self._state == _State.OPEN:
            elapsed = time.monotonic() - (self._opened_at or 0.0)
            if elapsed >= self._backoff_seconds:
                self._state = _State.HALF_OPEN
                log.info("circuit_breaker.half_open", backoff=self._backoff_seconds)
                return False
            return True
        # HALF_OPEN — allow one probe
        return False

    def record_success(self) -> None:
        """Record a successful call; close the circuit if it was open/half-open."""
        if self._state != _State.CLOSED:
            log.info("circuit_breaker.closed", previous_state=self._state.value)
        self._state = _State.CLOSED
        self._failure_count = 0
        self._opened_at = None

    def record_failure(self) -> None:
        """Record a failed call; open the circuit after reaching the threshold."""
        self._failure_count += 1
        log.warning(
            "circuit_breaker.failure",
            failure_count=self._failure_count,
            threshold=self._threshold,
            state=self._state.value,
        )
        if self._state == _State.HALF_OPEN or self._failure_count >= self._threshold:
            self._state = _State.OPEN
            self._opened_at = time.monotonic()
            log.error(
                "circuit_breaker.opened",
                failure_count=self._failure_count,
                backoff_seconds=self._backoff_seconds,
            )

    def reset(self) -> None:
        """Force the circuit back to closed state."""
        self._state = _State.CLOSED
        self._failure_count = 0
        self._opened_at = None
        log.info("circuit_breaker.reset")
