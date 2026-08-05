"""Destructive work that takes long enough to need a number attached to it.

Emptying the trash is one Discord round trip per attachment, serialised behind
the rate limiter. A few hundred files is minutes, and the HTTP request that
started it would have spent all of them holding a connection open with nothing
to say -- so the browser either shows a spinner that means nothing or a
progress bar that is an animation. This module is what makes the bar a
fraction of something real.

Three decisions worth stating, because each has a cheaper alternative that is
wrong:

**A job id that is polled, not an event stream.** The work outlives the
request, and it should also outlive the tab: closing it, reloading, or losing
the network mid-purge must not turn "still deleting" into "no idea". A stream
ties progress to a connection; an id does not, and reconnecting is a GET.

**Cancellation is cooperative and lands only between attachments.** See
`DiscordVFS.purge` for why that is the one safe seam. What cancelling buys is
stopping the rest, not undoing the part already done, and every message this
module produces has to keep saying so.

**A job dies with the session that started it.** It runs on that session's
`vfs`, which holds the unwrapped master key, and a job that kept working after
the session expired would be a background task quietly extending the lifetime
the session store exists to enforce. So the loop re-reads `session.vfs` each
time round and stops the moment it is gone.
"""

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

RUNNING = "running"
DONE = "done"
CANCELLED = "cancelled"
FAILED = "failed"

# How long a finished job stays readable. It only has to outlive the poll that
# was in flight when the work stopped, plus enough slack for a person to read
# the result -- long enough to be useful, short enough that an abandoned tab's
# job list does not become a record of everything ever deleted.
RETAIN_SECONDS = 300


@dataclass
class Job:
    """One batch purge, and how far through it is."""

    id: str
    kind: str
    root_id: str
    session_id: str
    total_attachments: int
    total_entries: int
    done_attachments: int = 0
    done_entries: int = 0
    nodes: int = 0
    state: str = RUNNING
    # What is being destroyed right now, so the UI can name it rather than
    # showing a bare percentage.
    current: str = ""
    error: str = ""
    # Set by `cancel`, read by the loop. Cooperative on purpose: the
    # alternative is cancelling the asyncio task, which could land in the
    # middle of a Discord delete or a Mongo write.
    cancel_requested: bool = False
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float = None
    task: object = None

    @property
    def finished(self) -> bool:
        return self.state != RUNNING

    def as_dict(self) -> dict:
        """The shape the API returns. No ids beyond the job's own."""
        return {
            "id": self.id,
            "kind": self.kind,
            "state": self.state,
            "current": self.current,
            "entries": {"done": self.done_entries, "total": self.total_entries},
            # The one that moves smoothly. `total` is counted up front by
            # `purge_cost`, so it does not drift as the work proceeds.
            "attachments": {"done": self.done_attachments,
                            "total": self.total_attachments},
            "nodes": self.nodes,
            "cancel_requested": self.cancel_requested,
            "error": self.error,
        }


class JobConflict(RuntimeError):
    """Raised when a tree already has a purge running."""


