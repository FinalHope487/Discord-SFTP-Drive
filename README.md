# Discord Drive

An SFTP server whose storage backend is Discord. Clients see an ordinary
filesystem — directories, random reads and writes, `truncate`, permissions,
timestamps — while the bytes live as encrypted attachments on Discord messages
and the structure lives in MongoDB.

Files are split into 9 MB chunks, each encrypted with AES-256-CTR under its own
nonce and authenticated with HMAC-SHA256. The tags live in MongoDB, never on
Discord, so whoever controls the Discord side can neither read a chunk nor forge
one. The master key is random and stored wrapped under the SFTP password
(Argon2id), so changing the password costs one 32-byte rewrite rather than
re-uploading everything.

Tags cover identity as well as content: a file's name and parent directory, each
directory's own name and place, and the set of entries it holds. An attacker
holding the database — a leaked backup, stolen Mongo credentials — cannot rename
a file, move it, swap two files' names, or delete one without it being caught on
the next read or listing.

## Running it

```bash
cp .env.example .env   # then fill it in; every REQUIRED value has no default
docker compose up -d --build
```

`.env.example` documents each setting and what getting it wrong costs. The
server validates the whole set at startup and reports every problem at once
rather than one per restart.

```bash
sftp -P 2222 <SFTP_USER>@localhost
```

There is also a file manager on `http://127.0.0.1:8080` — a web API and the
static client that uses it, both served from the same process as the SFTP
server. Deliberately the same process, since a second one against the same
MongoDB is the replica problem below. It is published on the host's loopback
only; `.env.example` covers reaching it from another device and what each
option costs. Signing in unwraps the master key into process memory for the life
of the session, which is why the session has both an idle timeout and an
absolute ceiling, and why the browser can shorten them but not extend them.

The client is a build product and is not in the repository:

```bash
cd client/app && npm install && npm run build
```

`docker-compose.yml` mounts `client/app/dist` into the container read-only, so
rebuilding the frontend costs one command and a refresh rather than an image
rebuild — which would drop every live session and every unwrapped key with them.
Until it is built, `/` serves a page saying so; the API and SFTP are unaffected.

There is a desktop app as well. It is a window with a size floor and a first-run
screen asking where the server is — it carries no copy of the client, because
the session cookie is `SameSite=Strict` and a page loaded from `file://` would
never be allowed to send it. [`BUILD.md`](BUILD.md) is the whole story, from an
empty machine to a `.exe` that runs on any Windows device:

```bash
cd client/shell && npm install && npm run dist
```

One account can be signed in from several places at once, which is what sharing
this drive looks like today. The status bar shows how many, and there is a
control to end the others without ending your own. Separate accounts are not
implemented — see `ROADMAP.md`, where that step is blocked on a password
recovery path.

**Back up `SFTP_PASSWORD`.** It wraps the key every stored file is encrypted
with; losing it loses the files, not just the login.

The account lives in the database rather than the environment: on startup
`SFTP_USER` and `SFTP_PASSWORD` are synchronised into a `users` row holding an
Argon2id hash, the id of that account's tree, and through it that account's own
wrapped master key. The environment is still authoritative and there is still
exactly one account. Adding a second is not implemented.

**`users` and `keystore` have to travel together** when restoring a backup. The
account row is what says which wrapped key belongs to this deployment, so
restoring one without the other leaves a key nothing points at. The server
refuses to start rather than creating a fresh one on top of data it could not
read.

## Run exactly one replica

Do not `--scale` the service, point a second copy at the same MongoDB, or
overlap two of them during a deploy.

Open file handles coordinate through a dictionary that lives in the process,
consulted before every read and write to learn whether another handle has
committed a change underneath. A node it has never heard of means "nobody has
touched this" — true within one process, false with two. A second replica would
keep serving a stale chunk layout for files the first had already rewritten: no
error, no log line, just old bytes.

Supporting multiple replicas needs optimistic locking at the node level, not a
larger cache. It is on the roadmap and is not done.

## Tests

```bash
./venv/Scripts/python.exe -m pytest
```

515 tests, about 25 seconds, no credentials or network required — MongoDB and
the Discord API are faked. The fakes model neither rate limits nor attachment
URL expiry, they do not enforce uniqueness, and they do not validate index
specifications — the trash shipped with a partial unique index MongoDB rejects
outright, and the suite stayed green for three days because nothing had
restarted against a real server. Several of the bugs in the history here were
only ever found by hand against a real bot token. A green suite is also not a
substitute for running inside the production image, which is the point of
pinning both to the same Python version:

```bash
docker run --rm --user root -v "$PWD:/repo" -w /repo discord-drive-sftp-discord-server \
  sh -c "pip install -q -r requirements-dev.txt && python -m pytest -q"
```

## Where things are

| File | What it holds |
| --- | --- |
| [`BUILD.md`](BUILD.md) | From an empty machine to a packaged `.exe`, and what is and is not inside it. |
| [`client/README.md`](client/README.md) | The two npm packages: the file manager and the desktop shell. |
| [`ROADMAP.md`](ROADMAP.md) | Plans, every settled decision with its reasoning, the original open questions and how each resolved, and a changelog. Start here for "why is it like that". |
| [`BLUEPRINT.md`](BLUEPRINT.md) | Architecture, data flow, security design, known weaknesses. Start here for "how does it work" — but §4.3/§4.4 and §7.2 predate the integrity work, so `ROADMAP.md` wins on any conflict. |
| [`SOP.md`](SOP.md) | Recurring problems and the order to check things in. Environment traps, mostly. |
| [`session-handoff.md`](session-handoff.md) | Where the last session stopped. Rewritten each session. |
| [`design-multi-user.md`](design-multi-user.md) | The structural steps have landed; opening a second account has not. Its banner says which is which. |
| [`design-node-identity-integrity.md`](design-node-identity-integrity.md) | Built; its banner lists the four places plan and result diverged. |
| [`CLAUDE.md`](CLAUDE.md) | Collaboration rules for AI-assisted work on this repo. |

Source lives in `src/`: `sftp.py` is the protocol surface, `web.py` the HTTP one
with `websession.py` and `webauth.py` behind it, `vfs.py` the filesystem,
`crypto.py` and `keystore.py` the encryption and key handling, `users.py`
accounts and the password check, `discord_api.py` and `ratelimit.py` the Discord
client, `db.py` MongoDB, `config.py` the settings, `main.py` startup and
shutdown.

## Status

Working and in real use against a real bot token, single user, single replica.
The known gaps are written down rather than glossed over — see the `[later]` and
`[parked]` items in `ROADMAP.md`.

The one worth knowing up front: whoever can write to MongoDB can restore a file,
and its parent directory, to an older copy of both, and it will verify. Stopping
that needs a monotonic counter they cannot reach; three ways of providing one
were evaluated and none was worth its cost. It is an accepted, documented
residual risk — the only tampering that is not caught.

The repository is mirrored to a GitHub remote.
