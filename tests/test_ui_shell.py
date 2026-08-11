"""The desktop shell's two setup screens, driven in a real Electron window.

The layer `CLAUDE.md` names as missing for `client/shell`. Until now these two
pages could only be checked by looking at them, so every claim about them was
"I opened it and it seemed right".

What each test proves that the layer below it cannot: `backend.test.js` already
asserts `LocalDrive.status()` returns `first-run`, `awaiting-password` and the
rejection output. All of that stays green if `local.html` never renders a word
of it -- if `refresh()` reads the wrong field, if a screen id is misspelled, if
the button was never wired to `window.ddLocal`. The same holds for `probe()`
and `setup.html`. These tests fail on exactly that half.

Assertions are on visible text and element state rather than screenshots, for
the reason `test_ui_login.py` gives: pixels differ between machines for reasons
that have nothing to do with this application.
"""

import json

import pytest
from playwright.async_api import expect

from tests.shell_support import (
    backend_missing,
    electron_missing,
    health_server,
    refuse_to_skip_in_ci,
    shell_window,
)

_NO_ELECTRON = electron_missing()
_NO_BACKEND = backend_missing()

# A skip is correct on a machine that has not installed Electron or built the
# backend. It is not correct on CI, where it turns "this layer is covered"
# into a green run that exercised none of it.
refuse_to_skip_in_ci(_NO_ELECTRON, _NO_BACKEND)

pytestmark = pytest.mark.skipif(
    _NO_ELECTRON is not None, reason=_NO_ELECTRON or ""
)
requires_backend = pytest.mark.skipif(
    _NO_BACKEND is not None, reason=_NO_BACKEND or ""
)

# `probe()` gives up after 6s and the shell allows 30s for a spawn; a page
# assertion has to outlast whichever one it is waiting on.
PROBE_WAIT = 15_000
START_WAIT = 60_000

DRIVE_ENV = "\n".join(
    [
        # The same deliberately-invalid token `backend.test.js` uses: it gets
        # far enough to prove the password unwrapped the keystore, and stops
        # one round trip short of needing a real bot.
        "DISCORD_BOT_TOKEN=not-a-real-token-for-the-shell-integration-test",
        "DISCORD_CHANNEL_ID=100000000000000000",
        "SFTP_USER=shelltest",
        "WEB_ENABLED=0",
        "SFTP_PORT=39223",
        "ARGON2_TIME_COST=1",
        "ARGON2_MEMORY_KIB=64",
        "ARGON2_PARALLELISM=1",
    ]
)


# --------------------------------------------------------------- setup.html


async def test_the_saved_address_is_offered_on_arrival(tmp_path):
    """A full IPC round trip before the user touches anything.

    The value asserted is deliberately not the placeholder default. Filling
    the field from a constant would satisfy any assertion for
    `http://127.0.0.1:8080`, so only a saved address that differs proves
    `dd.current()` resolved through the preload, reached `readConfig()` in the
    main process, and came back.

    The address is saved after the window is already up, because a config
    that carries one never reaches this screen -- `main.js` sends it straight
    to the drive window. `dd:current` re-reads the file on every invoke, so a
    reload is enough to ask again.
    """
    saved = "http://10.1.2.3:9999"
    async with shell_window(tmp_path) as page:
        await expect(
            page.get_by_role("heading", name="連線到你的 Discord Drive")
        ).to_be_visible()

        (tmp_path / "config.json").write_text(
            json.dumps({"serverUrl": saved}), encoding="utf-8"
        )
        await page.reload()

        await expect(page.locator("#url")).to_have_value(saved)


async def test_no_saved_address_falls_back_to_the_placeholder(tmp_path):
    """The other half of the same line, on a genuinely empty config."""
    async with shell_window(tmp_path) as page:
        await expect(page.locator("#url")).to_have_value("http://127.0.0.1:8080")


