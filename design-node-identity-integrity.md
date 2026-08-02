# 方案：把「身分與位置」納入完整性保護

> 對應 `BLUEPRINT.md` §7.2 H2。寫於 2026-08-01，**同日拍板同日落地**，
> 所以 `ROADMAP.md` 上那條 `[next]`「檔名與位置不受完整性保護」已經不在待辦裡了。

> ## ✅ 已實作（2026-08-01，同日拍板同日落地）
>
> 拍板結果：**D1/D2 = (b1) 納入子項集合、重算式；D3 = 加 `tag_version`；
> D4 = 不寫 migration，直接上**（當時線上 `nodes` 只有 root、0 個檔案）。
>
> **實作與本方案有四處出入**，以實際程式碼為準：
>
> 1. **多了兩階段寫入（`entries_mac_pending`）。**本方案沒有想到目錄 tag 與子項在不同文件、
>    而 standalone MongoDB 沒有 transaction，所以中間有一個崩潰視窗會讓**沒有被攻擊**的目錄
>    從此列不出來。見 `crypto.verify_dir_entries` 與 `DiscordVFS._stage_entries` 的 docstring。
> 2. **§4.2 說「走路徑時每段都驗身分」——既有的 `get_node()` 原本只驗最後一段**，
>    所以那不是「維持現狀」而是要改。已改。
> 3. **`scandir` 繞過 `list_dir`**，子項集合的保護對真實 SFTP 客戶端一度完全無效。
>    實地驗收才抓到，已補 `entries_of()` 共用路徑。
> 4. **§4.3 的 root 特例不需要了。**把 `ensure_root()` 從啟動移到認證之後，root 就是
>    一個普通的有 tag 的目錄。既有的 pre-tag root 只在空的時候就地升級，非空則拒絕。
>
> §5 的 migration 腳本**沒有寫**（D4）。§7 的決策點已全部拍板。
> 覆蓋範圍見 `tests/test_node_identity.py`；決策見 `ROADMAP.md`。
>
> **以下原文保留不動**，因為它記錄了拍板當下掌握的資訊，而上面四條正是「方案與現實的差距」。

---

## 1. 現況與威脅模型

威脅模型不變：**攻擊者能寫 MongoDB，但拿不到 master key。**
（備份外洩、Mongo 憑證外洩、內部人員。拿到宿主的人本來就贏了，不在此列。）

目前兩層 tag 蓋的東西（`src/crypto.py`）：

| tag | 涵蓋 |
| --- | --- |
| `chunk_tag` | `(file_id, index, offset, size, nonce, ciphertext)` |
| `node_tag` | `(file_id, size, 有序 chunk tag 列表)` |

`_verify_node()`（`src/vfs.py`）對 `is_dir` 直接 `return`——**目錄完全沒有 tag。**

### 1.1 今天驗得過的竄改

| # | 動作 | 現在會怎樣 |
| --- | --- | --- |
| A1 | 把 `secret.txt` 改名成 `boring.txt` | 通過 |
| A2 | 改 `parent_id`，把檔案搬到別的目錄 | 通過 |
| A3 | **交換兩個檔案的 `filename`** | 通過 |
| A4 | 把目錄 `/private` 改名成 `/public` | 通過（子項的 `parent_id` 是目錄 **id**，不是名字，所以沒有任何子項的 tag 會壞） |
| A5 | 刪掉某個節點文件 | 通過（沒有東西記錄「這個目錄應該有幾個小孩」） |
| A6 | 把先前存下來的節點文件重新插回去 | 通過 |

**A3 是實務上最危險的一個**：它是內容替換。使用者開 `report-2026.pdf`，讀到的是
`report-2024.pdf` 的位元組，而且每一層驗證都通過——因為那些位元組確實是用真金鑰
寫的，只是不屬於這個名字。整套完整性驗證在這裡給出的是**誤導性的保證**。

A4 同理，而且範圍是整棵子樹。

### 1.2 已經擋得住、不必重做的部分

