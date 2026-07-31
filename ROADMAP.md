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
- **檔案擴張採「稀疏尾端」，不實際補零**（2026-07-31 決定）。
  `size` 可以大於所有 chunk 的長度總和，中間那段是洞、讀回零、不佔 Discord 空間。
  **洞只會在尾端**；寫入落在 chunk 之後仍然實際補零（中間的洞沒有表示法）。
  理由是效能而不是省空間：真的補零會讓「先設定大小再上傳」的客戶端每個 SFTP 封包
  都落在檔案中間、各重傳一整塊 chunk，9MB 的 chunk 會被重傳數百次。
  這條由 `tests/test_truncate.py::test_presetting_the_size_does_not_change_the_upload_count`
  釘住，改回補零會直接讓它失敗。

---

## 項目

<!-- 格式：- [標記] 說明（可附上下文/來源 session） -->

- [next] **HMAC 尚未涵蓋 chunk 在檔案中的位置**。tag 蓋的是 `nonce||ciphertext`，
  所以單一 chunk 被換掉會被抓到，但「把整份 metadata 換成另一組自洽的舊版本」
  （replay / rollback）不會。要擋這個需要把 file id + index 也納入 tag，或做檔案層級的 MAC。
  威脅模型不同：前者防的是「能改 Discord 的人」，後者防的是「能改 MongoDB 的人」。
  **2026-07-31 補充**：稀疏尾端讓這個缺口多一種形狀——洞完全由 metadata 定義、沒有 tag，
  所以能改 MongoDB 的人可以把 chunk 換成洞，讓一段資料無聲地變成零。同一個威脅模型，
  同一個修法（檔案層級的 MAC），不是新的一條。
  **已實地確認（2026-07-31）**：把一個 9MB 檔案的第二個 chunk 從 MongoDB 的 `chunks`
  拿掉、`size` 不動 → 讀回全零、`stat` 大小不變、**沒有任何 integrity error**。
  不再是理論推導。
- [next] **測試又更慢了**（208 秒 / 197 項；上上輪 78 秒 / 77 項，上輪 149 秒 / 159 項）。
  每個 test 都重開一次 asyncssh server。若要進 CI，把 server 改成 module-scoped 是最大的一筆。
- [next] **跨 handle 的狀態不同步**。`setstat` 改大小走的是路徑，若同一個檔案正被另一個
  handle 開著，那個 handle 手上的 node 是舊的。`remove` / `rename` 早就有同樣問題，
  只是 `setstat` 讓它更容易被踩到。單一客戶端循序操作不會遇上。
  **已實地確認（2026-07-31）**：連線 B 把 20MB 檔案截到 4096 之後，連線 A 的 handle
  仍回報 20971520 bytes，並且**在新檔尾之後的 offset 讀得到 1024 bytes 的舊資料**
  （它手上的 chunk metadata 還指著已被刪掉的附件，讀的是自己的解密快取）。
  修法大概是 handle 每次操作前重新讀 node，或加一層 open-handle 註冊表。
- [later] **路徑版 `stat` 看不到別的 handle 還在 buffer 裡的位元組**。本輪修好的是同一個
  handle 的 `fstat`（見下方完成項）；跨 handle 的情況沒修，也修不乾淨——那些位元組還沒
  上傳，本來就不該對別人可見。記著是因為它看起來像 bug。
- [next] **`_request` 的重試對 5xx 不重試**。目前只有 429 會重試，Discord 的 502/503 會直接往上拋。實地驗收沒撞到，但長時間跑大概會遇上。
- [next] **附件 URL 過期未驗證**。`get_attachment_url` 每次重新取，但單一 chunk 下載途中簽章過期的情況仍沒有測過（實地驗收的檔案都在幾秒內下載完）。
  **2026-07-31 量到了視窗**：真實附件 URL 的 `ex=` 參數顯示簽章**有效 24 小時**。
  單一 chunk 上限 9MB，要在下載途中過期基本上不可能；真正的風險是**長時間開著的
  handle**——`_chunk_bytes` 每次都重新取 URL，所以其實已經是安全的那一邊。
  剩下沒驗的只有「取到 URL 之後、下載開始之前剛好跨過 24 小時」這個極窄的窗，
  優先度可以降低。
