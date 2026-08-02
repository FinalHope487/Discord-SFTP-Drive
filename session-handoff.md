# Session Handoff

依 `.claude/templates/session-handoff.md` 產出。**最後更新 2026-08-02。**

> **`ROADMAP.md` 的 `[now]` 是 Client UI，第 1～3 步已落地並上線，下一步是第 4 步（HTTP API 層）。**
> 396 項自動化測試全過（+25），突變 10/10，pyflakes 乾淨，image 內同樣 396 過，
> **實地驗收 20/20，遷移已在線上跑完。**

---

## 目前狀態

**服務跑在新程式碼上，帳號已是資料庫裡的一列，遷移已完成。**
行為與上一版完全相同：仍是一個使用者、仍由 `SFTP_USER` / `SFTP_PASSWORD` 決定。

線上 `nodes` 只剩 root（驗收用的檔案已清乾淨），Discord 上 0 個附件，
`keystore` 一筆記錄在 `user:a00e0bf3-…` 底下，`users` 一列。

`git` 上有兩個 commit（`2981dce` 文件、`ed1e3d6` 實作），**都還沒 push**。

改動內容與決策理由不在這裡複述——**看 `ROADMAP.md` 的「已拍板的長期決策」與「變更紀錄」**。
方案與實作的兩處出入在 `design-multi-user.md` 開頭的橫幅。

---

## 已完成

**四個決策拍板並寫進文件**（`ROADMAP.md`「已拍板的長期決策」）：UI 的 API 與 SFTP server
同 process、多使用者採模型 B、帳號存 `users` collection、分四步過渡。
`SFTP_PASSWORD` 走 docker secret 也拍了，但排在 UI 之後。

**`design-multi-user.md` §5 的第 1～3 步**：

| 檔案 | 改了什麼 |
|---|---|
| `src/users.py`（新） | 帳號、Argon2id 密碼雜湊、`sync_env_user`、`authenticate`、`Session` |
| `src/keystore.py` | 每個函式收 `record_id`；新增 `adopt_legacy_record()` 與孤兒守衛 |
| `src/vfs.py` | `DiscordVFS(key, root_id)`，`root_id` 無預設值 |
| `src/sftp.py` | `validate_password` 走 `users`；`extra_info` 從 `session_key` 換成 `Session` |
| `src/main.py` | 啟動順序：同步帳號 → 認領舊記錄 → `ensure_usable` |
| `src/db.py` | `users` 的 `username` / `id` 唯一索引 |
| `src/config.py` | `password_hash_settings()` |

### 驗證方式（五層全做完）

```bash
./venv/Scripts/python.exe -m pytest        # 396 passed, ~25s
```

1. **396 項單元/整合測試**，`tests/test_users.py` 24 項是本輪新增的。
2. **突變測試 10/10**：把十個保護逐一拿掉，每一個都有測試會失敗——包含「模型 A（所有帳號
   共用一把 key）」與「路徑解析從共用 root 出發」。腳本在 scratchpad，未留在 repo。
3. **pyflakes 乾淨。**
4. **production image 內跑同一份 suite**：396 passed（3.12.13 / Linux）。
5. **實地驗收 20/20**（真 bot token、真 MongoDB、12 MiB 跨兩個 chunk）：遷移前後
   keystore 記錄的 fingerprint 相同（`263b893e0206501d`）、舊密碼仍登得進去、
   密碼錯與帳號不存在都被拒、上傳／讀回／改名／刪除全正常、2/2 附件已釋放。
   **第二次重啟確認兩條遷移都是 no-op。**

---

## 未完成待辦

1. **兩個 commit 都還沒 push。**
2. **Client UI 第 4 步（HTTP API 層）**，動工前要先拍板 session 怎麼持有 master key。
3. **登入現在跑兩次 Argon2**（實測約 250ms）。照方案實作，不是疏忽；要合併成一次是
   偏離已拍板方案，要先問。
4. **`SFTP_PASSWORD` 走 docker secret**：已拍板要做，排在 Client UI 之後。
5. **仍未被真實環境驗證的舊項目**：5xx 與傳輸層重試、附件 URL 真的過期、`_rollback` 的保護。

