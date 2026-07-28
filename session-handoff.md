# Session Handoff

依 `.claude/templates/session-handoff.md` 產出。
涵蓋範圍：**真實憑證實地驗收** ＋ ROADMAP 的兩項 `[now]`（可達性檢查、完整性驗證）
與四項 `[next]`。

> 上一輪的交接說「所有進度都卡在沒有真實憑證」。**這個封鎖已經解除。**
> 你把 `.env` 填好了，本輪把驗收跑完，並在驗收通過的基礎上做完了 ROADMAP 的全部項目
> ——包含你本輪拍板方案的完整性驗證。**目前沒有 `[now]` 項目。**

---

## 目前狀態

模組：`src/discord_api.py`、`src/vfs.py`、`src/main.py`、`src/config.py`、
`src/crypto.py`、`src/sftp.py`、`src/ratelimit.py`（新增）、`Dockerfile`、
`tests/`（新增四支）。未動：`src/db.py`。

**服務目前是活的**：`docker compose up -d` 起著，以非 root 執行，Discord 可達性檢查通過，
SFTP listen 2222。你的 Discord DM 裡有本輪留下的測試附件（依你指示保留未刪）。

注意：DM 裡**前後有兩批**附件。前一批（HMAC 實作前上傳的）已在切換 schema 時
用 SFTP 正常刪除、Discord 訊息一併清掉；現在留著的是**帶 HMAC 的第二批**
（`acceptance_small` / `acceptance_large` / `acceptance_randomwrite`，共 9 個 chunk）。

### 一、實地驗收結果（本輪最重要的部分）

全部以真實 bot token（`Hu Tao#9753`）走 **DM 模式**，位元組真的送上 Discord。

| 驗收項 | 結果 |
|---|---|
| 1MB 單 chunk：mkdir→上傳→下載→SHA256 比對→5 次隨機 offset 讀→rename | **通過**，上傳 287 KB/s、下載 985 KB/s |
| 35MB 多 chunk（4 則訊息）：同上 | **通過**，上傳 1964 KB/s、下載 4354 KB/s，SHA256 相符 |
| chunk 切分正確性 | 9437184×3 + 8388608 = 36700160，與檔案大小一致 |
| **密文確認** | 上傳含已知明文標記的檔案，直接從 Discord CDN 抓原始附件，**標記不存在** |
| **刪除無孤兒** | SFTP 刪檔後該 Discord 訊息回 404 |
| 20MB 隨機寫入（5 個位置含兩處 chunk 邊界）＋延伸檔尾 | **通過**，全檔 SHA256 相符 |

**上一輪推測的四個風險，實測結果：**

1. 附件 URL 過期 — 未撞到（檔案都在數秒內下載完）。**仍未驗證**，已記進 ROADMAP。
2. 真實 rate limit 密度 — **35MB / 4 則訊息完全沒有觸發 429**。原本擔心的「重試預算被燒光」
   在這個量級沒有發生。但也因此，新寫的 rate limit bucket **只有單元測試覆蓋，
   沒有被真實 429 驗證過**。
3. 附件大小上限 — `MAX_CHUNK_SIZE = 9MB` 目前仍可用，未被 Discord 拒絕。
4. MongoDB 併發 — 未主動測試併發，本輪都是單一客戶端循序操作。

### 二、驗證方式與結果（自動化測試）

```bash
./venv/Scripts/python.exe -m pytest
./venv/Scripts/python.exe -m pyflakes src tests
```

**159 項全數通過**（149 秒），pyflakes 乾淨。上輪 77 項 → 本輪 159 項。

| 檔案 | 涵蓋 | 項數 |
|---|---|---|
| `tests/test_reachability.py`（新增） | token 401、DM 403/400、頻道不可見、四種權限缺漏、Administrator 短路、role/member overwrite 疊加順序、傳輸失敗不阻擋啟動 | 20 |
| `tests/test_random_write.py`（新增） | chunk 內/跨邊界/跨多 chunk 覆寫、補零、延伸檔尾、O_APPEND、**nonce 必換**、舊附件必刪、offset 連續性 | 25 |
| `tests/test_ratelimit.py`（新增） | route key 收斂、耗盡後等待、視窗過期不等、global vs route scope、並發上限、**不靠 429 就自我節流** | 16 |
| `tests/test_integrity.py`（新增） | 竄改密文/截斷/換 nonce/換金鑰/抽掉 tag/對調兩個 chunk 全部被拒；重寫後 tag 會更新且仍可驗證 | 21 |
| 既有六支（config/crypto/e2e/put_get/rename/retry） | 未改動 | 77 |