內容本身。`chunk_tag` 綁住 `(file_id, index, offset, size)`，`node_tag` 綁住整份
chunk tag 列表，所以重排、跨檔搬運 chunk、刪尾端 chunk、chunk 換洞、改位元組全部會被抓到。
**這份方案不動 `chunk_tag`，一個 chunk 都不需要重新上傳。**

---

## 2. 為什麼四件事必須一起做

`ROADMAP.md` 列的四件事不是四個可以分批出貨的階段，是一個原子改動：

1. **只做「tag 涵蓋 `filename` / `parent_id`」** → `rename` 不重算 tag，改完名字自己的檔案就讀不出來。壞的是功能，不是攻擊者。
2. **只做 1+2（rename 會重算）** → 擋得住改名與搬移（A1–A3），擋不住目錄改名（A4）。
3. **只做 1+2+3（目錄有自己的 tag）** → 上線當下所有既有節點的 tag 都是舊格式，而 fail closed 是**已拍板的決策**（`ROADMAP.md`），所以**每一個檔案立刻讀不出來**。

第 4 件（回填 migration）才是真正的成本所在，見 §5。

---

## 3. 設計：tag 怎麼擴

### 3.1 檔案節點

```python
def node_tag(key, *, file_id, parent_id, filename, size, chunk_tags) -> bytes:
    mac = hmac.new(_subkey(key, _MAC_INFO), digestmod="sha256")
    mac.update(b"node2")                                   # 見下方說明
    mac.update(_length_prefixed(file_id.encode("utf-8")))
    mac.update(_length_prefixed(parent_id.encode("utf-8")))
    mac.update(_length_prefixed(filename.encode("utf-8")))
    mac.update(size.to_bytes(8, "big"))
    mac.update(len(chunk_tags).to_bytes(8, "big"))
    for tag in chunk_tags:
        mac.update(_length_prefixed(bytes.fromhex(tag)))
```

- **網域分隔字串從 `b"node"` 換成 `b"node2"`**。所有欄位本來就是定長或帶長度前綴，
  不改也不會有歧義；換掉的理由是讓「舊 tag 一定驗不過新函式」變成寫在程式碼裡的事實，
  而不是「因為輸入剛好不同」的副作用。fail closed 要靠得住就不能靠巧合。
- `filename` 用 **NFC 正規化後**的 UTF-8。不做的話，同一個檔名在 macOS 客戶端（NFD）
  與 Linux 客戶端（NFC）之間會算出不同的 tag——那會變成一個只在特定客戶端出現的
  「檔案損毀」，是最難查的一種。**這是既有程式碼沒有處理的問題，本方案順帶納入。**

**刻意不納入的東西維持不變**：權限位與時間戳。理由見 `ROADMAP.md` 的拍板決策——
它們不是內容，納入會讓每次 `chmod` 都要重算一份蓋在完全沒動過的位元組上的 tag。

### 3.2 目錄節點

目錄需要一個 tag 蓋住**它自己的身分**：

```python
def dir_tag(key, *, dir_id, parent_id, filename) -> bytes:
    mac.update(b"dir")
    mac.update(_length_prefixed(dir_id))
    mac.update(_length_prefixed(parent_id or ""))       # root 的 parent 是 None
    mac.update(_length_prefixed(filename))               # root 是空字串
```

這擋掉 A4。**要不要另外蓋住「子項集合」是決策點 D1，見 §4 與 §7。**

---

## 4. 目錄的子項集合：兩個坑

這是整份方案裡唯一有實質設計難度的部分，也是 `ROADMAP.md` 那條記錄裡沒有展開的部分。

### 4.1 子項集合的邊際價值只有「刪除」

先算清楚加了它到底多擋掉什麼。假設 §3.1 已經做了（檔案自己的 tag 綁住 `parent_id` + `filename`）：

- 改名、搬移 → **檔案自己的 tag 就擋掉了**，不需要目錄幫忙。
- 偽造一個新檔案塞進目錄 → 攻擊者產不出合法的 `node_tag`，**已經擋掉**。
- **刪除一個子項 → 擋不掉，需要子項集合。**
- **把先前刪掉的子項插回去（A6）→ 需要同時 rollback 子項文件與目錄文件**，
  那正是**已拍板接受的「整檔 rollback」殘留風險**（`ROADMAP.md`），子項集合也擋不住。

