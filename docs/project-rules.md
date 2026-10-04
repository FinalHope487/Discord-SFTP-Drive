<!--
本專案專屬規則：規則檔的通用規則在本專案代入的值。
放：專案目錄、各層驗證指令、真依賴與替身、開發服務埠、專案專屬工具的執行方式。
不放：通用規則（寫規則檔）、測試數字（寫 ROADMAP.md〈基線〉）、決策（寫 docs/decisions/）。
與規則檔衝突時以規則檔為準，並修正這份。
-->

# 本專案規則

## 目錄

| 用途 | 路徑 |
|---|---|
| Python 目錄 | repo 根目錄（`pytest.ini`、`requirements.txt`、`requirements-dev.txt` 在這裡；沒有 `pyproject.toml`） |
| Python 直譯器 | `./venv/Scripts/python.exe`。不用裸 `python`，見 `SOP[指令沒有做到你以為的事]#1` |
| 前端：檔案管理員 SPA | `client/app`（Vite + React，建置產物 `client/app/dist/` 不在版控） |
| 前端：桌面外殼 | `client/shell`（Electron） |
| 單機版後端執行檔 | `dist-standalone/discord-drive.exe`（PyInstaller，不在版控） |

## 驗證指令

規則檔的 `uv run pytest -n auto -m "not serial"` 不適用：本專案沒有 uv 專案、沒裝 pytest-xdist、沒有 `serial` marker。用下表。

| 層 | 指令（repo 根目錄） | 打到哪 |
|---|---|---|
| Python 內層＋user 層 | `./venv/Scripts/python.exe -m pytest` | 全套，含下面兩個 user 層檔案 |
| 同一套換真 SQLite | `./venv/Scripts/python.exe -m pytest --db=sqlite` | 假件 `FakeDB` 換成真 SQLite |
| Electron 主程序純模組＋對真 exe 的整合 | `cd client/shell && node --test` | 缺 `dist-standalone/discord-drive.exe` 時整合部分 skip |
| lint | `./venv/Scripts/python.exe -m pyflakes src tests` | CI 同一條 |
| 真 MongoDB 與真 stack | `docker compose up -d` 後看啟動 log | 索引規格只有真 MongoDB 會驗 |

- `pytest.ini` 的 `addopts` 已有 `-q`，命令列不要再加 `-q`，否則看不到 `N passed`（`SOP[指令沒有做到你以為的事]#7`）
- 測試數字寫 `ROADMAP.md`〈基線〉，只寫已提交樹跑得出來的數字

### user 層

| 層 | 檔案 | 怎麼打 |
|---|---|---|
| 瀏覽器 | `tests/test_ui_login.py`、`tests/test_ui_language.py` | Playwright 真瀏覽器 → 真 aiohttp → 真 VFS／真資料庫 → `fake_discord` |
| 桌面視窗 | `tests/test_ui_shell.py` | 真 Electron 視窗（`--remote-debugging-port` + Playwright `connect_over_cdp`）→ 真 preload bridge → 真主行程 → 真 `discord-drive.exe` |

啟動與連線細節在 `tests/shell_support.py`；CI 上缺件要 fail 不 skip，作法是同檔的 `refuse_to_skip_in_ci`。
外殼測試的視窗放在螢幕外、不搶焦點（`DD_SHELL_OFFSCREEN=1`），跑測試時可以照常用電腦。`shell_window` 預設寫入 `lang: zh`，因為多數斷言是中文字；要測 app 自己的預設語言就傳 `lang=None`。

### 前置條件

| 缺什麼 | 症狀 | 補法 |
|---|---|---|
| `client/app/dist/`，或比 `client/app/src` 舊 | 10 項 fail（`test_ui_login.py` 5、`test_ui_language.py` 5） | `cd client/app && npm run build` |
| `client/shell/node_modules`（Electron） | 18 項 skip | `cd client/shell && npm install` |
| `dist-standalone/discord-drive.exe` | 4 項 skip | `./venv/Scripts/python.exe -m PyInstaller discord-drive.spec --noconfirm --distpath dist-standalone --workpath build-standalone` |
| Linux 沒有 `DISPLAY` | 18 項 skip | `xvfb-run -a` 包住 pytest |

### 真依賴與替身

| 外部 | 測試裡用什麼 | 真的那一個在哪驗 |
|---|---|---|
| MongoDB | `tests/fakes.py` 的 `FakeDB`（只記錄索引參數，不驗規格、不強制唯一）；`--db=sqlite` 換真 SQLite | `docker compose up -d` |
| Discord API | `fake_discord` fixture | `test_a_rejected_token_puts_the_backend_output_on_the_screen` 打真 Discord（CI 偶發紅，見 `SOP[測試紅、卡住或變慢]#6`） |

### 只有 Linux 驗得到的分支

相依套件在匯入時依平台綁不同實作。已知一處：asyncssh 依 `os.SEEK_DATA` 決定 SFTP 稀疏檔案走哪條路，Windows 不執行。
升級相依、或改到被這種分支包住的程式碼時，驗收看 Linux CI（`.github/workflows/test.yml`），不看本機；
補測試直接打自己那一側的方法，例 `tests/test_sparse_ranges.py` 直接對 `seek` 斷言。

## 開發服務埠

推送前確認沒人佔：

| 埠 | 誰 |
|---|---|
| 8080 | web UI／API（`WEB_PORT`，compose 綁 `127.0.0.1`） |
| 2222 | SFTP（`SFTP_PORT`） |
| 5173 | `client/app` 的 `npm run dev`（`strictPort`，代理 `/api` 到 8080） |

## 專案專屬工具

| 工具 | 執行方式 |
|---|---|
| `tools/outline.py` | `./venv/Scripts/python.exe tools/outline.py <檔> --min 20` |
| `tests/capture_readme_images.py` | `./venv/Scripts/python.exe -m pytest tests/capture_readme_images.py`：重產 README 截圖，見 `docs/visual-guide.md` |
| `scripts/find_orphans.py` | 只列不刪，見 `docs/decisions/2026-08-06-find-orphans-list-only.md` |

## 本專案專屬行為規則

- 根目錄的藍圖檔（`BLUEPRINT`，由 `/blueprint` 產出、不在版控）只給 user 看。user 沒明確要求（討論架構、重跑 `/blueprint`）就不讀，探索、規劃、委派都不先掃它
- 委派寫測試：
  - prompt 附 `git diff` 得到的函式清單，不只說「為這個模組寫測試」。實測不給軸的三個回合交出 212 支測試、全綠，對三個既有缺陷殺傷率 0；給函式清單後 19 支全中
  - 收貨時把被測行為逐一改壞，確認對應那一支變紅；沒變紅的那支不算。「跑過且全綠」不是證據：那 212 支用 `trace --count` 量，三行缺陷都被執行到卻沒被斷言（量法見 `SOP[測試綠但實際沒生效]#9`）
  - 指定驅動層。不指定時它一律直接呼叫內部函式：四個回合 231 支測試，走真實協定堆疊的是 0 支
