# Session Handoff

依 `.claude/templates/session-handoff.md` 產出。**最後更新 2026-08-02。**

---

## 目前狀態

**`ROADMAP.md` 的 `[now]` 是 Client UI，第 1～4 步已落地並上線，剩第 5 步：前端。**

服務同時跑 SFTP（`0.0.0.0:2222`）與 HTTP API（`127.0.0.1:8080`），同一個 process。
帳號是資料庫裡的一列，金鑰 per-account，樹 per-account。API 的所有端點都已上線驗過。

線上 `nodes` 只剩 root，Discord 上 0 個附件，`users` 一列，`keystore` 一筆在
`user:a00e0bf3-…` 底下。`git` 上四個 commit，`977b7d8`（HTTP API）**尚未 push**。

---

## 已完成：Client UI 第 1～4 步

| 檔案 | 內容 |
|---|---|
| `src/users.py` | 帳號、Argon2id 密碼雜湊、`authenticate`、`Session` |
| `src/keystore.py` | per-account 記錄、`adopt_legacy_record()`、孤兒守衛 |
| `src/vfs.py` | `DiscordVFS(key, root_id)`，`root_id` 無預設值 |
| `src/web.py` | aiohttp app：登入／登出／列目錄／stat／上傳／下載／刪除／建目錄／改名 |
| `src/websession.py` | session store，idle + 絕對雙期限，client 只能往短調 |
| `src/webauth.py` | 登入節流：並發上限 + 佇列上限 + (位址, 裝置) 鎖定 |
| `src/main.py` | 啟動 SFTP 與 HTTP，關機時先收 web 再 drain SFTP |
| `.env.example` | web 設定，以及「手機怎麼連」三條路與各自代價 |

### 驗證（五層全做完）

```bash
./venv/Scripts/python.exe -m pytest        # 455 passed, ~27s
```

1. **455 項測試**（`test_users` 24、`test_websession` 20、`test_webauth` 15、`test_web` 24）。
2. **突變 25/25**：結構層 10 條、web 層 15 條（CSRF 檢查、session 檢查、HttpOnly、SameSite、
   期限夾制、絕對上限、位址層鎖定、並發上限…）。腳本在 scratchpad，未留在 repo。
3. **pyflakes 乾淨。**
4. **production image 內同一份 suite**：455 passed（3.12.13 / Linux）。
5. **實地驗收**：帳號遷移 20/20、HTTP API 25/25（含 12 MiB 跨兩 chunk 的上傳／下載／改名／
   刪除、cookie 三個旗標、期限夾制、CSRF 拒絕、登出後失效）。

---

## 未完成待辦（依建議順序）

1. **push `977b7d8`。**
2. **第 5 步：前端**（純靜態 SPA）。API 契約已固定、端點列在 `ROADMAP.md`，需要決定的只剩
   畫面與互動；前端是純靜態資源，改版不必重建 image。
3. **`SFTP_PASSWORD` 走 docker secret**：已拍板要做，排在前端之後。
4. **仍未被真實環境驗證的舊項目**：5xx 與傳輸層重試、附件 URL 真的過期、`_rollback` 的保護。
5. **web 層尚未被真實環境驗證的**：上傳中途斷線的清理路徑、session 真的閒置到期
   （驗收只驗了夾制後的數值，沒有等 10 分鐘）。

---

## 資料狀態

- 帳號遷移已跑完且**不可逆**；`users` 與 `keystore` 從此必須一起備份、一起還原。
- master key 沒有變，fingerprint `263b893e0206501d`。一個 chunk 都沒重傳。
- 驗收建立的檔案與目錄全部刪除，附件一併釋放，對帳 1 個節點、0 孤兒。
- **HTTP API 沒有引入任何新的資料格式**：它走同一個 `DiscordVFS`，寫進去的節點與 SFTP 一樣。

---

## 本輪不可碰的範圍

- **`crypto.py`**：一行都沒動，tag 的涵蓋範圍不變。
- **SFTP 協定介面**：對既有客戶端完全沒有變化。
- **相依套件**：一個都沒加。測試用 `aiohttp.test_utils` 而不是 `pytest-aiohttp`，
  就是為了不新增相依。
- **`WEB_HOST` 不是安全邊界**：容器內必須綁 `0.0.0.0`，`docker-compose.yml` 的
  `127.0.0.1:8080:8080` 才是。改動時不要搞反。

---

## 環境備忘

- venv 在 `venv/`（3.12.7），上線 image 是 3.12.13。一律用 `./venv/Scripts/python.exe -m pytest`
  （裸 `python` 是 Store 的 stub，exit 49 且無輸出）。
- 程式碼是烤進 image 的，改完 `src/` 一定要 `docker compose up -d --build`。
- MongoDB 的埠沒有對宿主開放，一次性腳本走
  `docker compose exec -T sftp-discord-server python - < 腳本`。
- 在容器裡跑測試要 `MSYS_NO_PATHCONV=1` 前綴、`--user root`、`-p no:cacheprovider`。

### 本輪踩到、按規則還沒進 `SOP.md` 的坑

- **Windows 上用 `pathlib.write_text()` 還原檔案，會把整份檔案的 LF 換成 CRLF。**
  突變腳本就是這樣還原的，跑完之後三個檔案每一行都變了，而測試全過。
  要用 `read_bytes()` / `write_bytes()`，並在結尾比對 hash。
- **Git Bash 裡 `grep -c $'\r'` 不會展開成 CR**，它變成空字串、匹配每一行，回報的數字
  剛好等於總行數。查換行符用 Python 讀 bytes。
- **突變測試要給 pytest 子行程設 timeout。** 拿掉登入佇列上限那條讓測試**卡死**而不是失敗，
  而被突變的檔案當時還沒 commit——git 救不回來。處理方式是只殺子行程 pytest、
  讓父行程的 `finally` 正常還原。該測試已改成用 `asyncio.wait_for` 包住。
- **`pytest.ini` 已經帶了 `-q`**，命令列再加一個變成 `-qq`，摘要行會消失（看起來像沒跑完）。