- [later] 斷點續傳與下載進度紀錄（見 `todo.md`）。
- [later] PBKDF2/Argon2 動態金鑰，改用連線密碼推導（見 `todo.md`）。
- [later] 多使用者與各自獨立的 VFS 樹；目前是單一帳號共用同一棵樹。
- [parked] chunk 壓縮與去重。

---

## 本輪（2026-07-31）完成並移除的項目

- ~~[next] `setstat` / `fsetstat` 的 size 變更仍被拒絕~~ — 已實作。截短為
  `_resize_node()`（丟掉超出範圍的 chunk、跨邊界那塊換新 nonce 重傳縮短版）；
  擴張改採稀疏尾端（見上方拍板決策）。`O_TRUNC` 的舊路徑併入同一個函式。
  新增 `tests/test_truncate.py` 38 項，9 個突變全部被抓到。
- ~~（本輪順帶發現）`fstat` 少報還在 buffer 裡的位元組~~ — 已修。
  asyncssh 的 `read()` 不帶長度時會先 `fstat` 決定要讀多少，所以少報的後果不是
  「數字難看」，而是**客戶端剛寫完的資料直接被回 EOF、無聲消失**。
  這個缺陷在上輪隨機寫入時就已經在了，只是沒有測試踩到它。
- ~~本輪缺實地驗收~~ — 已補（2026-07-31）。真實 bot token、DM 模式、20MB 檔案，
  **15/15 通過**：預先設定大小的上傳耗時 9.7s vs 一般上傳 10.0s（比值 0.97，
  補零實作會是數十倍）、chunk 佈局完全相同、擴張到 500MB 上傳 0 則訊息且耗時 0.01s、
  截短後被取代的附件在 Discord 上真的 404、稀疏檔案重新連線後讀回零。
  收尾對帳：Discord 9 個附件、MongoDB 引用 9 個、**0 孤兒**。

## 上輪（2026-07-29）完成並移除的項目

- ~~[now] 設定值的可達性檢查~~ — 已實作於 `src/discord_api.py` 的 `check_reachability()`，`main.py` 在開 socket 前呼叫。真實憑證與壞 token 兩條路徑都實測過。
- ~~[next] 隨機寫入~~ — 已實作於 `DiscordFile._write_random()`。含補零、跨 chunk、延伸檔尾。
- ~~[next] Discord rate limit bucket~~ — 已實作於新檔 `src/ratelimit.py`，讀 `X-RateLimit-*` 並加上並發上限 `DISCORD_MAX_CONCURRENCY`。
- ~~[next] 容器以 root 執行~~ — `Dockerfile` 改用 uid 10001 的 `appuser`，volume 權限一併處理。
- ~~[next] `docker compose logs` 會印出 SFTP 密碼？~~ — 已實測稽核：密碼、AES 金鑰、bot token、Mongo 密碼皆未出現在 log。只有 SFTP 帳號會出現（asyncssh 的 `Beginning auth for user X`，與 sshd 慣例相同，非機密）。
- ~~[now] 完整性驗證~~ — 已依拍板方案實作 per-chunk HMAC-SHA256（encrypt-then-MAC，蓋 `nonce||ciphertext`，MAC 金鑰以 HKDF 從 `AES_SECRET_KEY` 導出）。既有測試資料已清空重跑。實地驗證：竄改 MongoDB 裡的 tag → 讀取被拒並留下 `Integrity check failed` log。
- ~~（本輪順帶發現）host key 權限是 0644~~ — 已修成 0600 並會自動修復既有金鑰。
