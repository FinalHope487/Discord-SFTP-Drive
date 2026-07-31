# Session Handoff

依 `.claude/templates/session-handoff.md` 產出。
涵蓋範圍：**setstat/fsetstat resize**（前半）＋ **把 UI 外的功能收尾**（後半）。
跨 handle 狀態同步依你的指示延後。

> **目前沒有 `[now]`。** 自動化測試 304 項全過，15 個突變全部被抓到，
> **實地驗收 18/18 通過**，Discord 上 0 孤兒。

---

## 目前狀態

本輪動到：`src/crypto.py`、`src/keystore.py`（新增）、`src/vfs.py`、`src/sftp.py`、
`src/main.py`、`src/config.py`、`src/db.py`、`src/discord_api.py`、
`.env.example`、`docker-compose.yml`、`tests/`（新增四支、改五支）。

**服務是活的**，跑的是本輪的程式碼（`docker compose up -d --build`），
keystore 已用你的新密碼初始化（600k 次 PBKDF2）。
**Discord 上目前一個附件都沒有**——舊的 9 個已依你的授權清除，驗收檔案也已清乾淨。

### 一、實地驗收結果（**18/18 通過**）

真實 bot token、真 MongoDB、真 SFTP，位元組真的送上 Discord。

| 驗收項 | 結果 |
|---|---|
| 包裝金鑰以正式參數存在（pbkdf2-sha256 / 600k） | 通過 |
| 錯誤密碼被真實記錄拒絕 | 通過 |
| 20MB 往返 SHA256 相符 | 通過，上傳 1.9 MB/s、下載 4.6 MB/s |
| 每個 chunk 有 tag、檔案有 node tag、offset 連續 | 通過（3 chunks） |
| **把 chunk 換成洞 → 被拒絕** | 通過（上一輪這裡是「讀回全零、無錯誤」） |
| 還原 metadata 後檔案又可讀 | 通過 |
| 只改 `size` → 連 `stat` 都被拒 | 通過 |
| 權限位與 mtime 跨重新連線保存 | 通過（mode 600、mtime 1400000000） |
| **MongoDB 真的擋下同名節點** | 通過 |
| 換密碼後主金鑰不變、舊密碼失效 | 通過（跑在 scratch DB，不碰真的 keystore） |
| **SIGTERM 真的把 buffer 寫進 Discord** | 通過，65536/65536 bytes |
| 預設大小上傳成本不變（上一輪的功能） | 通過，10.6s vs 10.7s（比值 1.01） |
| 截短後前綴正確、舊附件真的 404 | 通過（2/2 回 404） |
| 擴張到 500MB 上傳 0 則、讀回零 | 通過 |
| 收尾對帳 | **0 孤兒** |

**驗收找到兩個單元測試結構上抓不到的 bug**，兩個都是「假替身不可能重現」的那類：

1. **唯一索引無法升級，服務直接起不來**。MongoDB 不會就地把非唯一索引改成唯一，
   會回 `IndexKeySpecsConflict`。假的 collection 不會有這種行為，所以 302 項全綠，
   **但真的升級部署時服務拒絕啟動**。已加自動遷移路徑（含資料有重複時的可讀錯誤）。
2. **SIGTERM 沒有真的 flush**。原本靠 asyncssh 在 session 結束時對 handle 呼叫
   `close()`，但**那個 cleanup 不在 `conn.wait_closed()` 的等待範圍內**，行程在上傳
   還在飛的時候就結束了。log 完全正常（「Shutdown complete」照印），
   只有真的去讀那個檔案才發現 65536 bytes 全沒了。
   **原本的單元測試是靠時序巧合過的**——斷言寫在後面，剛好讓 cleanup 有機會跑完。
   已改成追蹤開著的 handle、在關連線前明確 flush，並把斷言移到 `_drain()` 回傳的當下。

### 二、驗證方式與結果（自動化測試）

```bash
./venv/Scripts/python.exe -m pytest
./venv/Scripts/python.exe -m pyflakes src tests
```

**304 項全數通過**，pyflakes 乾淨。上輪 197 項 / 208 秒。

