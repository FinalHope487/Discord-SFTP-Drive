# Session Handoff

依 `.claude/templates/session-handoff.md` 產出。
涵蓋範圍：**`setstat` / `fsetstat` 的 size 變更**（ROADMAP 的 `[next]`，上一輪交接指定的下一步）。

> 上一輪交接說「沒有 `[now]`，下一步做 setstat」。本輪把它做完了，並在過程中
> 修掉一個**上一輪就已經存在、但沒有測試踩到**的資料遺失缺陷（`fstat` 少報 buffer）。
> **目前仍然沒有 `[now]`。**

---

## 目前狀態

模組：`src/vfs.py`、`src/sftp.py`、`tests/test_truncate.py`（新增）、
`tests/test_random_write.py`（改一行註解）。未動：`src/crypto.py`、`src/db.py`、
`src/config.py`、`src/discord_api.py`、`src/ratelimit.py`、`src/main.py`、`Dockerfile`。

**服務是活的**：`docker compose up -d --build` 以新程式碼重建並啟動，非 root 執行，
Discord 可達性檢查通過（`Hu Tao#9753`），SFTP listen 2222。
上一輪留在你 Discord DM 的三個測試檔（9 個 chunk）**未動**；本輪的驗收檔案已全部清掉。

### 一、實地驗收結果（**15/15 通過**）

真實 bot token、DM 模式、位元組真的送上 Discord。收尾對帳：
**Discord 9 個附件、MongoDB 引用 9 個、0 孤兒**；server log 無 ERROR。

| 驗收項 | 結果 |
|---|---|
| **預先設定大小再上傳 20MB** vs 一般上傳 | **9.7s vs 10.0s，比值 0.97**；chunk 佈局完全相同、無補零殘留 |
| 預設大小上傳的 SHA256 | 相符 |
| 擴張到 500MB | **上傳 0 則訊息、耗時 0.01s**；`stat` 回報 500MB |
| 洞讀回零（跨真實重新連線） | offset 0 / 400MB / 最後 10 bytes 全部正確 |
| 截短到 chunk 中間（18886713 bytes） | SHA256 等於原檔前綴；跨邊界 chunk 換了新 nonce |
| 被取代的附件 | 在 Discord 上真的 **404** |
| 先截短再擴張 | 舊位元組沒有復活（前綴 ＋ 零） |
| 全部測試檔以 SFTP `remove` 清除 | 0 孤兒 |

**這裡最重要的是第一列。** 稀疏尾端的整個論證是效能論證，而效能論證對
`FakeDiscord` 是**結構上測不到的**——假物件不會有真實的上傳延遲。比值 0.97
是這個設計唯一能被真實 Discord 證實的方式。

順帶把三個 ROADMAP `[next]` 從「推測」變成「量到的事實」，詳見 `ROADMAP.md`：
附件 URL 簽章有效 **24 小時**、跨 handle staleness 會讓舊 handle 讀到**已刪除
附件的舊資料**、把 chunk 換成洞**不會**被完整性檢查抓到。

### 二、驗證方式與結果（自動化測試）

```bash
./venv/Scripts/python.exe -m pytest
./venv/Scripts/python.exe -m pyflakes src tests
```

**197 項全數通過**（208 秒），pyflakes 乾淨。上輪 159 項 → 本輪 197 項。

| 檔案 | 涵蓋 | 項數 |
|---|---|---|
| `tests/test_truncate.py`（新增） | 截短（6 個邊界位置參數化）、丟棄與孤兒、跨邊界 chunk 換 nonce/換 tag、擴張讀回零、洞的四種寫入位置、O_APPEND 越過洞、handle 快取失效與索引重用、拒絕情境、`fstat` 回報 buffer | 38 |
| 既有十支 | 未改動（`test_random_write.py` 只改一行註解） | 159 |

**九項突變驗證**（把程式故意改壞，確認測試真的會抓到，不是碰巧全綠）：

