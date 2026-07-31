# Session Handoff

依 `.claude/templates/session-handoff.md` 產出。
涵蓋範圍：**`BLUEPRINT.md` 全掃列出的 H1 / M2 / M3 / M4 / M7 五條全部處理完**，
加上 `README.md`，以及 **H2 的完整方案**（只出方案，未動工）。

> **一條資料遺失路徑已修**：對既有檔案附加寫入時若 Discord 故障，
> `_rollback()` 會把該檔案**原本就存在的** chunk 從 Discord 全部刪掉。
> 自動化測試 343 項全過（+7），pyflakes 乾淨。
> **本輪尚未 commit**——工作樹是髒的，見「目前狀態」。

---

## 目前狀態

**服務跑在 Python 3.12 上，起得來，Discord 可達，正常運作。**
本輪**沒有寫入任何使用者資料**（沒有上傳、沒有刪除、`nodes` 與 Discord 附件狀態未動）。

工作樹**未 commit**：

| 狀態 | 檔案 |
|---|---|
| M | `src/vfs.py`（`_rollback()`）、`tests/fakes.py`（失敗注入）、`Dockerfile`（3.12）、`docker-compose.yml`（設定 + 告示）、`ROADMAP.md`、`missing_info.md` |
| ?? | `README.md`、`design-node-identity-integrity.md`、`tests/test_write_failures.py`、`BLUEPRINT.md`（上輪產出，一直沒入庫） |

commit 訊息還沒寫，因為沒被要求。建議切成兩個：程式碼（`vfs.py` / `fakes.py` /
`test_write_failures.py` / `Dockerfile` / `docker-compose.yml`）與文件（其餘）。

## 已完成

### 一、`_rollback()` 會刪光既有檔案（`BLUEPRINT.md` H1，原 `[now]`）

`src/vfs.py:559`。原本無條件走訪 `self._node["chunks"]` 刪掉**每一個** chunk 的
Discord 訊息再把節點清零。它的 docstring 只假設了「本 handle 新建」與「已 truncate」
兩種情況，但 `open()` 也支援不帶 `O_TRUNC` 開啟既有檔案來寫（`put -a`、log 附加、續傳），
那時 `chunks` 裡裝的是檔案原本的內容。

**修法不是原本評估的那個。** ROADMAP 與 BLUEPRINT 建議的 (A)「還原成開啟時的
chunks/size 快照」，實作前發現它會生出**新的**資料損壞路徑：`_replace_chunk` 在 commit
**成功之後**才刪舊附件，所以開啟時的快照可能指向已經不存在的訊息，還原它等於寫進一個
懸空引用、那個 chunk 永久讀不出來——比原症狀更難察覺（原症狀至少 size 歸零看得見）。

追下去發現正解更小：兩個呼叫點（`:530`、`:554`）本來就各自釋放了自己那顆附件，
**所以對既有檔案而言 `_rollback()` 該做的清理是零**。現在它對既有檔案只標記 handle 失敗
就返回，一個 delete 都不發。新建的檔案維持原行為（整個消失）。

代價寫在 docstring 裡：**日後新增 `_rollback()` 呼叫點的人必須自己負責釋放附件。**

### 二、失敗路徑零測試覆蓋（`BLUEPRINT.md` M2）

`tests/fakes.py` 的 `FakeDiscord` 加了 `fail_uploads_from`（第 N 次上傳起開始失敗，
計數含失敗的那次）。**這個門檻就是失敗路徑長期沒人測的根因**，補完之後 H1 的迴歸測試
立刻寫得出來。

新增 `tests/test_write_failures.py` 7 項：

1. 新建檔案上傳到一半失敗 → 節點與附件都要整個消失
2. **既有檔案 append 失敗 → 既有內容與附件必須原封不動**（H1 迴歸）
3. 隨機寫入失敗 → 舊位元組還在（釘住 `_replace_chunk` 的順序）
4. metadata 寫入失敗 → 只釋放自己剛上傳的那一顆
5. truncate 過的檔案再失敗 → 留空（`_rollback` docstring 一直宣稱的那個情況）
6. 失敗的 handle 拒絕後續寫入與 truncate，且 `close()` 不會 flush
7. **走完整 SFTP 協定的 H1 迴歸測試**（`put -a` 遇上 Discord 故障）

