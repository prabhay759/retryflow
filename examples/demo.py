"""
retryflow — Examples
=====================
Run this file to see retryflow in action:
    python examples/demo.py
"""

import asyncio
import logging
import time
import random

# Configure logging so retry warnings are visible
logging.basicConfig(level=logging.WARNING, format="%(message)s")

from retryflow import retry, RetryConfig, RetryError, RetryContext, attempt

print("=" * 60)
print("  retryflow Demo")
print("=" * 60)


# ── Example 1: Basic decorator ────────────────────────────────────────────────
print("\n[1] Basic retry decorator (fails twice, succeeds 3rd)")

call_n = 0

@retry(max_attempts=5, delay=0.1, backoff=1.0)
def unstable_api():
    global call_n
    call_n += 1
    if call_n < 3:
        raise ConnectionError(f"Server unavailable (attempt {call_n})")
    return {"status": "ok", "data": [1, 2, 3]}

result = unstable_api()
print(f"  ✅ Result: {result}")


# ── Example 2: Exponential backoff + jitter ───────────────────────────────────
print("\n[2] Exponential backoff + jitter")
call_n = 0

@retry(max_attempts=4, delay=0.1, backoff=2.0, jitter=0.05)
def flaky_db():
    global call_n
    call_n += 1
    if call_n < 4:
        raise TimeoutError("DB timeout")
    return "Connected!"

result = flaky_db()
print(f"  ✅ Result: {result}")


# ── Example 3: Timeout per attempt ───────────────────────────────────────────
print("\n[3] Per-attempt timeout (0.3s timeout, function takes 0.1s)")

@retry(max_attempts=3, delay=0, timeout=0.3)
def fast_enough():
    time.sleep(0.1)
    return "Done in time!"

result = fast_enough()
print(f"  ✅ Result: {result}")


# ── Example 4: Retry only specific exceptions ─────────────────────────────────
print("\n[4] Retry only on ConnectionError, not ValueError")
call_n = 0

@retry(max_attempts=5, delay=0, exceptions=(ConnectionError,))
def selective():
    global call_n
    call_n += 1
    if call_n == 1:
        raise ConnectionError("retried")
    if call_n == 2:
        return "success after 1 retry"

result = selective()
print(f"  ✅ Result: {result}")


# ── Example 5: Hooks ──────────────────────────────────────────────────────────
print("\n[5] on_retry / on_success / on_failure hooks")
call_n = 0

def on_retry_hook(ctx: RetryContext):
    print(f"  ⚠️  Retry #{ctx.attempt}/{ctx.max_attempts} | waited {ctx.next_wait:.2f}s | err: {ctx.exception}")

def on_success_hook(ctx: RetryContext):
    print(f"  ✅ Succeeded on attempt #{ctx.attempt} after {ctx.elapsed:.2f}s")

@retry(
    max_attempts=4, delay=0.1, backoff=1.5,
    on_retry=on_retry_hook, on_success=on_success_hook
)
def hooked():
    global call_n
    call_n += 1
    if call_n < 3:
        raise RuntimeError("not ready yet")
    return "ready"

hooked()


# ── Example 6: on_failure hook ────────────────────────────────────────────────
print("\n[6] on_failure hook when all attempts exhausted")

def on_failure_hook(ctx: RetryContext):
    print(f"  ❌ Giving up after {ctx.attempts} attempts | last err: {ctx.exception}")

@retry(max_attempts=3, delay=0, on_failure=on_failure_hook)
def always_fails():
    raise RuntimeError("I never work")

try:
    always_fails()
except RetryError as e:
    print(f"  RetryError caught: {e.attempts} attempts, last: {type(e.last_exception).__name__}")


# ── Example 7: RetryConfig object ─────────────────────────────────────────────
print("\n[7] Reusable RetryConfig object")
call_n = 0

prod_config = RetryConfig(
    max_attempts=5,
    delay=0.05,
    backoff=2.0,
    jitter=0.02,
    timeout=2.0,
    exceptions=(ConnectionError, TimeoutError),
    log_retries=True,
)

@retry(config=prod_config)
def production_call():
    global call_n
    call_n += 1
    if call_n < 2:
        raise ConnectionError("prod flake")
    return "prod ok"

result = production_call()
print(f"  ✅ Result: {result}")


# ── Example 8: Context manager ────────────────────────────────────────────────
print("\n[8] Context manager (attempt)")
call_n = 0

def fetch(url):
    global call_n
    call_n += 1
    if call_n < 2:
        raise ConnectionError("not ready")
    return f"<html from {url}>"

with attempt(max_attempts=3, delay=0.05) as r:
    html = r.run(fetch, "https://example.com")
print(f"  ✅ Result: {html}")
print(f"  ⏱  Elapsed: {r.elapsed:.3f}s")


# ── Example 9: Async retry ────────────────────────────────────────────────────
print("\n[9] Async function retry")
call_n = 0

@retry(max_attempts=4, delay=0.05, timeout=1.0)
async def async_fetch(url: str):
    global call_n
    call_n += 1
    await asyncio.sleep(0.01)
    if call_n < 3:
        raise ConnectionError(f"async flake #{call_n}")
    return f"async data from {url}"

result = asyncio.run(async_fetch("https://api.example.com/data"))
print(f"  ✅ Result: {result}")


print("\n" + "=" * 60)
print("  All examples completed!")
print("=" * 60)
