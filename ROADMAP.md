# Roadmap

新想法寫進來並標記，不當場插隊打斷目前任務。

## 標記說明

- `[now]` — 阻塞當前任務，不做就走不下去
- `[next]` — 目前模組穩定後就做
- `[later]` — 現在做屬於過早設計，等需求明確再說
- `[parked]` — 先記錄，暫不評估要不要做

---

## 項目

<!-- 格式：- [標記] 說明（可附上下文/來源 session） -->

- [now] **設定值的可達性檢查**。目前的 fail-fast 只驗「有沒有填、格式對不對」，沒驗「填的東西能不能用」。啟動時應實際打 Discord API 確認：bot token 有效（`GET /users/@me`）、`DISCORD_USER_ID` 能開得成 DM、`DISCORD_CHANNEL_ID` 該頻道 bot 有讀寫與上傳附件權限。現在這些都要等第一次上傳才會炸。等接上真實憑證、確定能跑起來之後再做（在那之前無從驗證這段程式碼本身是對的）。
- [next] **隨機寫入（random-access write）**。目前 `DiscordFile.write_at` 只接受接續上一次的 offset，非循序寫入會回 `FX_OP_UNSUPPORTED`。已驗證 `sftp.put()` 與逐次 `write()` 都是循序，所以主流上傳路徑不受影響；但就地修改檔案不行。
- [next] **完整性驗證**。AES-CTR 與先前的 CBC 一樣沒有認證，密文可被竄改而無法偵測。選項：per-chunk AES-GCM，或在 metadata 存 HMAC。
- [next] **Discord rate limit bucket**。目前只被動處理 429，沒讀 `X-RateLimit-*` header，也沒有上傳並發上限。
- [next] **測試跑太慢**（78 秒 / 77 項）。每個 test 都重開一次 asyncssh server，e2e 那組又各自重寫 300KB。若之後要進 CI，可考慮把 server 改成 module-scoped、payload 改小。現在還在可忍受範圍，先不動。
- [next] **容器以 root 執行**。`Dockerfile` 沒建非特權使用者，host key volume 因此也是 root 持有。修的時候要一起處理 volume 權限，不然容器起不來。
- [next] **`docker compose logs` 會印出 SFTP 密碼？** 未確認。目前確定不會印金鑰，但錯誤路徑沒有全面檢查過。接真實憑證前值得掃一遍 log 呼叫。
- [later] 斷點續傳與下載進度紀錄（見 `todo.md`）。
- [later] PBKDF2/Argon2 動態金鑰，改用連線密碼推導（見 `todo.md`）。
- [later] 多使用者與各自獨立的 VFS 樹；目前是單一帳號共用同一棵樹。
- [parked] chunk 壓縮與去重。