async def test_a_non_http_address_is_refused_by_scheme(tmp_path):
    """`normaliseServerUrl` throwing "scheme" has to reach the right message.

    `server-url.test.js` already proves the refusal. It cannot prove that
    `REASONS` has an entry for it -- a missing key falls through to the
    generic "連線失敗" and the user learns nothing.
    """
    async with shell_window(tmp_path) as page:
        await page.fill("#url", "ftp://nope")
        await page.get_by_role("button", name="測試連線").click()

        status = page.locator("#status")
        await expect(status).to_have_class("status on bad", timeout=PROBE_WAIT)
        await expect(status).to_contain_text("只接受 http 或 https")


async def test_an_address_with_nothing_listening_reports_unreachable(tmp_path):
    """A real socket attempt, not a parse. The port is closed on purpose."""
    async with shell_window(tmp_path) as page:
        await page.fill("#url", "http://127.0.0.1:1")
        await page.get_by_role("button", name="測試連線").click()

        status = page.locator("#status")
        await expect(status).to_contain_text("連不上", timeout=PROBE_WAIT)
        await expect(status).to_contain_text("docker compose up -d")


async def test_something_listening_that_is_not_the_drive_is_named_as_such(tmp_path):
    """The distinction the whole probe exists for.

    "Nothing is listening" and "the wrong thing is listening" send the user to
    completely different fixes, and a wrong port produces the second one.
    """
    runner, origin = await health_server(ok=False)
    try:
        async with shell_window(tmp_path) as page:
            await page.fill("#url", origin)
            await page.get_by_role("button", name="測試連線").click()

            status = page.locator("#status")
            await expect(status).to_contain_text(
                "有東西在聽，但不是 Discord Drive", timeout=PROBE_WAIT
            )
    finally:
        await runner.cleanup()


async def test_a_healthy_server_reports_a_good_connection(tmp_path):
    """The happy path, end to end: typed address to green status.

    Everything between is real -- Chromium's network stack out of the main
    process, a socket, an HTTP response parsed against `parsed.ok === true`,
    and the result rendered back through the bridge.
    """
    runner, origin = await health_server(ok=True)
    try:
        async with shell_window(tmp_path) as page:
            await page.fill("#url", origin)
            await page.get_by_role("button", name="測試連線").click()

            status = page.locator("#status")
            await expect(status).to_have_class("status on ok", timeout=PROBE_WAIT)
            await expect(status).to_contain_text("連線正常")
    finally:
        await runner.cleanup()


async def test_the_busy_state_returns_the_button_to_its_label(tmp_path):
    """`withBusy` has a `finally`; this is what proves it runs.

    A button left disabled after one failed probe strands the user on a screen
    whose only two controls no longer respond.
    """
    async with shell_window(tmp_path) as page:
        button = page.get_by_role("button", name="測試連線")
        await page.fill("#url", "ftp://nope")
        await button.click()

        await expect(page.locator("#status")).to_contain_text(
            "只接受 http 或 https", timeout=PROBE_WAIT
        )
        await expect(button).to_be_enabled()
        await expect(button).to_have_text("測試連線")


async def test_the_privileged_bridge_survives_the_link_to_local_mode(tmp_path):
    """`main.js` claims Electron re-injects the preload across in-window
    navigation, which is why one preload file carries both bridges. If that
    were wrong, `local.html` would load and then sit on "檢查中…" forever,
    because `window.ddLocal` would be undefined and `refresh()` would throw.
    """
    async with shell_window(tmp_path) as page:
        await page.get_by_role("link", name="改在這台電腦上執行（不需要另一台伺服器）→").click()
        await page.wait_for_url("**/local.html")

        assert await page.evaluate("typeof window.ddLocal") == "object"
        assert await page.evaluate("typeof window.dd") == "object"


# --------------------------------------------------------------- local.html


@requires_backend
async def test_a_fresh_data_directory_shows_the_first_run_screen(tmp_path):
    """`first-run` reaching the screen, and the path it names being the real
    one. A hard-coded or mis-joined path here sends the user to edit a file
    that the backend will never read.
    """
    async with shell_window(tmp_path, config={"mode": "local"}) as page:
        await expect(
            page.get_by_role("heading", name="第一次執行：先填設定")
        ).to_be_visible(timeout=START_WAIT)

        shown = await page.locator("#config-path").inner_text()
        assert shown == str(tmp_path / "drive.env"), shown
        assert (tmp_path / "drive.env").is_file()

        await expect(page.get_by_role("button", name="重新檢查")).to_be_visible()
        await expect(page.locator("#password-screen")).to_be_hidden()


