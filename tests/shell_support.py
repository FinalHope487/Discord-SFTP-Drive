"""Driving the desktop shell's own two pages in the window a user would see.

`setup.html` and `local.html` are the only screens this project ships that had
no acceptance layer. Everything they do runs through a preload bridge into the
main process, and that is exactly the seam `SOP.md` keeps recording failures
at: `backend.test.js` proves `LocalDrive` reports `first-run`, and stays true
whether or not any page ever renders that state.

The window here is real. Electron is launched with `--remote-debugging-port`
and Playwright attaches over CDP, so the page under test carries its real
preload, its real `contextBridge` objects, and a real `ipcRenderer.invoke`
round trip into the real main process. Nothing is stubbed on this side of the
boundary -- `probe()` genuinely opens a socket, and `dd:localStatus` genuinely
spawns the packaged backend.

No new dependency buys this: Playwright is already in `requirements-dev.txt`
for `test_ui_login.py`, and Electron is already `client/shell`'s devDependency.

`--user-data-dir` is the isolation. The shell keeps both `config.json` and the
standalone build's entire data directory under `app.getPath("userData")`
(`main.js`), so pointing that at a tmpdir gives each test a private first-run
state and keeps the suite away from the real `%APPDATA%\\Discord Drive\\`.
"""

import asyncio
import contextlib
import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHELL = REPO / "client" / "shell"

# The `electron` npm package records its own executable name here. Resolving
# through it rather than `node_modules/.bin/electron` avoids the shim, which is
# a `.cmd` on Windows and would need a shell to launch.
_ELECTRON_DIST = SHELL / "node_modules" / "electron" / "dist"
_PATH_TXT = SHELL / "node_modules" / "electron" / "path.txt"

BACKEND = REPO / "dist-standalone" / (
    "discord-drive.exe" if sys.platform == "win32" else "discord-drive"
)

CDP_STARTUP_TIMEOUT = 60.0


def electron_binary():
    if not _PATH_TXT.is_file():
        return None
    exe = _ELECTRON_DIST / _PATH_TXT.read_text(encoding="utf-8").strip()
    return exe if exe.is_file() else None


def electron_missing():
    """A skip reason, or None when the window can actually be driven.

    Mirrors how `backend.test.js` skips without the built executable. A test
    that cannot run is worth more as a named skip than as a failure that says
    nothing about the code under test.
    """
    if electron_binary() is None:
        return "electron is not installed (cd client/shell && npm install)"
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        return "no DISPLAY -- run the suite under xvfb-run"
    return None


def backend_missing():
    if not BACKEND.is_file():
        return (
            f"{BACKEND.name} is not built -- PyInstaller only builds for the "
            "platform it runs on"
        )
    return None


def refuse_to_skip_in_ci(*reasons):
    """On CI a missing prerequisite is a broken workflow, not a skip.

    Written because this layer landed green in CI having run none of itself:
    Electron 43 dropped the `postinstall` that used to fetch its binary, so
    `npm ci` installed the JS package and nothing else, and every test here
    skipped. A skip is the right answer on a developer's machine that has not
    built the backend. In CI it means the job asserted nothing while
    reporting success, which is worse than a failure.
    """
    if not os.environ.get("CI"):
        return
    actual = [reason for reason in reasons if reason]
    if actual:
        raise RuntimeError(
            "CI is configured to run the desktop shell's acceptance layer, "
            "but its prerequisites are missing, so it would have skipped "
            "silently: " + "; ".join(actual)
        )


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _kill_tree(proc):
    """Electron is a process tree, and on Windows so is whatever it spawned.

    `terminate()` on the top process leaves the GPU and renderer children --
    and, once `local.html` has been visited, the backend the shell started --
    alive and holding their tmpdir open. The tree has to go as a unit.
    """
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    else:
        proc.terminate()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=15)
    if proc.poll() is None:
        proc.kill()


async def _wait_for_cdp(port, proc, log_path):
    """Poll the debugging endpoint until Electron has a window to attach to."""
    deadline = asyncio.get_running_loop().time() + CDP_STARTUP_TIMEOUT
    url = f"http://127.0.0.1:{port}/json/list"
    while asyncio.get_running_loop().time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"electron exited with {proc.returncode} before serving CDP\n"
                f"{log_path.read_text(encoding='utf-8', errors='replace')}"
            )
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                targets = json.load(response)
            pages = [t for t in targets if t.get("type") == "page"]
            if pages:
                return
        except (urllib.error.URLError, OSError, ValueError):
            pass
        await asyncio.sleep(0.25)
    raise RuntimeError(
        "electron never exposed a page over CDP\n"
        f"{log_path.read_text(encoding='utf-8', errors='replace')}"
    )


@contextlib.asynccontextmanager
async def shell_window(user_data_dir, config=None):
    """The real shell window, as a Playwright page.

    `config` is written to `config.json` before launch, which is how a test
    picks its entry screen: `main.js` opens `local.html` for `mode: "local"`,
    a remote window when `serverUrl` is set, and `setup.html` otherwise.
    """
    from playwright.async_api import async_playwright

    user_data_dir = Path(user_data_dir)
    user_data_dir.mkdir(parents=True, exist_ok=True)
    if config is not None:
        (user_data_dir / "config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )

    port = _free_port()
    log_path = user_data_dir / "electron.log"
    args = [
        str(electron_binary()),
        ".",
        f"--user-data-dir={user_data_dir}",
        f"--remote-debugging-port={port}",
    ]
    if sys.platform.startswith("linux"):
        # CI runners cannot use Chromium's setuid sandbox. This is the browser
        # process sandbox, not the `webPreferences.sandbox: true` the setup
        # window is created with -- that one stays on and is part of what these
        # tests exercise.
        args.append("--no-sandbox")

    with log_path.open("wb") as log:
        proc = subprocess.Popen(
            args, cwd=SHELL, stdout=log, stderr=subprocess.STDOUT
        )
        try:
            await _wait_for_cdp(port, proc, log_path)
            async with async_playwright() as driver:
                browser = await driver.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{port}"
                )
                try:
                    context = browser.contexts[0]
                    page = context.pages[0]
                    await page.wait_for_load_state("domcontentloaded")
                    yield page
                finally:
                    with contextlib.suppress(Exception):
                        await browser.close()
        finally:
            _kill_tree(proc)


async def health_server(ok=True):
    """A stand-in that answers `/api/health` the way the real drive does.

    `probe()` accepts only HTTP 200 with `{"ok": true}`; `ok=False` produces
    something listening that fails that check, which is the difference between
    the shell's "connected" and "not Discord Drive" answers.
    """
    from aiohttp import web

    async def handler(_request):
        return web.json_response({"ok": True} if ok else {"service": "something else"})

    app = web.Application()
    app.router.add_get("/api/health", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    return runner, f"http://127.0.0.1:{port}"
