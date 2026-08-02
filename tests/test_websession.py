"""The session store, and the two deadlines that bound a key's time in memory.

Driven directly rather than over HTTP because these are clock properties, and
a test that had to wait ten minutes for an idle timeout would never be run.
`now` is injected everywhere for that reason -- the store never reads the
clock behind a caller's back.
"""

import pytest

from src.websession import SessionStore

KEY = b"k" * 32


def store(idle=600, absolute=7200):
    return SessionStore(idle_ceiling=idle, absolute_ceiling=absolute)


def make(s, *, now=0.0, idle=None, absolute=None):
    return s.create(username="tester", root_id="root", key=KEY,
                    idle=idle, absolute=absolute, now=now)


def test_a_fresh_session_resolves():
    s = store()
    session = make(s, now=0.0)
    assert s.get(session.id, now=1.0) is session


def test_the_id_and_csrf_token_are_different_secrets():
    s = store()
    session = make(s)
    assert session.id != session.csrf_token
    assert len(session.id) > 20 and len(session.csrf_token) > 20


def test_two_sessions_never_share_an_id():
    s = store()
    assert make(s).id != make(s).id


def test_a_session_carries_a_vfs_bound_to_its_own_root():
    s = store()
    session = s.create(username="tester", root_id="root:someone", key=KEY)
    assert session.vfs.root_id == "root:someone"
    assert session.vfs.key == KEY


# ------------------------------------------------------------------ expiry


def test_an_idle_session_expires():
    s = store(idle=600, absolute=7200)
    session = make(s, now=0.0)
    assert s.get(session.id, now=599.0) is session
    assert s.get(session.id, now=1300.0) is None


def test_activity_pushes_the_idle_deadline_back():
    s = store(idle=600, absolute=7200)
    session = make(s, now=0.0)
    for moment in (500.0, 1000.0, 1500.0, 2000.0):
        assert s.get(session.id, now=moment) is session


def test_the_absolute_ceiling_is_not_pushed_back_by_activity():
    """The reason there are two deadlines rather than one.

    Without this, a tab left open with any background polling in it keeps a
    master key in memory until the process restarts -- the idle timeout is
    refreshed for ever and never elapses.
    """
    s = store(idle=600, absolute=7200)
    session = make(s, now=0.0)
    for moment in range(500, 7200, 500):
        assert s.get(session.id, now=float(moment)) is session
    assert s.get(session.id, now=7200.0) is None


def test_an_expired_session_is_forgotten_not_merely_refused():
    s = store(idle=10, absolute=100)
    session = make(s, now=0.0)
    s.get(session.id, now=50.0)
    assert len(s) == 0, "the key was still being held after the deadline"


def test_dropping_a_session_releases_its_key():
    # Python cannot wipe the bytes, so this is the same best effort the SFTP
    # layer makes when a connection ends: stop referring to them.
    s = store()
    session = make(s)
    assert s.drop(session.id) is True
    assert session.key == b""
    assert session.vfs is None
    assert s.get(session.id) is None


def test_the_sweeper_pass_drops_abandoned_sessions():
    """What actually enforces the absolute ceiling on a tab nobody returns to.

    `get` refusing an expired session only helps on a request that never
    comes; without the sweep, an abandoned session's key sits in memory until
    the process restarts.
    """
    s = store(idle=10, absolute=100)
    make(s, now=0.0)
    make(s, now=0.0)
    assert len(s) == 2
    assert s.sweep(now=500.0) == 2
    assert len(s) == 0


# ----------------------------------------------------------------- clamping


def test_a_client_may_ask_for_a_shorter_session():
    s = store(idle=600, absolute=7200)
    session = make(s, idle=60, absolute=300)
    assert (session.idle_seconds, session.absolute_seconds) == (60, 300)


def test_a_client_cannot_ask_for_a_longer_one():
    """The ceiling is a security control, not a default.

    A browser that could extend its own session would be handing that control
    to whoever stole the cookie.
    """
    s = store(idle=600, absolute=7200)
    session = make(s, idle=99999, absolute=99999)
    assert (session.idle_seconds, session.absolute_seconds) == (600, 7200)


def test_no_preference_takes_the_ceiling():
    s = store(idle=600, absolute=7200)
    session = make(s)
    assert (session.idle_seconds, session.absolute_seconds) == (600, 7200)


@pytest.mark.parametrize("bad", [0, -1, -99999])
def test_a_nonsense_lifetime_takes_the_ceiling(bad):
    # A session expiring instantly is indistinguishable from a broken sign-in,
    # so this reads as "no preference" rather than as an instruction.
    s = store(idle=600, absolute=7200)
    session = make(s, idle=bad, absolute=bad)
    assert (session.idle_seconds, session.absolute_seconds) == (600, 7200)


def test_an_idle_window_longer_than_the_absolute_one_is_pulled_down():
    # Otherwise it is a setting that can never take effect.
    s = store(idle=600, absolute=7200)
    session = make(s, idle=600, absolute=120)
    assert session.idle_seconds == 120


def test_expires_in_reports_the_nearer_deadline():
    s = store(idle=600, absolute=7200)
    session = make(s, now=0.0)
    assert session.expires_in(0.0) == 600
    session.last_seen = 7000.0
    assert session.expires_in(7000.0) == 200      # absolute wins now