@requires_backend
async def test_filling_the_config_and_rechecking_reaches_the_password_screen(tmp_path):
    """The button the first-run screen exists to hand over to.

    `refresh(true)` has to re-spawn the backend and swap screens. Only the
    force path can do this -- a cached `first-run` would leave the user
    clicking a button that never changes anything.
    """
    async with shell_window(tmp_path, config={"mode": "local"}) as page:
        await expect(
            page.get_by_role("heading", name="第一次執行：先填設定")
        ).to_be_visible(timeout=START_WAIT)

        (tmp_path / "drive.env").write_text(DRIVE_ENV, encoding="utf-8")
        await page.get_by_role("button", name="重新檢查").click()

        await expect(page.get_by_role("heading", name="輸入密碼")).to_be_visible(
            timeout=START_WAIT
        )
        await expect(page.locator("#password")).to_be_focused()
        await expect(page.locator("#first-run")).to_be_hidden()


@requires_backend
async def test_the_first_run_screen_can_be_read_in_english(tmp_path):
    """The preference reaching the screen a new user actually lands on.

    `local.html` renders four screens from one dictionary, and this is the one
    that decides whether somebody who cannot read Chinese can get the app
    running at all.
    """
    async with shell_window(tmp_path, config={"mode": "local", "lang": "en"}) as page:
        await expect(
            page.get_by_role("heading", name="First run: fill in the settings")
        ).to_be_visible(timeout=START_WAIT)
        await expect(page.get_by_role("button", name="Check again")).to_be_visible()
        assert await page.evaluate("document.documentElement.lang") == "en"


@requires_backend
async def test_a_rejected_token_puts_the_backend_output_on_the_screen(tmp_path):
    """The failure a user will actually hit, shown rather than swallowed.

    `backend.test.js` asserts `start()` returns that output. This asserts the
    page renders it, clears the password field, and leaves the button usable
    for a second attempt -- three things that are separate code from the
    result object, and all of which fail silently if the screen is wrong.
    """
    async with shell_window(tmp_path, config={"mode": "local"}) as page:
        await expect(
            page.get_by_role("heading", name="第一次執行：先填設定")
        ).to_be_visible(timeout=START_WAIT)

        (tmp_path / "drive.env").write_text(DRIVE_ENV, encoding="utf-8")
        await page.get_by_role("button", name="重新檢查").click()
        await expect(page.get_by_role("heading", name="輸入密碼")).to_be_visible(
            timeout=START_WAIT
        )

        await page.fill("#password", "integration-test-password-long-enough")
        await page.get_by_role("button", name="啟動").click()

        status = page.locator("#pw-status")
        await expect(status).to_contain_text("啟動失敗", timeout=START_WAIT)
        # The exact line a rejected token produces (`src/discord_api.py`). Its
        # presence proves the piped password got far enough to unwrap the
        # keystore, and that the page put the child's own output on screen
        # instead of a generic message.
        await expect(status).to_contain_text("DISCORD_BOT_TOKEN was rejected by Discord")

        await expect(page.locator("#password")).to_have_value("")
        await expect(page.get_by_role("button", name="啟動")).to_be_enabled()


# ------------------------------------------------------- the language switch