**這些測試在修好前後各跑了一次**：舊程式碼下第 2、4、7 三項失敗，新程式碼下全過。
（1、3、5、6 兩版都過——它們覆蓋的是本來就正確的行為。）

### 三、`DISCORD_MAX_CONCURRENCY` 在 compose 下無效（M3）

`docker-compose.yml` 加一行。**17 個設定值逐項比對過，確認只漏這一個。**
實測驗證：`DISCORD_MAX_CONCURRENCY=2 docker compose up -d` 之後容器內
`config.discord_max_concurrency()` 回 2；修之前永遠是 4，而且不會有任何錯誤訊息。

### 四、測試與上線的 Python 版本對齊（M4）

`Dockerfile` 從 `python:3.11-slim` 升到 `python:3.12-slim`。
**不只是改版號——整份 suite 已實際在那個 image 裡跑過一次：**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --user root -v "D:/my-projects/Discord-Drive:/repo" \
  -w /repo discord-drive-sftp-discord-server:latest \
  sh -c "pip install -q -r requirements-dev.txt && python -m pytest -q -p no:cacheprovider"
```

343 passed，Python 3.12.13 / Linux。四個編譯型相依全部有 cp312 wheel，image 仍不需要
建置工具鏈。所以「336 個測試從未在上線用的直譯器上跑過」這句話現在不成立了。
**沒有另外加 3.11 的測試環境**——兩邊同版之後那件事的價值就消失了。

### 五、`README.md`（M-「沒有入口文件」）與不可水平擴展的告示（M7）

`README.md` 新增：是什麼、怎麼跑起來、怎麼跑測試（含在 production image 裡跑的指令）、
各份文件各自負責什麼、誠實的現況與已知缺口。

水平擴展告示寫在 `README.md` 一節與 `docker-compose.yml` 服務定義上方，
兩處都寫明症狀是**無聲的**（沒有錯誤、沒有 log，只有舊位元組）。
**執行期守衛評估後不做**（Mongo 單例標記硬擋、心跳租約只 WARNING），理由見
`ROADMAP.md` 拍板決策——簡單說是不想用一個新的失敗模式（殘留標記害服務起不來）
去換一個需要刻意 `--scale` 才會觸發的誤用。

### 六、H2 的方案（`design-node-identity-integrity.md`，**只出方案，未動工**）

`ROADMAP.md` 對這條的拍板就是「做之前先出方案，不要直接動工」，所以只出方案。
寫的過程中發現三件原本沒展開的事，都在文件裡：

- **驗證目錄的子項集合必須從實際子項重算**，否則攻擊者刪掉子項、不動摘要欄位就好。
  所以成本會落在**每一次路徑查詢**上（`/a/b/c/x` 要列三個目錄的全部子項）。
  可行做法是把目錄 tag 拆成「身分」（走路徑時驗，O(1)）與「子項集合」（只在 `list_dir` 驗）兩層。
- **`ensure_root()` 在任何人認證之前就跑，那時沒有 master key**，所以 root 不可能在建立
  當下帶 tag。root 的身分可以合理豁免（`ROOT_ID` 是常數），但它的子項集合就不行。
- **子項集合的邊際價值精確地只有「偵測刪除」**——改名與搬移由檔案自己的 tag 就擋掉了，
  而「把刪掉的節點插回去」等於已拍板接受的整檔 rollback。這讓「不納入子項集合」的
  一致性論證比原先預期的強。

### 驗證方式

```bash
./venv/Scripts/python.exe -m pytest
```

343 passed（+7），約 30 秒，pyflakes 乾淨。另外在 production image 裡跑過同一份，
見上方第四節。實地驗證只做了設定值那條（第三節），**H1 的修法沒有在真實 Discord 故障下
驗證過**，理由見下。

---

## 未完成待辦

### 一、H2：檔名與位置不受完整性保護（`ROADMAP.md` `[next]`，**唯一阻擋上線的一條**）

方案在 `design-node-identity-integrity.md`。**§7 有四個必須先選的決策點**，
其中 **D1（目錄的子項集合要不要納入 tag）決定整件事是一輪還是兩輪的規模**，
而且選 (b) 會推翻一條既有拍板決策（`scandir` 不做完整性驗證）。

真正的成本是那支回填 migration：fail closed 是既有決策，沒有回填的話升級當下
**所有檔案立刻讀不出來**。方案 §5 列了它必須有的五條性質，其中最重要的是
**「寫新 tag 之前一定要先驗過舊 tag」**——對已被竄改的節點重算 tag 等於用真金鑰把
竄改結果洗白成合法。

### 二、repo 沒有 remote（`ROADMAP.md` `[next]`）

**你說要先開 GitHub MCP，本輪未動。**所有 commit 仍只存在這台機器的磁碟。

### 三、`SFTP_PASSWORD` 以明文環境變數注入（`ROADMAP.md` `[later]`，仍未拍板）

要嘛走 docker secret，要嘛明確寫成「已接受的風險」。目前兩者都不是，是預設狀態——
`BLUEPRINT.md` 那條的原話是「那應該是一個明確寫下來的決定，而不是預設」。

### 四、仍未被真實環境驗證的東西

- 5xx 重試與傳輸層重試從未被真實觸發（要 Discord 自己故障，無法從外部觸發）。
- 附件 URL 真的過期（24 小時後）沒有等過。
- **H1 的保護只在 fake 上驗證過**，與上面同一類——要在真實環境驗證得先讓 Discord
  對這個 bot 連續失敗五次以上。修好前的**症狀**也是只在 fake 上實證的。

---

## 資料狀態（本輪沒有動過，但寫清楚）

- **`nodes` 與 `keystore` 都沒有寫入。**本輪沒有透過 SFTP 上傳或刪除任何檔案。
- **Discord 上的附件數量未變。**沒有跑對帳（沒有動過，不需要）。
- `mongo_data` / `host_key_data` volume 未刪除，host key 沒換。
- **image 已重建**（3.12），服務重啟過數次。**線上那份 Argon2id 金鑰記錄在 3.12 下
  照常打得開**——啟動 log 無任何 WARNING，這順帶驗證了升版沒有動到 `argon2-cffi` 的行為。
- `.env` **未動**（測 `DISCORD_MAX_CONCURRENCY` 時是用命令列前綴傳的，沒有寫進檔案）。

---

## 本輪不可碰的範圍

- **加密與金鑰層一行未改**：`src/crypto.py`、`src/keystore.py`、`src/config.py` 都沒動。
  `node_tag` / `chunk_tag` 的涵蓋範圍**沒有改**——H2 只出方案沒動工。
- **`src/vfs.py` 只動了 `_rollback()` 一個方法**，沒有碰讀寫路徑、同步邏輯或 resize。
- **`src/sftp.py`、`src/discord_api.py`、`src/db.py`、`src/main.py` 一行未改。**
- **沒有 schema 變更、沒有 migration、沒有刪除任何資料。**
- **沒有 commit、沒有 push。**

---

## 下一步建議任務

1. **先 commit**。本輪改動已驗證（343 項 + production image 裡跑過一次），
   留在工作樹裡沒有好處，而且下一件事（H2）會動到同一批檔案。
2. **拍板 `design-node-identity-integrity.md` §7 的四個決策點**，特別是 D1。
   排在 remote 前面是因為它是唯一阻擋上線的一條，而且**愈晚做、要回填的節點愈多**——
   目前 `nodes` 幾乎是空的，是做這件事成本最低的時機（與 KDF 遷移那次的理由相同）。
3. **開好 GitHub MCP 之後推 remote。**在 H2 那種「跑錯一次全部讀不出來」的改動之前
   有一份異地備份，價值比平常高。

---

## 環境備忘

- venv 在 `venv/`（3.12.7）。一律用 `./venv/Scripts/python.exe -m pytest`（見 `SOP.md`）。
- **上線 image 現在也是 3.12**（3.12.13）。兩邊同版是刻意的，見 `ROADMAP.md` 拍板決策；
  改任何一邊之前先讀那條。
- repo 在 `master`，**沒有 remote**。
- 程式碼是烤進 image 的，改完 `src/` 一定要 `docker compose up -d --build`。
- **重啟服務目前不必先問**，見 `CLAUDE.md`「臨時例外」。那是一條有到期日的規則。
- MongoDB 的埠沒有對宿主開放，一次性腳本要走
  `docker compose exec -T sftp-discord-server python - < 腳本`（`SOP.md`）。
- 在容器裡跑測試要 `MSYS_NO_PATHCONV=1` 前綴（Git Bash 會改寫路徑參數，`SOP.md` 有這條），
  且要 `--user root`（image 內的 `appuser` 裝不了 dev 相依）與 `-p no:cacheprovider`
  （否則會在 repo 裡留下容器寫的 `.pytest_cache`）。
