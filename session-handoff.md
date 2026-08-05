# Session Handoff

依 `.claude/templates/session-handoff.md` 產出。**最後更新 2026-08-05。**

---

## 目前狀態

**`ROADMAP.md` 的 `[now]` Client UI 五個步驟全部落地，並多做了第 6 步（桌面外殼）。**

服務同時跑 SFTP（`0.0.0.0:2222`）、HTTP API 與**檔案管理前端**（`127.0.0.1:8080`），
同一個 process。前端是 `client/app/dist`，以唯讀方式掛進容器的 `/app/web`。
桌面 app 已打包成兩種 `.exe`。

**已在真環境驗過。** Docker Desktop 起來了，真的 MongoDB、真的 bot token、真的 Discord 附件。
驗證前後對帳：`nodes=1 live=1 users=1 keystore=1 chunk_records=0`，前後完全一致，root 底下沒有殘留。

---

## 已完成

### 起點：原本的 UI 一行 API 都沒接

`client/Discord Drive desktop.dc.html` 是設計工具的產物：`fetch` 出現 **0 次**，
所有資料是模組頂層常數，所有動作只推一個 toast。而且它執行時從 unpkg 抓 React／
ReactDOM／Babel，**離線是一片空白**（舊的 `client/README.md` 寫「只有圖示會不見」，那句是錯的）。
打包出來的兩種模式也都是壞的：預設模式載 `http://127.0.0.1:8080` 但 `web.py` 沒有 static route，
`DD_LOCAL=1` 載的 `packaging/electron/app/index.html` 根本不存在。

### 後端（`src/`）

| 檔案 | 改動 |
|---|---|
| `vfs.py` | 加 `search()`：沿 `parent_id` 廣度優先，**每一層都經過 `entries_of`**（逐層驗 membership 標籤）。雙重上限（`limit` / `max_nodes`）都由 `truncated` 誠實回報 |
| `web.py` | static route（SPA fallback、路徑逃逸防護、hashed assets 設 immutable／`index.html` 設 no-store）；`GET /api/search`；`POST /api/sessions/revoke-others`；`_error()` 支援 machine-readable `code`；`integrity_failure` 可與一般 5xx 分辨；`auth_middleware` 的公開判斷改成 `/api/` 前綴測試 |
| `websession.py` | `idle_expires_in()` / `absolute_expires_in()` 分開回報；`count_for_tree()`；`drop_others()`（**保留呼叫者自己的 session**） |
| `config.py` | `WEB_STATIC_DIR`（預設 `web`） |
| `docker-compose.yml` | `./client/app/dist:/app/web:ro` + `WEB_STATIC_DIR=/app/web` |

### 前端（`client/app/`，Vite + React）

真的接上 API 的重寫。修掉的具體 bug：

- **倒數計時快 43%**（原型每 700ms 扣一秒）→ 改成 `GET /api/session` 每 10 秒同步、
  中間用 `Date.now()` 真實差值內插，所以只可能慢、不可能快。
- **英文模式把插值蓋掉**（`el.textContent = el.dataset.en`）→ 改成中英兩份字典。
- **多選刪除只看 `sel[0]`** → 逐項處理，含非空目錄的整棵警告。
- **「下一頁」是死按鈕、`history` 宣告了沒用** → 真的歷史堆疊。
- **畫著 `⌘K` 但沒有監聽** → Ctrl/Cmd+K、Ctrl+A、F2、Delete、Enter、Backspace、Esc、F5。
- **每一列畫綠色打勾** → 改成空心盾牌（列目錄只驗子項集合，不驗每一項自己的標籤）。
- **完整性失敗可以用 × 關掉** → 改成必須明確確認，事件留在狀態列計數。
- **假的分塊進度／假的 2.6 MiB/s** → `XMLHttpRequest.upload.onprogress` 的真實位元組數。
- **登入表單預填 `operator` 與 12 個 `•` 字元** → 空白，且失敗後清空密碼。
- **17 個狀態的除錯清單、429/503/401 三顆按鈕、設計審查註解卡** → 移除。
- 新增：登出按鈕、連線數與「登出其他連線」、搜尋、上傳覆蓋確認、離線橫幅、
  伺服器回報的 session 長度選項。

**執行時零外部來源**：圖示內嵌 SVG、字型用系統字、`index.html` 的 CSP 寫死 `default-src 'self'`。

### 桌面外殼（`client/shell/`，Electron）

