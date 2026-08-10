"""Signing in, driven through a real browser against a real server.

The first tests in this suite that touch the layer the user touches. Everything
they assert is already covered by `test_web.py` at the HTTP level -- and that
is the point: these exist to catch the half `test_web.py` is blind to, where
the endpoint is correct and the form never calls it.

Assertions are on roles and visible text, not screenshots. A pixel comparison
across two machines with different fonts fails for reasons that have nothing
to do with this application.
"""

from playwright.async_api import expect

from tests.conftest import TEST_PASSWORD, TEST_USER

EMPTY_DRIVE = "這裡還沒有東西"


async def sign_in(page, *, username=TEST_USER, password=TEST_PASSWORD):
    await page.fill("#dd-user", username)
    await page.fill("#dd-pass", password)
    await page.get_by_role("button", name="登入").click()


async def test_the_sign_in_screen_is_served(page):
    """The bundle is reachable, React mounted, and the form rendered.

    A 200 on `/` proves none of that: the catch-all hands out `index.html`
    whatever happens afterwards, so a bundle that throws on mount looks
    identical to a working one from the API's side.
    """
    await expect(page.get_by_role("button", name="登入")).to_be_visible()


async def test_signing_in_reaches_the_file_manager(page):
    await sign_in(page)
    await expect(page.get_by_text(EMPTY_DRIVE)).to_be_visible()


async def test_a_wrong_password_keeps_you_on_the_sign_in_screen(page):
    await sign_in(page, password="not-the-password")
    await expect(page.get_by_role("button", name="登入")).to_be_visible()
    await expect(page.get_by_text(EMPTY_DRIVE)).not_to_be_visible()


async def test_the_session_survives_a_reload(page):
    """The cookie is what carries the login, so a reload must not undo it.

    `HttpOnly` plus `SameSite=Strict` means the page cannot check this for
    itself; only a browser actually storing and re-sending the cookie can.
    """
    await sign_in(page)
    await expect(page.get_by_text(EMPTY_DRIVE)).to_be_visible()

    await page.reload()
    await expect(page.get_by_text(EMPTY_DRIVE)).to_be_visible()


async def test_a_new_folder_appears_without_a_reload(page):
    """One full round trip: click, API call, state update, re-render."""
    await sign_in(page)
    await expect(page.get_by_text(EMPTY_DRIVE)).to_be_visible()

    await page.get_by_role("button", name="新資料夾").click()
    await page.get_by_role("textbox").last.fill("報告")
    await page.get_by_role("button", name="建立").click()

    # Scoped to the listing: the name also lands in the toast and in the
    # breadcrumb button, and matching those would pass even if the row never
    # rendered.
    await expect(page.get_by_role("main").get_by_text("報告")).to_be_visible()
    await expect(page.get_by_text(EMPTY_DRIVE)).not_to_be_visible()
