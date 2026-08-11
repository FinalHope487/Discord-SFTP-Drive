"""`DiscordFile.seek`, which exists only to answer asyncssh's hole probe.

asyncssh 2.21 added the `ranges@asyncssh.com` extension. Its client asks for
it on every `get()`, and its server side calls `seek(pos, SEEK_DATA)` directly
on the handle -- past every `SFTPServer` override in `src/sftp.py`. Before the
method existed, downloads by an asyncssh client failed on Linux with
"'DiscordFile' object has no attribute 'seek'".

**These tests are the only ones that can fail on Windows.** `os.SEEK_DATA`
does not exist here, so asyncssh binds a `_request_ranges` that never seeks,
and `test_put_get.py` passes on this platform whether the method is right,
wrong, or absent. It was absent and green locally while CI was red:
https://github.com/FinalHope487/Discord-SFTP-Drive/actions/runs/31452758429

The contract worth pinning is not "seek works". It is **that a hole is never
reported**, because under `sparse=True` a short copy is what the client
expects rather than an error: a hole claimed one byte early is a download that
arrives full of zeros with nothing logged. `test_a_real_tail_hole_is_still_
reported_as_data` is that assertion, and it is deliberately the opposite of an
optimisation someone will later be tempted to make.
"""

import errno
import os

import pytest

from src.vfs import SEEK_DATA, SEEK_HOLE
from tests.conftest import TEST_CHUNK_SIZE

PAYLOAD = os.urandom(TEST_CHUNK_SIZE + 1000)


async def _write(vfs, path, data):
    handle = await vfs.open(path, read=False, write=True, create=True)
    await handle.write_at(0, data)
    await handle.close()


def _ranges(handle, offset, length):
    """asyncssh's server-side loop, as `_request_ranges` runs it on Linux.

    A copy of somebody else's control flow is normally a bad test. It earns
    its place here because the platform that runs these tests cannot execute
    the original -- and because what it is checking is that the loop
    terminates and covers everything, which is a property of the pair rather
    than of either side alone.
    """
    end = offset
    limit = offset + length
    found = []

    try:
        while end < limit:
            start = handle.seek(end, SEEK_DATA)
            end = min(handle.seek(start, SEEK_HOLE), limit)
            found.append((start, end - start))

            if len(found) > 16:
                pytest.fail("the range loop did not terminate")
    except OSError as exc:
        if exc.errno != errno.ENXIO:
            raise

    return found


async def test_the_whole_file_is_one_range_of_data(vfs):
    await _write(vfs, "/blob.bin", PAYLOAD)
    handle = await vfs.open("/blob.bin", read=True, write=False)

    assert _ranges(handle, 0, handle.size) == [(0, len(PAYLOAD))]

    await handle.close()


async def test_a_real_tail_hole_is_still_reported_as_data(vfs):
    """The deliberate lie, and the reason for it.

    A grow-`truncate` leaves a hole at the tail -- `_covered_end()` knows
    where it starts, and reporting it here would save transferring those
    zeros. Answering honestly costs nothing when it is right and hands back a
    zero-filled file when it is wrong, with no error on either side. If this
    test is ever changed, the thing being given up is that asymmetry.
    """
    await _write(vfs, "/grown.bin", b"just a few bytes")
    handle = await vfs.open("/grown.bin", read=False, write=True)
    await handle.truncate_to(TEST_CHUNK_SIZE * 3)
    await handle.close()

    handle = await vfs.open("/grown.bin", read=True, write=False)

    assert handle.size == TEST_CHUNK_SIZE * 3
    assert handle._covered_end() < handle.size, "the fixture stopped being sparse"
    assert _ranges(handle, 0, handle.size) == [(0, TEST_CHUNK_SIZE * 3)]

    await handle.close()


async def test_there_is_no_data_and_no_hole_past_the_end(vfs):
    """What stops the loop when a client asks about a file that shrank.

    `limit` comes from the client's own earlier `fstat`, so it can outrun the
    file. POSIX answers ENXIO at and past the end, asyncssh catches exactly
    that errno, and the generator ends instead of yielding forever.
    """
    await _write(vfs, "/blob.bin", PAYLOAD)
    handle = await vfs.open("/blob.bin", read=True, write=False)

    for whence in (SEEK_DATA, SEEK_HOLE):
        with pytest.raises(OSError) as caught:
            handle.seek(handle.size, whence)
        assert caught.value.errno == errno.ENXIO

    assert _ranges(handle, 0, handle.size * 2) == [(0, len(PAYLOAD))]

    await handle.close()


async def test_any_other_whence_raises_rather_than_answering(vfs):
    """There is no file position here, so SEEK_SET has no honest answer.

    Returning one would be worse than failing: reads carry their own offset,
    so a caller that believed a seek had moved anything would read from
    somewhere else entirely.
    """
    await _write(vfs, "/blob.bin", PAYLOAD)
    handle = await vfs.open("/blob.bin", read=True, write=False)

    for whence in (os.SEEK_SET, os.SEEK_CUR, os.SEEK_END):
        with pytest.raises(NotImplementedError):
            handle.seek(0, whence)

    await handle.close()
