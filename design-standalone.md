# 獨立單機版 — 設計方案

> **狀態**：2026-08-07 產出。前提（一台裝置一份資料／換 SQLite／仍存 Discord）已於同日拍板，
> 見 `ROADMAP.md`「已拍板的長期決策」，理由不在這裡複述。
>
> **本方案的核心主張**：這件事的難處不在打包，在**換掉 MongoDB 而不動 `vfs.py`**。
> 打包只是把已經會動的東西塞進一個檔案；換資料庫是把 2328 行檔案系統邏輯底下的地板抽掉。
> 所以方案的重心全部放在資料庫那一層，打包排在後面。

---

## 一、為什麼可以不動 `vfs.py`

先數清楚 MongoDB 到底被用在哪裡。整個 `src/` 加 `scripts/` 對資料庫的呼叫是：

| 面向 | 實際用到的 |
|---|---|
| collection | `nodes`、`keystore`、`users`——就三個 |
| 方法 | `find_one`、`find`、`insert_one`、`update_one`、`replace_one`、`delete_one`，加上索引那四個 |
| 查詢運算子 | 等值、`$gt`、`$lte`、`$ne`，以及 `None` 的「缺欄位也算」語意 |
| 更新運算子 | `$set`、`$unset` |

**這已經是一個介面了，只是沒有被命名。** 41 個呼叫點全部長成
`db.get_db().<collection>.<method>(...)`，沒有一處用到 aggregation、transaction、
`$push`、`$inc`、或任何 MongoDB 特有的東西。

所以方案是：**寫一個 SQLite 後端，長得跟 Motor 一模一樣**，然後在 `db.py` 那一層換掉。
`vfs.py`、`keystore.py`、`users.py`、`web.py`、`sftp.py` **一行都不用改**。

這不是取巧，這是風險最小的路：加密、tag 完整性、金鑰處理那些
`CLAUDE.md` 列為「必須先問」的東西，**全部落在不用動的那一邊**。

### 反過來的做法為什麼不選

「順便把 `vfs.py` 改寫成 SQL」聽起來比較乾淨——真的用上關聯式的能力，
`list_dir` 一次 JOIN 就好，不用走訪。**否決理由**是它把「換資料庫」和
「重寫檔案系統」綁成同一件事，而後者要重簽每一個 tag。
一個能被既有 657 項測試驗證的改動，和一個會讓既有測試全部失效的改動，
風險差了一個數量級。真要走關聯式，那是這件事**落地之後**的獨立題目。

---

## 二、SQLite 這一層長什麼樣

### 2.1 文件怎麼存

節點文件是半結構化的：欄位會因為 `$unset` 消失（`trashed_at`、`entries_mac_pending`），
`chunks` 是一個長度不定的陣列。硬拆成關聯式欄位等於把 `vfs.py` 的內部結構
複製一份到 schema 裡，然後每次改欄位都要 migration。

所以：**整份文件存成 JSON，要索引的欄位用 generated column 拉出來。**

```sql
CREATE TABLE nodes (
    _id        TEXT PRIMARY KEY,
    doc        TEXT NOT NULL,
    id         TEXT GENERATED ALWAYS AS (json_extract(doc, '$.id'))        VIRTUAL,
    parent_id  TEXT GENERATED ALWAYS AS (json_extract(doc, '$.parent_id')) VIRTUAL,
    filename   TEXT GENERATED ALWAYS AS (json_extract(doc, '$.filename'))  VIRTUAL,
    is_dir          GENERATED ALWAYS AS (json_extract(doc, '$.is_dir'))    VIRTUAL,
    trashed_at      GENERATED ALWAYS AS (json_extract(doc, '$.trashed_at')) VIRTUAL
);
```

**用 generated column 而不是「寫入時順手也寫一份到欄位」的理由**：後者能寫錯。
一個和 `doc` 不同步的 `parent_id` 會讓節點在查詢裡消失，
而 `doc` 裡的內容看起來完全正常——這是最難查的那一類 bug，因為兩邊各自都對。
Generated column 讓這件事在結構上不可能發生。代價是 `VIRTUAL` 每次讀都要
`json_extract`，但索引本身是實體化的，所以走索引的查詢不付這個成本。

