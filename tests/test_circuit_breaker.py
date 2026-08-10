"""Tests for the circuit breaker (§8.1)."""

import time

from ferrite.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    get_circuit_breaker,
)


class TestCircuitBreakerStates:
    def test_starts_closed(self):
        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True

    def test_success_stays_closed(self):
        cb = CircuitBreaker()
        cb.call(lambda: "ok")
        assert cb.state == CircuitState.CLOSED

    def test_failures_trip_open(self):
        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(3):
            cb.call(lambda: (_ for _ in ()).throw(Exception("fail")),
                    fallback=None)
        assert cb.state == CircuitState.OPEN
        assert cb.can_execute() is False

    def test_open_returns_fallback(self):
        cb = CircuitBreaker(failure_threshold=1)
        cb.call(lambda: (_ for _ in ()).throw(Exception("fail")),
                fallback="fallback")
        result = cb.call(lambda: "should_not_run", fallback="fallback2")
        assert result == "fallback2"

    def test_closed_to_open_count(self):
        cb = CircuitBreaker(failure_threshold=5)
        for i in range(4):
            cb.call(lambda: (_ for _ in ()).throw(Exception("fail")),
                    fallback=None)
        # 4 failures, threshold 5 — still closed
        assert cb.state == CircuitState.CLOSED
        cb.call(lambda: (_ for _ in ()).throw(Exception("fail")),
                fallback=None)
        assert cb.state == CircuitState.OPEN

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.call(lambda: (_ for _ in ()).throw(Exception("fail")),
                fallback=None)
        cb.call(lambda: (_ for _ in ()).throw(Exception("fail")),
                fallback=None)
        cb.call(lambda: "ok")  # success resets
        assert cb.state == CircuitState.CLOSED
        assert cb._failure_count == 0


class TestHalfOpenTransition:
    def test_cooldown_transitions_to_half_open(self):
        cb = CircuitBreaker(
            failure_threshold=1,
            cooldown_seconds=0.1,
        )
        cb.call(lambda: (_ for _ in ()).throw(Exception("fail")),
                fallback=None)
        assert cb.state == CircuitState.OPEN
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.can_execute() is True

    def test_half_open_success_closes(self):
        cb = CircuitBreaker(
            failure_threshold=1,
            cooldown_seconds=0.1,
            half_open_max_calls=1,
        )
        cb.call(lambda: (_ for _ in ()).throw(Exception("fail")),
                fallback=None)
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN
        cb.call(lambda: "ok")
        assert cb.state == CircuitState.CLOSED

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(
            failure_threshold=1,
            cooldown_seconds=0.1,
        )
        cb.call(lambda: (_ for _ in ()).throw(Exception("fail")),
                fallback=None)
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN
        cb.call(lambda: (_ for _ in ()).throw(Exception("fail")),
                fallback=None)
        assert cb.state == CircuitState.OPEN


class TestReset:
    def test_manual_reset(self):
        cb = CircuitBreaker(failure_threshold=1)
        cb.call(lambda: (_ for _ in ()).throw(Exception("fail")),
                fallback=None)
        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb._failure_count == 0


class TestGetState:
    def test_state_dict(self):
        cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=60)
        state = cb.get_state()
        assert state.state == "closed"
        assert state.failure_count == 0
        assert state.failure_threshold == 5


class TestSingleton:
    def test_get_circuit_breaker_returns_same(self):
        cb1 = get_circuit_breaker()
        cb2 = get_circuit_breaker()
        assert cb1 is cb2