---

## 資料狀態

- **遷移已跑完，而且是不可逆的**：`keystore` 的 `"master"` 已改名成
  `user:a00e0bf3-cf35-4014-a951-84937c307b26`，`users` 多了一列。沒有反向 migration。
- **master key 沒有變**，fingerprint 遷移前後皆為 `263b893e0206501d`。
  `chunk_tag` 一個都沒改，所以一個 chunk 都不需要重傳。
- **root 節點沒有動**：仍是 `id="root"`、`tag_version=2`。
- **驗收期間建立的檔案與目錄已全部刪除**，Discord 附件一併釋放，對帳 1 個節點、0 孤兒。
- `mongo_data` / `host_key_data` volume 未刪除，host key 沒換。
- **`users` 與 `keystore` 從此必須一起備份、一起還原**。帳號那一列是「這個部署對應哪把
  master key」的唯一連結；只還原一邊會讓伺服器拒絕啟動（這是刻意的，見 `README.md`）。

---

## 本輪不可碰的範圍

- **`crypto.py`**：加密與 tag 的邏輯完全沒動。`chunk_tag` / `node_tag` / `dir_tag` 的涵蓋範圍
  一如既往，所以**一個 chunk 都不需要重傳**。
- **既有 root 節點的 id**：刻意沿用 `"root"`。方案原本寫的是改成 `"root:<user_id>"`，
  沒有照做，因為目錄的 tag 蓋住自己的 id——換 id 等於整棵樹重簽。
- **`owner_id` 欄位**：方案 §3.1 建議加，沒有加。前三步不需要走訪整棵樹，
  而它是一個必須跟 `parent_id` 保持一致的冗餘欄位。
- **SFTP 協定介面**：對客戶端而言完全沒有變化。
- **相依套件**：一個都沒加。`argon2-cffi` 與 `aiohttp` 本來就在。

---

## 下一步建議任務

驗證五層都做完了，結構已經就位，接下來是 Client UI 的第 4 步。

1. **push 那兩個 commit**。加密層與認證層都動過而且沒有反向 migration，
   這是這輪唯一還沒做的收尾。
2. **把「session 怎麼持有 master key」的方案拿出來拍板**，然後才動 HTTP API 層。
   這是 `CLAUDE.md` 明列的高風險項（認證流程 + 金鑰處理）。
3. 第 4 步落地後再做前端。前端是純靜態 SPA，可獨立改版，所以刻意排在最後。

---

## 環境備忘

- venv 在 `venv/`（3.12.7）；上線 image 也是 3.12（3.12.13）。一律用
  `./venv/Scripts/python.exe -m pytest`（見 `SOP.md`；裸 `python` 是 Store 的 stub，exit 49 且無輸出）。
- 程式碼是烤進 image 的，改完 `src/` 一定要 `docker compose up -d --build`。
- **重啟服務目前不必先問**，見 `CLAUDE.md`「臨時例外」。**但這一輪的重啟會順帶跑遷移**，
  那是另一條規則，仍然要先問。
- MongoDB 的埠沒有對宿主開放，一次性腳本要走
  `docker compose exec -T sftp-discord-server python - < 腳本`。
- 在容器裡跑測試要 `MSYS_NO_PATHCONV=1` 前綴、`--user root`、`-p no:cacheprovider`。

### 本輪踩到、按規則還沒進 `SOP.md` 的兩個坑

- **在 Windows 上用 `pathlib.write_text()` 還原檔案，會把整份檔案的 LF 換成 CRLF。**
  突變測試腳本就是這樣「還原」的，跑完之後 `src/` 三個檔案每一行都變了，而 diff 看起來
  完全正常、測試也全過。要用 `read_bytes()` / `write_bytes()`，並在腳本結尾比對 hash。
- **Git Bash 裡 `grep -c $'\r'` 不會展開成 CR**，它會變成空字串，於是 grep 匹配每一行、
  回報的數字剛好等於總行數——看起來像「每一行都是 CRLF」。查換行符用 Python 讀 bytes。
