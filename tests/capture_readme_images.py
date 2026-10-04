"""Regenerates the README screenshots in docs/images/ from the real UI.

Not a test: the name keeps it out of the default run, so it only happens when
asked for.

    ./venv/Scripts/python.exe -m pytest tests/capture_readme_images.py

Same stack as test_ui_login.py -- real browser, real aiohttp, real VFS,
`fake_discord` at the edge -- so every file name and size on screen is made up
here and nothing real can end up in a public image. The UI is switched to
English because the README is English.

After running it, look at `git diff --stat docs/images` and at the changed
images themselves: a script can take the picture, it cannot tell whether the
picture still teaches the step it sits next to.
"""

from pathlib import Path

from playwright.async_api import async_playwright, expect

from tests.conftest import TEST_PASSWORD, TEST_USER

OUT = Path(__file__).resolve().parents[1] / "docs" / "images"
VIEWPORT = {"width": 1280, "height": 800}


async def shoot(page, name):
    # Fade-ins would otherwise be caught half-way.
    await page.screenshot(path=OUT / name, animations="disabled")

FILES = [
    ("meeting-notes.txt", 18_400),
    ("budget-2026.xlsx", 96_000),
    ("holiday.jpg", 2_400_000),
]


async def test_capture(live_server, tmp_path):
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as driver:
        browser = await driver.chromium.launch()
        try:
            context = await browser.new_context(
                base_url=live_server, viewport=VIEWPORT, device_scale_factor=1
            )
            await context.add_init_script("localStorage.setItem('dd.lang', 'en')")
            page = await context.new_page()
            await page.goto(live_server)

            sign_in = page.get_by_role("button", name="Sign in")
            await expect(sign_in).to_be_visible()
            await page.fill("#dd-user", TEST_USER)
            await shoot(page, "sign-in.png")

            await page.fill("#dd-pass", TEST_PASSWORD)
            await sign_in.click()
            await expect(page.get_by_text("Nothing here yet")).to_be_visible()

            for i, name in enumerate(["Photos", "Taxes 2026"]):
                await page.get_by_role("button", name="New folder").click()
                await page.get_by_role("textbox").last.fill(name)
                if i == 0:
                    await expect(page.get_by_role("button", name="Create")).to_be_visible()
                    await shoot(page, "new-folder.png")
                await page.get_by_role("button", name="Create").click()
                await expect(page.get_by_role("main").get_by_text(name)).to_be_visible()

            paths = []
            for name, size in FILES:
                path = tmp_path / name
                path.write_bytes(b"\0" * size)
                paths.append(path)
            await page.locator("input[type=file]").set_input_files(paths)
            for name, _ in FILES:
                await expect(page.get_by_role("main").get_by_text(name).first).to_be_visible()
            # The transfer panel and the last toast both sit over the listing.
            await page.get_by_title("Clear finished").click()
            await expect(page.get_by_text("Created", exact=False)).to_have_count(0, timeout=15_000)
            await shoot(page, "file-list.png")

            await page.get_by_role("main").get_by_text("meeting-notes.txt").first.click()
            await page.keyboard.press("Delete")
            await page.get_by_role("button", name="Move to trash").last.click()
            await expect(page.get_by_role("main").get_by_text("meeting-notes.txt")).not_to_be_visible()

            await page.get_by_text("Trash", exact=True).first.click()
            await expect(page.get_by_role("button", name="Restore").first).to_be_visible()
            await expect(page.get_by_text("Moved to the trash")).to_have_count(0, timeout=15_000)
            await shoot(page, "trash.png")
        finally:
            await browser.close()