| 突變 | 結果 |
|---|---|
| `_covered_end()` 退化成回傳 `size`（等於沒有洞的概念） | 抓到 |
| 跨邊界 chunk 沿用舊 nonce | 抓到 |
| 截短時不刪 Discord 訊息 | 抓到 |
| `truncate_to()` 不清 chunk 快取 | 抓到 |
| `O_APPEND` 落在資料尾端而非檔案尾端 | 抓到 |
| `truncate_to()` 不 flush buffer | 抓到 |
| 讀到洞就停止而不補零 | 抓到 |
| `fstat` 忽略 buffer | 抓到 |
| 擴張時實際把零上傳到 Discord | 抓到 |

最後兩項是這輪最值得做的：兩者的 round-trip 都會過。`fstat` 那個會讓客戶端拿到
**空資料而不是錯誤**；補零那個功能完全正確，只是流量放大數百倍。

---

## 已完成

### 一、截短（shrink）

新增 `_resize_node(node, size)`（`src/vfs.py`，模組層級，因為路徑版與 handle 版都要用）：

- 完全落在新長度之後的 chunk → 丟掉，並釋放 Discord 訊息。
- 新結尾**落在中間**的那一塊 chunk → 下載、解密、切短、**換新 nonce** 重新加密、
  重算 HMAC、上傳新附件。附件是不可變的，「改短」只能是「換一個」。
- 順序刻意與 `_replace_chunk` 一致：**先上傳新的 → 再寫 metadata → 最後刪舊的**。
  中間掛掉最多留一個孤兒；反過來做，metadata 寫失敗就是真的資料遺失。
  metadata 只寫一次（`size` ＋ `chunks` 一起），不會出現寫到一半的中間狀態。
- `O_TRUNC` 原本的 `_truncate()` 併進來變成 `_resize_node(node, 0)`。順帶把它原本
  「先刪訊息再更新」的順序修成安全的那一邊。

### 二、擴張（grow）＝ 稀疏尾端

**這是本輪唯一一個問過你的決策，也是我建議推翻 ROADMAP 舊結論的地方。**

ROADMAP 原本寫「擴張其實已經可以支援（補零即可）」。實際不成立：真的補零之後，
整個檔案底下都有 chunk，於是客戶端接下來每一筆寫入都落在檔案中間、走
`_write_random()`，而 SFTP 的寫入是一個封包約 32KB —— **9MB 的 chunk 會被
重新上傳約 288 次，單一 chunk 產生 2.6GB 流量**。功能會是「正確但不能用」，
而且必然打爆上一輪才做的 rate limit。

改採稀疏尾端後：`size` 可大於 chunk 長度總和，中間那段是洞、讀回零、不佔空間。
**洞只會在尾端**——中間的洞沒有表示法，所以寫入落在 chunk 之後仍然實際補零。

配套改動（`src/vfs.py`）：

- `_append_position()` 從「`size` ＋ buffer」改成「**chunk 尾端** ＋ buffer」。
  這一行是效能的關鍵：預先設定大小之後，客戶端從 offset 0 開始寫仍然等於 append，
  照樣走原本的緩衝路徑，成本與沒設定大小時**完全相同**。
- `_end_of_file()` 新增，`max(size, chunk 尾端 + buffer)`。`O_APPEND` 用這個，
  因為 POSIX 的 append 是到**檔案**尾端，有洞的時候那不等於資料尾端。
- `read_at()` 讀到洞回零而不是停止。
- `_upload_chunk()` 的 offset 改用 chunk 尾端；`size` 改成 `max(舊值, 新結尾)`，
  因為從左邊填洞不該讓檔案變長。

### 三、`fstat` 少報 buffer（本輪順帶發現，屬於上一輪的缺陷）

寫測試時發現：VFS 層測試會過，同一件事走 asyncssh 客戶端就回空。原因是
**asyncssh 的 `read()` 不帶長度時，會先 `fstat` 問大小再決定要讀多少**
（`sftp.py:3070` 附近的 `size = (await self._end()) - offset`）。
我們的 `fstat` 回報 `node["size"]`，不含還在 buffer、尚未上傳的位元組，
於是客戶端算出「要讀 0 bytes」，**剛寫完的資料被當成 EOF，無聲消失**。

修法：`DiscordFile.size` 回報 handle 眼中的長度，`sftp.fstat()` 用它而不是 node 的值。
路徑版的 `stat` / `lstat` / `scandir` 維持回報已提交狀態（那些位元組還沒上傳，
本來就不該對別的連線可見）。

