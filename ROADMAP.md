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
- **主金鑰隨機產生、以密碼包裝，不由密碼直接推導**（2026-07-31 決定）。
  `.env` 不再有 `AES_SECRET_KEY`；資料用一把隨機主金鑰加密，主金鑰用 SFTP 密碼
  推導出的 KEK 包裝後存在 MongoDB 的 `keystore`。理由：直接由密碼推導的話，
  **改密碼＝所有資料永久讀不出來**；包裝之後改密碼只是重寫一份 32 bytes 的記錄，
  一個 chunk 都不用動。另外包裝用的 MAC 讓「密碼錯」與「資料壞了」可以區分開來。
  KDF 用 PBKDF2-HMAC-SHA256（600k 次）——**選它是因為不想為此新增相依套件**，
  包裝格式裡存了 `kdf` 名稱與參數，之後換 Argon2id 不需要 migration。
- **金鑰是每連線的，不是每行程的**（2026-07-31 決定）。`validate_password` 解開主金鑰
  後放在該連線上，`DiscordVFS` 每條連線各一個。連線結束即釋放參照
  （Python 無法真的抹除 bytes，這是盡力而為，不是安全抹除）。
- **完整性 tag 的涵蓋範圍：內容，不含 metadata**（2026-07-31 決定）。
  chunk tag 綁 (file id, index, offset, size)；node tag 蓋 (file id, size, 有序 chunk tag 列表)。
  **權限位與時間戳刻意不在裡面**——它們不是內容，把它們納入會讓每次 chmod 都要重算
  一份蓋在完全沒動過的位元組上的 tag。
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

- [next] **整檔 rollback 仍然擋不住**。本輪的 node tag 關掉了重排、跨檔搬運、刪尾端 chunk
  與「把 chunk 換成洞」，但「把整份 node 換成**同一個檔案的舊版本**」仍然自洽、仍然驗得過。
  要擋它需要一個放在持有資料庫的人碰不到的地方的單調版本計數器，這個架構裡沒有那種地方
  （外部 KMS、TPM，或把根雜湊釘在別的服務上）。**這是目前唯一已知的完整性缺口。**
- [next] **跨 handle 的狀態不同步**（你指示延後）。`setstat` 改大小走的是路徑，若同一個
  檔案正被另一個 handle 開著，那個 handle 手上的 node 是舊的。
  **已實地確認（2026-07-31）**：連線 B 把 20MB 檔案截到 4096 之後，連線 A 的 handle
  仍回報 20971520 bytes，並且**在新檔尾之後的 offset 讀得到 1024 bytes 的舊資料**。
  修法大概是 handle 每次操作前重新讀 node，或加一層 open-handle 註冊表。
  單一客戶端循序操作碰不到，所以優先度由「這台是不是單人用」決定。
- [later] **路徑版 `stat` 看不到別的 handle 還在 buffer 裡的位元組**。同一 handle 的
  `fstat` 已修；跨 handle 的沒修，也修不乾淨——那些位元組還沒上傳，本來就不該對別人可見。
- [later] **KDF 換成 Argon2id**。目前是 PBKDF2-HMAC-SHA256 600k 次，選它純粹是為了
  不新增相依套件。Argon2id 對 GPU 破解強得多。包裝格式已經有 `kdf` 欄位，換過去
  只要加一個分支＋一個相依，既有記錄照樣能開。
- [later] **完整性檢查不涵蓋列目錄**。`stat` / `open` / `rename` / `remove` 都會驗，
  `scandir` 不驗——刻意的，否則一個被竄改的檔案會讓整個目錄列不出來。代價是
  `ls -l` 顯示的大小未經驗證，但真的去讀或 stat 它就會失敗。
- [later] **權限位與時間戳不受完整性保護**（見上方拍板決策）。能改 MongoDB 的人可以改它們。
- [later] **不支援符號連結**（你本輪選擇不做）。`symlink` / `readlink` / `link` 回 FX_OP_UNSUPPORTED。
- [later] 多使用者與各自獨立的 VFS 樹；目前是單一帳號共用同一棵樹。
- [parked] chunk 壓縮與去重。

---

## 本輪後半（2026-07-31）完成並移除的項目

> **實地驗收 18/18 通過**（真實 bot token、真 MongoDB、真 SFTP）。
> 收尾對帳：Discord 0 個附件、0 個被引用、**0 孤兒**。
> 驗收過程找到**兩個單元測試結構上抓不到的 bug**，見下方兩條。

