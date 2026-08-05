"""Batch purge as a job: a real denominator, and a stop that means something.

Emptying the trash is one Discord round trip per attachment behind the rate
limiter, so the interesting states are all mid-flight. `fake_discord.before_delete`
is what makes them reachable: it holds the purge still between attachments,
which is exactly the seam cancellation is allowed to land on.

The claim these exist to hold down is the uncomfortable half of the feature --
**cancelling stops the rest, it does not give back what has already gone**.
A test that only checked "state == cancelled" would pass just as happily
against an implementation that quietly rolled back, or one that destroyed
everything anyway.
"""

import asyncio

import pytest

from src import jobs
from tests.test_web import _client_for, csrf, finished_job, sign_in


@pytest.fixture
async def app(fake_db, fake_discord, account):
    from src import web as web_mod
    return web_mod.create_app()


@pytest.fixture
async def client(app):
    c = _client_for(app)
    await c.start_server()
    try:
        yield c
    finally:
        await c.close()


async def _trash_ids(client):
    response = await client.get("/api/trash")
    return [entry["id"] for entry in (await response.json())["entries"]]


async def _seed_trash(client, headers, names, *, body=b"x"):
    """Write each name, then delete it, leaving it in the trash."""
    for name in names:
        await client.put(f"/api/file?path=/{name}", data=body, headers=headers)
        await client.delete(f"/api/file?path=/{name}", headers=headers)


class Gate:
    """Holds every delete until released, counting arrivals.

    One barrier rather than one per call: a test wants "the purge has started
    and is stuck on an attachment", and reaching in per message id would make
    the test depend on chunk ordering it has no reason to know.
    """

    def __init__(self):
        self.arrived = asyncio.Event()
        self.proceed = asyncio.Event()
        self.count = 0

    async def __call__(self, message_id):
        self.count += 1
        self.arrived.set()
        await self.proceed.wait()

    def release(self):
        self.proceed.set()

    async def wait_for_first(self, timeout=2.0):
        await asyncio.wait_for(self.arrived.wait(), timeout)


# ------------------------------------------------------------- the counting


async def test_the_denominator_is_counted_before_anything_is_destroyed(
        client, fake_discord):
    # The whole point of the up-front walk in `purge_cost`. A bar whose total
    # is discovered as it goes is a bar that reaches 90% and stays there.
    payload = await sign_in(client)
    headers = csrf(payload)
    await _seed_trash(client, headers, ["a.txt", "b.txt", "c.txt"])

    response = await client.post("/api/trash/empty", headers=headers)
    assert response.status == 202
    job = (await response.json())["job"]

    assert job["entries"] == {"done": 0, "total": 3}
    assert job["attachments"]["total"] == 3
    assert job["attachments"]["done"] == 0

    # Drain, so the fixture does not tear the server down mid-purge.
    assert (await finished_job(client, response))["state"] == "done"


async def test_progress_moves_while_the_work_is_in_flight(client, fake_discord):
    payload = await sign_in(client)
    headers = csrf(payload)
    await _seed_trash(client, headers, ["a.txt", "b.txt", "c.txt"])

    gate = Gate()
    fake_discord.before_delete = gate

    response = await client.post("/api/trash/empty", headers=headers)
    job_id = (await response.json())["job"]["id"]
    await gate.wait_for_first()

    stalled = (await (await client.get(f"/api/jobs/{job_id}")).json())["job"]
    assert stalled["state"] == "running"
    assert stalled["attachments"]["done"] == 0, "stuck on the first delete"
    assert stalled["attachments"]["total"] == 3

    gate.release()
    done = await finished_job(client, response)
    assert done["state"] == "done"
    assert done["attachments"] == {"done": 3, "total": 3}


# --------------------------------------------------------------- cancelling


