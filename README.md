# retryflow

> Smart retry engine for Python — exponential backoff, jitter, per-exception rules, per-attempt timeout, hooks, and full async support.

[![PyPI version](https://img.shields.io/pypi/v/retryflow.svg)](https://pypi.org/project/retryflow/)
[![Python](https://img.shields.io/pypi/pyversions/retryflow.svg)](https://pypi.org/project/retryflow/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen.svg)]()

---

## Why retryflow?

Every app that talks to a network, a database, or an external API will eventually hit a transient failure. `retryflow` gives you a battle-tested, zero-dependency retry engine that handles the hard parts:

- **Exponential backoff** so you don't hammer a struggling service
- **Jitter** to avoid thundering-herd problems across multiple clients
- **Per-attempt timeouts** so a single hung call can't block forever
- **Per-exception rules** so you only retry errors that make sense to retry
- **Lifecycle hooks** (`on_retry`, `on_success`, `on_failure`) for logging, alerting, metrics
- **Native async support** — works seamlessly with `async def` functions
- **Context manager API** for one-off retry blocks without decorating a function

---

## Installation

```bash
pip install retryflow
```

No dependencies. Requires Python 3.8+.

---

## Quick Start

```python
from retryflow import retry

@retry(max_attempts=3, delay=1.0, backoff=2.0)
def fetch_data(url):
    response = requests.get(url, timeout=5)
    response.raise_for_status()
    return response.json()
```

That's it. On failure, retryflow waits 1s, then 2s, then raises `RetryError`.

---

## Usage

### 1. Basic Decorator

```python
from retryflow import retry

@retry(max_attempts=5, delay=0.5, backoff=2.0, jitter=0.1)
def call_api():
    ...
```

Also works with no arguments (uses defaults):

```python
@retry
def call_api():
    ...
```

### 2. Per-Attempt Timeout

Stop a single attempt if it hangs beyond a time limit:

```python
@retry(max_attempts=3, delay=1.0, timeout=5.0)
def slow_query():
    # If this takes more than 5s, a TimeoutError is raised
    # and retryflow retries it
    return db.execute(heavy_query)
```

### 3. Retry Only Specific Exceptions

```python
@retry(
    max_attempts=5,
    delay=1.0,
    exceptions=(ConnectionError, TimeoutError)  # Only retry these
)
def connect():
    ...
```

Non-matching exceptions (e.g., `ValueError`) propagate immediately without retrying.

### 4. Lifecycle Hooks

```python
from retryflow import retry, RetryContext

def on_retry(ctx: RetryContext):
    print(f"Attempt {ctx.attempt}/{ctx.max_attempts} failed: {ctx.exception}")
    print(f"Retrying in {ctx.next_wait:.2f}s...")

def on_failure(ctx: RetryContext):
    alert_team(f"{ctx.func_name} failed after {ctx.attempts} attempts")

def on_success(ctx: RetryContext):
    metrics.record("api.success", tags={"attempt": ctx.attempt})

@retry(
    max_attempts=5,
    delay=1.0,
    on_retry=on_retry,
    on_failure=on_failure,
    on_success=on_success,
)
def critical_call():
    ...
```

### 5. Async Functions

Works exactly the same — retryflow detects `async def` automatically:

```python
@retry(max_attempts=4, delay=0.5, timeout=3.0)
async def async_fetch(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.json()
```

### 6. Reusable Config Object

```python
from retryflow import retry, RetryConfig

# Define once, apply everywhere
production_retry = RetryConfig(
    max_attempts=5,
    delay=1.0,
    backoff=2.0,
    jitter=0.3,
    timeout=10.0,
    exceptions=(ConnectionError, TimeoutError),
    log_retries=True,
)

@retry(config=production_retry)
def service_a(): ...

@retry(config=production_retry)
def service_b(): ...
```

### 7. Context Manager

For one-off retries without decorating a function:

```python
from retryflow import attempt

with attempt(max_attempts=3, delay=1.0, timeout=5.0) as r:
    data = r.run(fetch, "https://api.example.com/data")

print(f"Completed in {r.elapsed:.2f}s")
```

---

## API Reference

### `@retry` decorator

```python
retry(
    func=None,              # The function to wrap (when used without parentheses)
    *,
    max_attempts=3,         # Total attempts including the first call
    delay=1.0,              # Base delay in seconds between attempts
    backoff=2.0,            # Delay multiplier (1.0=constant, 2.0=exponential)
    jitter=0.0,             # Random seconds added to each wait (0 to jitter)
    timeout=None,           # Max seconds per attempt (None = no limit)
    exceptions=(Exception,),# Only retry on these exception types
    on_retry=None,          # Callable(RetryContext) called before each retry
    on_failure=None,        # Callable(RetryContext) called when all attempts fail
    on_success=None,        # Callable(RetryContext) called on success
    log_retries=True,       # Emit warning logs on each retry
    config=None,            # RetryConfig object (overrides all other params)
)
```

### `RetryConfig`

All the same parameters as `@retry`, packaged into a reusable object.

```python
cfg = RetryConfig(max_attempts=5, delay=1.0, backoff=2.0, timeout=10.0)
```

### `RetryContext`

Passed to hook callbacks with information about the current state.

| Attribute | Type | Description |
|---|---|---|
| `attempt` | `int` | Current attempt number (1-indexed) |
| `max_attempts` | `int` | Total configured attempts |
| `exception` | `Exception` | The exception that just occurred |
| `elapsed` | `float` | Total elapsed seconds since first attempt |
| `next_wait` | `float \| None` | Seconds until next retry (None on final attempt) |
| `func_name` | `str` | Name of the decorated function |

### `RetryError`

Raised when all attempts are exhausted.

| Attribute | Type | Description |
|---|---|---|
| `last_exception` | `Exception` | The final exception raised |
| `attempts` | `int` | Total number of attempts made |

### `attempt` context manager

```python
with attempt(max_attempts=3, delay=1.0, ...) as r:
    result = r.run(func, *args, **kwargs)
```

After the `with` block, `r.result`, `r.elapsed`, `r.last_error`, and `r.total_attempts` are available.

---

## Backoff Formula

```
wait = delay * (backoff ^ (attempt - 1)) + random(0, jitter)
```

| attempt | delay=1, backoff=2 | delay=0.5, backoff=3 |
|---|---|---|
| 1st retry | 1.0s | 0.5s |
| 2nd retry | 2.0s | 1.5s |
| 3rd retry | 4.0s | 4.5s |

---

## Running Tests

```bash
pip install pytest pytest-asyncio
pytest tests/ -v
```

---

## License

MIT © prabhay759