- ~~（實地驗收找到）唯一索引無法升級，服務直接起不來~~ — 已修。
  MongoDB **不會就地把既有的非唯一索引改成唯一**，會回 `IndexKeySpecsConflict`。
  單元測試對假的 collection 跑當然不會遇到，**升級既有部署時服務直接拒絕啟動**。
  `src/db.py` 現在會偵測衝突、卸掉舊索引、重建為唯一；若資料本身已有重複，
  會把舊索引放回去並給出一則說明「哪些重複要人工處理」的錯誤，而不是留下沒有索引的集合。
- ~~（實地驗收找到）SIGTERM 沒有真的 flush~~ — 已修。原本靠 asyncssh 在 session 結束時
  對每個 handle 呼叫 `close()`，**但那個 cleanup 不在 `conn.wait_closed()` 的等待範圍內**，
  所以行程在上傳還在飛的時候就結束了，客戶端最後不滿一個 chunk 的資料整個消失。
  log 看起來完全正常（「Shutdown complete」照印），只有真的去讀那個檔案才會發現。
  現在 `src/sftp.py` 會追蹤開著的 handle，`_drain()` 在關連線**之前**明確 flush 它們。
  **原本的單元測試是靠時序巧合過的**（斷言寫在後面，剛好讓 cleanup 有機會跑完），
  已改成在 `_drain()` 回傳的當下就斷言，不留巧合空間。


- ~~[next] 測試又更慢了（208 秒 / 197 項）~~ — **208 秒 → 23 秒**，302 項。
  根因不是 fixture 結構：asyncssh 預設會用 `socket.getfqdn()` 算 GSS 主機名，
  這台機器的反向 DNS 每次要 1.04 秒，而每個測試呼叫兩次（listen 一次、connect 一次）。
  設 `gss_host=None` 同時關掉了本來就不打算提供的 GSSAPI 認證路徑。
  **原本計畫的「把 server 改成 module-scoped」不需要了。**
- ~~[next] `_request` 對 5xx 不重試~~ — 已補 500/502/503/504 與傳輸層例外的重試，
  指數退避＋抖動，具名的 `DiscordAPIError`，明確的逾時設定。4xx 仍然不重試。
- ~~[next] 附件 URL 過期~~ — 依 `ex=` 快取並在過期前重新解析；真的被 CDN 以 403/404
  拒絕時再解析一次重試。順帶：下載改用不帶 bot token 的獨立 session。
- ~~[next] HMAC 未涵蓋 chunk 位置 / 洞沒有 tag~~ — chunk tag 現在綁
  (file id, index, offset, size)，另加 node tag 蓋 (file id, size, 有序 chunk tag 列表)。
  重排、跨檔搬運、刪尾端 chunk、chunk 換洞全部會被抓到。剩下的只有整檔 rollback（見上）。
- ~~[later] PBKDF2/Argon2 動態金鑰（`todo.md`）~~ — 已實作，但**改成金鑰包裝**而不是
  直接推導，理由見上方拍板決策。
- ~~[later] 斷點續傳與下載進度紀錄（`todo.md`）~~ — **不實作，前提不成立**。
  SFTP 讀取是無狀態的 offset 讀，寫入的 chunk 一上傳就寫進 Mongo，斷線時 asyncssh
  的 session cleanup 會呼叫 `close()` 把 buffer flush 掉——所以檔案大小就是續傳點。
  已用測試釘住（`test_an_interrupted_upload_can_be_resumed_by_appending`）而不是加程式碼。
- ~~（新增）POSIX metadata~~ — 權限位與 mtime/atime 現在會保存。順帶修掉兩個既有錯誤：
  `close()` 會蓋掉客戶端剛設定的 mtime（`put -p` 的實際流程），以及 rename 會重設 mtime
  （移動檔案不是修改檔案）。目錄的 mtime 現在會在內容變動時更新。
- ~~（新增）檔名唯一索引~~ — `(parent_id, filename)` 改為 unique。
- ~~（新增）優雅關機~~ — SIGTERM 會停止接受新連線、給既有 session 20 秒完成、
  再關閉它們（這會觸發 handle 的 flush）。所有等待都有上限。
  `docker-compose.yml` 的 `stop_grace_period` 一併設成 30s。
- ~~（新增）log 噪音~~ — 正常斷線不再記成 WARNING。

## 本輪前半（2026-07-31）完成並移除的項目

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
