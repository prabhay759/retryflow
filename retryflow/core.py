"""
retryflow.core
--------------
Core retry logic: decorator, config, exceptions, async support, timeout.
"""

import time
import random
import asyncio
import logging
import functools
import threading
from typing import Any, Callable, Optional, Sequence, Type, Tuple, Union

logger = logging.getLogger("retryflow")


class RetryError(Exception):
    """Raised when all retry attempts are exhausted."""

    def __init__(self, message: str, last_exception: Exception, attempts: int):
        super().__init__(message)
        self.last_exception = last_exception
        self.attempts = attempts

    def __str__(self):
        return (
            f"RetryError: All {self.attempts} attempt(s) failed. "
            f"Last error: {type(self.last_exception).__name__}: {self.last_exception}"
        )


class RetryContext:
    """Passed to hooks — carries info about the current retry state."""

    def __init__(
        self,
        attempt: int,
        max_attempts: int,
        exception: Optional[Exception],
        elapsed: float,
        next_wait: Optional[float],
        func_name: str,
    ):
        self.attempt = attempt
        self.max_attempts = max_attempts
        self.exception = exception
        self.elapsed = elapsed
        self.next_wait = next_wait
        self.func_name = func_name

    def __repr__(self):
        return (
            f"<RetryContext func={self.func_name!r} attempt={self.attempt}/"
            f"{self.max_attempts} elapsed={self.elapsed:.2f}s>"
        )


class RetryConfig:
    """
    Encapsulates all retry settings.

    Parameters
    ----------
    max_attempts : int
        Total number of attempts (including the first call). Default: 3.
    delay : float
        Base delay in seconds between attempts. Default: 1.0.
    backoff : float
        Multiplier applied to delay after each failure. 1.0 = constant delay,
        2.0 = exponential. Default: 2.0.
    jitter : float
        Random seconds (0 to jitter) added to each wait to avoid thundering herd.
        Default: 0.0.
    timeout : Optional[float]
        Max seconds allowed for a single attempt. Raises TimeoutError if exceeded.
        Default: None (no timeout).
    exceptions : Tuple[Type[Exception], ...]
        Only retry on these exception types. Default: (Exception,) — retries on all.
    on_retry : Optional[Callable[[RetryContext], None]]
        Called before each retry (not the first attempt).
    on_failure : Optional[Callable[[RetryContext], None]]
        Called when all attempts are exhausted.
    on_success : Optional[Callable[[RetryContext], None]]
        Called on a successful attempt (including first try).
    log_retries : bool
        Emit a warning log on each retry. Default: True.
    """

    def __init__(
        self,
        max_attempts: int = 3,
        delay: float = 1.0,
        backoff: float = 2.0,
        jitter: float = 0.0,
        timeout: Optional[float] = None,
        exceptions: Tuple[Type[Exception], ...] = (Exception,),
        on_retry: Optional[Callable[[RetryContext], None]] = None,
        on_failure: Optional[Callable[[RetryContext], None]] = None,
        on_success: Optional[Callable[[RetryContext], None]] = None,
        log_retries: bool = True,
    ):
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if delay < 0:
            raise ValueError("delay must be >= 0")
        if backoff < 1:
            raise ValueError("backoff must be >= 1")
        if jitter < 0:
            raise ValueError("jitter must be >= 0")
        if timeout is not None and timeout <= 0:
            raise ValueError("timeout must be > 0")

        self.max_attempts = max_attempts
        self.delay = delay
        self.backoff = backoff
        self.jitter = jitter
        self.timeout = timeout
        self.exceptions = tuple(exceptions)
        self.on_retry = on_retry
        self.on_failure = on_failure
        self.on_success = on_success
        self.log_retries = log_retries

    def wait_for(self, attempt: int) -> float:
        """Compute wait time before the given attempt number (1-indexed)."""
        wait = self.delay * (self.backoff ** (attempt - 1))
        if self.jitter:
            wait += random.uniform(0, self.jitter)
        return wait


# ── Timeout helper (thread-based, works for sync functions) ───────────────────

def _run_with_timeout(func, timeout, *args, **kwargs):
    """Run func(*args, **kwargs) with a wall-clock timeout (seconds)."""
    result = [None]
    exc = [None]

    def target():
        try:
            result[0] = func(*args, **kwargs)
        except Exception as e:
            exc[0] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(
            f"Function '{func.__name__}' timed out after {timeout}s"
        )
    if exc[0]:
        raise exc[0]
    return result[0]


# ── Sync retry ────────────────────────────────────────────────────────────────