### 2.2 三個語意必須對得起來，否則會靜默地錯

**(a) `{"trashed_at": None}` 要匹配「缺欄位」與「明確是 null」兩種。**
JSON 裡缺 key，`json_extract` 回 SQL NULL；明確是 null，也回 SQL NULL。
兩邊自然合流，`WHERE trashed_at IS NULL` 就是對的。這是運氣好，不是設計出來的，
所以要有測試釘住。

**(b) `{"$lte": cutoff}` 不可以匹配 NULL。**
`tests/fakes.py` 已經把這條寫成註解並拿真 MongoDB 6.0 驗過：
比較運算子有型別分隔，缺欄位不會被 `$lte` 撈到。
SQL 這邊 `NULL <= 5` 的結果是 NULL 而不是 true，`WHERE` 會濾掉——同樣自然合流。
**這條錯了的後果是把活節點餵給 `purge()`**，也就是刪掉沒人刪的檔案，
所以它不能只靠「自然合流」，要單獨測。

**(c) `{"$gt": 0}` 對非數字要回 false 而不是拋錯或亂比。**
SQLite 會跨型別比較（型別優先序：NULL < 數字 < 文字 < BLOB），
所以 `'abc' > 0` 在 SQLite 是 true，在 MongoDB 是 false。
**這一條不會自然合流，必須明寫型別守衛**：
`json_type(doc, '$.trashed_at') IN ('integer','real') AND trashed_at > 0`。

### 2.3 索引

`db.py` 現在建的五個索引，一對一搬過來：

| MongoDB | SQLite |
|---|---|
| `nodes` 唯一 `(parent_id, filename)`，partial `{"trashed_at": null}` | `CREATE UNIQUE INDEX ... WHERE trashed_at IS NULL` |
| `nodes` 唯一 `id` | `CREATE UNIQUE INDEX` |
| `nodes` `trashed_at`，partial `{"$gt": 0}` | `CREATE INDEX ... WHERE trashed_at > 0` |
| `keystore` 唯一 `id` | 同上 |
| `users` 唯一 `username` / `id` | 同上 |

SQLite 的 partial index 語法和語意都對得上，這是選 SQLite 而不是別的內嵌資料庫的
實際理由之一——**部分唯一索引是這個專案的正確性前提**，不是最佳化：
它是「兩個活節點不能同名」與「垃圾桶裡可以同名」同時成立的唯一原因。

**已量過的查詢計畫**（`EXPLAIN QUERY PLAN`，對照 MongoDB 那邊記在
`ROADMAP.md` 的 IXSCAN／COLLSCAN 驗證）：

| 查詢 | 計畫 |
|---|---|
| `{"id": x}` | `SEARCH ... USING INDEX id_1` |
| `{"parent_id": x, "filename": y, "trashed_at": None}` | `SEARCH ... USING INDEX parent_id_1_filename_1` |
| `{"trashed_at": {"$gt": 0, "$lte": cutoff}}` | `SEARCH ... USING INDEX trashed_at_1` |
| `{"parent_id": x}`（`children()`，含垃圾桶項） | **`SCAN nodes`** |

最後一條要講清楚：**它在 MongoDB 上也是全掃**，不是這個後端引入的退化。
`(parent_id, filename)` 是部分索引（`trashed_at` 為 null），而
`{"parent_id": x}` 沒有帶上那個條件，兩邊的 planner 都無法證明它是子集，
所以兩邊都不走索引。`live_children()` 有帶條件，兩邊都走索引。

**沒有順手加一個 `parent_id` 單欄索引**，理由有兩層：只加在 SQLite 這邊會製造
§2.3 一開始就要避免的「兩邊索引集合不一致」；兩邊都加就是改 schema，
而 `CLAUDE.md` 把改 schema 列為必須先問。**這一條留給使用者決定。**