async def test_cancelling_stops_the_rest_but_keeps_what_is_already_gone(
        client, fake_discord):
    # The honest half. Three files, each one attachment; let the first delete
    # through, cancel, and the first file must be gone for good while the other
    # two are still in the trash and still purgeable.
    payload = await sign_in(client)
    headers = csrf(payload)
    await _seed_trash(client, headers, ["a.txt", "b.txt", "c.txt"])
    before = len(fake_discord.store)
    assert before == 3

    gate = Gate()
    fake_discord.before_delete = gate

    response = await client.post("/api/trash/empty", headers=headers)
    job_id = (await response.json())["job"]["id"]
    await gate.wait_for_first()

    cancelled = await client.post(f"/api/jobs/{job_id}/cancel", headers=headers)
    assert cancelled.status == 200
    assert (await cancelled.json())["job"]["cancel_requested"] is True

    gate.release()
    job = await finished_job(client, response)
    assert job["state"] == "cancelled"

    # Exactly one attachment was destroyed: the one already in flight when the
    # cancellation arrived. Nothing came back.
    assert job["attachments"]["done"] == 1
    assert len(fake_discord.store) == 2

    # And the two survivors are still addressable, which is what makes
    # "cancel" recoverable rather than a way to strand data.
    assert len(await _trash_ids(client)) == 2


async def test_a_cancelled_entry_can_be_purged_again(client, fake_discord):
    # Cancelling mid-entry leaves a node whose chunks are partly destroyed.
    # It has to still be in the trash and still purgeable, or cancelling would
    # be a way to create something nobody can finish deleting.
    payload = await sign_in(client)
    headers = csrf(payload)
    await _seed_trash(client, headers, ["a.txt", "b.txt"])

    gate = Gate()
    fake_discord.before_delete = gate
    response = await client.post("/api/trash/empty", headers=headers)
    job_id = (await response.json())["job"]["id"]
    await gate.wait_for_first()
    await client.post(f"/api/jobs/{job_id}/cancel", headers=headers)
    gate.release()
    assert (await finished_job(client, response))["state"] == "cancelled"

    fake_discord.before_delete = None
    again = await finished_job(
        client, await client.post("/api/trash/empty", headers=headers))
    assert again["state"] == "done"
    assert await _trash_ids(client) == []
    assert fake_discord.store == {}


async def test_cancelling_a_finished_job_is_not_an_error(client):
    payload = await sign_in(client)
    headers = csrf(payload)
    await _seed_trash(client, headers, ["a.txt"])

    response = await client.post("/api/trash/empty", headers=headers)
    job = await finished_job(client, response)
    late = await client.post(f"/api/jobs/{job['id']}/cancel", headers=headers)
    assert late.status == 200
    assert (await late.json())["job"]["state"] == "done"


# ------------------------------------------------------- one purge at a time


async def test_a_second_purge_on_the_same_tree_is_refused(client, fake_discord):
    # Not a policy. Two concurrent purges under one parent both stage that
    # parent's next entry tag into `entries_mac_pending`, and the second
    # overwrites the first -- the tag finally promoted would describe a child
    # set that never existed and the directory would stop listing.
    payload = await sign_in(client)
    headers = csrf(payload)
    await _seed_trash(client, headers, ["a.txt", "b.txt"])

    gate = Gate()
    fake_discord.before_delete = gate
    first = await client.post("/api/trash/empty", headers=headers)
    await gate.wait_for_first()

    second = await client.post("/api/trash/empty", headers=headers)
    assert second.status == 409

    gate.release()
    assert (await finished_job(client, first))["state"] == "done"


async def test_a_new_purge_is_allowed_once_the_last_one_finished(client):
    payload = await sign_in(client)
    headers = csrf(payload)
    await _seed_trash(client, headers, ["a.txt"])
    assert (await finished_job(
        client, await client.post("/api/trash/empty", headers=headers)
    ))["state"] == "done"

    await _seed_trash(client, headers, ["b.txt"])
    assert (await finished_job(
        client, await client.post("/api/trash/empty", headers=headers)
    ))["state"] == "done"


# ------------------------------------------------------------------ reaching