**三個關鍵測試做過突變驗證**（故意把程式改壞，確認測試真的會抓到）：

- 把 `_replace_chunk` 改成重用舊 nonce → 2 項 nonce 測試失敗。
- 把 rate limiter 的 `_delay()` 改成永遠回 0 → 5 項測試失敗。
- 把 `_chunk_bytes` 裡的 `verify_chunk()` 拿掉 → 7 項竄改測試失敗。

這件事值得做，因為這三個東西壞掉的時候**round-trip 測試照樣會過**——
資料能正確解回來，但保護已經沒了。只驗 round-trip 的測試對這類缺陷完全無感。

---

## 已完成

### 一、設定值可達性檢查（ROADMAP 的 `[now]`）

`src/discord_api.py` 新增 `check_reachability()`，`src/main.py` 在 `check_discord_reachable()`
裡於**開 socket 之前**呼叫。

- `GET /users/@me` 驗 token；401 就直接回報並**不再往下檢查**（auth 掛了之後的錯誤都是衍生的，
  一次報一串只會蓋掉真正要修的那一個）。
- DM 模式：`POST /users/@me/channels`。403 的訊息直接寫明「bot 必須跟你有共同伺服器」，
  400 寫明「要數字 ID 不是使用者名稱」——這兩個是最常見的卡點。
- 頻道模式：算出 bot 在該頻道的**有效權限**（@everyone → 各 role → 頻道 overwrite，
  role 的 deny/allow 先各自累加再套用，否則一個 role 的 allow 會被另一個 role 的 deny 誤殺），
  檢查 View Channel / Send Messages / Attach Files / **Read Message History**。
  最後一項容易漏：少了它可以上傳但讀不回來，等於只寫不讀的儲存。
- **只檢查實際在用的那一個**。`.env` 裡留了沒在用的另一個 ID 不該害啟動失敗。

**失敗處理刻意分兩類**（這是設計決定，不是實作偷懶）：

- 設定問題（token 被撤、沒權限）→ **exit 1**。這種伺服器會接受 SFTP 登入然後每次上傳都失敗，
  對客戶端而言看起來像資料遺失。
- 連不上 Discord（網路/DNS）→ **警告後照常啟動**。容器開機時 Discord 剛好連不上就燒掉
  `on-failure:5` 的重試額度、然後在網路恢復後很久仍然是停的，這比暫時性錯誤更糟。

實測：真實憑證通過並印出 bot 身分；`DISCORD_BOT_TOKEN=totally.invalid.token` → exit 1 並印出可讀訊息。

### 二、隨機寫入（`[next]`）

`DiscordFile` 的寫入路徑改寫。原本非循序 offset 一律回 `FX_OP_UNSUPPORTED`。

- 落在檔尾 → 走原本的緩衝／整塊上傳路徑（一般上傳都走這條，效能不變）。
- 落在其他位置 → `_write_random()`：把涵蓋到的 chunk 下載、解密、拼接、**換新 nonce** 重新加密、
  重新上傳，再刪掉舊訊息。
- 寫超過檔尾會**實際補零**（POSIX 說 hole 讀回來是 0，這裡沒有 sparse 表示法）。
- `open()` 不再拒絕「不帶 O_TRUNC 開啟既有檔案」。

**換新 nonce 是這件事的安全前提，不是可選的最佳實務。** AES-CTR 是把明文跟
(key, nonce) 決定的 keystream 做 XOR；同一組 nonce 加密兩份不同明文，任何人拿到兩份密文
XOR 起來就能還原兩份明文。所以測試裡直接斷言 nonce 有換，而不是只驗 round-trip。

順序也是刻意的：**先上傳新的 → 再更新 metadata → 最後刪舊的**。中間掛掉最多留下一個孤兒附件；
反過來做，metadata 寫失敗就是真的資料遺失。

### 三、Discord rate limit bucket（`[next]`）

新檔 `src/ratelimit.py`。原本只被動處理 429。

