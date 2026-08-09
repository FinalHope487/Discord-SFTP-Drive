// Running the standalone drive as a child process, for the no-Docker path.
//
// The backend (`discord-drive[.exe]`, built by `discord-drive.spec`) is a
// console program that reads its settings from a per-user data directory,
// asks for the drive password, and then listens until it is told to stop.
// This module is what turns that into something a GUI can drive: it spawns
// the process, feeds it the password over stdin instead of a terminal, and
// stops it the same way -- by closing that pipe -- because Windows gives a
// GUI parent no reliable way to deliver an actual signal to a console child
// it spawned.
//
// That last part was measured, not assumed, while building this: on Windows,
// `child.kill()` is an unconditional TerminateProcess no matter which signal
// is named, and `taskkill` without `/f` refuses outright on a process with no
// window to close ("this process can only be terminated forcefully"). Closing
// the child's own stdin is the one mechanism that is both reliable and needs
// nothing OS-specific -- `src/main.py`'s `_wait_for_shutdown` races it against
// the signal wait it already had, and `src/standalone.py` is the other half
// of this protocol.
//
// The protocol, in order:
//   1. Spawn with DISCORD_DRIVE_STDIN_LIFECYCLE=1 and DISCORD_DRIVE_HOME set
//      to this app's own data directory.
//   2. The child either exits on its own (nothing configured yet -- first
//      run) or prints "AWAITING_PASSWORD" and blocks on stdin (configured,
//      waiting). Racing a timeout against this instead would have to survive
//      Windows Defender scanning a freshly-written exe on first launch, which
//      can itself take several seconds -- long enough to misread "still
//      starting" as "this is the first run".
//   3. Write the password plus a newline. The child either starts listening
//      or exits with an error (wrong password, bad Discord credentials).
//   4. Poll /api/health the same way the remote-server setup screen does,
//      until it answers or too much time passes.
//   5. To stop: end the child's stdin. It drains in-flight writes and exits
//      on its own; if it does not within a generous grace period, only then
//      is it killed outright.

const { spawn } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

const READY_MARKER = "AWAITING_PASSWORD";
const DEFAULT_WEB_PORT = 8080;

// How long a fresh spawn gets before its stdout is trusted to have said
// everything it is going to say before either exiting or printing the marker.
// This is a safety net only -- the real signal is the marker or the exit
// event, both of which resolve `_checking` immediately when they happen. This
// timeout exists for the one case neither covers: the executable is missing,
// unrunnable, or hangs before reaching either.
const SPAWN_TIMEOUT_MS = 30000;

const HEALTH_POLL_MS = 300;
const HEALTH_TIMEOUT_MS = 20000;

// Generous on purpose: `main.py`'s own drain waits up to 20s for live
// sessions before it starts closing connections, and this has to leave that
// room rather than cut it off. Only a hang past this gets force-killed.
const STOP_GRACE_MS = 25000;

/* ------------------------------------------------------------- locating it */

/**
 * The backend executable, wherever this build put it.
 *
 * Packaged: electron-builder copies it in as an extraResource, next to the
 * app's own resources -- not inside the asar, because it has to be launched
 * as a real file, not read out of an archive.
 *
 * Unpackaged (`npm start` from a checkout): the PyInstaller output sits at
 * `dist-standalone/` two directories up, built by `discord-drive.spec` at the
 * repo root. Returning that path when it does not exist is deliberate rather
 * than an error here -- `status()` reports "missing" distinctly from every
 * other failure, because "you have not built the backend yet" and "the
 * backend crashed" call for completely different next steps.
 */
function backendPath(isPackaged, resourcesPath) {
  if (isPackaged) {
    const exe = process.platform === "win32" ? "discord-drive.exe" : "discord-drive";
    return path.join(resourcesPath, "backend", exe);
  }
  const exe = process.platform === "win32" ? "discord-drive.exe" : "discord-drive";
  return path.join(__dirname, "..", "..", "dist-standalone", exe);
}

/**
 * The port the backend will actually bind, read from its own settings file.
 *
 * Not asked of the running process -- there is no IPC for it besides stdout,
 * and parsing a log line's exact format would make this depend on something
 * `main.py` is free to reword. The settings file is plain `KEY=value` text
 * and is the same file `standalone.py` itself resolves `WEB_PORT` from, so
 * reading it directly here cannot disagree with what the backend actually
 * does with it.
 */
function readWebPort(configPath) {
  let text;
  try {
    text = fs.readFileSync(configPath, "utf8");
  } catch {
    return DEFAULT_WEB_PORT;
  }
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (trimmed.startsWith("#")) continue;
    const match = trimmed.match(/^WEB_PORT\s*=\s*(\d+)\s*$/);
    if (match) return Number(match[1]);
  }
  return DEFAULT_WEB_PORT;
}

/* ------------------------------------------------------------ health check */

/** GET a loopback /api/health, the same contract the remote-server probe in
 * main.js checks -- distinguishing "answered and it is this service" from
 * everything else, without needing a session. */
function checkHealth(origin) {
  return new Promise((resolve) => {
    const request = http.get(`${origin}/api/health`, { timeout: 2000 }, (response) => {
      let body = "";
      response.on("data", (chunk) => (body += chunk));
      response.on("end", () => {
        try {
          const parsed = JSON.parse(body);
          resolve(response.statusCode === 200 && parsed.ok === true);
        } catch {
          resolve(false);
        }
      });
    });
    request.on("timeout", () => request.destroy());
    request.on("error", () => resolve(false));
  });
}

