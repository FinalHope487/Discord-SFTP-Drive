# Session Handoff

依 `.claude/templates/session-handoff.md` 產出。**最後更新 2026-08-02。**

> **`ROADMAP.md` 的 `[now]` 是 Client UI，第 1～3 步已落地，下一步是第 4 步（HTTP API 層）。**
> 396 項自動化測試全過（+25），突變測試 10/10，pyflakes 乾淨。
> **但這一段完全沒有碰過線上環境**——Docker Desktop 沒在跑，image 內的 suite 與實地驗收都還沒做。

---

## 目前狀態

**程式碼層面：多使用者的結構三步已完成，行為與上一版完全相同。**
仍是一個使用者、仍由 `SFTP_USER` / `SFTP_PASSWORD` 決定，只是憑證從模組常數變成 `users` 的一列。

**部署層面：什麼都沒動。** 線上跑的還是上一版的 image、上一版的資料形狀。
`git` 上有兩個未 push 的 commit（`2981dce` 文件、以及本輪的實作 commit）。

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

### 驗證方式（三層做完，第四、五層沒做）

```bash
./venv/Scripts/python.exe -m pytest        # 396 passed, ~30s
```

1. **396 項單元/整合測試**，`tests/test_users.py` 24 項是本輪新增的。
2. **突變測試 10/10**：把十個保護逐一拿掉，每一個都有測試會失敗——包含「模型 A（所有帳號
   共用一把 key）」與「路徑解析從共用 root 出發」。腳本在 scratchpad，未留在 repo。
3. **pyflakes 乾淨。**
4. **production image 內跑同一份 suite——沒做**，Docker Desktop 沒在跑。
5. **實地驗收——沒做**，見下。

---

## 未完成待辦

1. **image 內的 suite 沒跑過。** 開 Docker Desktop 後 `docker compose build`，再照
   `README.md` 的指令在 image 內跑一次。**只 build 不 up**，理由見下一項。
2. **實地驗收沒做，而且它不是唯讀的。** 新程式碼一啟動就會對線上 MongoDB 做兩件寫入：
   建 `users` 那一列、把 keystore 的 `"master"` 改名成 `user:<uuid>`。
   **這是 `CLAUDE.md` 的高風險項，要先問過才能跑。**
   - 改名只動 `id` 一個欄位，密文原樣，所以風險很低——但它仍然是**沒有反向 migration** 的。
   - 驗收要確認的最小集合：舊密碼仍登得進去、既有 root 仍列得出來、上傳/下載/刪除仍正常、
     重啟第二次時 `adopt_legacy_record` 是 no-op。
3. **Client UI 第 4 步（HTTP API 層）**，動工前要先拍板 session 怎麼持有 master key。
4. **登入現在跑兩次 Argon2**（約 250ms）。照方案實作，不是疏忽；要合併成一次是偏離已拍板方案。
5. **仍未被真實環境驗證的舊項目**：5xx 與傳輸層重試、附件 URL 真的過期、`_rollback` 的保護。

---

## 本輪不可碰的範圍

- **線上 MongoDB 與 Discord**：一個 byte 都沒動過。沒有重啟服務，沒有跑任何遷移。
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

**先驗證，再往前。** 這一段是純結構搬移、行為理應不變，而「理應不變」正是最值得
拿真實環境對一次的東西——上一輪的實地驗收就抓到過兩個單元測試結構上抓不到的 bug。

1. 開 Docker Desktop，`docker compose build`，在 image 內跑 396 項。
2. **問過之後**再重啟服務，做上面第 2 項列的實地驗收。
3. 兩者都過了，再開 Client UI 第 4 步，並在動工前把 session 持有金鑰的方案拿出來拍板。

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
