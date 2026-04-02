"""
Tests for retryflow.
Run with: pytest tests/ -v
"""

import asyncio
import time
import pytest
from unittest.mock import MagicMock, call

from retryflow import retry, RetryConfig, RetryError, RetryContext, attempt


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_flaky(fail_times: int, exc=ValueError):
    """Returns a function that fails `fail_times` before succeeding."""
    calls = {"n": 0}
    def fn():
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise exc(f"Forced failure #{calls['n']}")
        return "ok"
    return fn


# ── Basic retry ───────────────────────────────────────────────────────────────

class TestBasicRetry:
    def test_succeeds_on_first_try(self):
        @retry(max_attempts=3, delay=0)
        def fn():
            return 42
        assert fn() == 42

    def test_retries_and_succeeds(self):
        fn = make_flaky(2)
        decorated = retry(fn, max_attempts=5, delay=0)
        assert decorated() == "ok"

    def test_raises_retry_error_after_exhaustion(self):
        @retry(max_attempts=3, delay=0)
        def always_fails():
            raise RuntimeError("boom")
        with pytest.raises(RetryError) as exc_info:
            always_fails()
        assert exc_info.value.attempts == 3
        assert isinstance(exc_info.value.last_exception, RuntimeError)

    def test_no_retry_on_non_matching_exception(self):
        @retry(max_attempts=5, delay=0, exceptions=(ValueError,))
        def fn():
            raise TypeError("not retried")
        with pytest.raises(TypeError):
            fn()

    def test_retry_only_on_matching_exception(self):
        call_count = {"n": 0}
        @retry(max_attempts=5, delay=0, exceptions=(ValueError,))
        def fn():
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise ValueError("retried")
            return "done"
        assert fn() == "done"
        assert call_count["n"] == 3

    def test_return_value_preserved(self):
        @retry(max_attempts=3, delay=0)
        def fn():
            return {"key": "value"}
        assert fn() == {"key": "value"}


# ── Backoff & jitter ──────────────────────────────────────────────────────────

class TestBackoffJitter:
    def test_exponential_backoff(self):
        cfg = RetryConfig(max_attempts=4, delay=1.0, backoff=2.0)
        assert cfg.wait_for(1) == 1.0
        assert cfg.wait_for(2) == 2.0
        assert cfg.wait_for(3) == 4.0

    def test_constant_delay_with_backoff_one(self):
        cfg = RetryConfig(max_attempts=4, delay=0.5, backoff=1.0)
        assert cfg.wait_for(1) == 0.5
        assert cfg.wait_for(2) == 0.5
        assert cfg.wait_for(3) == 0.5

    def test_jitter_adds_randomness(self):
        cfg = RetryConfig(max_attempts=3, delay=1.0, backoff=1.0, jitter=0.5)
        waits = {cfg.wait_for(1) for _ in range(20)}
        assert len(waits) > 1  # Should vary

    def test_jitter_within_bounds(self):
        cfg = RetryConfig(max_attempts=3, delay=1.0, backoff=1.0, jitter=0.5)
        for _ in range(50):
            w = cfg.wait_for(1)
            assert 1.0 <= w <= 1.5


# ── Timeout ───────────────────────────────────────────────────────────────────

class TestTimeout:
    def test_timeout_raises_on_slow_function(self):
        @retry(max_attempts=2, delay=0, timeout=0.1)
        def slow():
            time.sleep(5)
            return "done"
        with pytest.raises(RetryError) as exc_info:
            slow()
        assert isinstance(exc_info.value.last_exception, TimeoutError)

    def test_timeout_does_not_trigger_on_fast_function(self):
        @retry(max_attempts=2, delay=0, timeout=5.0)
        def fast():
            return "fast"
        assert fast() == "fast"

    def test_timeout_counted_as_retry(self):
        attempts = {"n": 0}
        @retry(max_attempts=3, delay=0, timeout=0.05)
        def slow():
            attempts["n"] += 1
            time.sleep(1)
        with pytest.raises(RetryError):
            slow()
        assert attempts["n"] == 3


