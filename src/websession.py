"""HTTP sessions: where an unwrapped master key lives between requests.

The SFTP side has an obvious answer to this. `validate_password` unwraps the
key, hands it to the connection, and the connection's end is the key's end.
HTTP has no such lifetime, and the two alternatives are both worse than
inventing one:

* **re-derive per request.** A login runs Argon2id twice -- once to verify the
  stored password hash, once to derive the KEK -- which is about 250ms and
  128 MiB. Per request, that is not a design, it is an outage.
* **put the key in the cookie.** Then stealing the cookie is stealing the key
  itself rather than a reference to it, and the key crosses the network on
  every single request.

So: the key stays in this process, in this module, and the browser gets an
opaque id that means nothing anywhere else. A restart drops every session,
deliberately -- surviving one would mean writing the master key down somewhere
weaker than the password, which is the exact property the keystore exists to
provide.

**The lifetimes here are ceilings, not defaults.** A client may ask for a
shorter session and gets it; asking for a longer one is clamped. A session
lifetime is a security control, and a control the browser can extend is a
control held by whoever stole the cookie.
"""

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field

from src.vfs import DiscordVFS

logger = logging.getLogger(__name__)

# 256 bits of entropy in the id and the CSRF token. Both are compared with
# `compare_digest`, and neither is ever logged.
_TOKEN_BYTES = 32


@dataclass
class WebSession:
    """One logged-in browser, and the key it unlocked.

    `vfs` is built once and kept, matching what the SFTP layer does per
    connection: the cross-handle version cache is process-global, so reusing
    the instance costs nothing and rebuilding it per request would only throw
    away the `root_id` pairing that makes a key safe to use at all.
    """

    id: str
    csrf_token: str
    username: str
    root_id: str
    key: bytes
    vfs: DiscordVFS
    created_at: float
    last_seen: float
    idle_seconds: int
    absolute_seconds: int

    def expired_at(self, now: float) -> str:
        """Why this session is over, or "" while it is still live."""
        if now - self.last_seen >= self.idle_seconds:
            return "idle"
        if now - self.created_at >= self.absolute_seconds:
            return "absolute"
        return ""

    def expires_in(self, now: float) -> int:
        """Seconds until the nearer of the two deadlines. For the UI's clock."""
        return max(0, int(min(self.last_seen + self.idle_seconds,
                              self.created_at + self.absolute_seconds) - now))


@dataclass
class SessionStore:
    """Every live session, keyed by id. One per process.

    Not an LRU and not bounded by count: eviction under memory pressure would
    log people out at exactly the moment the machine is busiest, and the
    entries are tiny. What bounds it is time -- `sweep` drops anything past
    either deadline, and `create` sweeps first, so an abandoned session cannot
    outlive its ceiling just because nobody asked about it.
    """

    idle_ceiling: int
    absolute_ceiling: int
    _sessions: dict = field(default_factory=dict)

    def clamp(self, idle=None, absolute=None):
        """What a client actually gets when it asks for a lifetime.

        Only downward. `None` means "no preference", which takes the ceiling.
        A value at or below 0 is nonsense rather than an instruction, so it
        also takes the ceiling -- a session that expires immediately would be
        indistinguishable from a broken login.
        """
        chosen_idle = self.idle_ceiling if not idle or idle <= 0 \
            else min(int(idle), self.idle_ceiling)
        chosen_absolute = self.absolute_ceiling if not absolute or absolute <= 0 \
            else min(int(absolute), self.absolute_ceiling)
        # An idle window longer than the absolute one can never elapse, so it
        # would be a setting that silently does nothing.
        return min(chosen_idle, chosen_absolute), chosen_absolute

    def create(self, *, username: str, root_id: str, key: bytes,
               idle=None, absolute=None, now=None) -> WebSession:
        now = time.monotonic() if now is None else now
        self.sweep(now=now)

        chosen_idle, chosen_absolute = self.clamp(idle, absolute)
        session = WebSession(
            id=secrets.token_urlsafe(_TOKEN_BYTES),
            csrf_token=secrets.token_urlsafe(_TOKEN_BYTES),
            username=username,
            root_id=root_id,
            key=key,
            vfs=DiscordVFS(key, root_id),
            created_at=now,
            last_seen=now,
            idle_seconds=chosen_idle,
            absolute_seconds=chosen_absolute,
        )
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str, *, now=None):
        """The live session for this id, or `None`. Touches `last_seen`.

        Expiry is checked on read rather than only in the sweep, so a session
        that passed its deadline is over from the first request after it --
        not from whenever a periodic task next happens to run.
        """
        if not session_id:
            return None
        now = time.monotonic() if now is None else now

        session = self._sessions.get(session_id)
        if session is None:
            return None

        reason = session.expired_at(now)
        if reason:
            self.drop(session_id)
            logger.info("Session for %r expired (%s)", session.username, reason)
            return None

        session.last_seen = now
        return session

    def drop(self, session_id: str):
        """Forget a session, releasing this process's reference to its key.

        Python cannot overwrite the bytes, so this drops the reference rather
        than erasing the key -- the same best effort the SFTP layer makes when
        a connection ends.
        """
        session = self._sessions.pop(session_id, None)
        if session is not None:
            session.key = b""
            session.vfs = None
        return session is not None

    def drop_all(self):
        for session_id in list(self._sessions):
            self.drop(session_id)

    def sweep(self, *, now=None) -> int:
        now = time.monotonic() if now is None else now
        dead = [sid for sid, s in self._sessions.items() if s.expired_at(now)]
        for session_id in dead:
            self.drop(session_id)
        return len(dead)

    def __len__(self):
        return len(self._sessions)


async def sweeper(store: SessionStore, interval: float = 60.0):
    """Drop expired sessions even while nobody is asking for them.

    `get` already refuses an expired session, so this is not what makes the
    deadline correct -- it is what stops an abandoned tab's key from sitting in
    memory until the process restarts. That is the whole point of an absolute
    ceiling, and without this it would only be enforced on the request that
    never comes.
    """
    while True:
        await asyncio.sleep(interval)
        dropped = store.sweep()
        if dropped:
            logger.info("Swept %d expired session(s)", dropped)
