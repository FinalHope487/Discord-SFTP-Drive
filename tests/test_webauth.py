"""The login guard: what it bounds, and what it must never lock.

Two separate jobs, and a test file that mixed them up would let one silently
stop working. Resource exhaustion is bounded by a semaphore and a queue depth;
guessing is slowed by a lockout keyed on *where the attempt came from*.

The property worth stating out loud is the negative one: **no account is ever
locked**. With a single account, an account lockout is a denial of service
anyone can trigger by typing a wrong password, and the person locked out is
always the owner.
"""

import asyncio

import pytest

from src.webauth import (
    ADDRESS_THRESHOLD,
    BASE_LOCKOUT_SECONDS,
    DEVICE_THRESHOLD,
    FORGET_AFTER_SECONDS,
    MAX_LOCKOUT_SECONDS,
    LoginBusy,
    LoginGuard,
    LoginLocked,
)

IP = "203.0.113.7"
DEVICE = "device-a"


def fail(guard, n, *, address=IP, device=DEVICE, now=0.0):
    for _ in range(n):
        guard.record_failure(address, device, now=now)


# ----------------------------------------------------------------- lockouts


def test_a_few_failures_do_not_lock_anything():
    guard = LoginGuard()
    fail(guard, DEVICE_THRESHOLD - 1)
    guard.check(IP, DEVICE, now=0.0)          # does not raise


def test_enough_failures_lock_that_device():
    guard = LoginGuard()
    fail(guard, DEVICE_THRESHOLD)
    with pytest.raises(LoginLocked):
        guard.check(IP, DEVICE, now=0.0)


def test_the_lockout_lifts_on_its_own():
    guard = LoginGuard()
    fail(guard, DEVICE_THRESHOLD)
    guard.check(IP, DEVICE, now=BASE_LOCKOUT_SECONDS + 1)


def test_each_further_failure_doubles_the_wait():
    guard = LoginGuard()
    fail(guard, DEVICE_THRESHOLD)
    with pytest.raises(LoginLocked) as first:
        guard.check(IP, DEVICE, now=0.0)

    guard.record_failure(IP, DEVICE, now=0.0)
    with pytest.raises(LoginLocked) as second:
        guard.check(IP, DEVICE, now=0.0)

    assert second.value.retry_after > first.value.retry_after


def test_the_wait_is_capped():
    guard = LoginGuard()
    fail(guard, DEVICE_THRESHOLD + 40)
    with pytest.raises(LoginLocked) as locked:
        guard.check(IP, DEVICE, now=0.0)
    assert locked.value.retry_after <= MAX_LOCKOUT_SECONDS + 1


def test_a_different_device_at_the_same_address_is_not_locked_yet():
    """What the device id buys: precision, not protection.

    One bad browser must not lock out every other browser in the house -- but
    see the next test for the limit of that, which is the part that matters.
    """
    guard = LoginGuard()
    fail(guard, DEVICE_THRESHOLD)
    guard.check(IP, "device-b", now=0.0)      # does not raise


def test_clearing_the_device_cookie_does_not_escape_the_address_lockout():
    """The property the device id is *not* allowed to weaken.

    A device id is a cookie; anybody can drop it and arrive as somebody new.
    If that reset the counter, the precise half of the lockout would be a way
    to switch the lockout off. The address-level counter underneath is what
    actually bounds an attacker, and arriving as a new device every time makes
    it arrive sooner rather than later.
    """
    guard = LoginGuard()
    for n in range(ADDRESS_THRESHOLD):
        guard.record_failure(IP, f"fresh-device-{n}", now=0.0)

    with pytest.raises(LoginLocked):
        guard.check(IP, "yet-another-fresh-device", now=0.0)


def test_a_different_address_is_unaffected():
    guard = LoginGuard()
    for n in range(ADDRESS_THRESHOLD + 5):
        guard.record_failure(IP, f"d{n}", now=0.0)
    guard.check("198.51.100.9", "somebody-else", now=0.0)