| 檔案 | 涵蓋 | 項數 |
|---|---|---|
| `test_keystore.py`（新增） | 包裝/解開、錯誤密碼、竄改記錄、換密碼保留金鑰、`SFTP_PASSWORD_OLD` 重新包裝流程 | 25 |
| `test_discord_robustness.py`（新增） | 5xx 重試、4xx 不重試、連線中斷重試、上傳大小核對、URL 過期與快取、CDN 不帶 token | 22 |
| `test_metadata.py`（新增） | 權限位、mtime/atime、`put -p` 流程、rename 不改 mtime、唯一索引 | 25 |
| `test_session.py`（新增） | 登入、每連線金鑰、斷線 flush、**斷點續傳**、關機排空 | 13 |
| `test_integrity.py`（擴充） | 新增 node tag 的竄改情境：刪尾端 chunk、chunk 換洞、重排、改 size、跨檔搬運 | +18 |
| 既有各支 | 隨對應改動更新 | 199 |

**十五項突變驗證全部被抓到**（把程式故意改壞，確認測試真的會失敗）：

file-level tag 不驗 / 不重算、chunk tag 不綁位置、node tag 不含 chunk 列表、
解包不驗密碼、5xx 不重試、CDN 帶 token、過期 URL 照用、不核對上傳大小、
close 蓋掉 mtime、忽略釘住的 mtime、rename 重設 mtime、不存權限位、
登入自己造金鑰、關機不關連線。

**突變驗證本身找到兩個真的 bug**（測試沒抓到、只有突變會暴露）：

1. `_drain()` 的 `conn.wait_closed()` 與 `server.wait_closed()` **都沒有上限**。
   一條關不掉的連線會讓行程永遠不結束——正好是優雅關機要避免的「被 SIGKILL 砍掉」。
   已改成全部有上限。
2. 有兩個測試在失敗時會 **hang 而不是 fail**。CI 上那會變成整包逾時而不是一行錯誤。
   已加 `asyncio.wait_for` 護欄。

---

## 已完成

### 一、測試從 208 秒降到 23 秒——但根因不在原本以為的地方

ROADMAP 原本記的是「每個 test 都重開 asyncssh server，改成 module-scoped」。
**先 profile 才發現那完全打錯地方**：真正的成本是 asyncssh 為了算 GSSAPI 預設主機名
呼叫 `socket.getfqdn()`，這台機器的反向 DNS 每次 1.04 秒，每個測試兩次
（listen 一次、connect 一次）。208 秒裡有將近 190 秒是這個。

兩端都傳 `gss_host=None` 之後 302 項只要 23 秒，**而且不需要動 fixture 結構**。

順帶的效果是關掉 GSSAPI 認證路徑。這裡不誇大：本機與容器都回報
`gss_available: False`，所以它**目前是惰性的**，不是一個活著的繞過。
但那取決於哪些選用套件剛好有裝，而這台伺服器只打算做密碼認證，明確關掉比較好。

### 二、Discord 層的韌性（`src/discord_api.py`）

- **5xx 與傳輸層例外會重試**，指數退避加抖動；4xx 仍然一次就放棄（重試不會成功，
  只會燒掉留給暫時性故障的額度）。`Exception("Max retries exceeded")` 換成具名的
  `DiscordAPIError`（帶 status），並設定明確的逾時。
- **附件 URL 依 `ex=` 快取**，過期前重新解析；真被 CDN 以 403/404 拒絕時再解析一次重試。
  快取讓每讀一個 chunk 少一次 API 呼叫。
- **下載改用不帶 bot token 的獨立 session**。原本共用的 session 會把 token 送到 CDN 主機，
  那是另一個 origin、而且 URL 本來就已簽章，送過去沒有任何好處。
- **上傳後核對 Discord 回報的大小**，不符就刪掉那則訊息再報錯——而不是留給下次讀取
  時的 HMAC 去發現一個開不起來的檔案。

### 三、完整性：chunk 位置綁定 ＋ 檔案層級 MAC（`src/crypto.py`）

- `chunk_tag` 現在綁 **(file id, index, offset, size)**：同一組位元組不再「在哪裡都合法」。
- 新增 `node_tag`，蓋 **(file id, size, 有序的 chunk tag 列表)**。
  這關掉的是 per-chunk tag **結構上做不到**的那類攻擊——chunk 被整個刪掉時，
  根本沒有 tag 可以驗。上一輪實地確認過的「把 chunk 換成洞讓資料無聲變成零」就是這種。