- 讀 `X-RateLimit-Bucket` / `-Remaining` / `-Reset-After`，在額度用完時**主動等待**。
- 用 `Reset-After`（相對秒數）而不是 `Reset`（絕對時戳），避免本機與 Discord 的時鐘偏差
  變成永久卡住或空轉。
- global 限流才暫停全部路由；route scope 的只擋自己那個 bucket。
- 新增並發上限 `DISCORD_MAX_CONCURRENCY`（預設 4）。

意義在於**把重試額度留給真正的錯誤**——原本每撞一次可預期的 429 就吃掉 5 次重試的其中一次。

### 四、容器非 root 執行（`[next]`）＋ 一個順帶發現的問題

`Dockerfile` 改用 uid 10001 的 `appuser`。volume 權限靠「Docker 建立新的 named volume 時
會沿用 image 裡該路徑的擁有者」解決，不需要 runtime chown 或 root entrypoint。

**過程中發現 host key 的權限是 `0644`（全世界可讀）。** 私鑰 0644 等於任何能讀到那個 volume
的人都可以冒充這台伺服器，而客戶端分辨不出來。asyncssh 的 `write_private_key` 是照 umask 寫的。
已修：`ensure_host_key()` 寫完後強制 `0600`，並且**會修復既有的 0644 金鑰**。
實測 log 出現 `Tightened host key permissions from 644 to 600`。

另外補了一個可讀的錯誤：金鑰不存在且目錄不可寫時，直接說明是舊 root volume 的問題該怎麼辦，
而不是丟一個 asyncssh 深處的 `PermissionError`。

### 五、log 洩密稽核（`[next]`）

不是用讀程式碼的方式做的——讀 log 呼叫點只能證明「我們想到要看的地方」沒問題。
實際驅動了失敗登入（用一個獨特的 canary 密碼）、成功登入、上傳、刪除、錯誤路徑，
再拿真實的祕密值去 grep 183KB 的容器 log：

- SFTP 密碼、AES 金鑰、bot token、Mongo 密碼 → **都沒有出現**。
- 失敗登入用的 canary 密碼 → **沒有出現**。
- **SFTP 帳號會出現**（asyncssh 的 `Beginning auth for user X`）。與 sshd 慣例相同，非機密，判定為可接受。

### 六、完整性驗證（第二個 `[now]`，本輪你拍板後實作）

方案是你選的：**metadata 存 per-chunk HMAC**，不採 AES-GCM；**不做向後相容**，既有資料清掉重跑。

`src/crypto.py` 新增 `chunk_tag()` / `verify_chunk()`：

- **Encrypt-then-MAC**。tag 蓋在密文上，偽造的 chunk 在進到 cipher 之前就被擋掉。
- **tag 存在 MongoDB，不在 Discord**。這是選 HMAC 而不是 GCM 的主因：
  GCM 的 tag 會跟密文一起放在 Discord 上，能改密文的人就能一起改 tag；
  HMAC 放在 metadata，**能改 Discord 的人算不出對應的 tag**。
- **nonce 也被蓋進 tag**。只蓋密文的話，把 metadata 裡的 nonce 換掉就能改變解密結果而 tag 照樣通過。
- **MAC 金鑰用 HKDF 導出**，不跟 AES 金鑰共用。加密層本身（AES-256-CTR）一行未改。
- **缺 tag 一律拒絕**（fail closed）。允許「沒有 tag 就跳過驗證」等於留一條把 tag 刪掉就能關掉驗證的降級路徑。

`src/sftp.py` 把 `IntegrityError` 獨立處理，記成安全事件而不是「未處理的例外＋traceback」。

**實地驗證（不是只有單元測試）**：把 MongoDB 裡真實檔案的 tag 改壞一個字元 →
讀取被拒（`SFTPFailure: chunk failed integrity verification`），server log 出現
`Integrity check failed in SFTP read`；改回去 → 三個檔案全部正常讀回。

### 七、文件回填

- `ROADMAP.md`：完成的七項移除並列在「本輪完成」段；新增 5 項本輪發現的項目；
  新增「已拍板的長期決策」段落，把你這輪對加密方案的兩個決定寫死在裡面，
  避免下一輪又被拿出來重問。
- `SOP.md`：更新 python 直譯器那條（venv 現在常駐於 `venv/`，不必每輪重建）；
  新增一條 Git Bash 路徑改寫（`docker compose exec` 帶絕對路徑會被 MSYS2 改成
  `C:/Program Files/Git/...`）。