所以子項集合買到的東西**精確地只有一項：偵測刪除**。這值得寫清楚，因為它決定了 D1 該怎麼選。

### 4.2 坑一：驗證子項集合的成本落在路徑查詢上

儲存一個「子項摘要」欄位不等於保護了子項集合——攻擊者刪掉一個子項、不動那個欄位就好。
**要有意義就必須在驗證時從實際子項重算**，而 `get_node()`（`src/vfs.py`）是逐段
走路徑的，所以 `/a/b/c/file.txt` 會變成**每次 open 都要把 a、b、c 三個目錄的子項全部列出來**。
一個一萬筆的目錄就是每次路徑查詢讀一萬份文件。不可接受。

出路是把目錄 tag 拆成兩層：

- **身分 tag**（`dir_tag`，§3.2）：走路徑時一定驗，成本 O(1)。
- **子項集合 tag**：只在 `list_dir` / `scandir` 時驗。

但這與**既有的拍板決策衝突**：「完整性檢查不涵蓋列目錄——刻意的，否則一個被竄改的
檔案會讓整個目錄列不出來」（`ROADMAP.md` `[later]`）。子項集合的驗證失敗確實應該
讓整個 `ls` 失敗（少了一個檔案就是少了），語意上跟那條的理由不同，但**它會讓 `scandir`
從「永不失敗」變成「可能失敗」**，這是要明講的行為改變。

### 4.3 坑二：`ensure_root()` 沒有金鑰

`ensure_root()`（`src/vfs.py`）在**任何人認證之前**跑，那時沒有 master key，
所以 root 目錄不可能在建立當下帶 tag。docstring 現在寫「root 是目錄、不帶 content tag，
所以不需要」——這條方案會讓那句話失效。

- **root 的身分 tag 不需要**：`ROOT_ID` 是程式碼裡的常數、`parent_id` 是 `None`、
  `filename` 是空字串，沒有一項是攻擊者改了會有意義的（改了就查不到 root，服務直接壞掉，
  那是可見的故障不是無聲竄改）。**建議 root 在身分驗證上直接豁免，並把理由寫進程式碼。**
- **root 的子項集合 tag 就麻煩了**：需要金鑰，所以只能在第一次登入後補上。
  這是「選了 D1=蓋住子項集合」才會出現的額外複雜度，且它引進一個
  「第一次登入時要不要自動寫入」的新分支——而那個分支長得像本專案最不喜歡的那種東西
  （啟動時自動修改既有資料）。

### 4.4 若選擇蓋住子項集合：重算 vs 累加器

- **重算式**：`dir_entries_tag = HMAC(key, "entries" || dir_id || n || 排序後的 (child_id, filename) 列表)`。
  簡單、好讀、好測。代價是**每次建檔／刪檔／改名都要讀出該目錄全部子項**才能重算。
- **累加器**：每個子項算一份 `HMAC(key, "entry" || dir_id || child_id || filename)`，
  目錄存的是**所有子項這份值的模 2²⁵⁶ 加總**加上筆數。新增就加、刪除就減，**O(1)**。
  - **必須用加法不能用 XOR**：XOR 下同一筆進去兩次會互相抵銷。
    `(parent_id, filename)` 上的唯一索引通常能擋住重複，但**攻擊者有 DB 寫入權就能把索引砍掉**，
    所以不能把安全性建在索引上。模 2²⁵⁶ 加法沒有這個性質。
  - 代價是這是本專案裡最「聰明」的一段程式碼，出錯的方式也最安靜。

---

## 5. 回填 migration（真正的成本）

fail closed 是已拍板決策，所以**沒有回填就等於升級當下全部檔案讀不出來**。

### 5.1 必要性質

1. **可重跑**。tag 是既有資料加金鑰的純函式，重算同一個節點必得同一個值。
2. **先 dry-run**。預設不寫入，只輸出：節點總數、檔案/目錄各幾個、已是新版幾個、
   以及**任何一個現在就驗不過的節點**。