- 所有 MAC 輸入都**加長度前綴並做 domain separation**（`b"chunk"` / `b"node"`），
  純串接會讓兩種不同結構產生同一個位元組串。
- 標籤版本 `v1 → v2`，改變涵蓋範圍會讓所有既有 tag 失效——刻意讓破壞是大聲的。

**剩下的缺口只有一個**：把整份 node 換成同一檔案的舊版本。要擋它需要一個放在
「持有資料庫的人碰不到的地方」的單調計數器，這個架構裡沒有。已記進 ROADMAP。

### 四、金鑰包裝與每連線金鑰（`src/keystore.py` 新增）

`.env` 不再有 `AES_SECRET_KEY`。資料用一把**隨機**主金鑰加密；主金鑰用 SFTP 密碼
經 PBKDF2-HMAC-SHA256（600k）推導出的 KEK 包裝後存在 MongoDB。

- **為什麼包裝而不是直接推導**（偏離 `todo.md` 原文，理由已寫進 ROADMAP 拍板段）：
  直接推導的話**改密碼＝所有資料永久讀不出來**，而且「密碼錯」與「資料壞了」
  在現象上完全一樣。包裝之後換密碼只是重寫 32 bytes；包裝上的 MAC 讓錯誤密碼
  在解開當下就被判定為密碼錯誤。
- **換密碼流程**：`SFTP_PASSWORD_OLD` 設成舊的、`SFTP_PASSWORD` 設成新的、重啟。
  不會重新加密或重傳任何 chunk。沒設 `SFTP_PASSWORD_OLD` 而密碼對不上 → **啟動失敗**，
  而不是啟動成功然後每次讀取都壞掉。
- **金鑰是每連線的**：`validate_password` 解開後放在該連線上，`DiscordVFS` 每條連線一個。
  連線結束釋放參照。**這不是安全抹除**——Python 無法抹除 bytes 物件的內容。
- KDF 選 PBKDF2 而不是 Argon2id 的唯一理由是**不想為此新增相依套件**
  （CLAUDE.md 規定新增相依要先問，而你那一題選的是「做，且加金鑰包裝」，沒指定 KDF）。
  包裝格式存了 `kdf` 名稱與參數，之後要換不需要 migration。

### 五、POSIX metadata（權限位、時間戳）

原本 `ls -l` 永遠顯示 0644、mtime 永遠是上傳時間。現在都會保存。
過程中修掉兩個既有錯誤，**兩個都是測試寫出來才發現的**：

1. **`close()` 會蓋掉客戶端剛設定的 mtime**。`put -p` 的實際流程是「寫入 → 設定時間 →
   關閉」，而不滿一個 chunk 的資料要到關閉才 flush，於是那次 flush 把時間蓋掉了。
   改成：handle 上被明確指定過的 mtime 會「釘住」，最後的 flush 沿用它；
   指定之後又有新的寫入才解除釘住（那是真的新修改）。
2. **rename 會重設 mtime**。移動檔案不是修改檔案。改成只更新兩端目錄的 mtime。

另外 `(parent_id, filename)` 改成 unique index。

### 六、優雅關機

SIGTERM 原本會直接砍掉行程，不滿一個 chunk 的 buffer 全丟。現在會停止接受新連線、
給既有 session 20 秒完成、再關閉它們（關閉會觸發 asyncssh 的 cleanup，也就是 flush）。
所有等待都有上限（見上面突變驗證找到的 bug）。`docker-compose.yml` 的
`stop_grace_period` 設成 30s，比伺服器自己的 20 秒長。

### 七、`todo.md` 兩項都結案

第二項「斷點下載進度紀錄」**不實作，因為前提不成立**：SFTP 讀取是無狀態 offset 讀，
上傳的 chunk 一上傳就進 MongoDB，斷線時 asyncssh 會 flush buffer——**檔案大小就是
續傳點**，另記一份只會多出一份可能不一致的資料。已用測試釘住而不是加程式碼。

---

## 未完成待辦

