# 2026-08-09 · 使用者層的驗收用 Playwright，測試檔放 `tests/`

**決策**：使用者層的驗收用 Playwright，測試檔放 `tests/`

**依據**：
（2026-08-09）。真瀏覽器 → 真 aiohttp
程序 → 真 VFS / 真資料庫 → `fake_discord`，只有最外層的外部服務是假的。
否決 vitest + jsdom：jsdom 是第二套假件，而這個專案已經被「假件不模擬的那一半」咬過三次
（見 `SOP.md`），不需要第四次。放 `tests/` 而不是 `client/app/` 的理由是它因此直接繼承
`fake_db` / `fake_discord` / `account` 三個既有 fixture 與 `--db=sqlite`，
測試總數維持單一數字。
**Electron 外殼的兩頁 UI 不做**——Python Playwright 不支援 Electron，要另外引進 JS 端
Playwright，而 `setup.html` 只有一個表單，投報率遠低於 SPA。列在下方 `[later]`。

**部分已推翻**：「Electron 外殼兩頁 UI 不做」於 2026-08-11 改為用 `connect_over_cdp` 做掉（`tests/test_ui_shell.py`）。

**反悔成本**：搬入時原文未單獨記錄（2026-10-04 自 `ROADMAP.md`〈已拍板的長期決策〉搬入）。
