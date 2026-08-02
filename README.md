# Discord Drive

An SFTP server whose storage backend is Discord. Clients see an ordinary
filesystem — directories, random reads and writes, `truncate`, permissions,
timestamps — while the bytes themselves live as encrypted attachments on
Discord messages and the filesystem structure lives in MongoDB.

Files are split into 9 MB chunks, each encrypted with AES-256-CTR under its
own nonce and authenticated with HMAC-SHA256. The tags are kept in MongoDB,
never on Discord, so whoever controls the Discord side can neither read a
chunk nor forge one. The master key is random and stored wrapped under the
SFTP password (Argon2id), which is why changing the password costs one 32-byte
rewrite rather than re-uploading everything.

The tags cover identity as well as content: a file's name and the directory
it sits in, each directory's own name and place, and the set of entries a
directory holds. So an attacker holding the database — a leaked backup, stolen
Mongo credentials — cannot rename a file, move it, swap two files' names, or
delete one without it being caught on the next read or listing.

## Running it

```bash
cp .env.example .env   # then fill it in; every REQUIRED value has no default
docker compose up -d --build
```

`.env.example` documents each setting and the consequences of getting it
wrong. The server validates the whole set at startup and refuses to run with
an incomplete one, reporting every problem at once rather than one per
restart.

Then connect on the port you set (2222 by default):

```bash
sftp -P 2222 <SFTP_USER>@localhost
```

There is also a web API on `http://127.0.0.1:8080`, served from the same
process — deliberately, since a second process against the same MongoDB is the
replica problem described below. It is published on the host's loopback only,
so nothing on the network can reach it; `.env.example` covers what to change
to use it from a phone and what each option costs. Signing in unwraps the
master key into that process's memory for as long as the session lasts, which
is why the session has both an idle timeout and an absolute ceiling, and why
the browser can shorten them but not extend them.

**Back up `SFTP_PASSWORD`.** It wraps the key every stored file is encrypted
with; losing it means losing the files, not just the login.

The account itself lives in the database rather than in the environment: on
startup `SFTP_USER` and `SFTP_PASSWORD` are synchronised into a `users` row
holding an Argon2id hash, the id of that account's tree, and — through it —
that account's own wrapped master key. The environment is still authoritative
and there is still exactly one account, so nothing about running it changes.
What changed is that a second row would be a second account with a key of its
own, rather than a schema change. Adding one is not implemented.

One consequence worth knowing before restoring a backup: **`users` and
`keystore` have to travel together.** The account row is what says which
wrapped key belongs to this deployment, so restoring one collection without
the other leaves a key nothing points at. The server refuses to start rather
than creating a fresh one on top of data it could not read.

## Run exactly one replica

Do not `--scale` the service, do not point a second copy at the same MongoDB,
and do not overlap two of them during a deploy.

Open file handles coordinate through a dictionary that lives in the process,
consulted before every read and write to learn whether another handle has
committed a change underneath. A node it has never heard of means "nobody has
touched this" — true within one process, false with two. A second replica
would keep serving a stale chunk layout for files the first had already
rewritten: no error, no log line, just old bytes.

Supporting multiple replicas needs optimistic locking at the node level, not a
larger cache. It is on the roadmap and is not done.

## Tests

```bash
./venv/Scripts/python.exe -m pytest
```

455 tests, about 30 seconds, no credentials or network required — MongoDB and
the Discord API are faked. A green suite is not a substitute for a run against
real infrastructure; the fakes model neither rate limits nor attachment URL
expiry, and several of the bugs in the history here were only ever found by
hand against a real bot token.

A green suite is also not a substitute for running it inside the production
image, which is the point of pinning both to the same Python version:

```bash
docker run --rm --user root -v "$PWD:/repo" -w /repo discord-drive-sftp-discord-server \
  sh -c "pip install -q -r requirements-dev.txt && python -m pytest -q"
```

## Where things are

| File | What it holds |
| --- | --- |
| [`ROADMAP.md`](ROADMAP.md) | What is planned, every settled decision with the reasoning behind it, and a short changelog. Start here for "why is it like that". |
| [`BLUEPRINT.md`](BLUEPRINT.md) | Architecture, data flow, security design, and a frank list of the known weaknesses. Long; start here for "how does it work" — but §4.3/§4.4 and §7.2 predate the integrity work and have not been regenerated, so `ROADMAP.md` wins on any conflict. |
| [`SOP.md`](SOP.md) | Recurring problems and the order to check things in. Environment traps, mostly. |
| [`missing_info.md`](missing_info.md) | The original open questions and what each was finally decided to be. |
| [`session-handoff.md`](session-handoff.md) | Where the last session stopped: live data state, what is not committed, environment notes. Rewritten each session. |
| [`design-multi-user.md`](design-multi-user.md) | Part proposal, part description: the structural steps have landed, opening a second account has not. Its banner says which is which. |
| [`design-node-identity-integrity.md`](design-node-identity-integrity.md) | Built, and its banner lists the four places the plan and the result diverged. |
| [`CLAUDE.md`](CLAUDE.md) | Collaboration rules for AI-assisted work on this repo. |

Source lives in `src/`: `sftp.py` is the protocol surface, `web.py` the HTTP
one with `websession.py` and `webauth.py` behind it, `vfs.py` the filesystem,
`crypto.py` and `keystore.py` the encryption and key handling, `users.py`
accounts and the password check, `discord_api.py` and `ratelimit.py` the
Discord client, `db.py` MongoDB, `config.py` the settings, `main.py` startup
and shutdown.

## Status

Working and in real use against a real bot token, single user, single replica.
The known gaps are written down rather than glossed over — see the `[later]`
and `[parked]` items in `ROADMAP.md`.

The one worth knowing up front: whoever can write to MongoDB can restore a
file, and its parent directory, to an older copy of both, and it will verify.
Stopping that needs a monotonic counter they cannot reach; three ways of
providing one were evaluated and none was worth its cost. It is an accepted,
documented residual risk rather than an oversight. Everything else — altered
bytes, reordered or missing chunks, renames, moves, name swaps, deletions —
is caught.

The repository is mirrored to a GitHub remote; the history, and the reasoning
that lives in its commit messages, exist somewhere other than this machine.
