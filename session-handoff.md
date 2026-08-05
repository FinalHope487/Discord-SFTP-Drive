# Session Handoff

依 `.claude/templates/session-handoff.md` 產出。**最後更新 2026-08-06（第二輪）。**

---

## 目前狀態

**跑得起來，改動已上線，六個 commit 已 push 到 `origin/master`（`1a84b95..77c0cbc`）。**
`docker compose up -d --build` 重建過兩次，production 兩次都乾淨啟動：MongoDB 連上、
Discord 可達、SFTP（2222）與 Web（127.0.0.1:8080）都在聽。
**啟動 log 沒有 `Created a new wrapped master key`**，也沒有 `SFTP_PASSWORD no longer
matches the stored hash`——密碼改走 docker secret 之後，既有金鑰與帳號記錄照樣打得開。

**578 項測試通過（+55），沒有 xfail 殘留**，pyflakes 乾淨（`src/` `tests/` `scripts/`），
前端建置通過，i18n 兩個語系各 179 個鍵、完全對齊。

`[next]` 四項做完三項，**第四項（重產出 `BLUEPRINT.md`）刻意留著**，理由見下。

---

## 已完成

### 1. `SFTP_PASSWORD` 走 docker secret（`1df3d77`）

`config.py` 支援 `_FILE` 後綴（`DISCORD_BOT_TOKEN` / `MONGO_URI` / `SFTP_PASSWORD` /
`SFTP_PASSWORD_OLD`），compose 加 `secrets:` 區塊，密碼掛在 `/run/secrets/sftp_password`。

**在 production 上逐項驗過**：`docker inspect` 現在只吐得出 `SFTP_PASSWORD_FILE=<路徑>`，
容器環境變數裡沒有 `SFTP_PASSWORD`，secret 檔以 `-r-xr-xr-x` 掛進去、appuser 讀得到。

兩個決策值得記住：
- **同時設變數與 `_FILE` 是拒絕，不是取捨優先權**。兩種順序都是在猜操作者的意思。
- **只剝掉一個結尾換行**。`echo pw > file` 會加一個；但貪心地剝掉所有空白會毀掉
  合法結尾是空格的密碼，而這個密碼差一個 byte 在啟動時跟完全錯誤沒有區別。

遷移腳本 `scripts/adopt_password_secret.py`（已進版控）**從執行中的容器搬密碼，
不重新解析 `.env`**——`.env` 的解析權在 compose 手上。密碼全程沒有進入終端機或對話紀錄。

### 2. 批次刪除變成可輪詢、可中斷的 job（`4faa853`）

新 `src/jobs.py`；`DELETE /api/trash` 與 `POST /api/trash/empty` 改回 202 + job，
新增 `GET /api/jobs`、`GET /api/jobs/{id}`、`POST /api/jobs/{id}/cancel`。
前端新增 `PurgeProgressDialog`，中英文案各一組。

- **分母在動工前就算完**（`vfs.purge_cost`），否則進度條會跑到 90% 然後停住
- **取消只落在附件與附件之間**——唯一一個「節點還在垃圾桶、可以再刪一次」的狀態
- **「已刪掉的不會回來」在四種狀態下都顯示**，不是只在中止之後
- **一棵樹同時只准一個 job**：兩個並行 purge 會互相蓋掉 `entries_mac_pending`
- **job 隨 session 死**：它跑在 session 的 vfs 上，那裡有解開的主金鑰

### 3. 附件 URL 過期的兩條重取分支（`209ab77`）

`tests/test_url_expiry.py`：stub 會簽出帶 `ex=` 的 URL 並**真的在過期後回 404**。
預測性重取（讀 `ex`、留安全邊界）與反應性重取（403/404 後再解析）都跑得到，14 項通過。
**做過突變測試**：拿掉安全邊界、拿掉 403/404 重試，各有測試變紅。

### 4. 兩個缺陷——這一輪最重要的產出（`d44565c`、`5aab08c`）

去驗「上傳中途斷線的清理路徑」時掉出來的。詳見下一節。

---

## 這一輪抓到的兩個 bug

### 一次失敗的上傳會讓所在目錄永久列不出來（**既有缺陷**）

重現只用 `FakeDiscord.fail_uploads_from`，也就是既有失敗測試本來就在用的開關。
**在根目錄發生就是整個硬碟。**

建節點時把子項寫進父目錄的 `entries_mac` 並 promote；`_rollback()` 刪掉節點文件卻
**沒有把那個 tag 還原**，於是 tag 永遠涵蓋一個不存在的子項，`verify_dir_entries` 之後
每次都失敗。**沒有 API 上的復原路徑——壞掉的正是「列目錄」這個動作本身。**

修法照 `purge()` 的 stage → 改動 → commit 三步。staging 自己失敗時**不刪節點**，
改回報 `stale_node`：那代表目錄本來就已經對不上，刪下去只會更難救。

**既有測試為什麼沒抓到**：它們斷言失敗上傳回傳的 500 形狀（`chunks_uploaded` /
`orphans` / `stale_node` 全是對的），然後就結束了，**從來沒有人在那之後列一次目錄**。
被測的命題是「上傳會回報它留下什麼」，而不是「硬碟還能用」。

