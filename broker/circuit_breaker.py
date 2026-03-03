"""Broker circuit breaker with partial execution recovery.

H-006: Implements a state machine that tracks broker API failures and
trips to OPEN state after consecutive failures exceed a threshold.
Supports half-open probing and automatic recovery.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    """Circuit breaker states."""
    CLOSED = "closed"        # Normal operation — requests flow through
    OPEN = "open"            # Tripped — all requests rejected immediately
    HALF_OPEN = "half_open"  # Probing — one test request allowed through


@dataclass
class CircuitBreakerConfig:
    """Configuration for the circuit breaker."""
    failure_threshold: int = 5       # Consecutive failures before tripping
    recovery_timeout_secs: float = 60.0  # Seconds before OPEN -> HALF_OPEN
    half_open_max_calls: int = 1     # Probe calls allowed in HALF_OPEN
    enabled: bool = True


@dataclass
class CircuitBreakerStats:
    """Observable circuit breaker statistics."""
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    total_failures: int = 0
    total_successes: int = 0
    total_rejected: int = 0
    last_failure_time: Optional[float] = None
    last_success_time: Optional[float] = None
    last_state_change_time: Optional[float] = None
    trips_total: int = 0


@dataclass
class CircuitBreakerDecision:
    """Result of a circuit breaker check."""
    allowed: bool
    state: CircuitState
    reason: str
    broker_name: str


class BrokerCircuitBreaker:
    """Circuit breaker for broker API calls.

    State machine:
    - CLOSED: Normal. Track failures. Trip to OPEN after `failure_threshold`
      consecutive failures.
    - OPEN: Reject all calls. After `recovery_timeout_secs`, move to HALF_OPEN.
    - HALF_OPEN: Allow `half_open_max_calls` probe calls. If probe succeeds,
      reset to CLOSED. If probe fails, trip back to OPEN.
    """

    def __init__(
        self,
        broker_name: str,
        config: Optional[CircuitBreakerConfig] = None,
    ):
        self.broker_name = broker_name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._total_failures = 0
        self._total_successes = 0
        self._total_rejected = 0
        self._last_failure_time: Optional[float] = None
        self._last_success_time: Optional[float] = None
        self._last_state_change_time: Optional[float] = None
        self._trips_total = 0
        self._half_open_calls = 0

    @property
    def state(self) -> CircuitState:
        """Current state, accounting for automatic OPEN -> HALF_OPEN transition."""
        if self._state == CircuitState.OPEN and self._should_attempt_recovery():
            self._transition_to(CircuitState.HALF_OPEN)
        return self._state

    def check(self) -> CircuitBreakerDecision:
        """Check if a request should be allowed through."""
        if not self.config.enabled:
            return CircuitBreakerDecision(
                allowed=True,
                state=CircuitState.CLOSED,
                reason="Circuit breaker disabled.",
                broker_name=self.broker_name,
            )

        current_state = self.state  # Triggers auto-recovery check

        if current_state == CircuitState.CLOSED:
            return CircuitBreakerDecision(
                allowed=True,
                state=current_state,
                reason="Circuit closed — normal operation.",
                broker_name=self.broker_name,
            )

        if current_state == CircuitState.HALF_OPEN:
            if self._half_open_calls < self.config.half_open_max_calls:
                self._half_open_calls += 1
                return CircuitBreakerDecision(
                    allowed=True,
                    state=current_state,
                    reason="Circuit half-open — probe request allowed.",
                    broker_name=self.broker_name,
                )
            self._total_rejected += 1
            return CircuitBreakerDecision(
                allowed=False,
                state=current_state,
                reason="Circuit half-open — probe limit reached, waiting for result.",
                broker_name=self.broker_name,
            )

        # OPEN state
        self._total_rejected += 1
        return CircuitBreakerDecision(
            allowed=False,
            state=current_state,
            reason=f"Circuit OPEN for broker '{self.broker_name}' — "
                   f"{self._consecutive_failures} consecutive failures. "
                   f"Recovery in {self._recovery_remaining_secs():.0f}s.",
            broker_name=self.broker_name,
        )

    def record_success(self) -> None:
        """Record a successful broker call."""
        now = time.monotonic()
        self._total_successes += 1
        self._last_success_time = now
        self._consecutive_failures = 0

        if self._state == CircuitState.HALF_OPEN:
            # Probe succeeded — close the circuit
            self._transition_to(CircuitState.CLOSED)
            self._half_open_calls = 0
            logger.info(
                "Circuit breaker CLOSED for broker '%s' — probe succeeded.",
                self.broker_name,
            )

    def record_failure(self) -> None:
        """Record a failed broker call."""
        now = time.monotonic()
        self._total_failures += 1
        self._consecutive_failures += 1
        self._last_failure_time = now

        if self._state == CircuitState.HALF_OPEN:
            # Probe failed — trip back to OPEN
            self._transition_to(CircuitState.OPEN)
            self._half_open_calls = 0
            self._trips_total += 1
            logger.warning(
                "Circuit breaker RE-OPENED for broker '%s' — probe failed.",
                self.broker_name,
            )
            return

        if (
            self._state == CircuitState.CLOSED
            and self._consecutive_failures >= self.config.failure_threshold
        ):
            self._transition_to(CircuitState.OPEN)
            self._trips_total += 1
            logger.warning(
                "Circuit breaker TRIPPED for broker '%s' — "
                "%d consecutive failures (threshold: %d).",
                self.broker_name,
                self._consecutive_failures,
                self.config.failure_threshold,
            )

    def reset(self) -> None:
        """Force-reset the circuit breaker to CLOSED state."""
        self._transition_to(CircuitState.CLOSED)
        self._consecutive_failures = 0
        self._half_open_calls = 0
        logger.info(
            "Circuit breaker force-RESET for broker '%s'.",
            self.broker_name,
        )

    def get_stats(self) -> CircuitBreakerStats:
        """Return observable statistics."""
        return CircuitBreakerStats(
            state=self.state,
            consecutive_failures=self._consecutive_failures,
            total_failures=self._total_failures,
            total_successes=self._total_successes,
            total_rejected=self._total_rejected,
            last_failure_time=self._last_failure_time,
            last_success_time=self._last_success_time,
            last_state_change_time=self._last_state_change_time,
            trips_total=self._trips_total,
        )

    def _should_attempt_recovery(self) -> bool:
        """Check if enough time has passed to attempt recovery."""
        if self._last_state_change_time is None:
            return False
        elapsed = time.monotonic() - self._last_state_change_time
        return elapsed >= self.config.recovery_timeout_secs

    def _recovery_remaining_secs(self) -> float:
        """Seconds remaining until recovery attempt."""
        if self._last_state_change_time is None:
            return 0.0
        elapsed = time.monotonic() - self._last_state_change_time
        return max(0.0, self.config.recovery_timeout_secs - elapsed)

    def _transition_to(self, new_state: CircuitState) -> None:
        """Internal state transition."""
        self._state = new_state
        self._last_state_change_time = time.monotonic()
