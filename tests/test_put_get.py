"""`sftp.put()` / `sftp.get()` -- the parallel copier real clients use.

Distinct from the sequential write path in test_sftp_e2e: asyncssh's copier
issues several overlapping writes at once, which is the closest thing in the
suite to how FileZilla or rsync actually move a file.
"""

import os

import pytest

PAYLOAD_SIZE = 512 * 1024  # 8 chunks at the test chunk size


@pytest.fixture
def local_payload(tmp_path):
    path = tmp_path / "payload.bin"
    data = os.urandom(PAYLOAD_SIZE)
    path.write_bytes(data)
    return path, data


async def test_put_uploads_with_exact_size(sftp, local_payload):
    src, data = local_payload
    await sftp.put(str(src), "/upload.bin")
    st = await sftp.stat("/upload.bin")
    assert st.size == len(data)


async def test_get_returns_identical_bytes(sftp, local_payload, tmp_path):
    src, data = local_payload
    dst = tmp_path / "payload.out"

    await sftp.put(str(src), "/upload.bin")
    await sftp.get("/upload.bin", str(dst))

    assert dst.read_bytes() == data
