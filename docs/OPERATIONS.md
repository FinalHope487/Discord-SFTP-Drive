# Operations

Everything after the drive is running. Setting it up is in
[`../README.md`](../README.md).

[Remote access](#reaching-it-from-another-device) ·
[Backup and recovery](#backup-and-recovery) ·
[Changing the password](#changing-the-password) ·
[Troubleshooting](#troubleshooting) ·
[Known limits](#known-limits)

---

## Reaching it from another device

The web UI is published on the host's loopback only. The desktop app's first-run
screen asks where the server is, and `http://127.0.0.1:8080` is only correct
when the backend is on that same machine. Three ways out, with their costs:

| Approach | Cost |
|---|---|
| **Private network** (Tailscale, WireGuard) | Software on both ends. **The only option that adds no new way to be wrong.** |
| Reverse proxy with a real certificate | A domain and a certificate to renew |
| Plaintext LAN | Needs both `WEB_BIND=0.0.0.0` and `WEB_COOKIE_SECURE=0`. **Not recommended.** |

---

## Backup and recovery

**Two things must be backed up, and kept apart. Neither substitutes for the
other; losing either loses the files.**

| Thing | What losing it costs |
|---|---|
| **The password** | **Files never open again.** It wraps the master key. No recovery path, no back door. |
| **The metadata store** (`drive.sqlite3` or MongoDB) | **No idea which chunks form which file.** The chunks are still on Discord and cannot be reassembled. |

Keep them in separate places, or one accident takes both.

### Standalone

Stop the drive, then copy all three files:

```
drive.sqlite3
drive.sqlite3-wal      ← if present, must travel with it
drive.sqlite3-shm      ← same
```

> **Do not skip `-wal`.** SQLite runs in WAL mode and recent writes may live
> only there. Copying the main file alone gives you a stale drive.

### Standard

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

---

## Changing the password

Set `SFTP_PASSWORD_OLD` to the old one and `SFTP_PASSWORD` to the new one, start
once, then remove `SFTP_PASSWORD_OLD`. Only those 32 bytes are rewritten;
nothing is re-uploaded.

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

- **Run exactly one replica.** Do not `--scale`, do not point a second process
  at the same metadata store, do not overlap two during a deploy. Open file
  handles coordinate through a dictionary that lives *in the process*, consulted
  before every read and write to learn whether another handle changed something
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
- **Database-level rollback is not detected.** See
  [how the encryption works](../README.md#how-the-encryption-works).