# ── Hooks ─────────────────────────────────────────────────────────────────────

class TestHooks:
    def test_on_retry_called_on_each_retry(self):
        on_retry = MagicMock()
        fn = make_flaky(2)
        retry(fn, max_attempts=5, delay=0, on_retry=on_retry)()
        assert on_retry.call_count == 2
        ctx = on_retry.call_args_list[0][0][0]
        assert isinstance(ctx, RetryContext)

    def test_on_failure_called_when_exhausted(self):
        on_failure = MagicMock()
        @retry(max_attempts=2, delay=0, on_failure=on_failure)
        def fn():
            raise RuntimeError("x")
        with pytest.raises(RetryError):
            fn()
        assert on_failure.call_count == 1

    def test_on_success_called_on_success(self):
        on_success = MagicMock()
        @retry(max_attempts=3, delay=0, on_success=on_success)
        def fn():
            return 1
        fn()
        assert on_success.call_count == 1

    def test_on_retry_not_called_on_first_success(self):
        on_retry = MagicMock()
        @retry(max_attempts=3, delay=0, on_retry=on_retry)
        def fn():
            return 1
        fn()
        on_retry.assert_not_called()


# ── Async support ─────────────────────────────────────────────────────────────

class TestAsync:
    def test_async_success(self):
        @retry(max_attempts=3, delay=0)
        async def fn():
            return "async_ok"
        assert asyncio.run(fn()) == "async_ok"

    def test_async_retries(self):
        calls = {"n": 0}
        @retry(max_attempts=5, delay=0)
        async def fn():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ValueError("fail")
            return "done"
        assert asyncio.run(fn()) == "done"

    def test_async_exhaustion(self):
        @retry(max_attempts=2, delay=0)
        async def fn():
            raise RuntimeError("always")
        with pytest.raises(RetryError):
            asyncio.run(fn())

    def test_async_timeout(self):
        @retry(max_attempts=2, delay=0, timeout=0.05)
        async def slow():
            await asyncio.sleep(5)
        with pytest.raises(RetryError) as exc_info:
            asyncio.run(slow())
        assert isinstance(exc_info.value.last_exception, asyncio.TimeoutError)


# ── RetryConfig ───────────────────────────────────────────────────────────────

class TestRetryConfig:
    def test_invalid_max_attempts(self):
        with pytest.raises(ValueError):
            RetryConfig(max_attempts=0)

    def test_invalid_delay(self):
        with pytest.raises(ValueError):
            RetryConfig(delay=-1)

    def test_invalid_backoff(self):
        with pytest.raises(ValueError):
            RetryConfig(backoff=0.5)

    def test_config_object_passed_to_decorator(self):
        cfg = RetryConfig(max_attempts=2, delay=0)
        @retry(config=cfg)
        def fn():
            raise RuntimeError("x")
        with pytest.raises(RetryError) as exc_info:
            fn()
        assert exc_info.value.attempts == 2


# ── Context manager ───────────────────────────────────────────────────────────

class TestAttemptContextManager:
    def test_basic_run(self):
        with attempt(max_attempts=3, delay=0) as r:
            result = r.run(lambda: 99)
        assert result == 99

    def test_run_with_args(self):
        def add(a, b):
            return a + b
        with attempt(max_attempts=3, delay=0) as r:
            result = r.run(add, 3, 4)
        assert result == 7

    def test_run_raises_retry_error(self):
        with pytest.raises(RetryError):
            with attempt(max_attempts=2, delay=0) as r:
                r.run(lambda: (_ for _ in ()).throw(RuntimeError("x")))

    def test_elapsed_is_set(self):
        with attempt(max_attempts=2, delay=0) as r:
            r.run(lambda: None)
        assert r.elapsed >= 0
