# Discord Drive

An SFTP server whose storage backend is Discord. Clients see an ordinary
filesystem — directories, random reads and writes, `truncate`, permissions,
timestamps — while the bytes live as encrypted attachments on Discord messages
and the structure lives in a metadata store you control. A web file manager and
a desktop app come with it.

[Read this first](#read-this-before-you-set-it-up) ·
[Which build](#which-build-to-pick) ·
[Encryption](#how-the-encryption-works) ·
[Step 1: Discord](#step-1--the-discord-side-both-builds-need-this) ·
[Step 2a: standalone](#step-2a--standalone-no-docker) ·
[Step 2b: standard](#step-2b--standard-docker) ·
[Using it](#using-it) ·
[Where things are](#where-things-are)

Running it day to day — backup, recovery, troubleshooting, known limits — is in
[`docs/OPERATIONS.md`](docs/OPERATIONS.md). Building and testing it is in
[`CONTRIBUTING.md`](CONTRIBUTING.md).

---

## Read this before you set it up

**Storing general files on Discord is a grey area, and the risk lands on your
account, not on this project.**

No clause in Discord's [Terms of Service](https://discord.com/terms) names file
storage and forbids it. But the
[Developer Terms of Service](https://support-dev.discord.com/hc/en-us/articles/8562894815383-Discord-Developer-Terms-of-Service)
require API use to follow the documented purpose and stay inside rate limits,
and they reserve Discord's discretion to cut off any developer it believes is
negatively affecting the platform. That discretion is the risk: no rule has to
change for your bot — or the account behind it — to be terminated.

- **Do not make this your only copy of anything.** It is a second home for
  files, not a backup strategy. Losing access is a decision someone else can
  make without warning.
- **Use a bot and an account you can afford to lose.**
- **Do not run this at a scale that draws attention.** The rate limiter backs
  off politely, which helps, but volume is volume.

---

## Which build to pick

They are two products, not two ways to reach one drive.

| | **Standalone** | **Standard** |
|---|---|---|
| Needs Docker | No | Yes |
| Metadata store | One SQLite file | MongoDB (Compose starts it) |
| Runs as | A terminal window, or the desktop app's "run on this device" | Docker on one machine, reached by a browser or the desktop app |
| Same data from several devices | No — one device, one drive | Yes — all clients reach one backend |
| Download size | ~17 MB executable, or ~102 MB desktop app, which bundles it | ~102 MB desktop app + the Docker images |

Pick **standalone** for one computer with no Docker. Pick **standard** to reach
the same files from a phone, laptop and desktop.

> **They cannot be joined.** There is no migration in either direction. Pointing
> the standalone build at a channel an existing deployment uses starts an *empty*
> drive alongside it rather than importing anything: what says which chunks make
> up which file, in what order, under what name, lives only in the metadata
> store.

---

## How the encryption works

- 9 MB chunks, each encrypted with AES-256-CTR under its own nonce and
  authenticated with HMAC-SHA256.
- The tags live in the metadata store, never on Discord: whoever controls the
  Discord side can neither read a chunk nor forge one.
- The master key is random and stored wrapped under your password (Argon2id), so
  changing the password rewrites 32 bytes rather than re-uploading everything.
- Tags cover identity as well as content — a file's name and parent directory,
  each directory's own name and place, the set of entries it holds. An attacker
  holding the database cannot rename a file, move it, swap two files' names, or
  delete one without it being caught on the next read or listing.

The one tampering that is **not** caught: whoever can write to the database can
roll a file *and* its parent directory back to an older copy of both, and it
will verify. Stopping that needs a monotonic counter they cannot reach, and none
of the three ways of providing one was worth its cost — an accepted, documented
residual risk. See
[`design-node-identity-integrity.md`](design-node-identity-integrity.md).

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

Two prerequisites, each with a clear startup error if missing: the bot must
share a server with you (Discord does not let bots DM strangers), and your
privacy settings must allow DMs from server members. Even the DM route therefore
needs the bot in some server — a private one with only you is fine.

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

**There is deliberately no `SFTP_PASSWORD=` line in `drive.env`**: it sits in the
same directory as `drive.sqlite3`, so writing the password there puts the lock
and the key in one drawer, and copying that folder copies the whole drive. For
unattended starts:

```bash
SFTP_PASSWORD='<password>' ./discord-drive
SFTP_PASSWORD_FILE=/path/to/secret ./discord-drive
```

The desktop app can run this build for you: pick **run on this device** in its
first-run screen and it asks for the password in a window instead of a terminal.

---

## Step 2b — standard (Docker)

```bash
cp .env.example .env
```

Fill in the values marked REQUIRED: `DISCORD_BOT_TOKEN`, `DISCORD_USER_ID` or
`DISCORD_CHANNEL_ID`, `SFTP_USER`, `MONGO_ROOT_PASSWORD`.
[`.env.example`](.env.example) documents every setting and what getting it wrong
costs; startup validates the whole set and reports every problem at once.

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
that window gives a connection refused that reads like a broken deployment.
What the script runs, by hand:

```bash
cd client/app && npm install && npm run build && cd ../..
docker compose up -d --build
curl http://127.0.0.1:8080/api/health     # {"ok": true}
```

The web UI is published on the host's loopback only. Reaching it from another
device has three routes with different costs — see
[`docs/OPERATIONS.md`](docs/OPERATIONS.md#reaching-it-from-another-device).

---

## Using it

**Web UI** — <http://127.0.0.1:8080>. Upload, download, rename, delete, trash.

Signing in unwraps the master key into process memory for the life of the
session. Hence both an idle timeout (default 10 minutes) and an absolute ceiling
(default 2 hours), which the browser can shorten but never extend. One account
can be signed in from several places at once: the status bar shows how many, and
a control ends the others without ending your own.

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

## Where things are

| File | What it holds |
| --- | --- |
| [`docs/OPERATIONS.md`](docs/OPERATIONS.md) | Remote access, backup and recovery, changing the password, troubleshooting, known limits. |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Building from source, running the tests, proposing a change. |
| [`.env.example`](.env.example) | Every setting and what getting it wrong costs. |
| [`ROADMAP.md`](ROADMAP.md) | Every settled decision with its reasoning, the open questions and how each resolved, and a changelog. Start here for "why is it like that". Written in Chinese. |
| [`SOP.md`](SOP.md) | Recurring problems and the order to check things in. Mostly environment traps. Chinese. |
| [`design-multi-user.md`](design-multi-user.md) | The structural steps have landed; opening a second account has not. Its banner says which is which. |
| [`design-node-identity-integrity.md`](design-node-identity-integrity.md) | Built; its banner lists the four places plan and result diverged. |
| [`design-standalone.md`](design-standalone.md) | How the metadata store was replaced without touching `vfs.py`, and the route that was rejected. |
| [`client/README.md`](client/README.md) | The two npm packages: the file manager and the desktop shell. |
| [`SECURITY.md`](SECURITY.md) | How to report a vulnerability. Not a public issue. |
| [`CLAUDE.md`](CLAUDE.md) | Collaboration rules for AI-assisted work on this repo. Chinese. |

Source lives in `src/`:

| Module | What it is |
| --- | --- |
| `sftp.py` | The SFTP protocol surface |
| `web.py` | The HTTP one, with `websession.py` and `webauth.py` behind it |
| `vfs.py` | The filesystem both surfaces talk to |
| `crypto.py`, `keystore.py` | Chunk encryption; master key wrapping |
| `users.py` | The account and the password check |
| `discord_api.py`, `ratelimit.py` | The Discord client and its backoff |
| `db.py`, `sqlitedb.py` | MongoDB, and SQLite behind the same interface |
| `standalone.py` | The no-Docker entry point: data directory, `drive.env`, password prompt |
| `jobs.py` | Emptying the trash as a polled job: real progress, cancellation, dies with the session |
| `config.py`, `main.py` | Settings; startup and shutdown |

---

## Status

Working and in real use against a real bot token: single user, single replica.
The known gaps are written down rather than glossed over — see
[`docs/OPERATIONS.md`](docs/OPERATIONS.md#known-limits) and the `[later]` and
`[parked]` items in [`ROADMAP.md`](ROADMAP.md).

## License

[Apache License 2.0](LICENSE).