async function waitForHealth(origin, deadline) {
  while (Date.now() < deadline) {
    if (await checkHealth(origin)) return true;
    await new Promise((r) => setTimeout(r, HEALTH_POLL_MS));
  }
  return false;
}

/* -------------------------------------------------------------- the class */

/**
 * Owns at most one child process across the whole "check status, ask for the
 * password, wait for it to come up, stop it later" sequence -- deliberately
 * the same child throughout, not a fresh spawn per step. Respawning between
 * "found out it's waiting for a password" and "here is the password" would
 * lose that blocked-on-stdin state and pay the startup cost twice, which
 * matters when that cost includes an antivirus scan.
 */
class LocalDrive {
  constructor() {
    this.child = null;
    this.dataHome = null;
    this.configPath = null;
    this.output = "";
  }

  get running() {
    return this.child !== null && !this.child.killed;
  }

  _attach(child) {
    this.child = child;
    this.output = "";
    child.stdout.on("data", (d) => (this.output += d.toString("utf8")));
    child.stderr.on("data", (d) => (this.output += d.toString("utf8")));
    child.on("exit", () => {
      if (this.child === child) this.child = null;
    });
  }

  /** Recent output, for surfacing why a start or a status check failed. Not
   * the whole log -- a wrong password or a rejected token says so in its
   * last few lines, and the rest is only ever noise for this purpose. */
  recentOutput(lines = 12) {
    return this.output.trim().split("\n").slice(-lines).join("\n");
  }

  /**
   * Spawn (if nothing is running yet) and settle into one of:
   *   { state: "first-run", configPath }
   *   { state: "awaiting-password" }
   *   { state: "missing" }          -- the executable is not where expected
   *   { state: "error", output }    -- exited before either of the above
   *
   * Idempotent while a check is already in flight or already settled into
   * "awaiting-password": calling this again returns the same live child
   * rather than spawning a second one alongside it. Pass `force: true` to
   * discard whatever is there and check again, which is what "I edited the
   * settings file, check again" needs.
   */
  async status(exePath, dataHome, { force = false } = {}) {
    if (force && this.child) {
      this.child.stdin.end();
      this.child = null;
    }

    if (this.child) {
      // Already resolved to "awaiting-password" by an earlier call; nothing
      // new to learn without ending it, which `force` above already covers.
      return { state: "awaiting-password" };
    }

    if (!fs.existsSync(exePath)) {
      return { state: "missing", path: exePath };
    }

    this.dataHome = dataHome;
    this.configPath = path.join(dataHome, "drive.env");

    const child = spawn(exePath, [], {
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
      env: {
        ...process.env,
        DISCORD_DRIVE_HOME: dataHome,
        DISCORD_DRIVE_STDIN_LIFECYCLE: "1",
      },
    });
    this._attach(child);

    return new Promise((resolve) => {
      let settled = false;
      const finish = (result) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(result);
      };

      const timer = setTimeout(() => {
        finish({ state: "error", output: this.recentOutput() });
      }, SPAWN_TIMEOUT_MS);

      child.stdout.on("data", () => {
        if (this.output.includes(READY_MARKER)) {
          finish({ state: "awaiting-password" });
        }
      });

      child.on("exit", () => {
        // Exiting before the marker means the first-run branch: it is the
        // only path that returns before ever printing it or touching stdin.
        finish({ state: "first-run", configPath: this.configPath });
      });

      child.on("error", () => {
        finish({ state: "missing", path: exePath });
      });
    });
  }

  /**
   * Send the password to a child already in "awaiting-password", then wait
   * for it to either answer its own health check or give up.
   *
   * Returns { ok: true, origin } or { ok: false, reason, output }. `reason`
   * is "exited" (the backend gave up and said why, in `output`) or "timeout"
   * (still running, but /api/health never answered in time).
   */
  async start(password) {
    if (!this.child) {
      return { ok: false, reason: "exited", output: "the backend is not running" };
    }

    const child = this.child;
    const origin = `http://127.0.0.1:${readWebPort(this.configPath)}`;

    child.stdin.write(`${password}\n`);

    const deadline = Date.now() + HEALTH_TIMEOUT_MS;
    let exited = false;
    const onExit = () => {
      exited = true;
    };
    child.once("exit", onExit);

    try {
      while (Date.now() < deadline) {
        if (exited) {
          return { ok: false, reason: "exited", output: this.recentOutput() };
        }
        if (await checkHealth(origin)) {
          return { ok: true, origin };
        }
        await new Promise((r) => setTimeout(r, HEALTH_POLL_MS));
      }
      return exited
        ? { ok: false, reason: "exited", output: this.recentOutput() }
        : { ok: false, reason: "timeout", output: this.recentOutput() };
    } finally {
      child.removeListener("exit", onExit);
    }
  }

  /**
   * Ask the backend to stop, and mean it eventually even if it does not.
   *
   * Ending stdin is the graceful request regardless of which state the child
   * is in: past the password prompt it is what `_wait_for_shutdown`'s
   * `extra_stop` races against the signal wait, and short of that it is just
   * EOF on the read `resolve_password` was blocked in, which makes that
   * function return false and the process exit on its own either way.
   */
  async stop() {
    if (!this.child) return;
    const child = this.child;

    await new Promise((resolve) => {
      let done = false;
      const finish = () => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        resolve();
      };

      child.once("exit", finish);

      const timer = setTimeout(() => {
        if (!done) child.kill();
        finish();
      }, STOP_GRACE_MS);

      try {
        child.stdin.end();
      } catch {
        // Already gone; the exit listener above already covers that case.
      }
    });

    if (this.child === child) this.child = null;
  }
}

module.exports = { LocalDrive, backendPath, readWebPort, checkHealth, waitForHealth };
