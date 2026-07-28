"""Proactive Discord rate-limit accounting.

Reacting to 429s alone is functional but wasteful. A 1GB upload is roughly
114 messages against a per-channel bucket far smaller than that, so the limit
gets hit repeatedly, and each hit spends one of the five retry attempts that
exist to absorb *unexpected* failures. Discord states the budget on every
response; reading it lets the client wait before being told to, and keeps the
retries for genuine errors.

Two mechanisms, doing different jobs:

* the bucket accounting below, which paces requests against the server's
  stated allowance;
* a concurrency cap, which bounds how many uploads are in flight at once.
  Without it a multi-chunk write fires every chunk simultaneously, blows
  through the allowance in one burst and then stalls -- slower overall than
  sending them at a steady rate.

Timing uses `time.monotonic()` throughout, and the reset comes from
`X-RateLimit-Reset-After` (a duration) rather than `X-RateLimit-Reset` (an
absolute server timestamp), so a clock offset between here and Discord cannot
turn into a permanent stall or a busy loop.
"""

import asyncio
import contextlib
import logging
import time

logger = logging.getLogger(__name__)

# Major parameters get their own bucket on Discord's side, so they have to
# stay in the key; every other id is collapsed or each message would be
# tracked as its own route.
_MAJOR_PARENTS = ("channels", "guilds", "webhooks")


def route_key(method: str, endpoint: str) -> str:
    """A stable per-route key, with minor ids collapsed."""
    parts = endpoint.split("/")
    out = []
    for i, part in enumerate(parts):
        keep_major = i > 0 and parts[i - 1] in _MAJOR_PARENTS
        out.append(part if (keep_major or not part.isdigit()) else "{id}")
    return f"{method} " + "/".join(out)


class _Bucket:
    __slots__ = ("remaining", "reset_at")

    def __init__(self):
        self.remaining = None   # None means "not learned yet"
        self.reset_at = 0.0


class RateLimiter:
    def __init__(self, max_concurrency: int = 4):
        self._buckets = {}        # bucket id -> _Bucket
        self._routes = {}         # route key -> bucket id
        self._global_until = 0.0
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------- accounting

    def _bucket_for(self, route: str):
        bucket_id = self._routes.get(route)
        if bucket_id is None:
            return None
        return self._buckets.get(bucket_id)

    def _delay(self, route: str) -> float:
        """Seconds to wait before this route may be used. 0 means go now."""
        now = time.monotonic()
        wait = self._global_until - now

        bucket = self._bucket_for(route)
        if bucket is not None and bucket.remaining is not None:
            # A bucket whose window already elapsed is spent, not blocking:
            # the next response will refresh it.
            if bucket.remaining <= 0 and bucket.reset_at > now:
                wait = max(wait, bucket.reset_at - now)

        return max(0.0, wait)

    async def acquire(self, route: str):
        """Block until this route is within its allowance.

        Loops rather than sleeping once: another task may consume the
        remaining allowance while this one waits.
        """
        while True:
            async with self._lock:
                delay = self._delay(route)
                if delay <= 0:
                    bucket = self._bucket_for(route)
                    if bucket is not None and bucket.remaining is not None:
                        # Spend the slot under the lock so two callers cannot
                        # both read the last unit as available.
                        bucket.remaining -= 1
                    return
            logger.debug("Rate limit: waiting %.2fs before %s", delay, route)
            await asyncio.sleep(delay)

    def update(self, route: str, headers):
        """Record what the response said about the remaining allowance."""
        bucket_id = headers.get("X-RateLimit-Bucket")
        if bucket_id is None:
            return
        self._routes[route] = bucket_id
        bucket = self._buckets.setdefault(bucket_id, _Bucket())

        remaining = headers.get("X-RateLimit-Remaining")
        reset_after = headers.get("X-RateLimit-Reset-After")
        try:
            if remaining is not None:
                # The server's count is authoritative; the optimistic
                # decrement in acquire() is only a guard against overshooting
                # between responses.
                bucket.remaining = int(remaining)
            if reset_after is not None:
                bucket.reset_at = time.monotonic() + float(reset_after)
        except (TypeError, ValueError):
            logger.debug("Unparseable rate-limit headers for %s", route)

    def note_429(self, headers, retry_after: float):
        """Record a rate limit that was hit anyway.

        A global limit pauses every route; a per-route one only closes its own
        bucket, so blocking everything would needlessly stall unrelated calls.
        """
        scope = headers.get("X-RateLimit-Scope")
        is_global = (
            str(headers.get("X-RateLimit-Global", "")).lower() == "true"
            or scope == "global"
        )
        if is_global:
            self._global_until = max(
                self._global_until, time.monotonic() + retry_after)
            logger.warning("Global Discord rate limit: pausing %.2fs", retry_after)

    @contextlib.asynccontextmanager
    async def slot(self, route: str):
        """Hold a concurrency slot and stay within the route's allowance."""
        async with self._semaphore:
            await self.acquire(route)
            yield
