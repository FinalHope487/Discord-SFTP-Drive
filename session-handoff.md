# Session Handoff

跨多輪任務結束前，依此模板產出交接摘要。

> 2026-08-06 · 清掉 `/blueprint` 第三輪開出的兩個 `[now]` 與四個 `[next]`

---

## 目前狀態

**可以跑，而且已經跑起來了。** production stack 用改後的程式碼重建過，乾淨啟動
（MongoDB 連上、Discord 認證通過、索引建成、SFTP 2222 與 Web 8080 都在聽）。

- 657 項測試全過（+79），pyflakes 乾淨。
- image 內 624 過、33 skip。skip 的是 `test_compose_coverage.py`——它比對的是 repo 的
  部署描述檔，而 image 只有 `src/`。
- 另外對**真的 mongod**（scratch database，跑完就 drop）跑過 16 項專門驗證，全過。
- **`ROADMAP.md` 的 `[now]` 歸零，`[next]` 只剩一項，而那一項只能你本人做。**
- **尚未 commit**，工作區是髒的，你沒要求我提交。

## 已完成

兩個 `[now]`：

- **同目錄並行結構性寫入的競態**（`src/vfs.py`）。新增 `_locked_dirs()`，把
  stage→改動→commit 整段圈起來，六個呼叫點全包（`makedir` / `_create_file` / `purge` /
  `rename` / `restore` / `_rollback`）。`_stage_entries` 會檢查呼叫端真的持有鎖，
  沒有就 `RuntimeError`。鎖是模組層級、依 id 排序、對同一 task 可重入。
  測試 `tests/test_concurrent_writes.py`（8 項）。突變測試：把鎖換成 no-op，5 項變紅。
- **被中斷的覆寫毀掉舊內容**（`src/vfs.py`、`src/web.py`、`src/config.py`）。
  `open(truncate=True)` 改為寫游離節點（`parent_id`/`filename` 皆 `None`），
  `close()` 才換名、舊節點進垃圾桶。HTTP 與 SFTP 同一條路。
  配套 `sweep_incoming()` + `INCOMING_MAX_AGE_HOURS`（預設 24 小時），掛在既有 trash sweeper 上。
  測試 `tests/test_overwrite.py`（16 項）、`tests/test_sftp_disconnect.py`（7 項），
  以及 `tests/test_upload_disconnect.py` 新增的真斷線覆寫案例。

四個 `[next]`：

- **`TRASH_*` 在 compose 下無效** → compose 補四行（含 `INCOMING_MAX_AGE_HOURS`），
  並加 `tests/test_compose_coverage.py`：用 `ast` 解析 `config.py` 找出所有讀取的變數名，
  斷言每一個都出現在 `docker-compose.yml` 與 `.env.example`。**這個測試當場抓到兩個真缺口**
  （`INCOMING_MAX_AGE_HOURS` 與早就存在的 `WEB_HOST` 沒被文件化）。
  已在真容器內驗過：改 `TRASH_SWEEP_BATCH=7` 會真的變成 7。
- **覆寫式 `rename` 孤兒化垃圾桶子項** → 改走 `_set_trashed` + `purge()`。
  `tests/test_rename.py` 新增 3 項。突變測試：改回「只刪一列」，正好那一項變紅。
- **孤兒附件沒有盤點工具** → `scripts/find_orphans.py`（唯讀，**沒有刪除開關**）
  加上 `DiscordAPI.iter_messages()`。測試 `tests/test_find_orphans.py`（10 項）。
- **SFTP 側的 unwind 缺口** → `tests/test_sftp_disconnect.py`。
  結論：**SFTP 斷線確實會 commit，而那是對的**——SFTP 不宣告長度、每個 write 都被回覆成功，
  所以「檔案就是被確認過的那麼長」是誠實的，也是續傳測試依賴的前提。HTTP 不同是因為
  `Content-Length` 讓伺服器知道身體短了。差異寫在該檔的 docstring 裡。

其他：

- `tests/fakes.py` 每個資料庫方法都改成先 `await asyncio.sleep(0)`。
  **這是這一輪的前置條件**：在那之前並行 bug 連測都測不出來。
- `tests/conftest.py` 加 autouse fixture 清空鎖 registry（理由見下方坑）。
- `SOP.md` 新增一條、`ROADMAP.md` 新增五條已拍板決策與變更紀錄。

## 未完成待辦

- **`[next]` 用真密碼在 UI 上手動點一輪** — 只剩這一項，**只能你本人做**。
  真密碼是主金鑰的包裝，刻意不讓它進入對話或指令紀錄。
- **未 commit**。要我提交再說。
- **`sweep_incoming` 的實測有一半沒驗到**：真 mongod 那一輪裡被丟棄的寫入只有 9 bytes，
  還在 buffer 裡沒上 Discord，所以 `attachments: 0`——**附件釋放那一半只有假件驗過**。
  要補的話，用一個大於 chunk size 的 payload 重跑一次。
- 新開的 `[later]`：`sweep_incoming` 只在有人登入網頁時才跑（純 SFTP 用法永遠不觸發）；
  垃圾桶掃描因覆寫 churn 而成長變快（**修法要加索引，屬於改 schema，我沒動，要先問你**）。
- `README.md` 的測試數字現在是三重過期（寫 515、上輪 578、現在 657）。屬於既有的
  `[later]` 註解漂移那條，我沒單獨動它。

## 本輪不可碰的範圍

- **schema / 索引**：一個都沒加、沒改、沒刪。垃圾桶掃描那條要加 `nodes.trashed_at` 索引，
  我停下來沒做，因為 CLAUDE.md 把改 schema 列為必須先問。
- **`TAG_VERSION` 與 tag 涵蓋範圍**：仍是 3，一個 migration 都沒有。游離節點的 tag 走的是
  既有的 `_file_mac`（空 parent、空檔名），沒有新增任何受保護欄位。
- **加密演算法、金鑰處理、認證流程**：完全沒動。
- **相依套件**：沒有新增或移除任何一個。
- **production 的既有資料**：真 mongod 的驗證跑在一次性的 scratch database 上，跑完 drop；
  Discord 用假件，沒有往真頻道傳過任何東西。
- **`BLUEPRINT.md`**：沒讀（CLAUDE.md 說除非你明確要求）。

## 下一步建議任務

1. **先你自己在 UI 上點一輪**（那個 `[next]`），順便就是這一輪覆寫改動的實地驗收——
   傳一個大檔案、覆寫它、中途關掉分頁，然後確認舊的還在垃圾桶裡。
   這一步排第一是因為它同時關掉待辦、又驗到唯一沒被自動化涵蓋的路徑。
2. **決定要不要 commit**，以及要不要拆成幾個 commit（六件事彼此獨立，拆得開）。
3. **決定垃圾桶索引那條**（要加 `nodes.trashed_at` 索引嗎）。這是唯一一個我因為
   「改 schema 要先問」而停下來的東西，而覆寫 churn 讓它比上輪更值得做。
4. 想清楚**覆寫保留舊版本 30 天**是不是你要的語意。這是照你選的 (a) 實作的，
   但它意味著反覆覆寫同一個大檔案會累積多份。要縮短的話 `TRASH_RETENTION_DAYS`
   是全域的，分開設定會是新設計。
