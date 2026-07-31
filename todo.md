# Discord-Drive TODO 清單

原本規劃的兩項進階功能。**兩項都在 2026-07-31 結案**，但結案方式不同，
所以保留原文並附上實際做法。

---

- [x] **動態加密金鑰 (Key Derivation)** — 已實作，但**改了做法**。
  （2026-07-31 補：KDF 已從 PBKDF2 換成原文也提到的 Argon2id，用 `argon2-cffi`。
  既有的 PBKDF2 記錄不需要 migration，因為記錄自己帶著參數。細節見 `ROADMAP.md`。）
  - **原文**：取消在 `.env` 中寫死 `AES_SECRET_KEY`，改為接收使用者在 SFTP 客戶端
    輸入的密碼，並透過 PBKDF2 或 Argon2 動態生成 AES 金鑰。金鑰僅在連線期間留存於
    記憶體，不寫入資料庫，連線中斷即銷毀。
  - **實際做法**：`AES_SECRET_KEY` 已移除，金鑰確實只在連線期間存在——但**資料金鑰
    不是由密碼推導出來的**。它是一把隨機主金鑰，用密碼推導出的 KEK 包裝後存在
    MongoDB 的 `keystore` 集合裡（`src/keystore.py`）。
  - **為什麼偏離原文**：密碼直接推導金鑰的話，**改密碼就等於所有資料永久讀不出來**，
    而且「密碼打錯」與「資料損毀」在現象上完全一樣。包裝之後，改密碼只是重寫一份
    32 bytes 的記錄（`SFTP_PASSWORD_OLD`），一個 chunk 都不用重傳；包裝上的 MAC
    也讓錯誤的密碼在解開的當下就被判定為密碼錯誤。
  - 「連線中斷即銷毀」是盡力而為：連線結束會釋放參照，但 Python 無法真的抹除
    一個 bytes 物件的內容。這不是安全抹除。

- [x] **斷點下載進度紀錄 (MongoDB)** — **不實作，前提不成立**。
  - **原文**：把下載進度（Chunk Index 或 Offset）記在 MongoDB，作為斷點續傳的基礎，
    讓伺服器重啟後也能得知未完成的狀態。
  - **調查結果**：SFTP 的讀取本來就是**無狀態的 offset 讀**——客戶端 seek 到哪就讀哪，
    伺服器不需要、也不應該記得任何進度。上傳方向則是每個 chunk 一上傳就寫進 MongoDB，
    而 asyncssh 在連線結束時會對每個還開著的 handle 呼叫 `close()`，把不滿一個 chunk
    的 buffer flush 掉。**因此「檔案目前的大小」就是續傳點**，不需要另一份紀錄。
  - 記一份進度在 MongoDB 只會多出一份可能與真實狀態不一致的資料。
  - 已用測試釘住而不是加程式碼：
    `tests/test_session.py::test_an_interrupted_upload_can_be_resumed_by_appending`
    會中途 abort 連線，確認保留的位元組數就是續傳點，再從那裡接續寫完並比對全檔。
