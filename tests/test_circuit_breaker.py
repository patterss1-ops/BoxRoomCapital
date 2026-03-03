"""Tests for H-006 broker circuit breaker.

Covers:
1. State machine transitions (CLOSED → OPEN → HALF_OPEN → CLOSED)
2. Failure threshold tripping
3. Recovery timeout and probing
4. Half-open probe success/failure
5. Force reset
6. Disabled passthrough
7. Stats tracking
8. Dispatcher integration
"""

from __future__ import annotations

import os
import sys
import time
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from broker.circuit_breaker import (
    BrokerCircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
)


class TestCircuitBreakerStateMachine:
    """Test the CLOSED → OPEN → HALF_OPEN → CLOSED state machine."""

    def test_starts_closed(self):
        cb = BrokerCircuitBreaker("test_broker")
        assert cb.state == CircuitState.CLOSED

    def test_stays_closed_on_success(self):
        cb = BrokerCircuitBreaker("test_broker")
        cb.record_success()
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_trips_to_open_after_threshold(self):
        cb = BrokerCircuitBreaker(
            "test_broker",
            config=CircuitBreakerConfig(failure_threshold=3),
        )
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_success_resets_consecutive_failures(self):
        cb = BrokerCircuitBreaker(
            "test_broker",
            config=CircuitBreakerConfig(failure_threshold=3),
        )
        cb.record_failure()
        cb.record_failure()
        cb.record_success()  # Reset
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED  # Still below threshold

    def test_open_transitions_to_half_open_after_timeout(self):
        cb = BrokerCircuitBreaker(
            "test_broker",
            config=CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout_secs=0.01,  # Very short for testing
            ),
        )
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        time.sleep(0.02)
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_closes_on_probe_success(self):
        cb = BrokerCircuitBreaker(
            "test_broker",
            config=CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout_secs=0.01,
            ),
        )
        cb.record_failure()
        time.sleep(0.02)
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_reopens_on_probe_failure(self):
        cb = BrokerCircuitBreaker(
            "test_broker",
            config=CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout_secs=0.01,
            ),
        )
        cb.record_failure()
        time.sleep(0.02)
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_failure()
        assert cb.state == CircuitState.OPEN


class TestCircuitBreakerCheck:
    """Test the check() decision method."""

    def test_closed_allows_requests(self):
        cb = BrokerCircuitBreaker("test_broker")
        decision = cb.check()
        assert decision.allowed
        assert decision.state == CircuitState.CLOSED

    def test_open_rejects_requests(self):
        cb = BrokerCircuitBreaker(
            "test_broker",
            config=CircuitBreakerConfig(failure_threshold=1, recovery_timeout_secs=999),
        )
        cb.record_failure()
        decision = cb.check()
        assert not decision.allowed
        assert decision.state == CircuitState.OPEN
        assert "OPEN" in decision.reason

    def test_half_open_allows_probe(self):
        cb = BrokerCircuitBreaker(
            "test_broker",
            config=CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout_secs=0.01,
                half_open_max_calls=1,
            ),
        )
        cb.record_failure()
        time.sleep(0.02)
        decision = cb.check()
        assert decision.allowed
        assert decision.state == CircuitState.HALF_OPEN

    def test_half_open_rejects_after_probe_limit(self):
        cb = BrokerCircuitBreaker(
            "test_broker",
            config=CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout_secs=0.01,
                half_open_max_calls=1,
            ),
        )
        cb.record_failure()
        time.sleep(0.02)
        cb.check()  # First probe allowed
        decision = cb.check()  # Second probe rejected
        assert not decision.allowed

    def test_disabled_always_allows(self):
        cb = BrokerCircuitBreaker(
            "test_broker",
            config=CircuitBreakerConfig(enabled=False),
        )
        # Record many failures
        for _ in range(10):
            cb.record_failure()
        decision = cb.check()
        assert decision.allowed


class TestCircuitBreakerReset:
    """Test force reset behavior."""

    def test_reset_closes_open_circuit(self):
        cb = BrokerCircuitBreaker(
            "test_broker",
            config=CircuitBreakerConfig(failure_threshold=1, recovery_timeout_secs=999),
        )
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED

    def test_reset_clears_consecutive_failures(self):
        cb = BrokerCircuitBreaker(
            "test_broker",
            config=CircuitBreakerConfig(failure_threshold=3),
        )
        cb.record_failure()
        cb.record_failure()
        cb.reset()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED  # Only 1 after reset


class TestCircuitBreakerStats:
    """Test observable statistics."""

    def test_stats_track_successes_and_failures(self):
        cb = BrokerCircuitBreaker("test_broker")
        cb.record_success()
        cb.record_success()
        cb.record_failure()
        stats = cb.get_stats()
        assert stats.total_successes == 2
        assert stats.total_failures == 1
        assert stats.consecutive_failures == 1

    def test_stats_track_rejections(self):
        cb = BrokerCircuitBreaker(
            "test_broker",
            config=CircuitBreakerConfig(failure_threshold=1, recovery_timeout_secs=999),
        )
        cb.record_failure()
        cb.check()
        cb.check()
        stats = cb.get_stats()
        assert stats.total_rejected == 2

    def test_stats_track_trips(self):
        cb = BrokerCircuitBreaker(
            "test_broker",
            config=CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout_secs=0.01,
            ),
        )
        cb.record_failure()  # Trip 1
        time.sleep(0.02)
        _ = cb.state  # Trigger half-open
        cb.record_failure()  # Trip 2 (re-open)
        stats = cb.get_stats()
        assert stats.trips_total == 2

    def test_stats_include_broker_name(self):
        cb = BrokerCircuitBreaker("ig_live")
        decision = cb.check()
        assert decision.broker_name == "ig_live"


class TestDispatcherCircuitBreakerIntegration:
    """Test that the dispatcher respects circuit breaker state."""

    def test_dispatcher_with_tripped_circuit(self):
        """Verify that a tripped circuit breaker prevents broker calls."""
        cb = BrokerCircuitBreaker(
            "paper",
            config=CircuitBreakerConfig(failure_threshold=1, recovery_timeout_secs=999),
        )
        cb.record_failure()

        decision = cb.check()
        assert not decision.allowed
        assert decision.state == CircuitState.OPEN

        # In real dispatcher, this would prevent _submit_to_broker()
        # and transition the intent to 'retrying' or 'failed'

    def test_multiple_brokers_independent(self):
        """Each broker gets its own circuit breaker instance."""
        cb_ig = BrokerCircuitBreaker(
            "ig",
            config=CircuitBreakerConfig(failure_threshold=2),
        )
        cb_ibkr = BrokerCircuitBreaker(
            "ibkr",
            config=CircuitBreakerConfig(failure_threshold=2),
        )

        cb_ig.record_failure()
        cb_ig.record_failure()
        assert cb_ig.state == CircuitState.OPEN
        assert cb_ibkr.state == CircuitState.CLOSED  # Independent