async def test_the_switch_puts_the_setup_screen_into_english_and_back(tmp_path):
    """One click, and every string on the page moves -- including the ones
    that are not in the markup.

    `language.test.js` proves the toggle picks the other language. It cannot
    prove the button is wired to it, that the dictionary has an English half,
    or that `applyLanguage` reaches an element whose `data-i18n` was misspelt.
    """
    async with shell_window(tmp_path) as page:
        await expect(
            page.get_by_role("heading", name="連線到你的 Discord Drive")
        ).to_be_visible()

        await page.get_by_role("button", name="語言").click()

        await expect(
            page.get_by_role("heading", name="Connect to your Discord Drive")
        ).to_be_visible()
        await expect(page.get_by_role("button", name="Test connection")).to_be_visible()
        # Not decoration: it is what a screen reader picks a voice from, and
        # what the browser hyphenates and picks fonts by.
        assert await page.evaluate("document.documentElement.lang") == "en"

        await page.get_by_role("button", name="Language").click()

        await expect(
            page.get_by_role("heading", name="連線到你的 Discord Drive")
        ).to_be_visible()
        assert await page.evaluate("document.documentElement.lang") == "zh-Hant"


async def test_the_chosen_language_outlives_the_window(tmp_path):
    """It has to be the main process holding this, not the page.

    Both pages are file:// and link to each other, so a preference kept in the
    page would be gone at the first click and gone again at the next launch.
    The second window here is a genuinely new Electron process reading the
    config the first one wrote.
    """
    async with shell_window(tmp_path) as page:
        await page.get_by_role("button", name="語言").click()
        await expect(
            page.get_by_role("heading", name="Connect to your Discord Drive")
        ).to_be_visible()

    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved["lang"] == "en", saved

    # config=None so the file the first window wrote is left alone.
    async with shell_window(tmp_path) as page:
        await expect(
            page.get_by_role("heading", name="Connect to your Discord Drive")
        ).to_be_visible()


async def test_the_language_follows_the_link_into_local_mode(tmp_path):
    """One preference across both pages, not one per page.

    The two screens carry separate dictionaries -- their CSP allows no shared
    script file -- so "both pages agree" is a claim about the value they read,
    and only crossing the link actually tests it.
    """
    async with shell_window(tmp_path) as page:
        await page.get_by_role("button", name="語言").click()
        await expect(page.get_by_role("button", name="Test connection")).to_be_visible()

        await page.get_by_role(
            "link", name="Run it on this computer instead (no separate server) →"
        ).click()
        await page.wait_for_url("**/local.html")

        await expect(page.get_by_role("button", name="Language")).to_be_visible()
        assert await page.evaluate("document.documentElement.lang") == "en"


async def test_a_message_already_on_screen_is_translated_with_the_page(tmp_path):
    """The half that a naive implementation gets wrong.

    Status text is built when the probe returns, not when the page renders, so
    translating only `[data-i18n]` elements leaves a Chinese failure sitting
    under an English page -- and it is a failure message, which is the moment
    the reader can least afford it.
    """
    async with shell_window(tmp_path) as page:
        await page.fill("#url", "ftp://nope")
        await page.get_by_role("button", name="測試連線").click()

        status = page.locator("#status")
        await expect(status).to_contain_text("只接受 http 或 https", timeout=PROBE_WAIT)

        await page.get_by_role("button", name="語言").click()

        await expect(status).to_contain_text("Only http or https")
        await expect(status).not_to_contain_text("只接受")


async def test_a_config_with_no_language_still_opens(tmp_path):
    """Every build before this one wrote a config without the key.

    `normaliseLanguage` has a unit test for the value; this is the path that
    actually delivers it, and the failure it guards against is a blank window
    rather than a wrong word.
    """
    async with shell_window(tmp_path, config={"serverUrl": ""}) as page:
        await expect(
            page.get_by_role("heading", name="連線到你的 Discord Drive")
        ).to_be_visible()
        # The heading alone is not enough: a missing key still renders Chinese
        # through the dictionary's own fallback, and only this says the value
        # that reached the page was a language rather than `undefined`.
        assert await page.evaluate("document.documentElement.lang") == "zh-Hant"


async def test_a_hand_edited_language_falls_back_rather_than_blanking(tmp_path):
    """config.json is a file a user can open and mistype in."""
    async with shell_window(tmp_path, config={"lang": "klingon"}) as page:
        await expect(
            page.get_by_role("heading", name="連線到你的 Discord Drive")
        ).to_be_visible()
        assert await page.evaluate("document.documentElement.lang") == "zh-Hant"