@dataclass
class JobRegistry:
    """Every job this process knows about, keyed by id. One per process.

    Bounded by time rather than count, the same way `SessionStore` is: a
    finished job is a few hundred bytes and is swept once anybody can no longer
    reasonably be waiting for it.
    """

    _jobs: dict = field(default_factory=dict)

    def running_for_tree(self, root_id: str):
        """The live job on this tree, or `None`.

        There may be at most one, and that is a correctness requirement rather
        than a policy: two concurrent purges under the same parent both stage
        that parent's next entry tag into `entries_mac_pending`, and the second
        would overwrite the first's staged value. The tag that finally gets
        promoted would then describe a child set that never existed, and the
        directory would stop listing.
        """
        for job in self._jobs.values():
            if job.root_id == root_id and not job.finished:
                return job
        return None

    def sweep(self, *, now=None) -> int:
        now = time.monotonic() if now is None else now
        stale = [
            job_id for job_id, job in self._jobs.items()
            if job.finished and job.finished_at is not None
            and now - job.finished_at >= RETAIN_SECONDS
        ]
        for job_id in stale:
            del self._jobs[job_id]
        return len(stale)

    def get(self, job_id: str, *, root_id: str):
        """A job by id, but only to a caller holding the same tree.

        Scoped to `root_id` rather than to the session that started it, so that
        signing in again -- or watching from a second tab -- still finds the
        purge that is running. The job's *lifetime* is still tied to its
        original session; see the module docstring.
        """
        job = self._jobs.get(job_id)
        if job is None or job.root_id != root_id:
            return None
        return job

    def list_for_tree(self, root_id: str) -> list:
        return [job for job in self._jobs.values() if job.root_id == root_id]

    def list_for_tree_all(self) -> list:
        """Every job, whatever tree it belongs to. For shutdown only."""
        return list(self._jobs.values())

    def cancel(self, job: Job) -> bool:
        """Ask a job to stop. False if it had already finished."""
        if job.finished:
            return False
        job.cancel_requested = True
        return True

    async def start_purge(self, session, *, node_ids: list, kind: str) -> Job:
        """Begin destroying `node_ids` in the background, returning the job.

        The cost of the whole batch is counted before anything is destroyed.
        That is a read-only walk of every subtree involved, and it is what buys
        a denominator that does not move -- the alternative, counting as we go,
        produces a bar that reaches 90% and stays there.
        """
        self.sweep()
        if self.running_for_tree(session.root_id) is not None:
            raise JobConflict(
                "a purge is already running on this drive; wait for it to "
                "finish or cancel it first")

        vfs = session.vfs
        total_attachments = 0
        countable = []
        for node_id in node_ids:
            cost = await vfs.purge_cost(node_id)
            total_attachments += cost["attachments"]
            countable.append(node_id)

        job = Job(
            id=secrets.token_urlsafe(12),
            kind=kind,
            root_id=session.root_id,
            session_id=session.id,
            total_attachments=total_attachments,
            total_entries=len(countable),
        )
        self._jobs[job.id] = job
        job.task = asyncio.create_task(self._run(job, session, countable))
        return job

    async def _run(self, job: Job, session, node_ids: list):
        from src.vfs import NotFound, Unsupported

        try:
            for node_id in node_ids:
                # Re-read the session's vfs every time round rather than
                # capturing it: `SessionStore.drop` sets it to None, and that
                # is the signal that this job's key is no longer supposed to
                # exist. Holding our own reference would keep it alive.
                vfs = session.vfs
                if vfs is None:
                    job.state = CANCELLED
                    job.error = "session ended"
                    break
                if job.cancel_requested:
                    job.state = CANCELLED
                    break

                node = await vfs.get_node_by_id(node_id)
                job.current = (node or {}).get("filename") or ""

                def tick():
                    job.done_attachments += 1

                try:
                    result = await vfs.purge(
                        node_id,
                        on_attachment=tick,
                        cancelled=lambda: job.cancel_requested,
                    )
                except NotFound:
                    # Something else purged it, or a restore took it out of the
                    # trash, between the cost walk and here. Not an error: the
                    # node is gone, which is what was asked for.
                    job.done_entries += 1
                    continue
                except Unsupported as exc:
                    job.state = FAILED
                    job.error = str(exc)
                    break

                job.nodes += result["nodes"]
                if result.get("cancelled"):
                    job.state = CANCELLED
                    break
                job.done_entries += 1
            else:
                job.state = DONE
        except asyncio.CancelledError:
            job.state = CANCELLED
            job.error = "server stopped"
            raise
        except Exception as exc:                      # noqa: BLE001
            # A batch that dies half way through has still destroyed
            # everything up to that point, and the number saying how much is
            # the only record of it. Swallowing the exception here is what
            # keeps that number reachable; it is logged, and the job reports
            # `failed` with the reason.
            job.state = FAILED
            job.error = f"{type(exc).__name__}: {exc}"
            logger.exception("Purge job %s failed", job.id)
        finally:
            job.current = ""
            job.finished_at = time.monotonic()
            if job.state == RUNNING:                  # defensive
                job.state = DONE
            logger.info(
                "Purge job %s finished as %s: %d/%d entries, %d/%d attachments",
                job.id, job.state, job.done_entries, job.total_entries,
                job.done_attachments, job.total_attachments)

    def cancel_all(self):
        """Ask every live job to stop. For shutdown."""
        for job in self._jobs.values():
            if not job.finished:
                job.cancel_requested = True
