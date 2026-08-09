"""`_wait_for_shutdown`'s `extra_stop` parameter -- what the standalone build
races against the container's signal handling.

Every deployment before this one asked to stop the same way: SIGTERM to the
process. The desktop shell cannot do that reliably on Windows -- measured
directly in the course of adding this, not assumed: `child.kill()` there is
an unconditional TerminateProcess no matter which signal is named, and
`taskkill` without `/f` refuses outright on a console process with no window
to close. `extra_stop` is a second, signal-free way in: any awaitable, raced
against the existing wait, either one ends it.

What is not retested here is the signal path itself. `loop.add_signal_handler`
is untouched by this change, and self-sending a real signal from a test is not
safe to do portably -- on Windows `os.kill(os.getpid(), signal.SIGTERM)` is
itself a TerminateProcess, which would kill the test runner rather than
exercise a handler.
"""

import asyncio

import pytest

from src.main import _wait_for_shutdown


async def test_extra_stop_ends_the_wait():
    """The whole point of the parameter."""
    fired = asyncio.Event()
    fired.set()

    async def already_done():
        await fired.wait()

    await asyncio.wait_for(_wait_for_shutdown(already_done()), timeout=2)


async def test_no_extra_stop_behaves_as_before():
    """The default. Every existing caller (the container) passes nothing, and
    this must not block forever or require one -- it should behave exactly as
    it did before this parameter existed, which for a coroutine with no
    signal ever delivered means it simply has not been asked to stop."""
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(_wait_for_shutdown(), timeout=0.2)


async def test_the_losing_side_is_cancelled_not_left_running():
    """When `extra_stop` wins, the internal signal-wait task must be cancelled
    rather than left pending -- an uncancelled task from a returned coroutine
    is exactly what produces a "Task was destroyed but it is pending" warning
    on every ordinary shutdown of a build that never even uses this feature.

    Proven by running `_wait_for_shutdown` and then asserting nothing is left
    running in this test's event loop once it returns -- `asyncio.all_tasks()`
    excluding the current one is empty, which it would not be if the losing
    side's task were merely abandoned rather than cancelled.
    """
    fired = asyncio.Event()
    fired.set()

    async def already_done():
        await fired.wait()

    before = asyncio.all_tasks() - {asyncio.current_task()}

    await asyncio.wait_for(_wait_for_shutdown(already_done()), timeout=2)

    # Cancellation is requested synchronously inside the `finally`, but a
    # cancelled task needs one more loop iteration to actually finish; give it
    # that before checking.
    await asyncio.sleep(0)

    leftover = asyncio.all_tasks() - {asyncio.current_task()} - before
    assert not leftover, f"tasks left running after shutdown: {leftover}"