async def test_a_reloaded_tab_finds_the_running_job(client, fake_discord):
    # The work outlives the request that started it, so it has to outlive the
    # tab too -- a progress bar nobody can get back to is barely better than
    # no progress bar.
    payload = await sign_in(client)
    headers = csrf(payload)
    await _seed_trash(client, headers, ["a.txt", "b.txt"])

    gate = Gate()
    fake_discord.before_delete = gate
    response = await client.post("/api/trash/empty", headers=headers)
    await gate.wait_for_first()

    listed = (await (await client.get("/api/jobs")).json())["jobs"]
    assert [job["state"] for job in listed] == ["running"]
    assert listed[0]["kind"] == "empty_trash"

    gate.release()
    await finished_job(client, response)


async def test_a_job_is_not_visible_to_another_drive(app, client, fake_discord):
    # Ownership is by tree. A job id is short and a caller holding a session on
    # a different drive must not be able to read or cancel one by guessing.
    payload = await sign_in(client)
    headers = csrf(payload)
    await _seed_trash(client, headers, ["a.txt"])

    gate = Gate()
    fake_discord.before_delete = gate
    response = await client.post("/api/trash/empty", headers=headers)
    job_id = (await response.json())["job"]["id"]
    await gate.wait_for_first()

    from src import web as web_mod

    registry = app[web_mod.JOBS]
    assert registry.get(job_id, root_id="some-other-tree") is None
    assert registry.get(job_id, root_id="") is None

    gate.release()
    await finished_job(client, response)


async def test_an_unknown_job_is_a_404(client):
    await sign_in(client)
    assert (await client.get("/api/jobs/nope")).status == 404


async def test_polling_a_job_needs_a_session(client):
    assert (await client.get("/api/jobs")).status == 401


async def test_cancelling_needs_the_csrf_token(client, fake_discord):
    payload = await sign_in(client)
    headers = csrf(payload)
    await _seed_trash(client, headers, ["a.txt"])

    gate = Gate()
    fake_discord.before_delete = gate
    response = await client.post("/api/trash/empty", headers=headers)
    job_id = (await response.json())["job"]["id"]
    await gate.wait_for_first()

    assert (await client.post(f"/api/jobs/{job_id}/cancel")).status == 403

    gate.release()
    await finished_job(client, response)


# ------------------------------------------------- the key outlives nothing


async def test_a_job_stops_when_its_session_ends(app, client, fake_discord):
    # The job runs on the session's `vfs`, which holds the unwrapped master
    # key. One that kept working after the session was dropped would be a
    # background task extending the lifetime the session store exists to
    # enforce.
    from src import web as web_mod

    payload = await sign_in(client)
    headers = csrf(payload)
    await _seed_trash(client, headers, ["a.txt", "b.txt", "c.txt"])

    gate = Gate()
    fake_discord.before_delete = gate
    response = await client.post("/api/trash/empty", headers=headers)
    job_id = (await response.json())["job"]["id"]
    await gate.wait_for_first()

    store = app[web_mod.SESSIONS]
    registry = app[web_mod.JOBS]
    job = registry.get(job_id, root_id=list(store._sessions.values())[0].root_id)
    store.drop_all()
    gate.release()

    for _ in range(200):
        if job.finished:
            break
        await asyncio.sleep(0.01)

    assert job.state == jobs.CANCELLED
    assert job.error == "session ended"
    # It stopped early rather than running the batch out.
    assert job.done_entries < job.total_entries


# ------------------------------------------------------------------ sweeping


def test_finished_jobs_are_swept_once_nobody_can_be_waiting():
    registry = jobs.JobRegistry()
    job = jobs.Job(id="j", kind="purge", root_id="tree", session_id="s",
                   total_attachments=0, total_entries=0)
    job.state = jobs.DONE
    job.finished_at = 1000.0
    registry._jobs[job.id] = job

    assert registry.sweep(now=1000.0 + jobs.RETAIN_SECONDS - 1) == 0
    assert registry.sweep(now=1000.0 + jobs.RETAIN_SECONDS) == 1
    assert registry.get("j", root_id="tree") is None


def test_a_running_job_is_never_swept():
    registry = jobs.JobRegistry()
    job = jobs.Job(id="j", kind="purge", root_id="tree", session_id="s",
                   total_attachments=1, total_entries=1)
    registry._jobs[job.id] = job
    assert registry.sweep(now=1_000_000.0) == 0
    assert registry.running_for_tree("tree") is job