def _sync_retry(func: Callable, cfg: RetryConfig, args, kwargs) -> Any:
    start = time.monotonic()
    last_exc: Optional[Exception] = None

    for attempt_num in range(1, cfg.max_attempts + 1):
        try:
            if cfg.timeout:
                result = _run_with_timeout(func, cfg.timeout, *args, **kwargs)
            else:
                result = func(*args, **kwargs)

            elapsed = time.monotonic() - start
            ctx = RetryContext(attempt_num, cfg.max_attempts, None, elapsed, None, func.__name__)
            if cfg.on_success:
                cfg.on_success(ctx)
            return result

        except cfg.exceptions as exc:
            last_exc = exc
            elapsed = time.monotonic() - start

            if attempt_num == cfg.max_attempts:
                break

            wait = cfg.wait_for(attempt_num)
            ctx = RetryContext(attempt_num, cfg.max_attempts, exc, elapsed, wait, func.__name__)

            if cfg.log_retries:
                logger.warning(
                    "[retryflow] '%s' failed (attempt %d/%d): %s: %s — retrying in %.2fs",
                    func.__name__, attempt_num, cfg.max_attempts,
                    type(exc).__name__, exc, wait,
                )
            if cfg.on_retry:
                cfg.on_retry(ctx)

            time.sleep(wait)

        except Exception:
            # Non-retryable exception — re-raise immediately
            raise

    # All attempts exhausted
    elapsed = time.monotonic() - start
    ctx = RetryContext(cfg.max_attempts, cfg.max_attempts, last_exc, elapsed, None, func.__name__)
    if cfg.on_failure:
        cfg.on_failure(ctx)
    raise RetryError(str(last_exc), last_exc, cfg.max_attempts)


# ── Async retry ───────────────────────────────────────────────────────────────

async def _async_retry(func: Callable, cfg: RetryConfig, args, kwargs) -> Any:
    start = time.monotonic()
    last_exc: Optional[Exception] = None

    for attempt_num in range(1, cfg.max_attempts + 1):
        try:
            if cfg.timeout:
                result = await asyncio.wait_for(func(*args, **kwargs), timeout=cfg.timeout)
            else:
                result = await func(*args, **kwargs)

            elapsed = time.monotonic() - start
            ctx = RetryContext(attempt_num, cfg.max_attempts, None, elapsed, None, func.__name__)
            if cfg.on_success:
                cfg.on_success(ctx)
            return result

        except cfg.exceptions as exc:
            last_exc = exc
            elapsed = time.monotonic() - start

            if attempt_num == cfg.max_attempts:
                break

            wait = cfg.wait_for(attempt_num)
            ctx = RetryContext(attempt_num, cfg.max_attempts, exc, elapsed, wait, func.__name__)

            if cfg.log_retries:
                logger.warning(
                    "[retryflow] '%s' failed (attempt %d/%d): %s: %s — retrying in %.2fs",
                    func.__name__, attempt_num, cfg.max_attempts,
                    type(exc).__name__, exc, wait,
                )
            if cfg.on_retry:
                cfg.on_retry(ctx)

            await asyncio.sleep(wait)

        except Exception:
            raise

    elapsed = time.monotonic() - start
    ctx = RetryContext(cfg.max_attempts, cfg.max_attempts, last_exc, elapsed, None, func.__name__)
    if cfg.on_failure:
        cfg.on_failure(ctx)
    raise RetryError(str(last_exc), last_exc, cfg.max_attempts)


# ── Public decorator ──────────────────────────────────────────────────────────

def retry(
    func: Optional[Callable] = None,
    *,
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    jitter: float = 0.0,
    timeout: Optional[float] = None,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable[[RetryContext], None]] = None,
    on_failure: Optional[Callable[[RetryContext], None]] = None,
    on_success: Optional[Callable[[RetryContext], None]] = None,
    log_retries: bool = True,
    config: Optional[RetryConfig] = None,
) -> Callable:
    """
    Decorator that retries a function on failure.

    Can be used with or without arguments:

        @retry
        def my_func(): ...

        @retry(max_attempts=5, delay=2, backoff=2, jitter=0.5, timeout=10)
        def my_func(): ...

        @retry(config=RetryConfig(max_attempts=5, delay=2))
        def my_func(): ...

    Works on both regular and async functions.
    """
    cfg = config or RetryConfig(
        max_attempts=max_attempts,
        delay=delay,
        backoff=backoff,
        jitter=jitter,
        timeout=timeout,
        exceptions=exceptions,
        on_retry=on_retry,
        on_failure=on_failure,
        on_success=on_success,
        log_retries=log_retries,
    )

    def decorator(fn: Callable) -> Callable:
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                return await _async_retry(fn, cfg, args, kwargs)
            return async_wrapper
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args, **kwargs):
                return _sync_retry(fn, cfg, args, kwargs)
            return sync_wrapper

    # Called as @retry (no parentheses)
    if func is not None:
        return decorator(func)

    # Called as @retry(...) (with parentheses)
    return decorator
