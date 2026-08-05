"""Move SFTP_PASSWORD out of the environment and into a docker secret.

Run this **before** recreating the container, from the project root, with the
current stack still up:

    python scripts/adopt_password_secret.py

It copies the password out of the *running* container and writes it to
`secrets/sftp_password`, which is where `docker-compose.yml` now expects it.

Why it reads the container rather than `.env`
---------------------------------------------
The value that wrapped the master key is the one Compose put into the
container, and Compose parses `.env` with its own rules -- quoting and escapes
do not agree with python-dotenv in every case. Re-deriving the password from
`.env` here would be a second parser guessing at the first one's output, and a
password that differs by one byte is indistinguishable at startup from an
outright wrong one. The running process already holds the answer, so this asks
it.

Consequently the password is never rendered as text: it moves as bytes from
the container's stdout into the file, and this script prints only its length
and a short digest so the result can be checked without being displayed.

What happens if it goes wrong anyway
------------------------------------
Nothing is destroyed. `keystore.ensure_usable` refuses to start under a
password that does not open the stored key, and only ever creates a new master
key when the keystore is empty. The one write that does land first is in
`users.sync_env_user`, which rewrites the account's password hash before the
key is proved -- so a bad run leaves a stopped server and a hash describing the
wrong password. Putting the right secret back and starting again overwrites it
in the same way. Read `docker compose logs` before assuming anything worse.
"""

import hashlib
import os
import subprocess
import sys

SERVICE = "sftp-discord-server"
TARGET = os.path.join("secrets", "sftp_password")

# Emitted inside the container. Writing to the raw buffer keeps the value
# exactly as many bytes as it is: `print` would append a newline and, on the
# way through the exec stream, could translate one.
_EXTRACT = (
    "import os, sys; "
    "v = os.environ.get('SFTP_PASSWORD'); "
    "sys.exit(3) if not v else sys.stdout.buffer.write(v.encode('utf-8'))"
)


def fail(message):
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def read_from_container():
    """The running container's SFTP_PASSWORD, as bytes."""
    try:
        done = subprocess.run(
            ["docker", "compose", "exec", "-T", SERVICE, "python", "-c", _EXTRACT],
            capture_output=True,
        )
    except FileNotFoundError:
        fail("`docker` is not on PATH; run this from a shell that can reach it")

    if done.returncode == 3:
        fail(
            f"the {SERVICE} container has no SFTP_PASSWORD in its environment. "
            "If it has already been recreated against the new compose file, "
            "this script cannot recover the password -- write "
            f"{TARGET} by hand from wherever you keep it."
        )
    if done.returncode != 0:
        detail = done.stderr.decode("utf-8", "replace").strip()
        fail(
            f"could not read the password from the {SERVICE} container "
            f"(exit {done.returncode}). Is the stack up? Compose said:\n{detail}"
        )
    if not done.stdout:
        fail("the container returned an empty password")
    return done.stdout


def main():
    if not os.path.isfile("docker-compose.yml"):
        fail("run this from the project root (no docker-compose.yml here)")

    if os.path.exists(TARGET) and "--force" not in sys.argv:
        fail(
            f"{TARGET} already exists. Delete it, or pass --force, once you "
            "are sure you want to overwrite it."
        )

    password = read_from_container()

    os.makedirs("secrets", exist_ok=True)
    # Binary mode, no newline translation, and nothing appended: the file's
    # bytes have to be the password's bytes. config.py strips at most one
    # trailing newline, so a password that ends in one cannot round-trip
    # through this format -- warn rather than silently corrupt it.
    if password.endswith(b"\n"):
        fail(
            "this password ends in a newline, which the secret-file format "
            "cannot represent (config.py strips one on read). Change the "
            "password first, or keep using the environment variable."
        )
    with open(TARGET, "wb") as handle:
        handle.write(password)

    # Best effort: on Windows this is a no-op that neither helps nor hurts.
    try:
        os.chmod(TARGET, 0o444)
    except OSError:
        pass

    digest = hashlib.sha256(password).hexdigest()[:12]
    print(f"wrote {TARGET}: {len(password)} bytes, sha256 starts {digest}")
    print()
    print("Next:")
    print("  1. docker compose up -d --build")
    print("  2. docker compose logs sftp-discord-server | tail -30")
    print("     A clean start says nothing about creating a new wrapped")
    print("     master key. If it refuses to start, the secret file is wrong")
    print("     and no stored data has been touched.")
    print("  3. Once it is up, remove SFTP_PASSWORD from .env.")


if __name__ == "__main__":
    main()
