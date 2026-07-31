# Session Handoff

依 `.claude/templates/session-handoff.md` 產出。
涵蓋範圍：**commit 積壓的跨 handle 同步**、**KDF 換 Argon2id**、
**整檔 rollback 拍板不做**、**多使用者設計方案**，以及一個讀程式碼時發現的新缺口。

> **有一條 `[next]`，是本輪新發現的**：`node_tag` 沒有涵蓋 `filename` / `parent_id`，
> 能寫 MongoDB 的人可以改名、搬檔而且驗得過。詳見 `ROADMAP.md`。
> 自動化測試 336 項全過，pyflakes 乾淨。
> **服務跑的仍是舊 image**——本輪改了 `src/` 但沒有重建，見「未完成待辦」第一條。

---

## 目前狀態

三個 commit，前兩個是程式碼，第三個是文件：

| commit | 內容 |
|---|---|
| `6ada626` | 跨 handle 的內容狀態同步（上一輪寫好、一直沒 commit 的） |
| `5643ccb` | KDF 換成 Argon2id；整檔 rollback 拍板不做 |
| （本輪最後一個） | 新缺口寫進 `ROADMAP.md`、`design-multi-user.md`、本檔 |

## 已完成

### 一、把積壓的跨 handle 同步 commit 掉（`6ada626`）

上一輪寫完、驗收過但沒 commit 的改動。內容沒有再改，只是入庫。

### 二、KDF 換成 Argon2id（`5643ccb`）

- 新增相依 `argon2-cffi==25.1.0`（**你本輪批准的**）。
- 新設定值 `KDF` / `ARGON2_TIME_COST` / `ARGON2_MEMORY_KIB` / `ARGON2_PARALLELISM`，
  預設 `argon2id` 64 MiB / t=3 / p=1。`.env.example` 與 `docker-compose.yml` 都補了。
- **選 `argon2-cffi` 而不是 `cryptography` 44 內建的**：後者要把 cryptography 從
  42.0.5 跨兩個 major 升上去，而 asyncssh 整個傳輸層坐在它上面。
- **實測 Argon2id 64 MiB/t=3 是 125ms，比原本 PBKDF2 600k 的 214ms 還快**，
  登入路徑沒有變慢。
- **「不需要 migration」這次被兌現並釘住了**：`derive_kek` 從記錄裡讀函式名與成本，
  不假設當前預設值。並且實地確認過線上那份記錄的形狀就是
  `pbkdf2-sha256 / kdf_iterations=600000`——正是新程式碼讀得懂的形狀。
- 成本參數改成「記錄裡缺一個就拒絕」而不是補當前預設值：補預設會推出一把不同的金鑰，
  然後以「密碼錯誤」的形式浮現，那是最糟的一種報錯方式。
- `KDF_UPGRADE`（**預設關**）才會把既有記錄重新包裝。覆蓋前會先確認新記錄真的解得開
  （`_replace_wrapping`）。這是系統裡最危險的一次寫入——寫壞不是壞掉一個檔案，
  是所有位元組永遠讀不出來。

### 三、拍板的兩個決策（已寫進 `ROADMAP.md` 的「已拍板的長期決策」）

- **整檔 rollback 不做**。釘 Discord / 本機 append-only 檔 / 外部 KMS-TPM 三條路都不走，
  改列為已評估並接受的威脅模型邊界，不再是待辦。
- **既有記錄的 KDF 升級是 opt-in**，預設不動。

### 四、多使用者設計方案（`design-multi-user.md`，只出方案未動工）

你選的是「只出方案」。文件裡最重要的一段是 §2：多使用者有兩種產品，
**共用金鑰（A）與每人一把金鑰（B）**，差別在「A 的密碼能不能解開 B 的資料」，
而且選了之後要改很貴。文件末尾列了三個必須先拍板的決策點。

### 驗證方式

```bash
./venv/Scripts/python.exe -m pytest
```

336 passed（本輪 +26，全在 `test_keystore.py` / `test_config.py`），24 秒，pyflakes 乾淨。
新增的測試裡值得一提的三個：

- `test_a_pbkdf2_record_still_opens_after_the_default_moved` — 釘住零 migration 的宣稱。
- `test_ensure_usable_does_not_upgrade_unless_asked` — 釘住「重啟不會自己改寫金鑰記錄」。
- `test_a_rewrap_that_does_not_open_again_is_not_stored` — 釘住覆蓋前的驗證。

另外實地做了兩項（腳本在 scratchpad，未留在 repo，沿用既有慣例）：
本機量測兩種 KDF 的實際耗時；從容器讀出線上 keystore 記錄的**欄位形狀**
（刻意只印欄位名與 KDF 參數，不印 salt / ciphertext / hmac）。

---

## 未完成待辦

### 一、`src/` 改了但沒有重建 image（**下一輪第一件事**）

本輪動了 `src/crypto.py`、`src/keystore.py`、`src/config.py`、`src/main.py`
與 `requirements.txt`，**但沒有 `docker compose up -d --build`**。
重建屬於會影響正在跑的服務的動作，本輪沒有自作主張。

重建後預期會看到的行為（都不是錯誤）：

- 啟動時多一則 WARNING：「stored master key is still wrapped with pbkdf2-sha256
  while this server is configured for argon2id ... only happens when KDF_UPGRADE is enabled」。