### 一、仍未被真實環境驗證的東西

- **rate limit bucket 仍未被真實 429 驗證過**（連續三輪記著）。本輪最大的一次也
  只有 20MB / 3 則訊息，這個量級碰不到限流。要驗只能刻意灌一批小檔案。
- **5xx 重試與傳輸層重試沒有在真實環境被觸發過**。Discord 本輪一次都沒出錯。
  單元測試用真的本機 HTTP server 涵蓋了邏輯，但真實 Discord 的 5xx 長什麼樣沒對照過。
- **附件 URL 真的過期（24 小時後）沒有等過**。過期路徑由 stub 涵蓋。

### 二、已知的坑

- **整檔 rollback 仍擋不住**（見 ROADMAP）。這是目前唯一已知的完整性缺口。
- **跨 handle 不同步**依你指示延後。已實地確認舊 handle 會讀到已刪除附件的舊資料。
- **列目錄不做完整性驗證**（刻意）。`ls -l` 的大小未經驗證，但 stat/open 會擋。
- **權限位與時間戳不受 MAC 保護**（刻意，見 ROADMAP 拍板段）。
- **「連線中斷即銷毀」是盡力而為**，不是安全抹除。
- **PBKDF2 每次登入約 200ms**（600k 次）。測試以 1000 次跑，靠的是每份包裝記錄
  自帶參數——不是測試偷改了程式行為。
- **rate limit bucket 仍未被真實 429 驗證過**（上兩輪就記著）。

---

## 資料狀態（本輪動過，寫清楚）

- **Discord DM 上目前沒有任何附件。** 上一輪留下的 9 個（3 個測試檔）依你的授權
  刪除了——本輪換了加密方案，它們再也解不開，留著只會變成永遠沒人指向的孤兒。
  本輪驗收自己產生的檔案也已全部經 SFTP `remove` 清掉。
- **`nodes` 集合已清空**（舊格式節點沒有 `mac`，一 stat 就會失敗）。
- **`keystore` 是新的**，用你更新後的 `SFTP_PASSWORD` 包裝。
  **這把密碼現在是唯一能解開所有資料的東西，弄丟了就沒了。**
- `mongo_data` / `host_key_data` volume 未刪除，host key 沒換。

---

## 本輪不可碰的範圍

- **跨 handle 狀態同步**——依你指示延後，一行未改。
- **符號連結**——你選擇不做，維持 `FX_OP_UNSUPPORTED`。
- **AES-256-CTR 本身**——`transform()` 一行未改。改的是它外面的金鑰來源與認證層。
- **相依套件**——`requirements.txt` 未改。沒有為 Argon2id 新增任何東西。
- **`mongo_data` / `host_key_data` volume**——未刪除。

---

## 下一步建議任務

**跨 handle 狀態同步**（你本輪指示延後的那一項）。

順序理由：它是清單裡唯一一條**會回傳錯誤資料**的問題——已實地確認舊 handle 會讀到
已刪除附件的舊資料。其餘 `[next]` 都是「已知的限制」或「還沒被真實環境觸發」。

不過這仍取決於你的使用情境：**單一客戶端循序操作永遠不會遇到**。
如果這台就是單人自用，它的實際優先度可能低於「刻意灌一批小檔案去撞真實 429」——
那是連續三輪都記著、始終沒驗證過的一條。

之後：Argon2id（要新增相依，得先問）、整檔 rollback（需要外部信任錨點，
不是這個架構內部能解的）。兩者都在 ROADMAP。

---

## 環境備忘

- venv 在 `venv/`。一律用 `./venv/Scripts/python.exe -m pytest`（見 `SOP.md`）。
- repo 在 `master`，**沒有 remote**，只有本機 commit。
- **`.env` 的 `SFTP_PASSWORD` 你本輪已更新為 12 bytes**（新的下限，因為它現在
  同時是包裝金鑰的密碼）。`AES_SECRET_KEY` 那一行現在沒有作用，可以刪掉。
- 程式碼是烤進 image 的，改完 `src/` 一定要 `docker compose up -d --build`。
- `tests/generate_secret.py` 是你加的密碼產生器；pytest 不會收集它（檔名不符 `test_*`）。