這個缺陷上一輪做隨機寫入時就已經存在，只是當時沒有測試會不帶長度讀取。

### 四、SFTP 層接線（`src/sftp.py`）

`_reject_resize()` 移除。`setstat` 帶 size 走 `vfs.truncate(path, size)`，
不帶 size 維持原本的「安靜接受」（客戶端上傳完常會 chmod，不能因此失敗，
但路徑不存在仍要失敗，所以查詢照做）。`fsetstat` 走 `file_obj.truncate_to(size)`
而不是路徑版，這樣 handle 還在 buffer 的位元組會被算進去、它的解密快取也會失效。

負數 size 回 `FX_FAILURE` 而不是 `FX_OP_UNSUPPORTED`：改完之後這台伺服器**確實會
resize**，回報「不支援」會讓客戶端關掉一個其實能用的功能。那是格式錯誤，不是缺功能。

### 五、實地驗收（結果見最上面的表）

一併把三個 `[next]` 從「推測」變成「量到的事實」：

- **附件 URL 過期**：真實 URL 的 `ex=` 參數顯示簽章**有效 24 小時**。
  單一 chunk 上限 9MB，下載途中過期基本上不可能，而 `_chunk_bytes` 每次讀都重新取
  URL，所以本來就在安全的那一邊。這條的優先度可以降低。
- **跨 handle staleness**：連線 B 把 20MB 檔案截到 4096 之後，連線 A 的 handle 仍回報
  20971520 bytes，**而且在新檔尾之後讀得到 1024 bytes 的舊資料**——它手上的 chunk
  metadata 還指著已被刪掉的附件，讀的是自己的解密快取。比原本記的更嚴重一點。
- **洞沒有 HMAC**：把一個 9MB 檔案的第二個 chunk 從 MongoDB 拿掉、`size` 不動 →
  讀回全零、`stat` 不變、**沒有任何 integrity error**。文件上寫的缺口是真的。

驗收過程中我自己的腳本寫壞過一次（多包一層 JSON，把 `chunks` 寫成字串），
在 server log 留下一筆 `Unhandled error in SFTP open: string indices must be
integers`（04:11）。**那是腳本的錯，不是伺服器的**；第二次跑完全無錯。
當下也確認了沒有因此產生孤兒。

### 六、文件回填

- `ROADMAP.md`：移除完成的 setstat 項；新增「稀疏尾端」到已拍板的長期決策
  （附上為什麼推翻舊結論、以及哪一個測試釘住它）；測試變慢那條更新數字；
  新增跨 handle 狀態不同步、路徑版 stat 看不到別人的 buffer 兩項；
  在 HMAC rollback、跨 handle、URL 過期三條各補上本輪實地量到的結果。
- `SOP.md`：**刻意沒有新增**。`fstat` 那個問題很值得寫成「VFS 層過、走客戶端卻失敗
  → 先查客戶端是否先問 stat」，但 SOP 自己的觸發條件是「同一類問題第二次出現才寫」，
  本輪是第一次。下輪若再遇到類似的「客戶端依賴 server 回報的屬性」問題，直接補上。

---

## 未完成待辦

**沒有 `[now]`。**

### 一、仍然沒被真實環境驗證過的東西

- **上傳「次數」只對假物件斷言過**。實地驗收量的是**耗時**（0.97 比值）與最終
  chunk 佈局，因為被取代的訊息會被刪掉，事後從 Discord 數不出中間發生過幾次上傳。
  耗時比值足以推翻「補零」實作（那會是數十倍），但精確次數仍只有單元測試涵蓋。
  要真的數，得在 `discord_api.upload_chunk` 加一個計數器或 log。
- **rate limit bucket 仍未被真實 429 驗證過**（上一輪就記著）。本輪 20MB / 3 則訊息
  的量級一樣碰不到。
- **5xx 重試**未實作也未驗證。

### 二、已知的坑

- **稀疏的洞沒有 HMAC 保護**。洞完全由 metadata 定義，能改 MongoDB 的人可以把一個
  chunk 換成洞，讓那段資料無聲地變成零。與既有的 rollback/replay 缺口是**同一個威脅
  模型**（防的是「能改 Discord 的人」，不是「能改 MongoDB 的人」），修法也相同
  ——檔案層級的 MAC。已記進 `ROADMAP.md`，不是新的一條。
