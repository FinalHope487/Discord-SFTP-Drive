# Session Handoff

依 `.claude/templates/session-handoff.md` 產出。
涵蓋範圍：**`BLUEPRINT.md` 全掃列出的 H1 / H2 / M2 / M3 / M4 / M7 六條全部處理完**，
加上 `README.md` 與三條順帶掃掉的 `[later]`。

> **`ROADMAP.md` 現在沒有 `[now]`，也沒有 `[next]` 需要動工的項目了**
> ——只剩 repo remote（等你開好 GitHub MCP）與一批 `[later]`。
> 371 項自動化測試全過，pyflakes 乾淨，production image 內同樣 371 過。
> **實地驗收 17/17**，收尾對帳 0 孤兒、0 懸空引用。

---

## 目前狀態

**服務跑在 Python 3.12 上，帶著新的完整性 tag，已上線並實地驗收過。**
線上 `nodes` 只剩 root（驗收用的檔案已清乾淨），Discord 上 0 個附件。

三個 commit 已入庫（`9669c7c` 程式碼、`9413874` 文件、`a28aa65` slash command），
**本段的改動尚未 commit。**

## 已完成

### 一、H2：完整性 tag 涵蓋身分與位置（本段的主體）

拍板：**D1/D2 = (b1) 納入子項集合、重算式；D3 = 加 `tag_version`；D4 = 不寫 migration。**

- `src/crypto.py`：`node_tag` 加 `parent_id` / `filename`（網域分隔字串 `b"node"` → `b"node2"`）；
  新增 `dir_tag`（目錄身分）、`dir_entries_tag`（子項集合）與各自的 verify；
  檔名一律 NFC 正規化（否則同一個名字在 macOS 的 NFD 與 Linux 的 NFC 之間會算出不同的 tag，
  變成只在單一平台出現的「檔案損毀」）。
- `src/vfs.py`：`_verify_node` 現在也驗目錄並檢查 `tag_version`；`get_node` **逐段**驗證；
  `_stage_entries` 統一處理所有結構變動；`rename` 會重算被移動節點自己的 tag；
  `ensure_root` 變成需要金鑰的方法。
- `src/sftp.py`：`scandir` 改走驗證過的 `entries_of()`；root 在 `validate_password` 建立。
- `src/main.py`：不再於啟動時建 root（那時沒有金鑰）。

**實作與方案有四處出入，每一處都寫進 `ROADMAP.md` 與方案文件的橫幅了。**
最值得記的兩條：

- **`scandir` 繞過了 `list_dir`**，所以子項集合的保護「對直接呼叫 VFS 的人有效、
  對真正的 SFTP 客戶端無效」。**單元測試全部通過，是實地驗收抓到的。**
  這個 suite 當初存在的理由就是抓這種「協定層沒接上」的錯，而我又踩了一次——
  教訓是**寫協定層的迴歸測試時，happy path 不算數，要竄改後從協定那一端驗**。
- **方案漏了一個崩潰視窗。**目錄 tag 與子項在不同文件，standalone MongoDB 沒有 transaction，
  所以中間崩潰會讓一個**沒有被攻擊**的目錄從此列不出來。補了兩階段寫入
  （`entries_mac_pending` 先存、變更後才升為正式，驗證接受兩者）。

### 二、三條順帶掃掉的 `[later]`

- **失敗 handle 的讀取語意**：`size` 不再計入永遠不會落地的 buffer；`read_at` 不再 flush
  失敗的 handle（否則 Discord 剛好復原時會復活一次已回報失敗的寫入）。
- **`_url_cache` 加上限**（LRU 4096）。註解寫明為什麼**這個**快取淘汰是安全的
  而 `_node_versions` 不是。
- **`_to_sftp_error` 的死分支**刪掉，換成一行說明。

### 驗證方式

```bash
./venv/Scripts/python.exe -m pytest        # 371 passed, ~30s
```

四層驗證，缺一條就會漏掉東西：

1. **371 項單元/整合測試**（+28），`tests/test_node_identity.py` 24 項是新的。
2. **突變測試**：把九個保護逐一拿掉，確認每一個都有測試會失敗——包含
   「`scandir` 改回直接呼叫 `children()`」這一條，就是它現在釘住了上面那個 bug。
   腳本在 scratchpad，未留在 repo。
3. **production image 內跑同一份 suite**：371 passed（3.12.13 / Linux）。
4. **實地驗收 17/17**（真實 bot token、真 MongoDB、12MB 檔案）：
   建樹、列目錄、12MB 往返、改名、跨目錄搬移、目錄改名之後子項仍讀得出來；
   然後**直接竄改 MongoDB**——改檔名、搬到別的目錄、交換兩個檔名、改目錄名、刪掉一個節點
   ——五種全部被拒；還原後所有位元組逐一比對相同；清理後只剩 root，
   root 帶著兩個 tag 與 `tag_version=2`，沒有殘留的 `entries_mac_pending`。
   收尾對帳 **0 孤兒、0 懸空引用**。

