# Session Handoff

依 `.claude/templates/session-handoff.md` 產出。**最後更新 2026-08-06。**

---

## 目前狀態

**跑得起來，且這一輪的改動已經上線。** `docker compose up -d --build` 重建過，
production 起來乾淨：MongoDB 連上、Discord 可達、SFTP（2222）與 Web（127.0.0.1:8080）都在聽。
**啟動 log 沒有出現 `Created a new wrapped master key`**，表示既有帳號與金鑰記錄被正常開啟，
`ensure_usable` 的守衛過了——沒有動到任何線上資料。

**523 項測試通過**，pyflakes 乾淨（`src/` `tests/` `scripts/`），前端建置通過。

三個 commit 已進 `master`，**尚未 push**：
`185e2a9` ROADMAP 標記與變更紀錄、`3602120` 忽略 dev stack、`fb356aa` 上傳失敗的回報。

---

## 已完成

### 1. ROADMAP 的標記與變更紀錄補齊

`[now]` 的 Client UI 六步全部落地卻還掛著 `[now]`，已退場（內容進變更紀錄，符合這份檔案
自己的慣例）。兩條 `[later]` 提成 `[next]`，理由都是它們自己的文字寫的前置條件已解除：
`SFTP_PASSWORD` 走 docker secret、重新產出 `BLUEPRINT.md`。

變更紀錄補上 2026-08-03（垃圾桶）與 2026-08-05（前端與桌面外殼）兩輪。
**測試數是實際跑出來的不是抄的**：把 `d3c8ec6` 的樹 export 出來用同一支 venv 跑，得到 491，
接得上前一條的 455。455 → 491 → 515 → 523。

### 2. 開發者模式：獨立的 dev stack，不是程式碼旁路

**關鍵前提**：這個系統的密碼不只是登入憑證，它是主金鑰的包裝
（`SFTP_PASSWORD` → KEK → 解開 `keystore` 的主金鑰）。所以「另一組開發帳密，
看得到同一批真實資料」在密碼學上做不到，除非那組帳密就是真密碼。
一條跳過密碼比對的旁路登得進去，但登入後會是一個解不開任何東西的 session。

做法是另起一個 compose project：

```
docker compose -p discord-drive-dev --env-file .env --env-file .env.dev up -d --build
```

- 多個 `--env-file` 後者覆蓋前者（已實測），所以 **`DISCORD_BOT_TOKEN` 直接從 `.env` 繼承，
  完全不經過對話紀錄**
- volume 自動前綴成 `discord-drive-dev_mongo_data`，與 production 是兩顆；
  dev 的 app 解析 `mongodb` 這個名字時落在自己的網路裡，碰不到 production
- 全新的丟棄式主金鑰，**解不開 production 任何一個 byte**
- port 2223 / 8081，`.env.dev` 把 session 期限縮短（idle 120s / 絕對 900s）好驗到期
- `.env.dev` 被 `.gitignore` 的 `.env.*` 蓋住，`dev-stack/` 本輪新增一條規則

### 3. UI 對真依賴的第一輪驗收，寫成可重跑的腳本

`dev-stack/ui-walkthrough.js`（不進版控）。**14/14 通過**，真 MongoDB、真 Discord：
建目錄／進入／244 KiB 上傳下載 sha256 相符／**12 MiB 跨 9 MiB 分塊邊界上傳，下載 sha256
跨分塊相符**／右鍵改名／搜尋／Delete 進垃圾桶／還原／遞迴刪整棵非空目錄／清空垃圾桶
真的釋放附件／連線數回報。收尾對帳 `nodes=1 live=1 trashed=0`。

腳本每次自帶亂數後綴（重跑不撞名）、失敗時仍產出報告、內建 keep-alive
（dev stack 的 idle 只有 120 秒，除錯停頓會把整輪打斷——第一次就是這樣死的）。
**憑證由 `window.__DD_*` 外部注入，腳本本身不含任何密碼。**

### 4. 上傳失敗的三個數字（`[next]` 第二項）

| 檔案 | 改動 |
|---|---|
| `src/vfs.py` | `_safe_delete_message()` 回傳成敗；`DiscordFile.failure_tally` 彙總；`_rollback()` 記下 node 那一列到底刪掉沒有 |
| `src/web.py` | `upload()` 把失敗留到 close 之後再報；`_upload_failed()` 產出形狀並保留既有錯誤語意 |
| `client/app/src/api.js` | `isUploadFailed` / `orphans` / `staleNode` |
| `client/app/src/Dialogs.jsx` | `UploadFailedDialog`，只依數字決定說哪一種話 |
| `client/app/src/i18n.js` | 中英各一組文案 |
| `tests/fakes.py` | `FakeDiscord.fail_deletes`、`FakeCollection.fail_deletes`、`DatabaseFailure` |

---

## 這一輪抓到的兩個 bug，以及它們的形狀

### 殘留節點：三個數字會說謊

上傳 30 MiB 途中停掉 dev 的 MongoDB：

