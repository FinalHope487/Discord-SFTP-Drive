# Security Policy

## Reporting a vulnerability

**Do not open a public issue.**

Use GitHub's private vulnerability reporting: the **Security** tab →
**Report a vulnerability**. That opens a private advisory visible only to the
maintainers.

Include what you would want if you were fixing it: the version or commit, what
an attacker needs to already have (database write access? the Discord side? a
valid session?), and the smallest reproduction you can manage.

Expect a first response within a week. This is a personal project, not a funded
one — there is no bounty, and the honest answer to some reports will be "this is
already documented as accepted".

## What is in scope

The security claim this project makes is narrow, so it is worth stating exactly:

**Whoever controls the Discord side can neither read a chunk nor forge one.**
Chunks are AES-256-CTR under per-chunk nonces, authenticated with HMAC-SHA256,
and the tags never leave the metadata store.

**Whoever holds the metadata store cannot rename a file, move it, swap two
files' names, or delete one without it being caught** on the next read or
listing. Tags cover identity — filename, parent directory, and each directory's
set of entries — not just content.

Anything that breaks either of those is in scope. So is anything that leaks the
master key or the password, escapes path resolution, or lets one session act as
another.

## What is already known and accepted

These are documented decisions, not undiscovered bugs. Reports about them will
be closed with a pointer here.

| Behaviour | Why it is accepted |
|---|---|
| **Whole-file rollback is not detected.** Someone who can write to the database can restore a file *and its parent directory* to an older copy of both, and it verifies. | Stopping it needs a monotonic counter the attacker cannot reach. Pinning one in Discord, in a local append-only file, and in an external KMS/TPM were all evaluated; none was worth its cost. Every *other* tampering is caught. |
| **Permission bits and timestamps are not covered by integrity tags.** | They are not content. Covering them would mean recomputing a tag over untouched bytes on every `chmod`. |
| **Losing the password loses the data, permanently.** | The master key is wrapped under it. A recovery path would be a second way in, which is the thing being avoided. |
| **Concurrent writes: last writer wins.** | Cross-connection guarantees cover visibility of committed state, not write exclusion. Node-level optimistic locking is on the roadmap. |
| **Running two replicas serves stale data silently.** | Documented as a hard constraint. Same root cause as the row above. |
| **The metadata store learns filenames, sizes and structure.** | It is the trusted side by design. Only the chunks are encrypted at rest on Discord. |

## Out of scope

- Discord terminating your bot or account. That is a platform risk disclosed in
  the README, not a vulnerability in this code.
- Anything requiring physical access to an unlocked machine with a live session,
  since the master key is in that process's memory by design.
- Denial of service against your own self-hosted instance.