### 斷線會把截斷的檔案當成完整的存下來

aiohttp 在客戶端消失時**取消 handler task**，而 `CancelledError` 是 `BaseException`，
`upload()` 的 `except Exception` 接不到，`finally` 照跑 `handle.close()`——而 close 就是 commit。
清單上因此出現一個以截斷長度列出的檔案，沒有任何地方說它是短的。

新增 `DiscordFile.abort()`，讓「不 commit 地收尾」變成可以說出口的事。
unwind 用 `asyncio.shield` 包住：這個 task 本來就在被拆掉，unwind 做到一半被打斷
正是孤兒的來源。另補 `_incomplete_body` 處理「截斷是餵 EOF 而不是取消」的形狀。

**順序不能反。** 先修目錄那條再修斷線——只修斷線會把「一個被截斷的檔案」
升級成「它所在的資料夾整個不能用」。

### 診斷過程本身的教訓

我第一次的診斷是錯的：以為 `read()` 回空字串讓迴圈正常結束。實際上是 task 被取消，
`_incomplete_body` 那一行從來沒被執行過。**分辨方式是在產品碼上插一行 log 然後發現
它沒印出來**——「加了檢查但行為沒變」跟「檢查根本沒跑到」看起來一模一樣。

---

## 未完成待辦

1. **重新產出 `BLUEPRINT.md`**（`[next]`，唯一還沒動的一條）。前置條件現在全數解除：
   docker secret 動的 `config.py` 載入路徑、以及 `_rollback()` 的行為（§4 描述的正是它），
   都已落地。這一輪刻意沒跑，是因為當時 `_rollback()` 還沒修；現在可以跑了。
2. **同一類 unwind 缺口要在 SFTP 那一側也查一次**（`[next]`，這一輪新開）。
   `_rollback()` 的修正是共用的（在 `DiscordFile` 上），所以**目錄鎖死那條 SFTP 也一起好了**；
   沒把握的是「SFTP 連線被砍會不會被當成正常結束」。驗法照 `tests/test_upload_disconnect.py`。
3. **驗證缺口還剩「真人點一輪」那半**（`[next]`）。真密碼是主金鑰的包裝，刻意不讓它進入
   對話或指令紀錄，**所以這半只能由人自己點**，不是可以委派的工作。
   另外 URL 過期那條，**stub 驗到的是邏輯，不是 Discord 自己的 CDN**，也不是真的等 24 小時。
4. **多帳號第 4 步**（`[later]`），仍卡在密碼救援路徑。

---

## 本輪不可碰的範圍

- **`crypto.py` / `keystore.py` / `users.py`**：一行都沒動。認證流程與金鑰處理不變，
  `TAG_VERSION` 仍是 3。
- **相依套件**：一個都沒加，Python 與 npm 都是。
- **schema 與線上資料**：沒有跑 migration，沒有改 schema，沒有刪任何既有資料。
- **既有錯誤語意**：名稱衝突仍然是 409，完整性失敗仍然是 `integrity_failure`，
  上傳失敗仍然是 `upload_failed` 加同樣四個欄位。
- **SFTP 協定介面**：`_rollback()` 的修正對 SFTP 呼叫端是純修復，沒有新增或改變回傳形狀。
- **`dev-stack/` 與 `.env.dev` 不進版本控制**；`secrets/` 這一輪新增進 `.gitignore`。

---

## 下一步建議任務

**跑 `/blueprint`。** 它是 `[next]` 唯一剩下的一條，前置條件剛剛全部解除，
而且這一輪之後文件缺的東西又多了一層（HTTP 層、前端、桌面外殼、jobs）。

之後建議接 SFTP 那一側的 unwind 檢查。它便宜——測試寫法已經有了現成模板——
而且如果那裡也有同樣的問題，現在正是兩條一起收的時候。

---

## 環境備忘

- venv 在 `venv/`（3.12.7），上線 image 是 3.12.13。一律用 `./venv/Scripts/python.exe -m pytest`。
- **`pytest.ini` 的 `addopts` 已經有 `-q`**；再加一個 `-q` 會變 `-qq` 而**吞掉最後的統計行**。
  要看「N passed」請用 `-o addopts="" -q`。
- **PowerShell 的 cwd 會漂**；跑測試用絕對路徑。
- 改 `src/` 一定要 `docker compose up -d --build`；**改前端不用**（`dist/` 是掛進去的）。
- **`secrets/sftp_password` 不進版控，重建環境要自己產**：stack 還在跑的時候執行
  `python scripts/adopt_password_secret.py`；容器已經換過就得手動寫。
  搞砸的代價是服務起不來（`ensure_usable` 會拒絕啟動，不會毀資料），
  但 `sync_env_user` 會先覆蓋 password hash，放回正確密碼會自癒。
- `tests/fakes.py` 這一輪多了 `FakeDiscord.before_delete`（一個在每次刪除前 await 的鉤子），
  批次刪除的進度與取消測試靠它把工作停在半空中。
