"""What guards the login endpoint, and what it deliberately does not guard.

Adding an HTTP login gave this server an attack surface SSH never had. Two
different problems live here, and conflating them produces a guard that stops
neither.

**Resource exhaustion.** A login runs Argon2id twice at 64 MiB apiece. asyncssh
caps its own concurrent connections, so the SFTP path was bounded by
accident; an HTTP handler is bounded by nothing. A hundred parallel POSTs is
6.4 GB of allocation from an attacker who never has to guess a single
password, and the process that dies takes the SFTP server down with it. The
semaphore below bounds how many derivations run at once, and the queue depth
bounds how many may wait -- past that the answer is 503, because an unbounded
queue is the same failure with extra steps.

**Guessing.** Slowing an attacker down without giving them a way to lock the
real user out. **Accounts are never locked.** With one account on this
deployment, an account lockout is a denial of service anybody can trigger by
typing the wrong password a few times, and the person locked out is always
the owner. So the lockout keys on where the attempt came from.

That key is a pair: the source address, and a device id this server set in a
cookie. The device id is what makes a lockout *precise* -- one bad browser
behind a shared address does not lock the household -- but it is **not** the
security boundary, because anybody can clear a cookie and arrive as a new
device. The address-level counter underneath is the one that actually bounds
an attacker, and clearing cookies makes it arrive sooner rather than later.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Failures tolerated before a (address, device) pair starts being refused.
DEVICE_THRESHOLD = 5

# And before the address is refused whatever device it claims to be. Higher
# than the pair threshold because several honest devices can share an address,
# but reachable: it is what a cookie-clearing attacker runs into.
ADDRESS_THRESHOLD = 20

# Lockout doubles per failure past the threshold, from here up to this cap.
BASE_LOCKOUT_SECONDS = 30
MAX_LOCKOUT_SECONDS = 15 * 60

# How long a quiet counter takes to disappear. Long enough that an attacker
# cannot wait out a lockout much faster than the lockout itself.
FORGET_AFTER_SECONDS = 60 * 60


class LoginBusy(Exception):
    """Too many derivations already queued. Answered as 503, not 401."""


class LoginLocked(Exception):
    """This source is locked out. Carries how long is left."""

    def __init__(self, retry_after: int):
        super().__init__(f"locked out for another {retry_after}s")
        self.retry_after = retry_after


@dataclass
class _Counter:
    failures: int = 0
    locked_until: float = 0.0
    last_touched: float = 0.0


@dataclass
class LoginGuard:
    concurrency: int = 2
    queue: int = 16
    _counters: dict = field(default_factory=dict)
    _semaphore: asyncio.Semaphore = None
    _waiting: int = 0

    def _sem(self) -> asyncio.Semaphore:
        # Built lazily: a Semaphore binds to the running loop, and this object
        # is constructed while the app is being assembled rather than served.
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.concurrency)
        return self._semaphore

    # ------------------------------------------------------------- lockouts

    @staticmethod
    def _keys(address: str, device_id: str):
        """The pair key first, then the address key it can never escape."""
        return [("pair", address, device_id or "-"), ("addr", address)]

    def _threshold(self, key) -> int:
        return DEVICE_THRESHOLD if key[0] == "pair" else ADDRESS_THRESHOLD

    def check(self, address: str, device_id: str, *, now=None):
        """Raise `LoginLocked` if this source may not try right now."""
        now = time.monotonic() if now is None else now
        self._forget_stale(now)

        retry = 0
        for key in self._keys(address, device_id):
            counter = self._counters.get(key)
            if counter is not None and counter.locked_until > now:
                retry = max(retry, int(counter.locked_until - now) + 1)
        if retry:
            raise LoginLocked(retry)

    def record_failure(self, address: str, device_id: str, *, now=None):
        now = time.monotonic() if now is None else now
        for key in self._keys(address, device_id):
            counter = self._counters.setdefault(key, _Counter())
            counter.failures += 1
            counter.last_touched = now

            over = counter.failures - self._threshold(key)
            if over >= 0:
                penalty = min(BASE_LOCKOUT_SECONDS * (2 ** over),
                              MAX_LOCKOUT_SECONDS)
                counter.locked_until = now + penalty
                logger.warning(
                    "Login refused from %s (%s): %d failures, locked for %ds. "
                    "No account is locked by this.",
                    address, key[0], counter.failures, penalty)

    def record_success(self, address: str, device_id: str, *, now=None):
        """Clear the pair, but not the address.

        One correct password proves the person at this browser, not that the
        address is friendly -- an attacker who happens to share it would
        otherwise get a free reset by logging into their own account.
        """
        now = time.monotonic() if now is None else now
        self._counters.pop(("pair", address, device_id or "-"), None)

    def _forget_stale(self, now: float):
        stale = [k for k, c in self._counters.items()
                 if now - c.last_touched > FORGET_AFTER_SECONDS
                 and c.locked_until <= now]
        for key in stale:
            del self._counters[key]

    # ---------------------------------------------------------- concurrency

    def slot(self):
        """Async context manager bounding concurrent key derivations."""
        return _Slot(self)


class _Slot:
    def __init__(self, guard: LoginGuard):
        self._guard = guard

    async def __aenter__(self):
        guard = self._guard
        if guard._waiting >= guard.queue:
            # Refuse rather than queue. An unbounded queue turns a burst into
            # a pile of requests that all time out having achieved nothing,
            # while the memory they are waiting for is still spoken for.
            raise LoginBusy()
        guard._waiting += 1
        try:
            await guard._sem().acquire()
        finally:
            guard._waiting -= 1
        return self

    async def __aexit__(self, *exc):
        self._guard._sem().release()
        return False
