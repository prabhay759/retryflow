"""
retryflow.context
-----------------
Context manager interface for retryflow.

Usage:
    with attempt(max_attempts=3, delay=1) as r:
        result = r.run(some_function, arg1, arg2)
"""

import time
from typing import Any, Callable, Optional, Tuple, Type

from .core import RetryConfig, RetryContext, RetryError, _sync_retry


class attempt:
    """
    Context manager that executes a callable with retry logic.

    Example
    -------
    >>> with attempt(max_attempts=3, delay=0.5, timeout=5) as r:
    ...     data = r.run(fetch_data, url)

    Parameters
    ----------
    Same as RetryConfig / @retry decorator.
    """

    def __init__(
        self,
        max_attempts: int = 3,
        delay: float = 1.0,
        backoff: float = 2.0,
        jitter: float = 0.0,
        timeout: Optional[float] = None,
        exceptions: Tuple[Type[Exception], ...] = (Exception,),
        on_retry=None,
        on_failure=None,
        on_success=None,
        log_retries: bool = True,
        config: Optional[RetryConfig] = None,
    ):
        self._cfg = config or RetryConfig(
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
        self.result: Any = None
        self.last_error: Optional[Exception] = None
        self.total_attempts: int = 0
        self.elapsed: float = 0.0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False  # Never suppress exceptions from the with-block body

    def run(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute func(*args, **kwargs) with the configured retry policy.

        Returns the function's return value, or raises RetryError if
        all attempts are exhausted.
        """
        start = time.monotonic()
        try:
            self.result = _sync_retry(func, self._cfg, args, kwargs)
        except RetryError as e:
            self.last_error = e.last_exception
            self.total_attempts = e.attempts
            self.elapsed = time.monotonic() - start
            raise
        self.elapsed = time.monotonic() - start
        return self.result