1. `_rollback()` 成功刪掉 Discord 上的兩塊附件（`orphans=0`）
2. 接著要刪 node 那一列——**那也需要 Mongo**，失敗，而該失敗被 `except Exception` 吞掉
3. Mongo 裡留下一個 9 MiB 的節點，指向已不存在的附件

伺服器 log：`DiscordAPIError 404: Unknown Message`。**清單上看得到、點下去讀不出來**，
而只看三個數字的介面會說「這個檔案不存在，可以直接重試」。

修法是加第四個欄位 `stale_node`（不動任何既有欄位），UI 改說真話並給可行動的指示。
**吞掉那個失敗仍然是既有行為**——unwinding 的寫入路徑沒有更好的事可做——改變的只是
它不再對呼叫端沉默。決策與理由已寫進 `ROADMAP.md`。

**這一條的教訓不是「多加一個欄位」**，是：回報「我清乾淨了」的程式碼，
它自己的清理動作也可能失敗，而失敗的那半正好是沉默的那半。

### 裸 token 漏進 UI

規格把 `error` 那一格定成機器讀的 `upload_failed`，而 `ApiError.message` 取自
`body.error`，於是傳輸清單那一列直接印出這個 token。**測試斷言的是 JSON，
不是使用者看到的字**——只有真的點一輪才會抓到。

### 兩個「查了但不是 bug」

- **UI 點擊全部失效**。實測要求 (908,502)、實際落在 (2425,1342)，固定放大 2.672 倍
  ＝`(1440/674) × dpr 1.25`。in-app 瀏覽器覆寫 viewport 後的座標映射問題，**工具不是產品**。
- **`ConfirmDialog` 的「取消」`type` 是 `submit`**。它外面沒有 `<form>`，那只是 `<button>`
  沒寫 type 時的 HTML 預設值，只有 `onClick` 生效。**今天無害**，但 `PromptDialog` 的取消
  明確寫了 `type="button"`，這裡沒有——哪天有人把它包進 `<form>`，取消就會變成執行。一行的事。

---

## 未完成待辦

1. **push**。三個 commit 還在本機。`CLAUDE.md` 把 push main 列在必須先問那一層，所以沒做。
2. **驗證缺口還剩兩項半**（`[next]` 第一條）：附件 URL 真的過期後的重取路徑、
   上傳中途斷線的清理路徑，以及「真人用滑鼠鍵盤點一輪 + production 自己的資料」那半。
3. **批次刪除撞 429 的進度與中斷**（`[next]`）。仍然沒做，需要後端吐進度。
4. **`SFTP_PASSWORD` 走 docker secret**（`[next]`，前置條件已解除）。
5. **重新產出 `BLUEPRINT.md`**（`[next]`）。**排在 docker secret 之後**，那條會動 `config.py`。
6. **多帳號第 4 步**，仍卡在密碼救援路徑。

---

## 本輪不可碰的範圍

- **`crypto.py` / `keystore.py` / `users.py`**：一行都沒動。認證流程與金鑰處理不變，
  `TAG_VERSION` 仍是 3。
- **SFTP 協定介面**：`_rollback()` 的行為對 SFTP 呼叫端完全不變，新增的只有回報。
- **相依套件**：一個都沒加，Python 與 npm 都是。
- **既有的錯誤語意**：名稱衝突仍然是 409，完整性失敗仍然是 `integrity_failure`。
- **線上資料**：沒有跑 migration，沒有改 schema。dev stack 全程與 production 隔離。
- **`dev-stack/` 與 `.env.dev` 不進版本控制**——要沿用得自己重建，指令在 `.env.dev` 開頭。

---

## 下一步建議任務

**先 push**，三個 commit 的備份缺口比什麼都便宜關掉。

之後建議 `SFTP_PASSWORD` 走 docker secret：它是唯一一條純安全性、已拍板、
前置條件剛解除的，而且做完之後 `BLUEPRINT.md` 才值得重產出。

「真人點一輪」那半可以隨時插隊——服務就在 `127.0.0.1:8080`，
而這一輪已經證明真依賴那半是乾淨的，剩下的只是滑鼠。

---

## 環境備忘

- venv 在 `venv/`（3.12.7），上線 image 是 3.12.13。一律用 `./venv/Scripts/python.exe -m pytest`。
- **PowerShell 的 cwd 會漂**；跑測試用絕對路徑，或確認 `Push-Location` 有配對的 `Pop-Location`。
- 改 `src/` 一定要 `docker compose up -d --build`；**改前端不用**（`dist/` 是掛進去的）。
- dev stack 的啟用／停止／移除指令寫在 `.env.dev` 開頭。它與 production 共用同一個
  Discord DM 頻道，測試檔案會短暫出現在那裡，清空垃圾桶會真的把它們刪掉。
- 前端腳本要在 CSP `default-src 'self'` 底下載入，得先複製進 `client/app/dist/`
  用同源 `<script>` 標籤拉——**但那個目錄兩個 stack 都掛，用完要刪掉**。
