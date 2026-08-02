# 待確認與補充資訊 (Missing Information & Settings)

根據您提供的專案概述，以下是一些尚未明確指定，因此在實作中會先使用 placeholder 或預設行為處理的設定與邏輯。請您確認或補充這些細節：

> **本檔的「現況」段落是後續實作回填的**，記錄每一項最後實際做成什麼。
> 原始問題保留不動，方便對照當初的假設與最終決定。
>
> **「現況」只寫結論並指向權威來源，不複述內容**——2026-07-31 對帳時發現 §3 / §5 / §6
> 三段複述的細節全都落後於程式碼好幾輪，而且是往「比實際更糟」的方向錯（會讓讀者以為
> 系統沒有完整性驗證）。決策的權威來源是 `ROADMAP.md` 的「已拍板的長期決策」，
> 架構與缺陷的權威來源是 `BLUEPRINT.md`。
>
> **最後與程式碼對帳：2026-08-01（`881d0d5`）。**
>
> **入口文件現在是 `README.md`**，先讀那份再決定要來這裡看什麼。

## 1. SFTP 認證機制 (Authentication)
目前專案概述中提到「處理基本的使用者認證 (Username/Password 或 SSH Key)」，但未具體說明：
- 憑證應該儲存在哪裡？(寫死在環境變數 `.env`？存在 MongoDB？還是外部檔案？)
- 預設先以單一使用者（透過 `.env` 注入 `SFTP_USER` 與 `SFTP_PASSWORD`）作為 placeholder。

**現況**：維持單一使用者、`.env` 注入。比對改為 `hmac.compare_digest`（常數時間，不洩漏
時序）。`SFTP_USER` / `SFTP_PASSWORD` 已無預設值，未設定會在啟動時直接失敗——先前的
`testuser` / `testpass` 是公開已知值，等同無認證。

**密碼的職責後來變大了**：它同時是登入憑證與包裝主金鑰的密碼來源，所以有 12 bytes 的長度下限
（一般登入密碼不需要這種東西）。細節見 §3 與 `ROADMAP.md` 的拍板決策。

多使用者仍未實作。**設計方案已產出於 `design-multi-user.md`（2026-07-31），但尚未拍板要不要做**
——該文件 §6 有三個必須先選的決策點。`ROADMAP.md` 該條仍是 `[later]`。

## 2. 檔案刪除邏輯 (File Deletion)
當使用者透過 SFTP 刪除一個檔案時：
- 我們只需要在 MongoDB 移除 Metadata 嗎？還是需要同時呼叫 Discord API 刪除對應的訊息 (Messages) 以釋放空間？
- 預設行為：先實作從 MongoDB 刪除 Metadata，並發送非同步請求嘗試刪除 Discord 上的訊息。

**現況**：照此實作，且涵蓋三條會產生孤兒附件的路徑——刪除、truncate 覆寫、`posix_rename`
覆寫掉既有目標。三者都有測試盯著「操作後 Discord 端不得殘留附件」
（`tests/test_sftp_e2e.py`、`tests/test_rename.py`）。

## 3. AES-256-CBC 初始向量 (IV) 管理
AES-CBC 加密需要一個隨機的 Initial Vector (IV)。
- IV 應該如何儲存？(常見做法是附加在檔案的第一個 chunk 前面，或者存在 MongoDB 的 Metadata 裡面)。
- 預設行為：將 IV 儲存在 MongoDB 該檔案的 Metadata 屬性中，確保每次加解密都能取得正確的 IV。

**現況：此段的前提已作廢。** 演算法改為 **AES-256-CTR**，不再是 CBC，因此沒有「單一
file-level IV」這回事。改為**每個 chunk 各自帶一個 16 bytes nonce**，存在該 chunk 的
metadata 裡。

換掉的理由是 SFTP 本身：協定是 offset-based，客戶端會要求檔案中任意一段。CBC 必須從頭
依序解到該位置，per-chunk nonce 則讓每個 chunk 能獨立解密，隨機讀取才成立。附帶的好處是
CTR 密文長度等於明文長度，不需要 padding，metadata 記的 `size` 就是使用者寫入的真實大小。

**完整性已經解決**（2026-07-29 起實作，本段原本寫「尚未解決」，那是過時的）。CTR 本身確實
只有機密性，所以另外疊了兩層 HMAC-SHA256：`chunk_tag` 蓋單一 chunk 的位元組**與它的位置**，
`node_tag` 蓋整個檔案的形狀。**tag 存在 MongoDB 而不是跟著密文放在 Discord 上**，
這正是選 HMAC 而不是 AES-GCM 的主要理由——控制 Discord 那一側的人產不出合法 tag。
採 encrypt-then-MAC，驗證發生在解密**之前**。

- 決策與理由，以及**實際涵蓋範圍與明確沒有涵蓋的部分**：`ROADMAP.md`「已拍板的長期決策」。
  （`BLUEPRINT.md` §4.3 / §4.4 也寫了同一件事，但它落後於 H2，重新產出前不要拿它當準。）