3. **寫新 tag 之前一定要先驗過舊 tag**。這是整支腳本最重要的一條：
   對一個已經被竄改的節點重算 tag，等於**用真金鑰把竄改結果洗白成合法**。
   驗不過就中止整支 migration，不是跳過、不是警告。
4. **停機執行**。跨文件不可能有原子性，中途死掉會留下混合狀態，而混合狀態在 fail closed
   之下就是「有些檔案讀不出來」。停機 + 可重跑 = 死掉就再跑一次。
5. **跑之前先 `mongodump`**。這是唯一真正的退路，而且很便宜。
   （註：這與 KDF 遷移那次「刻意不做記錄備份」的決定不衝突——那次不備份是因為留著
   舊的弱包裝會直接抵銷升級效果；這裡備份的是節點 metadata，沒有那個性質。）

### 5.2 混合狀態怎麼辨識

節點文件加一個 `tag_version` 欄位：

- 缺欄位或 `1` → 舊格式。**伺服器一律拒絕**（fail closed），migration 認得出來要處理。
- `2` → 新格式。migration 跳過，伺服器接受。

這讓「可重跑」是結構上成立的，而不是靠腳本自己記進度。

### 5.3 腳本形狀

放在 `scripts/migrate_node_tags.py`（**這會是本專案第一個留在 repo 裡的腳本**——
歷次對帳腳本都是 scratchpad 一次性的，但這支不同：它要能被重跑、被審、被下一個人讀懂）。

```
python -m scripts.migrate_node_tags --dry-run     # 預設；只報告
python -m scripts.migrate_node_tags --apply
python -m scripts.migrate_node_tags --verify      # 全部重讀一次，確認都是 v2 且驗得過
```

密碼從 `.env` 讀（`SOP.md` 那條：`load_dotenv` 要給絕對路徑），走
`keystore.open_master_key()`，與伺服器同一條路徑。

---

## 6. 逐項改動清單

| 檔案 | 改什麼 |
| --- | --- |
| `src/crypto.py` | `node_tag` / `verify_node` 加 `parent_id`、`filename` 參數；新增 `dir_tag` / `verify_dir`；檔名 NFC 正規化 helper |
| `src/vfs.py` `_content_update` | 傳入 `parent_id` / `filename`（節點 dict 上本來就有） |
| `src/vfs.py` `_verify_node` | 不再對 `is_dir` 直接放行；root 豁免；檢查 `tag_version` |
| `src/vfs.py` `_create_file` / `makedir` | 算自己的 tag（目錄現在也要）＋更新父目錄 |
| `src/vfs.py` `remove` / `removedir` / `_discard` | 更新父目錄 |
| `src/vfs.py` `rename` | **重算被移動節點自己的 tag**（`parent_id` / `filename` 都變了）＋更新來源與目的兩個目錄 |
| `src/vfs.py` `_touch_dir` | 與「更新目錄 tag」合併成一個函式。現在只寫 `modified_at`，而**兩者的觸發時機完全相同**（子項集合變動），分開寫遲早會漏掉一邊 |
| `src/vfs.py` `ensure_root` | docstring 失效，要改；root 的豁免理由寫在這裡 |
| `scripts/migrate_node_tags.py` | 新檔，見 §5 |
| `tests/` | 見 §8 |

**`rename` 與目錄 mtime 已經纏在一起**（`ROADMAP.md` 提過）：`rename` 現在刻意
**不**動被移動檔案的 `modified_at`（移動不是修改），但**會**動兩端目錄的。
加上 tag 之後，「不動 mtime」與「必須重算 tag」會同時發生在同一個節點上，
所以 `_content_update` 那種「size/chunks/mac 一起動」的打包方式在這裡不適用，
需要一條只寫 `parent_id` / `filename` / `mac` 的路徑。這是最容易寫錯的一處。

---

## 7. 必須先拍板的決策點

> **未拍板前不動工。**

### D1. 目錄的子項集合要不要納入 tag？