**唯一性在這裡是真的被強制的，這和 `FakeDB` 不同。** `fakes.py` 明說它不強制唯一性
（「uniqueness is MongoDB's job」），所以既有測試裡沒有一項證明過重複鍵真的會被擋。
SQLite 後端會真的擋，這是它比假件更嚴格的地方——也意味著**如果有測試依賴了
「假件放行重複鍵」的行為，它會在這裡變紅**。那不是回歸，是假件一直在放水。

### 2.4 async 怎麼處理

`sqlite3` 是同步的。三個選項：

| 做法 | 代價 |
|---|---|
| 加 `aiosqlite` | 新相依套件，抵銷掉「SQLite 是標準庫」的好處 |
| 丟 executor 執行緒 | SQLite 預設 `check_same_thread`，且引入這個 app 本來沒有的執行緒併發 |
| **同步呼叫 + 每個方法開頭 `await asyncio.sleep(0)`** | 阻塞事件迴圈幾百微秒 |

**選第三個。** 這是本機檔案上的 metadata 寫入（真正的位元組在 Discord，不在這裡），
單使用者單副本，阻塞時間遠低於任何一次 Discord 往返。

`asyncio.sleep(0)` **不是裝飾**：`fakes.py` 的檔頭記著，2026-08-06 之前假件不讓出
事件迴圈，導致並行寫入的 bug「連測都測不出來」，578 項綠燈對它一無所知。
真 MongoDB 每次呼叫都是網路往返、一定會讓出，所以**測試套件要在兩個後端上代表同一件事，
這個 `sleep(0)` 就必須在**。

### 2.5 不模擬的運算子要拋錯，不要靜默匹配零筆

照抄 `fakes.py` 的立場：沒實作的運算子拋例外。
理由那裡寫得很清楚——大多數呼叫端把「零筆」讀成「沒有這個東西」，
所以一個靜默匹配零筆的未知運算子，會表現為**測試通過、功能不會動**。

---

## 三、怎麼證明它是對的

這是整個方案裡我最有把握的一段，也是選這條路的最大理由。

**既有的 657 項測試就是一致性測試。** 現在它們跑在 `FakeDB` 上；
只要讓 fixture 可以換成真的 SQLite 後端，同一套測試就變成
「SQLite 後端與 MongoDB 語意是否等價」的證明。

做法：`pytest --db=sqlite` 讓 `fake_db` fixture 回傳一個建在 `tmp_path` 上的
真 SQLite 資料庫，其餘一律不動。兩種模式都要在 CI（或至少在提交前）各跑一次。

這比另外寫一份 SQLite 專用測試強得多，因為它涵蓋的是
**真正會跑的那些路徑**——跨 chunk 上傳、覆寫、垃圾桶、tag 驗證、並行寫入——
而不是我對「adapter 應該怎麼行為」的想像。

另外補一份 `tests/test_sqlite_backend.py`，專門釘住 §2.2 那三條語意
與唯一索引，因為那些是**假件從來沒測過、真 MongoDB 才有的行為**，
既有測試不會涵蓋。

---

## 四、打包（第二階段）

資料庫換完、測試綠了之後才動這一段。

1. **`standalone.py`**：不經 Docker 的進入點。讀設定檔而不是 `.env`
   （單機版沒有 compose 幫忙注入環境變數），起 SFTP + Web，用 SQLite。
2. **PyInstaller spec**：把 Python、`src/`、`client/app/dist/` 打成一支執行檔。
3. **Electron 外殼改成會啟動後端**：現在 `main.js` 只是開視窗連遠端；
   單機版要多一段生命週期管理（起子行程、等 `/api/health`、關閉時收掉）。

**打包這一段的驗證天生比第一段弱**：我能驗到「建得出來、跑得起來、健康檢查會過」，
驗不到「在一台乾淨的 Windows 上雙擊會怎樣」。後者只有使用者能做。

---

## 五、這個方案不碰什麼

- **加密演算法、金鑰處理、認證流程**：一行都不動。
- **`vfs.py`**：一行都不動。tag 版本維持 3，不需要任何 migration。
- **既有的 MongoDB 部署**：`DB_BACKEND` 預設仍是 `mongo`，不設就是現在的行為。
- **兩份資料庫之間沒有遷移工具**，這是拍板時就講明的：單機版是新起點，不是搬家。