---

## 未完成待辦

### 一、repo 沒有 remote（`ROADMAP.md` 唯一的 `[next]`）

**你說要先開 GitHub MCP。**所有 commit 仍只存在這台機器。
現在比之前更值得做：這一段動了加密層，而且**沒有 migration 可以退回去**。

### 二、`BLUEPRINT.md` 該重新產出（新列的 `[later]`）

它以 `5968362` 為準，之後 H1 與 H2 都落地了，§4.3 / §4.4 描述的 tag 涵蓋範圍已明顯落後。
目前靠開頭的狀態橫幅與逐條註記撐著，那是權宜。`/blueprint` 跑一次就好。

### 三、仍未拍板的 `[later]`

`SFTP_PASSWORD` 走 docker secret 還是明確接受風險；多使用者
（`design-multi-user.md` §6 三個決策點）；其餘見 `ROADMAP.md`。

### 四、仍未被真實環境驗證的東西

5xx 與傳輸層重試（要 Discord 自己故障）、附件 URL 真的過期（要等 24 小時）、
`_rollback` 的保護（同樣要 Discord 連續失敗五次以上）。

---

## 資料狀態（本段動過，寫清楚）

- **root 節點已就地升級**：原本是 pre-tag 形狀（沒有 `tag_version` / `mac` / `entries_mac`），
  升級當下它是空的，所以升級是安全的（`ensure_root` 對**非空**的 pre-tag root 會拒絕啟動
  並說明原因——對有內容的目錄重算 tag 等於把已經發生的刪除簽成合法）。
- **驗收期間建立的檔案與目錄已全部刪除**，Discord 附件一併釋放，對帳 0 孤兒、0 懸空。
- **`keystore` 未動**，master key 沒有變。
- `mongo_data` / `host_key_data` volume 未刪除，host key 沒換。
- **從這一刻起寫入的資料回不去舊格式**——沒有 migration，這是 D4 拍板的內容。

---

## 本段不可碰的範圍

- **`keystore.py` 一行未改**，KDF 與金鑰包裝完全沒動。
- **`chunk_tag` 沒有改**，所以**一個 chunk 都不需要重新上傳**。加密演算法本身沒動。
- **`ratelimit.py`、`db.py` 未改。**
- 沒有新增相依套件；沒有 schema migration；沒有 push。

---

## 下一步建議任務

1. **commit 這一段**（建議同樣切成程式碼／文件兩個）。
2. **開好 GitHub MCP 之後推 remote。**這一段沒有回頭路，異地備份的價值比之前高。
3. **`/blueprint` 重跑一次**，讓藍圖回到與程式碼一致。排在 remote 之後是因為它會產生
   一份大檔，先有備份再說。

---

## 環境備忘

- venv 在 `venv/`（3.12.7）；上線 image 也是 3.12（3.12.13）。一律用
  `./venv/Scripts/python.exe -m pytest`（見 `SOP.md`；裸 `python` 是 Store 的 stub，
  **本輪又踩了一次，exit 49 且無輸出**）。
- 程式碼是烤進 image 的，改完 `src/` 一定要 `docker compose up -d --build`。
- **重啟服務目前不必先問**，見 `CLAUDE.md`「臨時例外」。那是一條有到期日的規則。
- MongoDB 的埠沒有對宿主開放，一次性腳本要走
  `docker compose exec -T sftp-discord-server python - < 腳本`（`SOP.md`）。
  容器裡有 compose 注入的環境變數，**不需要也讀不到 `.env`**。
- 在容器裡跑測試要 `MSYS_NO_PATHCONV=1` 前綴、`--user root`、`-p no:cacheprovider`。
- **竄改腳本要注意真實 MongoDB 有 `(parent_id, filename)` 唯一索引**：直接對調兩個檔名
  會撞 DuplicateKeyError，要經過一個暫時名稱。in-memory fake 不模擬唯一性，所以單元測試
  不會遇到。本輪第一次踩，按規則沒寫進 `SOP.md`。
- **竄改腳本的「還原」要用明確的目標值，不要「再做一次同樣的操作來還原」**——
  對調兩次讀的是同一份快照，等於再對調一次。本輪踩到，而且**只檢查檔名的斷言驗不出來**
  （名字的集合兩種情況下一模一樣，那正是被測的攻擊）。斷言要讀位元組。