**身分與位置也已經涵蓋（2026-08-01）**：`node_tag` 現在還蓋 `filename` 與 `parent_id`，
目錄有自己的身分 tag，目錄還有一個蓋住子項集合的 tag（在列目錄時驗）。所以改名、搬檔、
交換兩個檔案的名字、改目錄名、從資料庫刪掉一個節點，全部會被抓到。
每個節點帶 `tag_version`，舊格式一律拒絕。

- **仍沒有涵蓋的**：權限位與時間戳（刻意，見 `ROADMAP.md`），以及把節點與其父目錄
  一起還原成舊版本——那是已拍板接受的整檔 rollback。
- 方案與實作的出入：`design-node-identity-integrity.md` 開頭的已實作橫幅。

## 4. Discord 上傳附件檔名
在將 Chunk 上傳至 Discord 時，附件需要一個檔名。
- 為了避免名稱衝突或洩漏原始檔名，是否使用隨機 UUID 或特定的命名規則（如 `chunk_0.bin`）？
- 預設行為：使用 `{file_id}_chunk_{index}.bin` 的格式上傳。

**現況**：照此實作。`file_id` 是內部 UUID，不含原始檔名，所以不會從附件名洩漏。

## 5. 重試與斷線處理 (Error Handling)
如果在傳輸過程中的其中一個 Chunk 上傳/下載失敗（且已達到最大重試次數）：
- 是否需要實作 Rollback（刪除已上傳的 Chunks）？
- 預設行為：記錄 Error Log 並回傳失敗給 SFTP 客戶端，但不主動清空不完整的 Chunk (可能成為孤兒資料)。

**現況（本段三句原文全部過時，2026-07-31 重寫）**：

- **Rollback 已實作**（`DiscordFile._rollback()`，`src/vfs.py`）：上傳或 metadata 寫入失敗時
  會把 handle 標記為失敗、拒絕後續寫入，並且**只有本 handle 新建的檔案會被整個回收**
  （連同它的附件與節點）。既有檔案一律不刪任何東西，維持在最後一次成功 commit 的狀態
  ——不論它是被 append、被隨機寫入還是被 truncate 過。
  **2026-08-01 修正**：此處原本會無條件刪光整個檔案的 chunk，對既有檔案做附加寫入時
  等於一次 Discord 故障就吃掉整份已經安全落地的資料。決策與理由見 `ROADMAP.md`
  的拍板決策，覆蓋範圍見 `tests/test_write_failures.py`。
- **重試不只涵蓋 429**：500 / 502 / 503 / 504 與傳輸層例外（斷線、逾時）都會重試，
  指數退避加抖動，最多 5 次；4xx 仍然不重試（那是呼叫端的問題，重試只是浪費預算）。
  每次 attempt 重建 request body，因為 `aiohttp.FormData` 是一次性的。
  見 `src/discord_api.py`、`tests/test_discord_retry.py`、`tests/test_discord_robustness.py`。
- **主動節流已實作**：`src/ratelimit.py` 讀 `X-RateLimit-Bucket` / `-Remaining` / `-Reset-After`
  在被告知之前就先等，另有並發上限。已用真實 bot token 實地觸發驗證過（`ROADMAP.md` 第四段）。

孤兒附件的處理原則沒有變，而且是刻意的：**一律先寫 metadata 才刪舊附件**。
反過來會把一次失敗的更新變成真的資料遺失，這樣做最壞只是留下孤兒。

## 6. Host Key (伺服器金鑰)
SFTP Server 啟動需要一個 Host Key (RSA/Ed25519 等)。
- 啟動時動態生成，還是從環境變數 / 掛載的 volume 讀取？
- 預設行為：在啟動時自動產生一個暫時的 RSA key，或是讀取環境變數指定的路徑。

**現況**：`SFTP_HOST_KEY_PATH`（預設 `host_key`）指定路徑；檔案不存在才產生，存在就沿用，
所以重啟不會換金鑰。

**volume 已經掛上了**（本段原文寫「docker-compose 沒有替它掛 volume」，那是過時的）：
`docker-compose.yml` 有 `host_key_data:/app/keys`，容器重建不會換金鑰，客戶端也就不會跳
mismatch——那個警告與真實中間人攻擊產生的警告長得一模一樣，所以不能讓它變成日常噪音。

順帶兩件後來補的：金鑰權限強制 `0600`（`asyncssh` 是透過 umask 寫檔的，在容器裡留下了
world-readable 的金鑰），既有金鑰也會被自動修復；以及 volume 的 ownership 交給 Docker
在建立時從 image 帶入，所以不需要 runtime chown。**唯一的例外是舊的 root-running build
留下的 volume**，`ensure_host_key()`（`src/main.py`）會偵測並在錯誤訊息裡說明一次性遷移做法。
