# 待確認與補充資訊 (Missing Information & Settings)

根據您提供的專案概述，以下是一些尚未明確指定，因此在實作中會先使用 placeholder 或預設行為處理的設定與邏輯。請您確認或補充這些細節：

> **本檔的「現況」段落是後續實作回填的**，記錄每一項最後實際做成什麼。
> 原始問題保留不動，方便對照當初的假設與最終決定。

## 1. SFTP 認證機制 (Authentication)
目前專案概述中提到「處理基本的使用者認證 (Username/Password 或 SSH Key)」，但未具體說明：
- 憑證應該儲存在哪裡？(寫死在環境變數 `.env`？存在 MongoDB？還是外部檔案？)
- 預設先以單一使用者（透過 `.env` 注入 `SFTP_USER` 與 `SFTP_PASSWORD`）作為 placeholder。

**現況**：維持單一使用者、`.env` 注入。比對改為 `hmac.compare_digest`（常數時間，不洩漏
時序）。`SFTP_USER` / `SFTP_PASSWORD` 已無預設值，未設定會在啟動時直接失敗——先前的
`testuser` / `testpass` 是公開已知值，等同無認證。多使用者仍未實作（`ROADMAP.md` `[later]`）。

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

尚未解決的是**完整性**：CTR 與 CBC 一樣沒有認證，密文被竄改不會被偵測
（`ROADMAP.md` `[next]`）。

## 4. Discord 上傳附件檔名
在將 Chunk 上傳至 Discord 時，附件需要一個檔名。
- 為了避免名稱衝突或洩漏原始檔名，是否使用隨機 UUID 或特定的命名規則（如 `chunk_0.bin`）？
- 預設行為：使用 `{file_id}_chunk_{index}.bin` 的格式上傳。

**現況**：照此實作。`file_id` 是內部 UUID，不含原始檔名，所以不會從附件名洩漏。

## 5. 重試與斷線處理 (Error Handling)
如果在傳輸過程中的其中一個 Chunk 上傳/下載失敗（且已達到最大重試次數）：
- 是否需要實作 Rollback（刪除已上傳的 Chunks）？
- 預設行為：記錄 Error Log 並回傳失敗給 SFTP 客戶端，但不主動清空不完整的 Chunk (可能成為孤兒資料)。

**現況**：Rollback 仍未實作，維持預設行為。重試本身是 429 專用，最多 5 次，每次重建
request body（`tests/test_discord_retry.py`）。尚未讀 `X-RateLimit-*` header 做主動節流
（`ROADMAP.md` `[next]`）。

## 6. Host Key (伺服器金鑰)
SFTP Server 啟動需要一個 Host Key (RSA/Ed25519 等)。
- 啟動時動態生成，還是從環境變數 / 掛載的 volume 讀取？
- 預設行為：在啟動時自動產生一個暫時的 RSA key，或是讀取環境變數指定的路徑。

**現況**：`SFTP_HOST_KEY_PATH`（預設 `host_key`）指定路徑；檔案不存在才產生，存在就沿用，
所以重啟不會換金鑰。但 docker-compose 沒有替它掛 volume，容器重建後客戶端仍會跳 host key
mismatch（`ROADMAP.md` `[next]`）。
