# Discord Drive

An SFTP server whose storage backend is Discord. Clients see an ordinary
filesystem — directories, random reads and writes, `truncate`, permissions,
timestamps — while the bytes live as encrypted attachments on Discord messages
and the structure lives in a metadata store you control.

There is a web file manager and a desktop app as well, and a second build that
runs without Docker or MongoDB.

---

## Read this before you set it up

**Storing general files on Discord is a grey area, and the risk lands on your
account, not on this project.**

Discord's [Terms of Service](https://discord.com/terms) do not contain a clause
that names file storage and forbids it. But the
[Developer Terms of Service](https://support-dev.discord.com/hc/en-us/articles/8562894815383-Discord-Developer-Terms-of-Service)
require API use to follow the documented purpose and stay inside rate limits,
and they reserve Discord's discretion to cut off any developer it believes is
negatively affecting the platform. That discretion is the risk: no rule has to
change for your bot — or the account behind it — to be terminated.

So:

- **Do not make this your only copy of anything.** It is a second home for
  files, not a backup strategy. Losing access is a decision someone else can
  make without warning.
- **Use a bot and an account you can afford to lose.**
- **Do not run this at a scale that draws attention.** The rate limiter here
  backs off politely, which helps, but volume is volume.

This is disclosed rather than buried because the people running it are the ones
carrying the consequence. Decide with that in front of you.

---

## How the encryption works

Files are split into 9 MB chunks, each encrypted with AES-256-CTR under its own
nonce and authenticated with HMAC-SHA256. The tags live in the metadata store,
never on Discord, so whoever controls the Discord side can neither read a chunk
nor forge one. The master key is random and stored wrapped under your password
(Argon2id), so changing the password costs one 32-byte rewrite rather than
re-uploading everything.

Tags cover identity as well as content: a file's name and parent directory, each
directory's own name and place, and the set of entries it holds. An attacker
holding the database — a leaked backup, stolen credentials — cannot rename a
file, move it, swap two files' names, or delete one without it being caught on
the next read or listing.

The one tampering that is **not** caught: whoever can write to the database can
restore a file, and its parent directory, to an older copy of both, and it will
verify. Stopping that needs a monotonic counter they cannot reach; three ways of
providing one were evaluated and none was worth its cost. It is an accepted,
documented residual risk.

---

## Which build to pick

They are two products, not two ways to reach one drive.

| | **Standalone** | **Standard** |
|---|---|---|
| Needs Docker | No | Yes |
| Metadata store | One SQLite file | MongoDB (Compose starts it) |
| Looks like | Terminal window + browser | Desktop app window |
| Same data from several devices | No — one device, one drive | Yes — all clients reach one backend |
| Binary size | ~17 MB | ~89 MB desktop shell + backend |

Pick **standalone** for one computer with no Docker. Pick **standard** to reach
the same files from a phone, laptop and desktop.

> **They cannot be joined.** There is no migration in either direction and the
> two metadata formats have nothing in common. Pointing the standalone build at
> a channel an existing deployment uses starts an *empty* drive alongside it
> rather than importing anything. The chunks on Discord are encrypted either
> way; what says which chunks make up which file, in what order, under what
> name, lives only in the metadata store.

---

## Step 1 — the Discord side (both builds need this)

This is the step people get stuck on.

### Create a bot

1. Open <https://discord.com/developers/applications>
2. **New Application** → name it → Create
3. **Bot** in the sidebar → **Reset Token** → copy the token

That token is `DISCORD_BOT_TOKEN`. It is shown once. **No intents are
required** — this service uses only the REST API and never connects to the
gateway.

### Choose where chunks go: a DM or a channel

Pick one. If both are set, the DM wins.

**Option A — a DM with yourself.** Needs `DISCORD_USER_ID`, which is *your*
user ID, not the bot's: Settings → Advanced → enable **Developer Mode**, then
right-click your own avatar → **Copy User ID**.

Two prerequisites, and startup fails with a clear message if either is missing:
the bot must share a server with you (Discord does not let bots DM strangers),
and your privacy settings must allow direct messages from server members. So
even the DM route needs the bot in some server — a private one with only you is
fine.

**Option B — a channel.** Needs `DISCORD_CHANNEL_ID`: right-click the channel →
**Copy Channel ID**. The bot needs four permissions there, and startup names
whichever one is missing:

| Permission | Why |
|---|---|
| View Channel | Without it the channel cannot even be addressed |
| Send Messages | Each chunk is a message |
| Attach Files | The chunk is the attachment |
| Read Message History | Re-fetching attachment URLs on read |

### Invite the bot

**OAuth2** → **URL Generator** → scope **bot**, plus the permissions above.
Open the generated URL and authorise it into your server.

### Choose a password

**At least 12 bytes**, enforced at startup.

```bash
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

> **This password is not just a login. It wraps the key every stored file is
> encrypted with.** Losing it loses the files, not just the session. There is no
> recovery path and no back door. Put it in a password manager now.

---

## Step 2a — standalone (no Docker)

Run the executable once. It writes a settings file, stops on purpose, and prints
the path:

```
Wrote a settings file to:
  C:\Users\<you>\AppData\Roaming\Discord Drive\drive.env

Fill in the REQUIRED values and start the drive again.
```

| Platform | Data directory |
|---|---|
| Windows | `%APPDATA%\Discord Drive\` |
| macOS | `~/Library/Application Support/Discord Drive/` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/discord-drive/` |

`DISCORD_DRIVE_HOME` overrides it — that is how you run two independent drives
on one machine.

Fill in `drive.env`:

```ini
DISCORD_BOT_TOKEN=<the token you copied>
DISCORD_USER_ID=<your user ID>       # DM route
# DISCORD_CHANNEL_ID=<channel ID>    # channel route
SFTP_USER=<a username you choose>
```

Run it again and it asks for the password:

```
Drive password: ▂
```

Success looks like this:

```
INFO  src.db: Metadata store: SQLite at ...\drive.sqlite3
INFO  src.discord_api: Discord bot authenticated as ...
INFO  src.main: SFTP server listening on port 2222
INFO  src.main: Web API listening on 127.0.0.1:8080
```

Open <http://127.0.0.1:8080> and sign in.

**There is deliberately no `SFTP_PASSWORD=` line in `drive.env`**, because
`drive.env` sits in the same directory as `drive.sqlite3` — writing the password
there puts the lock and the key in one drawer, and copying that folder would
copy the whole drive. For unattended starts, both of these still work:

```bash
SFTP_PASSWORD='<password>' ./discord-drive
SFTP_PASSWORD_FILE=/path/to/secret ./discord-drive
```

The desktop shell can drive this build directly — pick "run on this device" in
its setup screen and it asks for the password in a window instead of a terminal.

## Step 2b — standard (Docker)

```bash
cp .env.example .env
```

Fill in the values marked REQUIRED: `DISCORD_BOT_TOKEN`, `DISCORD_USER_ID` or
`DISCORD_CHANNEL_ID`, `SFTP_USER`, `MONGO_ROOT_PASSWORD`. `.env.example`
documents every setting and what getting it wrong costs; the server validates
the whole set at startup and reports every problem at once rather than one per
restart.

The password goes in a Docker secret rather than `.env`:

```bash
mkdir -p secrets
python -c "import secrets; print(secrets.token_urlsafe(24))" > secrets/sftp_password
```

Then one command builds the client, starts everything, and waits until the drive
can actually be opened:

```powershell
.\scripts\start.ps1
```

The wait matters: `docker compose up -d` returns while the server is still
authenticating against Discord and building indexes, and opening the address in
that window gives a connection refused that reads like a broken deployment. By
hand, which is what the script runs:

```bash
cd client/app && npm install && npm run build && cd ../..
docker compose up -d --build
curl http://127.0.0.1:8080/api/health     # {"ok": true}
```

### Reaching it from another device

The web UI is published on the host's loopback only. The desktop app's first-run
screen asks where the server is, and `http://127.0.0.1:8080` is only correct
when the backend is on that same machine. Three ways out, with their costs:

| Approach | Cost |
|---|---|
| **Private network** (Tailscale, WireGuard) | Software on both ends. **The only option that adds no new way to be wrong.** |
| Reverse proxy with a real certificate | A domain and a certificate to renew |
| Plaintext LAN | Needs both `WEB_BIND=0.0.0.0` and `WEB_COOKIE_SECURE=0`. **Not recommended.** |

---

## Using it

**Web UI** — <http://127.0.0.1:8080>. Upload, download, rename, delete, trash.

Signing in unwraps the master key into process memory for the life of the
session, which is why a session has both an idle timeout (default 10 minutes)
and an absolute ceiling (default 2 hours), and why the browser can shorten them
but never extend them. One account can be signed in from several places at once
— the status bar shows how many, and there is a control that ends the others
without ending your own.

**SFTP** — ordinary clients work; WinSCP, FileZilla and Cyberduck want host
`localhost`, port `2222`.

```bash
sftp -P 2222 <SFTP_USER>@localhost
```

`ls`, `cd`, `get`, `put`, `rm`, `mkdir`, `rename`, `chmod`, random reads and
writes, and `truncate` all work. **Symlinks do not** — `symlink`, `readlink` and
`link` return unsupported.

**Trash** — deleting moves to trash; the real purge happens after 30 days by
default (`TRASH_RETENTION_DAYS`). The guarantee is "at least that long": the
sweep is a background scan, not a timer accurate to the second.

---

## Backup and recovery

**Two things must be backed up, and kept apart. Neither substitutes for the
other; losing either loses the files.**

| Thing | What losing it costs |
|---|---|
| **The password** | **Files never open again.** It wraps the master key. No recovery path, no back door. |
| **The metadata store** (`drive.sqlite3` or MongoDB) | **No idea which chunks form which file.** The chunks are still on Discord and cannot be reassembled. |

Keep them in separate places, or one accident takes both.

**Standalone** — stop the drive, then copy all three files:

```
drive.sqlite3
drive.sqlite3-wal      ← if present, must travel with it
drive.sqlite3-shm      ← same
```

> **Do not skip `-wal`.** SQLite runs in WAL mode and recent writes may live
> only there. Copying the main file alone gives you a stale drive.

**Standard** —

```bash
docker compose exec mongodb mongodump --archive --gzip \
  -u "$MONGO_ROOT_USERNAME" -p "$MONGO_ROOT_PASSWORD" --authenticationDatabase admin \
  > backup-$(date +%F).gz
```

> **`users` and `keystore` must be restored together.** The account row is the
> only thing that says which wrapped key belongs to this deployment. Restore one
> without the other and you have a key nothing points at — the server **refuses
> to start** rather than quietly creating a fresh one on top of data it could
> not read. That guard is deliberate: without it the symptom is not an error,
> it is "nothing ever decrypts again".

**Changing the password** — set `SFTP_PASSWORD_OLD` to the old one and
`SFTP_PASSWORD` to the new one, start once, then remove `SFTP_PASSWORD_OLD`.
Only those 32 bytes are rewritten; nothing is re-uploaded.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `DISCORD_BOT_TOKEN was rejected by Discord (401)` | Wrong or reset token | Developer Portal → Bot → Reset Token |
| `cannot open a DM with DISCORD_USER_ID=... (403)` | No shared server, or DMs from members are off | Add the bot to a server you are in; check privacy settings |
| `Discord rejected DISCORD_USER_ID=... as malformed (400)` | A username was used instead of the numeric ID | Developer Mode → right-click → Copy User ID |
| `cannot see DISCORD_CHANNEL_ID=... (403/404)` | Wrong ID, or the bot is not in that server | Discord returns the same code for "invisible" and "nonexistent" — check both |
| `bot is missing permissions on channel ...` | One of the four permissions | The message names the missing one |
| Web page says the frontend is not built | `client/app/dist` is empty | `cd client/app && npm run build`, then `docker compose restart` for the standard build |
| Reachable remotely but every action 401s after login | `WEB_COOKIE_SECURE=1` over plaintext HTTP, so the browser drops the cookie | Use Tailscale or a reverse proxy; set `0` only if you accept plaintext |
| SmartScreen blocks the executable | No code-signing certificate | "More info → Run anyway". This is not a broken build. |
| Large upload stalls | Discord rate limiting | The server backs off and retries; `429` appears in the log |
| Standalone asks for the password every start | Intended | Use `SFTP_PASSWORD_FILE` |

```bash
docker compose logs --tail 40 sftp-discord-server    # standard build
```

The standalone build logs to its terminal.

---

## Known limits

- **Run exactly one replica.** Do not `--scale`, do not point a second copy at
  the same MongoDB, do not overlap two during a deploy. Open file handles
  coordinate through a dictionary that lives *in the process*, consulted before
  every read and write to learn whether another handle changed something
  underneath. A node it has never heard of means "nobody has touched this" —
  true within one process, false with two. A second replica keeps serving a
  stale chunk layout for files the first already rewrote: no error, no log line,
  just old bytes. Fixing it needs optimistic locking at the node level, not a
  bigger cache.
- **One account.** A second is blocked on there being a password recovery path
  first. One account signed in from many places is supported and is what sharing
  looks like today.
- **Concurrent writes: last writer wins.** What is guaranteed across connections
  is that every operation sees state others have already committed — not write
  exclusion.
- **No symlinks.**
- **Standalone, SFTP-only usage never reclaims stranded nodes.** Temporary nodes
  left by an interrupted overwrite are collected when someone opens the web UI,
  because the background task deliberately does not hold the master key.
- **Database-level rollback is not detected.** See the encryption section above.

---

## Building from source

```bash
# Frontend (needed by both builds)
cd client/app && npm install && npm run build

# Standalone executable — needs Python 3.12
python -m pip install -r requirements-dev.txt
python -m PyInstaller discord-drive.spec --noconfirm \
  --distpath dist-standalone --workpath build-standalone

# Desktop shell
cd client/shell && npm install && npm run dist
```

PyInstaller and electron-builder both **only build for the platform they run
on** — a Linux build has to happen on Linux. `discord-drive.spec` must be built
before `npm run dist`, which copies the backend executable in as a packaged
resource.

The frontend is a build product and is not in the repository.
`docker-compose.yml` mounts `client/app/dist` read-only, so rebuilding it costs
one command and a refresh rather than an image rebuild — which would drop every
live session and every unwrapped key with it. Until it is built, `/` serves a
page saying so; the API and SFTP are unaffected.

The desktop app carries no copy of the web client. The session cookie is
`SameSite=Strict`, and a page loaded from `file://` would never be allowed to
send it, so the shell is a window plus a first-run screen asking where the
server is.

---

## Tests

```bash
python -m pytest             # 764 tests, about a minute
python -m pytest --db=sqlite # the same suite against a real SQLite backend
cd client/shell && node --test   # 16 tests
```

No credentials and no network required — MongoDB and the Discord API are faked.

The SQLite run is how that backend is checked: rather than a second set of tests
written against someone's reading of it, the same assertions written for
MongoDB's behaviour are pointed at it. Three tests skip there and say why — they
drive MongoDB's refusal to change an index in place, which has no counterpart.
That run is what found two bugs invisible to the default one.

**A green suite is not proof the thing works.** The fakes model neither rate
limits nor attachment URL expiry, they do not enforce uniqueness, and they do
not validate index specifications — the trash once shipped with a partial unique
index MongoDB rejects outright, and the suite stayed green for three days
because nothing had restarted against a real server. Several bugs in this
project's history were only ever found by hand against a real bot token. Running
inside the production image is a separate check, which is why both are pinned to
the same Python version:

```bash
docker run --rm --user root -v "$PWD:/repo" -w /repo discord-drive-sftp-discord-server \
  sh -c "pip install -q -r requirements-dev.txt && python -m pytest -q"
```

---

## Where things are

| File | What it holds |
| --- | --- |
| [`ROADMAP.md`](ROADMAP.md) | Every settled decision with its reasoning, the open questions and how each resolved, and a changelog. Start here for "why is it like that". Written in Chinese. |
| [`SOP.md`](SOP.md) | Recurring problems and the order to check things in. Mostly environment traps. Chinese. |
| [`design-multi-user.md`](design-multi-user.md) | The structural steps have landed; opening a second account has not. Its banner says which is which. |
| [`design-node-identity-integrity.md`](design-node-identity-integrity.md) | Built; its banner lists the four places plan and result diverged. |
| [`design-standalone.md`](design-standalone.md) | How the metadata store was replaced without touching `vfs.py`, and the route that was rejected. |
| [`client/README.md`](client/README.md) | The two npm packages: the file manager and the desktop shell. |
| [`.env.example`](.env.example) | Every setting and what getting it wrong costs. |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | How to build, test and propose a change. |
| [`SECURITY.md`](SECURITY.md) | How to report a vulnerability. Not a public issue. |
| [`CLAUDE.md`](CLAUDE.md) | Collaboration rules for AI-assisted work on this repo. Chinese. |

Source lives in `src/`: `sftp.py` is the protocol surface, `web.py` the HTTP one
with `websession.py` and `webauth.py` behind it, `vfs.py` the filesystem,
`crypto.py` and `keystore.py` the encryption and key handling, `users.py`
accounts and the password check, `discord_api.py` and `ratelimit.py` the Discord
client, `db.py` MongoDB, `sqlitedb.py` the SQLite backend behind the same
interface, `config.py` the settings, `main.py` startup and shutdown.

---

## Status

Working and in real use against a real bot token: single user, single replica.
The known gaps are written down rather than glossed over — see the `[later]` and
`[parked]` items in [`ROADMAP.md`](ROADMAP.md).

## License

[Apache License 2.0](LICENSE).
