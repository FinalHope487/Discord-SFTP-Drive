# Roadmap

新想法寫進來並標記，不當場插隊打斷目前任務。

## 標記說明

- `[now]` — 阻塞當前任務，不做就走不下去
- `[next]` — 目前模組穩定後就做
- `[later]` — 現在做屬於過早設計，等需求明確再說
- `[parked]` — 先記錄，暫不評估要不要做

---

## 已拍板的長期決策

<!-- 這些是問過使用者、往後都適用的決定，不要下一輪又拿出來重問 -->

- **完整性驗證採 per-chunk HMAC，不採 AES-GCM**（2026-07-29 決定）。
  加密層維持 AES-256-CTR 不動；HMAC-SHA256 存在 MongoDB 的 chunk metadata。
  理由：改動面積最小，且**竄改者即使控制 Discord 也改不到 tag**（tag 不在 Discord 上）。
- **不做向後相容**（2026-07-29 決定）。沒有 HMAC 欄位的 chunk 一律拒絕（fail closed），
  不保留「舊檔跳過驗證」的路徑——那條路徑本身就是降級攻擊面。既有測試資料直接清掉重跑。

---

## 項目

<!-- 格式：- [標記] 說明（可附上下文/來源 session） -->

- [next] **HMAC 尚未涵蓋 chunk 在檔案中的位置**。tag 蓋的是 `nonce||ciphertext`，
  所以單一 chunk 被換掉會被抓到，但「把整份 metadata 換成另一組自洽的舊版本」
  （replay / rollback）不會。要擋這個需要把 file id + index 也納入 tag，或做檔案層級的 MAC。
  威脅模型不同：前者防的是「能改 Discord 的人」，後者防的是「能改 MongoDB 的人」。
- [next] **`setstat` / `fsetstat` 的 size 變更仍被拒絕**。隨機寫入完成後，「擴張」其實已經可以支援（補零即可），但「截短」還缺一個 truncate-to-size 的 VFS 操作。目前兩者都回 `FX_OP_UNSUPPORTED`。有些客戶端會在上傳前先 setstat 設定大小。
- [next] **測試跑更慢了**（149 秒 / 159 項，上輪是 78 秒 / 77 項）。每個 test 都重開一次 asyncssh server，新增的 random-write 與 integrity 兩組又各自重寫／重讀多個 chunk。若要進 CI，把 server 改成 module-scoped 是最大的一筆。
- [next] **`_request` 的重試對 5xx 不重試**。目前只有 429 會重試，Discord 的 502/503 會直接往上拋。實地驗收沒撞到，但長時間跑大概會遇上。
- [next] **附件 URL 過期未驗證**。`get_attachment_url` 每次重新取，但單一 chunk 下載途中簽章過期的情況仍沒有測過（實地驗收的檔案都在幾秒內下載完）。
- [later] 斷點續傳與下載進度紀錄（見 `todo.md`）。
- [later] PBKDF2/Argon2 動態金鑰，改用連線密碼推導（見 `todo.md`）。
- [later] 多使用者與各自獨立的 VFS 樹；目前是單一帳號共用同一棵樹。
- [parked] chunk 壓縮與去重。

---

## 本輪（2026-07-29）完成並移除的項目

- ~~[now] 設定值的可達性檢查~~ — 已實作於 `src/discord_api.py` 的 `check_reachability()`，`main.py` 在開 socket 前呼叫。真實憑證與壞 token 兩條路徑都實測過。
- ~~[next] 隨機寫入~~ — 已實作於 `DiscordFile._write_random()`。含補零、跨 chunk、延伸檔尾。
- ~~[next] Discord rate limit bucket~~ — 已實作於新檔 `src/ratelimit.py`，讀 `X-RateLimit-*` 並加上並發上限 `DISCORD_MAX_CONCURRENCY`。
- ~~[next] 容器以 root 執行~~ — `Dockerfile` 改用 uid 10001 的 `appuser`，volume 權限一併處理。
- ~~[next] `docker compose logs` 會印出 SFTP 密碼？~~ — 已實測稽核：密碼、AES 金鑰、bot token、Mongo 密碼皆未出現在 log。只有 SFTP 帳號會出現（asyncssh 的 `Beginning auth for user X`，與 sshd 慣例相同，非機密）。
- ~~[now] 完整性驗證~~ — 已依拍板方案實作 per-chunk HMAC-SHA256（encrypt-then-MAC，蓋 `nonce||ciphertext`，MAC 金鑰以 HKDF 從 `AES_SECRET_KEY` 導出）。既有測試資料已清空重跑。實地驗證：竄改 MongoDB 裡的 tag → 讀取被拒並留下 `Integrity check failed` log。
- ~~（本輪順帶發現）host key 權限是 0644~~ — 已修成 0600 並會自動修復既有金鑰。