def test_no_account_is_ever_locked():
    """Stated as an assertion because it is a decision, not an omission.

    Nothing in this module takes a username. If a later change adds one, this
    test is what says the change was deliberate.
    """
    import inspect

    for name in ("check", "record_failure", "record_success"):
        params = set(inspect.signature(getattr(LoginGuard, name)).parameters)
        assert "username" not in params and "account" not in params, (
            f"LoginGuard.{name} grew an account parameter, which is how a "
            "lockout becomes a denial of service aimed at the owner")


def test_a_success_clears_that_device_but_not_the_address():
    """An attacker sharing an address must not get a free reset.

    Signing into their own account correctly proves who is at that browser,
    not that the address is friendly.
    """
    guard = LoginGuard()
    # Two short of the address threshold, spread over devices so no pair is
    # anywhere near its own.
    for n in range(ADDRESS_THRESHOLD - 2):
        guard.record_failure(IP, f"attacker-{n}", now=0.0)
    guard.record_failure(IP, DEVICE, now=0.0)

    guard.record_success(IP, DEVICE, now=0.0)
    assert ("pair", IP, DEVICE) not in guard._counters, "the browser was not cleared"

    # One more failure from anywhere at this address reaches the threshold. It
    # would not if the success had reset the address counter too, and that is
    # the free reset an attacker sharing the address must not get.
    guard.record_failure(IP, "attacker-x", now=0.0)
    with pytest.raises(LoginLocked):
        guard.check(IP, "attacker-y", now=0.0)


def test_stale_counters_are_forgotten():
    guard = LoginGuard()
    fail(guard, DEVICE_THRESHOLD - 1)
    guard.check(IP, DEVICE, now=FORGET_AFTER_SECONDS + 1)
    assert guard._counters == {}


# -------------------------------------------------------------- concurrency


async def test_only_so_many_derivations_run_at_once():
    """The bound that stops a sign-in endpoint from being a memory bomb.

    Argon2id is 64 MiB per run and a sign-in does two. asyncssh caps its own
    concurrent connections, so the SFTP path was bounded by accident; this is
    what bounds the HTTP one.
    """
    guard = LoginGuard(concurrency=2, queue=99)
    peak = 0
    live = 0
    release = asyncio.Event()

    async def attempt():
        nonlocal peak, live
        async with guard.slot():
            live += 1
            peak = max(peak, live)
            await release.wait()
            live -= 1

    tasks = [asyncio.create_task(attempt()) for _ in range(8)]
    await asyncio.sleep(0.05)
    assert peak == 2, f"{peak} derivations ran at once"
    release.set()
    await asyncio.gather(*tasks)


async def test_a_full_queue_is_refused_rather_than_grown():
    """An unbounded queue is the same failure with extra steps.

    The requests would pile up, all eventually time out having achieved
    nothing, and the memory they were queued for is spoken for the whole time.
    Refusing is the honest answer.
    """
    guard = LoginGuard(concurrency=1, queue=2)
    release = asyncio.Event()

    async def attempt():
        async with guard.slot():
            await release.wait()

    holder = asyncio.create_task(attempt())
    await asyncio.sleep(0.02)
    waiters = [asyncio.create_task(attempt()) for _ in range(2)]
    await asyncio.sleep(0.02)

    async def one_more():
        async with guard.slot():
            pass

    # Bounded, so that removing the queue limit makes this *fail* rather than
    # deadlock the suite. Without the wait_for, the extra attempt simply
    # queues behind a semaphore nobody is going to release, and a hanging test
    # is worse than a failing one -- it says nothing and blocks everything.
    with pytest.raises(LoginBusy):
        await asyncio.wait_for(one_more(), timeout=0.5)

    release.set()
    await asyncio.gather(holder, *waiters)


async def test_a_slot_is_released_even_when_the_body_raises():
    guard = LoginGuard(concurrency=1, queue=4)

    with pytest.raises(ValueError):
        async with guard.slot():
            raise ValueError("boom")

    async with guard.slot():        # would deadlock if the first leaked
        pass