首次開啟問伺服器位址（存檔前先 probe `/api/health`，分辨「沒有東西在聽」與
「有東西在聽但不是這個服務」）。設定視窗有 preload、**主視窗完全沒有 preload**。
產出 `dist-desktop/DiscordDrive-0.1.0-portable.exe`（可攜，86 MB）與 `-setup.exe`（NSIS）。

### 驗證

1. **515 項 pytest 通過**（新增 28 項：search 8、session/connections 4、static client 6、索引 4，其餘含既有）。
   同一份套件在 **production image 內也是 515 passed**。
2. **pyflakes 乾淨**（`src/` `tests/` `scripts/` `client/shell/make-icon.py`）。
3. **`node --test`**（`client/shell/`）6 項通過。
4. **實地操作驗收**：用 fakes 起真的 aiohttp app 服務真的 `dist/`，瀏覽器逐項驗過
   登入／401 文案／導覽與上下頁／建目錄／改名／單選與多選刪除／垃圾桶／還原撞名
   （比較兩邊真實大小、`keep_both` 的預覽名稱與伺服器產出的 `README.md (2).bak` 一致）／
   搜尋（大小寫、CJK、排除垃圾桶、reveal）／上傳（244 KiB 真的 PUT）／覆蓋確認／
   中英切換／登出／deep-link reload／路徑逃逸。
5. **打包後的 exe 真的跑起來**：寫入 `config.json` 指向伺服器後啟動，Electron 的 cache 裡
   出現只存在於我們 bundle 的字串（`登入會把主金鑰解到`），證明它載到了 SPA。
   `asar list` 確認 6 個檔案都在（**`server-url.js` 一開始漏在 `files` 外，會導致打包後 crash**）。

### 抓到並修掉的兩個 bug（在驗證過程中）

- **`0 × 0` 被當成「視窗太小」**。初次量測可能是 0（視窗還沒繪製），而之後不會有 `resize`
  事件來修正，所以整個 app 被遮罩永久蓋住。改成 0 視為「還沒量到」，並加 `ResizeObserver`。
- **`file:///etc/passwd` 被接受**。原本的正規化把 `http://` 硬黏在前面，變成一個 host 叫
  `file` 的 origin。抽成 `server-url.js` 並補測試；判斷 scheme 看 `://` 不看冒號
  （`localhost:8080` 有冒號但不是 scheme）。

### 文件

新增 `BUILD.md`（從零到 `.exe` 的教學，含疑難排解表）。重寫 `client/README.md`、
更新 `README.md` / `.env.example` / `ROADMAP.md`（8 條新拍板決策 + 3 條待辦）。
刪除 `client/backend-todo.md` 與 `client/handoff-frontend.md`（內容已完成或移入 ROADMAP）。
原型移到 `design/v1-file-manager-prototype.dc.html`（連同 `support.js`、`_ds/`），
刪除 `client/packaging/`（Electron 設定被 `client/shell/` 取代，Tauri 路線未採用）。

---

## 真環境驗證結果（2026-08-05 第二輪）

### 起容器時就撞到一個真 bug：垃圾桶的部分唯一索引從來沒建成功過

`db.py` 用 `partialFilterExpression={"trashed_at": {"$exists": False}}`，
**MongoDB 直接拒絕**（`Expression not supported in partial index: $not`），
所以伺服器根本起不來。這個 bug 從 2026-08-03 的垃圾桶那一輪就存在，
沒被發現是因為 `FakeDB` 不驗證索引規格，而那三天沒有在真環境重啟過。

改成 `{"trashed_at": None}`——對 null 的等值比對同時匹配 null 與缺欄位，是同一組文件，
且在 partialFilterExpression 的文法內。**寫入格式完全沒變。**
先用一次性 scratch collection 在真的 6.0 上把四種語意都試過才改：
兩個活著的同名 → duplicate key；刪掉後可以建同名新檔；兩個垃圾桶裡的可以同名；
還原撞名仍然被擋。已加 `tests/test_db_indexes.py` 釘住，並更新 `test_metadata.py`
裡原本在釘錯誤值的那一條。SOP.md 補了一條。

### 檔案操作矩陣：61 項全過

單一 session：建目錄／巢狀／撞名 409、小檔上傳下載 byte-identical、
**20 MiB 跨 3 個 chunk 真的傳上 Discord**（14 秒）且下載 sha256 相符、
列目錄排序、改名、跨目錄搬移、改名撞名 409、搜尋（全路徑／大小寫／排除垃圾桶）、
刪除進垃圾桶、還原、**刪掉後建同名新檔**（就是上面那條索引的場景）、
還原撞名 409 → `keep_both` → 兩份並存、非空目錄 rmdir 409、遞迴進垃圾桶、
整個目錄還原且內容跟著回來。