- **服務照常運作，既有檔案照常讀得出來**，因為記錄自己帶著參數。
- 想真的搬到 Argon2id：`.env` 設 `KDF_UPGRADE=1` 重啟一次，看到
  「Master key re-wrapped」那則 WARNING 後改回 `0`。

**Argon2id 尚未在真實環境跑過**——本輪的驗證都在 venv 與單元測試裡。
重建後值得確認的是容器內 `argon2-cffi` 的 wheel 有裝起來（`python:3.11-slim`
有 manylinux wheel，理論上不需要編譯工具鏈，但沒有實測過）。

### 二、新發現的完整性缺口（`ROADMAP.md` `[next]`）

`node_tag` 蓋的是 `(id, size, 有序 chunk tags)`，**`filename` 與 `parent_id` 不在裡面**。
能寫 MongoDB 的人可以把任何檔案改名、搬到別的目錄，而且驗得過；目錄節點根本沒有 tag，
所以憑空往某個目錄塞一個檔案也沒東西擋。

`ROADMAP.md` 那條列了要修得完整需要一起做的四件事，其中第 4 件是
**一支回填既有節點的 migration 腳本**——這是這件事真正的成本所在，
跑錯一次就是全部讀不出來。**做之前先出方案。**

### 三、你本輪明確決定不做的（不要下一輪又拿出來問）

- **整檔 rollback** — 拍板不做，已成為接受的邊界。
- **scandir 完整性驗證**（A）— 不做。效益接近化妝品：讀和開都已經驗了，
  它只影響 `ls -l` 顯示的數字可不可信，而且目錄項本來就沒 tag、驗不了。
- **權限位/時間戳的 `meta_mac`**（B）— 不做，已被上面第二條取代
  （補小洞留大洞沒有意義）。
- **符號連結**（C）— 不做。要改 `get_node()`，而每個操作都走它。
- **多使用者**（D）— 只出方案，未動工。

### 四、仍未被真實環境驗證的東西（沿用上一輪）

- 5xx 重試與傳輸層重試沒有被真實觸發過（Discord 自己故障才會發生，無法從外部觸發）。
- 附件 URL 真的過期（24 小時後）沒有等過。
- **（本輪新增）Argon2id 沒有在容器裡跑過**，見上面第一條。

---

## 資料狀態

- **本輪沒有動任何資料。** `nodes` 未動，`keystore` **未動**（仍是那份 PBKDF2 記錄），
  Discord 上沒有新增或刪除任何附件。
- 本輪跑的兩支 scratchpad 腳本都是唯讀的：一支只 `find_one` 再印欄位名，
  一支完全不碰 DB（純本機量測）。
- `mongo_data` / `host_key_data` volume 未刪除，host key 沒換。

---

## 本輪不可碰的範圍

- **`src/vfs.py` / `src/sftp.py` / `src/discord_api.py` / `src/db.py` 一行未改**——
  本輪的改動集中在金鑰與設定層。
- **加密演算法本身未改**：AES-256-CTR、HMAC-SHA256 的 chunk/node tag 全部沒動。
  換掉的只有「密碼 → KEK」那一段。
- **`node_tag` 的涵蓋範圍未改**（新發現的缺口只寫進文件，沒有動程式碼）。
- **既有 keystore 記錄未覆蓋**——`KDF_UPGRADE` 預設關就是為了這件事。
- **image 未重建**，服務跑的是舊碼。

---

## 下一步建議任務

1. **`docker compose up -d --build`**，然後確認上面「未完成待辦」第一條列的
   三件事（WARNING 出現、既有檔案讀得出來、`argon2-cffi` 在容器裡裝得起來）。
   這是唯一會擋住其他事的一步——本輪的成果目前還沒真的上線。
2. 決定要不要 `KDF_UPGRADE=1` 跑一次，把線上那份記錄真的搬到 Argon2id。
   （不搬也不會壞，只是每次啟動多一則 WARNING。）
3. **`filename` / `parent_id` 的完整性**（`ROADMAP.md` `[next]`）——先出方案，
   重點在那支 migration 腳本要怎麼寫得能重跑、能 dry-run 對帳。
4. 多使用者要不要做，等你看完 `design-multi-user.md` §6 的三個決策點再說。
   文件裡建議的分四步走法，前三步跑完系統仍是單一使用者、行為不變，
   所以「要不要做」這個決定其實可以推遲。

---

## 環境備忘

- venv 在 `venv/`。一律用 `./venv/Scripts/python.exe -m pytest`（見 `SOP.md`）。
- repo 在 `master`，**沒有 remote**，只有本機 commit。
- 程式碼是烤進 image 的，改完 `src/` 一定要 `docker compose up -d --build`。
- **MongoDB 的埠沒有對宿主開放**，一次性腳本不能從 venv 直連 `127.0.0.1:27017`
  （本輪踩到一次）。要走
  `docker compose exec -T sftp-discord-server python - < 腳本`（`SOP.md` 既有條目）。
  這是第一次踩，按規則沒寫進 `SOP.md`；再踩一次就補條目。
- 驗收腳本寫在 scratchpad 時，`load_dotenv()` 要傳絕對路徑（見 `SOP.md`）。
