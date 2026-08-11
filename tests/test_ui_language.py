"""The file manager's language switch, driven through a real browser.

`i18n.js` has carried an English dictionary, and the chip an `onClick`, since
before this file existed -- and nothing ever proved either of them worked. The
failures that would have gone unnoticed are all silent: a key missing from the
English half renders as `dlg.purge.title` on screen, a chip wired to the wrong
setter renders nothing at all, and a string built in JavaScript rather than
looked up stays Chinese for ever. Every other suite in this repo is green
through all three.

The last test here is the one that generalises. Asserting particular English
sentences only covers the sentences somebody thought to assert; asserting that
no Chinese character survives on screen covers the ones nobody thought of,
which is where untranslated text actually hides.
"""

from playwright.async_api import expect

from tests.test_ui_login import EMPTY_DRIVE, sign_in

EMPTY_DRIVE_EN = "Nothing here yet"


def chinese_in(text):
    return sorted({c for c in text if "一" <= c <= "鿿"})


async def test_the_sign_in_screen_switches_to_english_and_back(page):
    """The switch before anybody has an account open.

    This is the screen that has to be readable first: somebody who cannot get
    past it never sees the one behind it.
    """
    await expect(page.get_by_role("button", name="登入")).to_be_visible()

    await page.get_by_role("button", name="語言").click()

    await expect(page.get_by_role("button", name="Sign in")).to_be_visible()
    assert await page.evaluate("document.documentElement.lang") == "en"

    await page.get_by_role("button", name="Language").click()

    await expect(page.get_by_role("button", name="登入")).to_be_visible()
    assert await page.evaluate("document.documentElement.lang") == "zh-Hant"


async def test_the_file_manager_switches_once_you_are_inside(page):
    """The chip in the title bar is a second control on a second screen.

    `App.jsx` and `Login.jsx` each render their own, and only one of them is
    reachable at a time -- so one of them working says nothing about the other.
    """
    await sign_in(page)
    await expect(page.get_by_text(EMPTY_DRIVE)).to_be_visible()

    await page.get_by_role("button", name="語言").click()

    await expect(page.get_by_text(EMPTY_DRIVE_EN)).to_be_visible()
    assert await page.evaluate("document.documentElement.lang") == "en"


async def test_the_choice_survives_a_reload(page):
    """Kept in localStorage, so a reload must not drop back to Chinese.

    The desktop shell keeps its own preference in config.json instead. The two
    are separate on purpose; see QUESTIONS.md.
    """
    await page.get_by_role("button", name="語言").click()
    await expect(page.get_by_role("button", name="Sign in")).to_be_visible()

    await page.reload()

    await expect(page.get_by_role("button", name="Sign in")).to_be_visible()


async def test_no_chinese_is_left_on_the_sign_in_screen_in_english(page):
    """Including the strings that are built rather than looked up.

    The session-length buttons are the ones that matter here: their labels are
    assembled in `labelFor()` from a number and a unit, so they cannot come
    from the dictionary and would stay Chinese if that function ignored the
    language it is handed.
    """
    await page.get_by_role("button", name="語言").click()
    await expect(page.get_by_role("button", name="Sign in")).to_be_visible()

    stray = chinese_in(await page.locator("body").inner_text())
    assert not stray, f"untranslated on the sign-in screen: {''.join(stray)}"


async def test_no_chinese_is_left_in_the_file_manager_in_english(page):
    """The same sweep over the interface behind the sign-in screen.

    The drive is empty, so everything on screen is the application's own text:
    a file or folder name would be the user's, and no language setting should
    touch those.
    """
    await sign_in(page)
    await expect(page.get_by_text(EMPTY_DRIVE)).to_be_visible()

    await page.get_by_role("button", name="語言").click()
    await expect(page.get_by_text(EMPTY_DRIVE_EN)).to_be_visible()

    stray = chinese_in(await page.locator("body").inner_text())
    assert not stray, f"untranslated in the file manager: {''.join(stray)}"