雙 session（各自獨立 cookie jar，同一個帳號）：兩邊都看到 `connections=2`、
CSRF token 不同且不能互用（403）、A 建檔 B 立刻看到、B 讀到 A 的位元組、
**B 用 12 MiB 覆寫後 A 讀到新位元組而不是過期的 chunk layout**、
B 改名 A 跟上（舊名 404）、B 刪除 A 的清單同步、A 看得到 B 的刪除進共用垃圾桶、
A 執行 revoke-others → B 收到 401、A 自己還在、連線數回到 1。

清理：整棵進垃圾桶 → 清空（釋放 7 個 Discord 附件）→ root 與垃圾桶都空。

### UI 的雙連線行為

真後端那半用 API 驗（上面 61 項）。**UI 這半刻意不用真密碼登入**——那組密碼是主金鑰的包裝，
不該進入任何對話紀錄。改用 fakes 後端跑同一份 `dist/` 與同一份 `web.py`：
狀態列顯示「2 個連線」、看得到另一個 session 剛建的檔、
對話框標題「目前有 2 個連線登入這個帳號」、按「登出其他所有連線」→
toast「已登出 1 個其他連線」、狀態列回到「1 個連線」、自己仍然可用（200），
而另一側的行程自己印出「B WAS SIGNED OUT by the UI」。

**還沒做過的**：用真密碼在 UI 上手動點一輪。若要補，服務就在 `127.0.0.1:8080`。

---

## 未完成待辦

1. **上傳失敗的三個數字**（`[next]`，ROADMAP）。前端目前顯示伺服器真的說了什麼，不假造。
2. **批次刪除撞 429 的進度與中斷**（`[next]`，ROADMAP）。這一版**沒有做**——
   沒有後端支撐的進度條是動畫，不是進度。
3. **`SFTP_PASSWORD` 走 docker secret**（早於這輪就拍板要做）。
4. **多帳號第 4 步**，仍卡在密碼救援路徑。
5. 仍未被真實環境驗證的舊項目：附件 URL 真的過期、`_rollback` 的保護、
   上傳中途斷線的清理路徑、session 真的閒置到期、**用真密碼在 UI 上手動點一輪**。

---

## 本輪不可碰的範圍

- **`crypto.py`**：一行都沒動，tag 涵蓋範圍不變，`TAG_VERSION` 仍是 3。
- **`keystore.py` / `users.py`**：沒動。認證流程與金鑰處理完全不變。
- **SFTP 協定介面**：對既有客戶端沒有任何變化。
- **Python 相依**：一個都沒加。新增的相依全部在 npm，且分成兩個獨立套件，
  `requirements.txt` 與 image 都沒動。
- **`WEB_HOST` 不是安全邊界**：容器內必須綁 `0.0.0.0`，`docker-compose.yml` 的
  `127.0.0.1:8080:8080` 才是。改動時不要搞反。
- **沒有動任何線上資料**，也沒有跑 migration。

---

## 下一步建議任務

**commit。** 這一輪的改動還沒有進版本控制，而其中一條是「服務起不起得來」等級的修正
（`db.py` 的索引）。在那之前先做任何新功能，等於把一個已知會擋住啟動的狀態留在 working tree。

之後建議照 ROADMAP 的 `[next]` 順序：上傳失敗的三個數字 → 批次刪除的進度與中斷。
兩者都會被「多帳號第 4 步」再次掀出來，先做的話那一步會輕很多。

---

## 環境備忘

- venv 在 `venv/`（3.12.7），上線 image 是 3.12.13。一律用 `./venv/Scripts/python.exe -m pytest`。
- Node 24.14 / npm 10.8。前端與外殼是**兩個各自獨立**的 npm 套件，各自 `npm install`。
- 程式碼是烤進 image 的，改完 `src/` 一定要 `docker compose up -d --build`；
  但**改前端不用**——`dist/` 是掛進去的。
- `electron-builder` 只能打自己平台的包。第一次 `npm run dist` 會下載 Electron（約 100 MB）。
- 打包設定的 `files` 白名單**漏一個檔案就是打包後 crash**，加檔案時記得同步更新，
  並用 `npx @electron/asar list` 對一次。