- **跨 handle 不同步**（本輪實地確認，比原本以為的嚴重）。`setstat` 走路徑；同一檔案
  若正被另一個 handle 開著，那個 handle 不只回報舊大小，還會**在新檔尾之後讀出舊資料**
  ——它的 chunk metadata 指著已刪除的附件，讀的是自己的解密快取。
  `remove` / `rename` 早就有同樣問題。
- **`truncate_to()` 直接清掉整個 chunk 快取**，沒有只清受影響的那幾筆。
  truncate 不是熱路徑，換取「一定不會讀到舊 chunk」很划算，但如果之後有人
  在迴圈裡 truncate，這裡會變成重複下載。
- **測試 208 秒 / 197 項**，又更慢了。
- **每次客戶端斷線都會留一筆 `WARNING SSH connection error: Connection lost`**。
  asyncssh 把「TCP 直接斷掉、沒有走 SSH disconnect」視為例外，而多數客戶端就是這樣
  收尾的。不是錯誤，但會讓 log 裡的真 WARNING 更難找。上一輪就有，未處理。
- 上一輪的坑大多仍然成立：rate limit bucket 沒被真實 429 驗證過、
  MongoDB 密碼只在 volume 初始化那次生效、驗證要整塊下載。
  （附件 URL 過期這條本輪量到視窗是 24 小時，優先度已降低。）

---

## 本輪不可碰的範圍

- **加密演算法**——未改。`src/crypto.py` 一行未動。截短時重新加密走的是既有的
  `transform()` / `chunk_tag()`，沒有新的密碼學決定。
- **`src/db.py`**——一行未改。
- **認證模型**——未改。
- **`src/config.py` / `src/discord_api.py` / `src/ratelimit.py` / `src/main.py` /
  `Dockerfile` / `docker-compose.yml` / `.env.example`**——未改，本輪沒有新設定值。
- **`todo.md`**——未改，兩項都還是 `[later]`。
- **`SOP.md`**——刻意未改，理由見上。
- **`mongo_data` volume 與 Discord 上的既有測試附件**——未動。
- **ROADMAP 的其他 `[next]`**（5xx 重試、測試加速）——你本輪選了只做 setstat resize，
  這兩項的**程式碼**未動；本輪只是把其中幾條的實地觀測結果補進 ROADMAP。

---

## 下一步建議任務

**跨 handle 狀態不同步。**

順序理由：本輪把它從「理論上的不一致」量成了「連線 A 會讀到已刪除附件的舊資料」。
這是清單裡唯一一條**會回傳錯誤資料**的問題——其餘都是「還沒做」或「做了但不夠快」。
而且 `setstat` 讓它比以前好踩：以前要兩個客戶端同時 `remove`／`rename` 才會遇上，
現在只要一邊改大小就會。

不過先確認你在意：**單一客戶端循序操作永遠不會遇到**，如果這台伺服器就是單人用，
它的實際優先度可能低於 5xx 重試。這是產品判斷不是技術判斷，我沒有替你決定。

之後：rate limit 的真實驗證 → 5xx 重試。
測試加速（module-scoped server）建議與其他項目分開一輪做，它會動到所有測試檔。

---

## 環境備忘

- venv 在 `venv/`。跑測試一律用 `./venv/Scripts/python.exe -m pytest`，
  裸 `python` 會打到 Microsoft Store 的 stub 並 exit 49（見 `SOP.md`）。
- 這個 repo **現在是 git repo 了**（上一輪的備忘已過期）。目前在 `master` 分支，
  **沒有設定 remote**，所以只有本機 commit，沒有推送。
- 服務目前是**起著的**（本輪 `--build` 重建過）。程式碼是烤進 image 的，
  **改完 `src/` 一定要 `docker compose up -d --build`**，只 restart 跑的還是舊的。
- 要停服務：`docker compose down`（**不要加 `-v`**，那會連 `mongo_data` 一起刪掉，
  你 Discord 上那三個測試檔案的 metadata 就沒了）。
