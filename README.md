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

**Back up `SFTP_PASSWORD`.** It wraps the key every stored file is encrypted
with; losing it means losing the files, not just the login.

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

343 tests, about 30 seconds, no credentials or network required — MongoDB and
the Discord API are faked. A green suite is not a substitute for a run against
real infrastructure; the fakes model neither rate limits nor attachment URL
expiry, and several of the bugs in the history here were only ever found by
hand against a real bot token.

The same suite runs inside the production image, which is the point of pinning
both to the same Python version:

```bash
docker run --rm --user root -v "$PWD:/repo" -w /repo discord-drive-sftp-discord-server \
  sh -c "pip install -q -r requirements-dev.txt && python -m pytest -q"
```

## Where things are

| File | What it holds |
| --- | --- |
| [`BLUEPRINT.md`](BLUEPRINT.md) | Architecture, data flow, security design, and a frank list of the known weaknesses. Long; start here for "how does it work". |
| [`ROADMAP.md`](ROADMAP.md) | What is planned, and — more useful — every settled decision with the reasoning behind it. Start here for "why is it like that". |
| [`SOP.md`](SOP.md) | Recurring problems and the order to check things in. Environment traps, mostly. |
| [`missing_info.md`](missing_info.md) | The original open questions and what each was finally decided to be. |
| [`design-multi-user.md`](design-multi-user.md) | A proposal, not a description. Multi-user support is not built. |
| [`CLAUDE.md`](CLAUDE.md) | Collaboration rules for AI-assisted work on this repo. |

Source lives in `src/`: `sftp.py` is the protocol surface, `vfs.py` the
filesystem, `crypto.py` and `keystore.py` the encryption and key handling,
`discord_api.py` and `ratelimit.py` the Discord client, `db.py` MongoDB,
`config.py` the settings, `main.py` startup and shutdown.

## Status

Working and in real use against a real bot token, single user, single replica.
The known gaps are written down rather than glossed over — see §7.2 of
`BLUEPRINT.md` and the `[next]` / `[later]` items in `ROADMAP.md`. The two
that matter most: file *names* and locations are not covered by the integrity
tags, and whole-file rollback by someone who can write to MongoDB is an
accepted, documented residual risk.