- `.env.example`：新增 `DISCORD_MAX_CONCURRENCY` 並說明「調高不會讓上傳變快」。

---

## 未完成待辦

**沒有 `[now]`。** 下面全部是 `[next]` 以下，可以直接接手。

### 可以直接接手的

見 `ROADMAP.md`，摘要：`setstat` 的 size 變更（擴張已經可支援，缺 truncate-to-size）、
HMAC 未涵蓋 rollback/replay、測試變慢（149 秒）、5xx 不重試、附件 URL 過期仍未驗證。

### 已知的坑

- **rate limit bucket 沒被真實 429 驗證過**。實地驗收的量級（4 則訊息）碰不到限流。
  單元測試涵蓋了邏輯，但真實 header 的欄位值與邊界行為沒有對照過。
- **`host_key_data` volume 本輪被重建過**，host key 換了。你如果先前用任何客戶端連過
  並存了 known_hosts，會看到 host key mismatch——這次是預期的。
- **MongoDB 密碼只在 volume 初始化那一次生效**（未變）。
- **HMAC 擋不住 rollback / replay**。tag 蓋的是 `nonce||ciphertext`，涵蓋不到「這個 chunk
  屬於哪個檔案的第幾塊」。換掉單一 chunk 會被抓到，但把整份 metadata 換成另一組
  自洽的舊版本不會。威脅模型不同——現在防的是「能改 Discord 的人」，不是「能改 MongoDB 的人」。
  已記進 `ROADMAP.md`。
- **驗證要整塊下載**。這是選 HMAC 時就知道的代價：讀 1 byte 仍需下載並驗證整個 chunk
  （最大 9MB）。實測隨機讀取仍在可接受範圍（每次 seek+read 約 0.5–1 秒）。
- **測試 149 秒 / 159 項**，比上輪的 78 秒 / 77 項更慢。
- 容器內 `/app/keys` 對 `appuser` 唯讀的情況（舊 volume）現在會給出可讀錯誤，但**不會自動修復**。

---

## 本輪不可碰的範圍

- **加密演算法本身**——未改。AES-256-CTR、per-chunk nonce 維持原樣；本輪是在它**外面**
  加一層 HMAC 認證，沒有動 `transform()` 的邏輯，也沒有改 AES 金鑰的取得方式。
- **認證模型**——未改，仍是單一使用者帳密比對。
- **`src/db.py`**——一行未改。
- **`src/sftp.py`**——只加了 `IntegrityError` 的分支，其餘未動。隨機寫入完全在 VFS 層做，
  SFTP 層原本就把 offset 原封不動傳下來。
- **`todo.md`**——未改，兩項都還是 `[later]`。
- **`mongo_data` volume**——**未刪除**。清資料是用 SFTP 逐檔 `remove()`，這樣 Discord 訊息
  會被一併釋放；直接砍 volume 會把那些附件留成永遠找不回來的孤兒。

---

## 下一步建議任務

**`setstat` / `fsetstat` 的 size 變更。**

順序理由：這是清單裡唯一還會讓某些 SFTP 客戶端**整個上傳失敗**的相容性問題
（有些客戶端會在寫入前先 setstat 設定大小，目前直接收到 `FX_OP_UNSUPPORTED`）。
其餘項目都是「已經能用、可以更好」，這個是「某些客戶端根本不能用」。

而且它現在比上輪便宜很多：隨機寫入做完之後，「擴張」已經等同於補零（`_write_random`
本來就會做），只剩「截短」需要一個新的 truncate-to-size VFS 操作。

之後再依序處理 rate limit 的真實驗證（見上面「已知的坑」第一項）、5xx 重試、附件 URL 過期。

---

## 環境備忘

- venv 在 `venv/`（本輪建立，已在 `.gitignore`）。跑測試一律用 `./venv/Scripts/python.exe -m pytest`，
  裸 `python` 會打到 Microsoft Store 的 stub 並 exit 49（見 `SOP.md`）。
- 這個 repo **仍然不是 git repo**。`.gitignore` 上輪就備好了，`git init` 之後第一次 commit
  不會誤帶 `.env`、host key 或 `venv/`。
- 服務現在是起著的。要停：`docker compose down`（**不要加 `-v`**，那會連 `mongo_data`
  一起刪掉，你 Discord 上那三個測試檔案的 metadata 就沒了）。