- **(a) 不納入**（只做 §3.2 的身分 tag）。擋掉 A1–A4。**刪除（A5）與復活（A6）仍然驗得過。**
  一致性論證很強：A6 本來就等於已拍板接受的整檔 rollback；而 A5 的攻擊者能刪檔就能 rollback。
  路徑查詢零額外成本，`scandir` 行為不變，root 不需要特例。**建議選這個。**
- **(b) 納入**，並拆成「身分 tag 走路徑時驗、子項集合 tag 只在 `list_dir` 驗」。
  多擋一個 A5。代價：`scandir` 從「永不失敗」變成「可能失敗」（與既有拍板決策的理由相衝，
  要重新拍一次）、root 需要第一次登入後補寫、複雜度顯著上升。

### D2. 若 D1 選 (b)：重算式還是累加器？

- **重算式**：簡單好測，但大目錄的每次建/刪/改名都要讀全部子項。
- **累加器**：O(1)，但要用模 2²⁵⁶ 加法而非 XOR（理由見 §4.4），是全專案最需要小心的一段。
- D1 選 (a) 的話這題不存在。

### D3. `tag_version` 欄位要不要加？

- **加**（建議）：混合狀態可辨識，migration 天生可重跑，伺服器可以明確拒絕舊版。
- 不加：靠「驗不過就是舊版」推斷——但那與「真的被竄改」無法區分，等於把最需要分辨的
  兩件事混在一起。**不建議。**

### D4. migration 的執行方式？

建議：**停機 → `mongodump` → `--dry-run` 對帳 → `--apply` → `--verify` → 起服務**，
且沿用 KDF 遷移那次的 canary 手法（遷移前用 SFTP 放一個內容由固定種子產生的檔案，
遷移後讀回來比對；種子固定是為了讓驗證端自己重算期望值，不去信任遷移前寫下的東西）。

---

## 8. 測試計畫

單元/整合（`tests/test_node_identity.py`，新檔）：

1. 改 `filename` → 讀取被拒（A1）
2. 改 `parent_id` → 讀取被拒（A2）
3. **交換兩個檔案的 `filename` → 兩邊都被拒**（A3，最重要的一條）
4. 改目錄的 `filename` → 該目錄下的存取被拒（A4）
5. 正常 `rename` 之後檔案照樣讀得出來（防止「修了安全性、砍了功能」）
6. 正常 `rename` **不會**改到檔案自己的 `modified_at`（釘住既有行為）
7. `mkdir` / `rmdir` / `remove` / `posix_rename` 覆寫之後，父目錄的 tag 仍驗得過
8. root 的豁免有測試釘住，且豁免範圍**僅限 root**
9. 檔名 NFC/NFD 正規化：同一個名字用兩種正規化寫入/查詢得到同一份 tag
10. `tag_version` 不是 2 的節點一律被拒（fail closed 的迴歸測試）
11. 若 D1 選 (b)：刪掉一個子項 → `list_dir` 被拒；且**單一檔案被竄改不會**讓 `list_dir` 失敗

migration 腳本：

12. dry-run 不寫入任何東西
13. 對一個「舊 tag 就驗不過」的節點，migration 中止而不是把它洗白（**最重要的一條**）
14. 跑兩次的結果與跑一次相同（冪等）
15. 中途中斷後重跑會完成（混合狀態可收斂）

實地驗收：真實 bot token、真 MongoDB、canary 檔案、收尾對帳 0 孤兒 0 懸空引用。

---

## 9. 規模與風險

- 程式碼改動本身不大（`crypto.py` 約 40 行、`vfs.py` 約 8 處），**風險不在行數**。
- 風險在 migration：跑錯一次是**所有檔案讀不出來**，而且是在 fail closed 之下立刻發生。
  §5.1 的五條性質每一條都是為了這件事。
- 第二個風險是 `rename` 那條路徑（§6 末段），它同時牽動 tag、mtime、兩個目錄，
  而目前的 `_content_update` 打包方式在那裡不適用。
- D1 選 (a) 的話，這份方案的規模約是「一輪」；選 (b) 大約多一倍，而且會推翻一條既有拍板決策。
