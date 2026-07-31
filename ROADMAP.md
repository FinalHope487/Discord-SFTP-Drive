# Roadmap

新想法寫進來並標記，不當場插隊打斷目前任務。

## 標記說明

- `[now]` — 阻塞當前任務，不做就走不下去
- `[next]` — 目前模組穩定後就做
- `[later]` — 現在做屬於過早設計，等需求明確再說
- `[parked]` — 先記錄，暫不評估要不要做

---

## 已拍板的長期決策

<!-- 這些是問過使用者、往後都適用的決定，不要下一輪又拿出來重問 -->

- **完整性驗證採 per-chunk HMAC，不採 AES-GCM**（2026-07-29 決定）。
  加密層維持 AES-256-CTR 不動；HMAC-SHA256 存在 MongoDB 的 chunk metadata。
  理由：改動面積最小，且**竄改者即使控制 Discord 也改不到 tag**（tag 不在 Discord 上）。
- **不做向後相容**（2026-07-29 決定）。沒有 HMAC 欄位的 chunk 一律拒絕（fail closed），
  不保留「舊檔跳過驗證」的路徑——那條路徑本身就是降級攻擊面。既有測試資料直接清掉重跑。
- **主金鑰隨機產生、以密碼包裝，不由密碼直接推導**（2026-07-31 決定）。
  `.env` 不再有 `AES_SECRET_KEY`；資料用一把隨機主金鑰加密，主金鑰用 SFTP 密碼
  推導出的 KEK 包裝後存在 MongoDB 的 `keystore`。理由：直接由密碼推導的話，
  **改密碼＝所有資料永久讀不出來**；包裝之後改密碼只是重寫一份 32 bytes 的記錄，
  一個 chunk 都不用動。另外包裝用的 MAC 讓「密碼錯」與「資料壞了」可以區分開來。
  KDF 原為 PBKDF2-HMAC-SHA256（600k 次），**2026-07-31 已換成 Argon2id**，見下一條。
- **KDF 用 Argon2id，套件用 `argon2-cffi`**（2026-07-31 決定）。新的包裝一律用
  Argon2id（預設 64 MiB / t=3 / p=1）；**既有的 PBKDF2 記錄照樣打得開**，因為每份
  記錄自己帶著「是哪個函式、什麼成本做出來的」——這正是當初把 `kdf` 欄位寫進格式的理由，
  這次兌現了，一行 migration 都沒有。
  選 `argon2-cffi` 而不是 `cryptography` 44 內建的 Argon2id：後者要把 cryptography
  從 42.0.5 跨兩個 major 升上去，而 asyncssh 整個傳輸層坐在它上面，**爆炸半徑差太多**。
  實測 Argon2id 64 MiB/t=3 是 125ms，比原本 PBKDF2 600k 的 214ms 還快，登入路徑沒有變慢。
- **既有記錄的 KDF 升級是 opt-in，預設不動**（2026-07-31 決定）。`KDF_UPGRADE=0` 是預設；
  設成 1 才會在啟動時把既有記錄重新包裝成 Argon2id。理由：這是整個系統裡最危險的一次寫入
  ——寫壞不是壞掉一個檔案，是**所有位元組永遠讀不出來**。升級本身會先確認新記錄真的解得開
  才覆蓋舊的，但「什麼時候發生」仍然留給操作者決定，不由一次重啟自己拍板。
- **整檔 rollback 不做，列為已評估並接受的殘留風險**（2026-07-31 決定）。
  擋它需要一個持有資料庫的人碰不到的單調版本計數器。評估過三條路：釘在 Discord 本身、
  本機 append-only 檔、外部 KMS/TPM——**都不做**。這條缺口從此不再是待辦，是已知且接受的
  威脅模型邊界：能寫 MongoDB 的攻擊者可以把某個檔案換回它自己的舊版本，而且驗得過。
  其他所有竄改（重排、跨檔搬運、刪尾端 chunk、chunk 換洞、改內容）都擋得住。
- **金鑰是每連線的，不是每行程的**（2026-07-31 決定）。`validate_password` 解開主金鑰
  後放在該連線上，`DiscordVFS` 每條連線各一個。連線結束即釋放參照
  （Python 無法真的抹除 bytes，這是盡力而為，不是安全抹除）。
- **完整性 tag 的涵蓋範圍：內容，不含 metadata**（2026-07-31 決定）。
  chunk tag 綁 (file id, index, offset, size)；node tag 蓋 (file id, size, 有序 chunk tag 列表)。
  **權限位與時間戳刻意不在裡面**——它們不是內容，把它們納入會讓每次 chmod 都要重算
  一份蓋在完全沒動過的位元組上的 tag。
- **完整性 tag 現在涵蓋節點的身分與位置**（2026-08-01 決定並實作）。`node_tag` 加上
  `parent_id` 與 `filename`；目錄有自己的身分 tag（`dir_tag`）；目錄另有一個蓋住
  「子項集合」的 tag（`dir_entries_tag`），採**重算式**。四個決策點的選擇：
  D1=(b1) 納入子項集合／重算式、D2 同前、D3 加 `tag_version` 欄位、D4 不寫 migration。
  - **不做 migration 是因為時機**：拍板當下線上 `nodes` 只有 root、0 個檔案，
    沒有東西要回填。這與 2026-07-29「不做向後相容，既有測試資料清掉重跑」同一個道理。
    **代價講明白：從這一刻起寫入的資料就回不去了，下次再改 tag 涵蓋範圍就真的需要 migration。**
    `tag_version` 欄位就是為了讓那一次便宜——與 `kdf` 欄位讓 Argon2id 遷移「一行 migration
    都沒有」是同一個設計。
  - **`ensure_root()` 從啟動移到認證之後**。它要金鑰，而金鑰是每連線的（見下方拍板決策），
    啟動時根本沒有。順帶讓 root 不需要任何豁免——它就是一個普通的有 tag 的目錄。
    對既有的 pre-tag root，只在**它是空的**時候就地升級；非空就拒絕啟動並說明原因，
    因為對有內容的目錄重算 tag 等於用真金鑰把「已經發生的刪除」簽成合法。
- **「完整性檢查不涵蓋列目錄」這條決策沒有被推翻，而是被收窄**（2026-08-01）。
  `list_dir` 現在會驗**子項集合**（誰在裡面），但**不驗每個子項自己的 tag**。
  所以原本的理由仍然成立——一個被竄改的檔案不會讓整個目錄列不出來，你還是看得到它、
  刪得掉它——而「有人從資料庫刪掉一個節點」現在會被抓到。
- **`_rollback()` 只負責「本 handle 建立的檔案」，既有檔案一律不刪任何東西**（2026-08-01 決定）。
  兩個呼叫點（`src/vfs.py:530`、`:554`）本來就各自把自己那顆附件收乾淨了，所以對既有檔案
  而言 rollback 該做的清理**是零**。原本評估的做法是「記下開啟時的 message id 集合、
  rollback 還原成開啟時的快照」，**實作前發現那條會生出新的資料損壞路徑**：
  `_replace_chunk` 在 commit 成功之後才刪舊附件，所以開啟時的快照可能指向已經不存在的
  訊息，還原它等於寫進一個懸空引用、那個 chunk 永久讀不出來。
  **代價**：日後新增 `_rollback()` 呼叫點的人必須自己負責釋放附件，這條寫在 docstring 裡。
- **上線與測試跑同一個 Python 版本（3.12）**（2026-08-01 決定）。`Dockerfile` 從
  `python:3.11-slim` 升到 `python:3.12-slim`，對齊本機 venv 的 3.12.7。
  理由不是「版本不同」本身，是 `pytest.ini` 把 `src.*` 的 `DeprecationWarning` 設成 error
  ——那正是最容易在 minor 版本之間漂移的東西，而它只會在容器裡浮現、容器裡又沒有測試可跑。
  已實地驗證：343 項在 `python:3.12-slim`（3.12.13 / Linux）裡全過，四個編譯型相依
  全部有 cp312 wheel，image 仍不需要建置工具鏈。**沒有另外加 3.11 的測試環境**——
  兩邊同版之後那件事的價值就消失了。
- **此服務不可水平擴展，且只用文件擋，不加執行期守衛**（2026-08-01 決定）。
  `README.md` 與 `docker-compose.yml` 各寫明一段。評估過「Mongo 單例標記硬擋」與
  「心跳租約只記 WARNING」，**都不做**：硬擋會在 SIGKILL／斷電之後被自己的殘留記錄擋住，
  錯誤訊息還會指向一個不存在的副本，配上 `restart: on-failure:5` 就是重試五次然後放棄
  ——用一個新的失敗模式去換一個需要刻意觸發的誤用，不划算。
  **若日後搬上 k8s／Swarm（副本數是宣告式的、手滑成本低很多），這條要重新評估。**
- **檔案擴張採「稀疏尾端」，不實際補零**（2026-07-31 決定）。
  `size` 可以大於所有 chunk 的長度總和，中間那段是洞、讀回零、不佔 Discord 空間。
  **洞只會在尾端**；寫入落在 chunk 之後仍然實際補零（中間的洞沒有表示法）。
  理由是效能而不是省空間：真的補零會讓「先設定大小再上傳」的客戶端每個 SFTP 封包
  都落在檔案中間、各重傳一整塊 chunk，9MB 的 chunk 會被重傳數百次。
  這條由 `tests/test_truncate.py::test_presetting_the_size_does_not_change_the_upload_count`
  釘住，改回補零會直接讓它失敗。

---

## 項目

<!-- 格式：- [標記] 說明（可附上下文/來源 session） -->

<!-- 以下由 2026-07-31 的 BLUEPRINT.md 全掃產出，來源標記為 (BP) -->

- [next] **(BP) repo 沒有 remote**。`master` 上的 commit 全部只存在這台機器的磁碟。
  這個專案的價值有很大一部分在那些解釋「為什麼」的 commit message 裡，
  而它們目前沒有任何備份。推一個私有 remote 的成本是五分鐘。
  CI 可以之後再說——本機 suite 只要 40 秒，痛點沒那麼強。
  - **2026-08-01 狀態**：你說要先開 GitHub MCP，這條之後再 push。本輪未動。

- [later] **(BP) `SFTP_PASSWORD` 以明文環境變數注入，而它是唯一能開出 master key 的東西**。
  `docker-compose.yml:44` 把它注入成環境變數，所以 `docker inspect`、`/proc/<pid>/environ`
  都看得到。拿到宿主的人本來就贏了，所以這不是新開的洞——但它把「讀得到容器設定」
  直接升級成「解得開所有資料」，中間沒有任何一層。
  - 中期做法：docker secret（掛檔案而非環境變數），compose 加 `secrets:`、
    `config.py` 支援 `*_FILE` 後綴。改動不大。
  - **這條也可以合理地判定為「已接受的風險」**，但那應該是一個明確寫下來的決定
    （寫進「已拍板的長期決策」），而不是預設。**要不要接受由你拍板。**

- [parked] **(BP) chunk metadata 的 `index` 與 `offset` 互為冗餘**——chunk 從 0 連續、
  大小固定（末塊除外），兩者可互推。**評估結論是不動**：冗餘已經被 `chunk_tag` 保護
  （不一致會被抓到），而拿掉 `index` 要改 tag 的涵蓋範圍，那等於觸發一次全檔重算，
  代價遠大於收益。記在這裡是為了下次不要再想一遍。

- [later] **重新產出 `BLUEPRINT.md`**。它以 commit `5968362` 為準，之後 H1 與 H2 都落地了，
  §3 / §4 描述的 tag 涵蓋範圍與 `_rollback()` 行為都已不同。目前靠開頭的狀態橫幅與逐條註記
  撐著，那是權宜，不是長久做法——`/blueprint` 跑一次就好。

- ~~[next] **檔名與位置不受完整性保護**~~ — **已實作，見下方「本輪第七段」。**
  以下原文保留，因為它記錄了當初對規模的評估，而實際做起來與評估有出入（見第七段）。
  <details><summary>原文</summary>

- [was next] **檔名與位置不受完整性保護——能寫 MongoDB 的人可以改名、搬檔，而且驗得過**。
  `node_tag` 蓋的是 `(id, size, 有序 chunk tags)`，**`filename` 與 `parent_id` 都不在裡面**
  （見 `src/crypto.py` 的 `node_tag()`）。所以：
  - 把 `secret.txt` 改名成 `boring.txt`、或搬到別的目錄 → `_verify_node` 照樣通過。
  - 目錄節點根本沒有 tag（`_verify_node` 對 `is_dir` 直接放行），所以**憑空塞一個檔案進
    某個目錄**也沒有東西擋——只要那個檔案自己的 chunk/node tag 是拿真金鑰算的
    （例如把攻擊者自己上傳的檔案「移植」進被害者的目錄）。
  - 唯一擋得住的是內容本身：改名後讀出來的位元組仍然是原本那些。
  這比原本 `[later]` 那條「權限位與時間戳不受保護」嚴重得多，2026-07-31 讀 `node_tag`
  時發現，**不是既有文件記過的東西**。
  - **方案已產出：`design-node-identity-integrity.md`（2026-08-01），但尚未拍板要不要做。**
    該文件 §7 有四個必須先選的決策點，其中 D1（目錄的子項集合要不要納入 tag）決定了
    整件事的規模是「一輪」還是「兩輪」。**未拍板前不動工。**
  - 寫方案時發現兩件原本沒展開的事，都寫在該文件裡：
    (1) 驗證目錄的子項集合必須從實際子項重算，所以成本會落在**每一次路徑查詢**上
    （`/a/b/c/x` 要列三個目錄），必須把目錄 tag 拆成「身分」與「子項集合」兩層才可行；
    (2) `ensure_root()` 在任何人認證之前就跑，**那時沒有 master key**，所以 root 不可能
    在建立當下帶 tag。
  - 另一個發現：子項集合的邊際價值**精確地只有「偵測刪除」**——改名與搬移由檔案自己的
    tag 就擋掉了，而「把刪掉的節點插回去」等於已拍板接受的整檔 rollback。這讓 D1 選 (a)
    （不納入子項集合）的一致性論證比原先預期的強。
  </details>
- [later] **跨 handle 的 metadata 變更仍不同步**（本輪內容同步的殘留缺口）。
  `_node_versions` 比對的是 `mac`，而 `mac` 刻意不涵蓋權限位與時間戳
  （見上方拍板決策），所以另一條連線的 `chmod` / `utimes` **不會**觸發重新抓取，
  開著的 handle 會繼續回報舊的 mode 與 mtime。內容（size / chunks）已經同步了。
  要一併涵蓋就得替 metadata 另開一個版本欄位，或讓 `mac` 蓋住 metadata——
  後者已經被拍板否決過。實務衝擊小：SFTP 客戶端不會邊開著檔案邊等別人改權限。
- [later] **真正同時寫入的競態仍是後寫的贏**。本輪同步的保證是「單一 handle
  在每次操作前看得到別人**已經 commit 完**的狀態」，不是寫入互斥。兩條連線同時
  對同一個檔案寫，後寫的仍會蓋掉先寫的——POSIX 對此本來也不保證原子性。
  要真的擋需要 node 層級的樂觀鎖（`update_one` 帶上舊 `mac` 當條件）。
- [later] **路徑版 `stat` 看不到別的 handle 還在 buffer 裡的位元組**。同一 handle 的
  `fstat` 已修；跨 handle 的沒修，也修不乾淨——那些位元組還沒上傳，本來就不該對別人可見。
- [later] **完整性檢查不涵蓋列目錄**。`stat` / `open` / `rename` / `remove` 都會驗，
  `scandir` 不驗——刻意的，否則一個被竄改的檔案會讓整個目錄列不出來。代價是
  `ls -l` 顯示的大小未經驗證，但真的去讀或 stat 它就會失敗。
- [later] **權限位與時間戳不受完整性保護**（見上方拍板決策）。能改 MongoDB 的人可以改它們。
- [later] **不支援符號連結**（你本輪選擇不做）。`symlink` / `readlink` / `link` 回 FX_OP_UNSUPPORTED。
- [later] 多使用者與各自獨立的 VFS 樹；目前是單一帳號共用同一棵樹。
  **方案已產出：`design-multi-user.md`（2026-07-31）**，但**尚未拍板要不要做**。
  方案裡有三個決策點還沒選（帳號存哪、金鑰隔離到什麼程度、既有單一使用者怎麼遷移），
  要開工前先把那三個選完。
- [parked] chunk 壓縮與去重。

---

## 本輪第七段（2026-08-01）完成並移除的項目

> **H2 落地：完整性 tag 現在涵蓋檔名、所在目錄與目錄的子項集合。**
> 371 項自動化測試全過（+28），pyflakes 乾淨，production image 內同樣 371 過。
> **實地驗收 17/17**（真實 bot token、真 MongoDB、12MB 檔案、直接竄改資料庫），
> 收尾對帳 **0 孤兒、0 懸空引用**。

- ~~[next] 檔名與位置不受完整性保護~~ — 已實作。`node_tag` 加上 `parent_id` / `filename`，
  新增 `dir_tag`（目錄身分）與 `dir_entries_tag`（子項集合），全部節點帶 `tag_version`。
  決策與理由見上方拍板決策。**實際做起來與方案有四處出入，每一處都值得記下來：**
  - **方案漏了一個崩潰視窗，補了兩階段寫入。**目錄的 tag 與它的子項是不同的文件，
    standalone MongoDB 沒有 transaction，所以「改子項」與「更新目錄 tag」不可能一起發生。
    先寫 tag 會讓崩潰看起來像多了一個子項，後寫則像少了一個——兩邊都會讓一個**沒有被攻擊**
    的目錄從此 `ls` 不出來。改成把新 tag 先存進 `entries_mac_pending`、做完變更才升為正式，
    驗證同時接受兩者。這不削弱任何東西：兩個值都是持有金鑰的程式碼算出來的。
  - **`get_node()` 原本只驗最後一段路徑。**方案假設「走路徑時每段都驗」，但既有實作
    只對終點呼叫 `_verify_node`。所以改名一個目錄之後，`/public/keys.txt` 完全驗得過
    ——檔案自己的 tag 記的是父目錄的 **id**，改名沒動到。單元測試抓到的。
  - **`scandir` 繞過了 `list_dir`，實地驗收才抓到。**SFTP 的列目錄直接呼叫 `children()`，
    所以子項集合的保護「對直接呼叫 VFS 的人有效、對真正的 SFTP 客戶端無效」。
    單元測試因為直接驅動 `list_dir` 而全部通過。**這是這輪最值得記的一條**：
    這個 suite 當初就是為了抓這種「協定層沒接上」的錯而存在的，而我又踩了一次。
    已補 `entries_of()` 讓兩條路徑共用，並加了走真實協定的迴歸測試。
  - **root 不需要豁免。**方案假設 root 得特殊處理（`ensure_root()` 沒有金鑰）。
    把它從啟動移到認證之後，root 就只是一個普通的有 tag 的目錄，特例整個消失。
- ~~[later] 失敗過的 handle 在「讀」這一側沒有一致的語意~~ — 已修。`size` 在失敗後回報
  已 commit 的長度（不再把永遠不會落地的 buffer 算進去），`read_at` 不再 flush 失敗的 handle
  （否則 Discord 剛好復原時會把一次已回報失敗的寫入悄悄復活）。已 commit 的內容照樣讀得到。
- ~~[later] `_url_cache` 沒有上限~~ — 改成有上限的 LRU（4096 筆）。註解寫明**為什麼這個
  快取淘汰是安全的而 `_node_versions` 不是**：這裡查不到只是多打一次 API，那裡查不到
  等於「沒人改過」，是錯的答案而不是慢的答案。
- ~~[later] `_to_sftp_error` 的死分支~~ — 刪掉，改成一段說明「SFTP v3 沒有更精確的碼」的註解。

## 本輪第六段（2026-08-01）完成並移除的項目

> **BLUEPRINT 全掃找出的 H1 / M2 / M3 / M4 四條，加上 README 與水平擴展告示。**
> 343 項自動化測試全過（+7），pyflakes 乾淨，且**整份 suite 已在上線用的 image
> 裡跑過一次**（`python:3.12-slim` / 3.12.13 / Linux）。

- ~~[now] (BP) `_rollback()` 會刪光既有檔案~~ — 已修。**實作前推翻了原本評估的做法**：
  ROADMAP 與 BLUEPRINT 建議的 (A)「還原成開啟時的快照」會生出新的資料損壞路徑，
  因為 `_replace_chunk` 在 commit 成功之後才刪舊附件，快照可能指向已不存在的訊息。
  改採「既有檔案一律不刪任何東西」，理由與代價見上方拍板決策。
  - **順帶查清楚一件事**：兩個呼叫點本來就各自釋放了自己那顆附件，所以 `_rollback()`
    對既有檔案該做的清理是零——它原本做的每一件事都是純粹的破壞。
  - 迴歸測試在修好前後各跑一次：**舊程式碼下三個測試失敗，新程式碼下全過。**
- ~~[next] (BP) 失敗路徑零測試覆蓋＋`FakeDiscord` 缺失敗注入~~ — 已補。
  `FakeDiscord.fail_uploads_from`（第 N 次上傳起開始失敗，計數含失敗的那次），
  新增 `tests/test_write_failures.py` 7 項，涵蓋：新建檔案失敗要整個消失、
  **既有檔案 append 失敗不得損失既有內容**（H1 迴歸）、隨機寫入失敗、
  metadata 寫入失敗要釋放自己剛上傳的附件、truncate 過的檔案失敗後留空、
  失敗 handle 拒絕後續寫入與 truncate 且 close 不會 flush、以及**一條走完整 SFTP
  協定的 H1 迴歸測試**（`put -a` 遇上 Discord 故障）。
  - 一個上傳失敗代表整組重試預算已耗盡（真實 client 內部重試 5xx 與傳輸層例外後才拋），
    這寫在 fake 的註解裡，免得下一個人以為它模擬的是單次失敗。
- ~~[next] (BP) `DISCORD_MAX_CONCURRENCY` 在 compose 下無效~~ — 已修，一行。
  17 個設定值逐項比對過，確認只漏這一個。**實測驗證**：設 `DISCORD_MAX_CONCURRENCY=2`
  重啟後容器內 `config.discord_max_concurrency()` 回 2（修之前永遠是 4）。
- ~~[next] (BP) 測試跑 3.12.7、上線跑 3.11~~ — 已對齊到 3.12，見上方拍板決策。
  **不只是改 Dockerfile**：整份 suite 已實際在該 image 裡跑過，343 項全過。
- ~~[next] (BP) 沒有 `README.md`~~ — 已補。是什麼、怎麼跑起來、怎麼跑測試（含在
  production image 裡跑的指令）、各份文件各自負責什麼、以及誠實的現況與已知缺口。
- ~~[next] (BP) 跨 handle 同步是 process 級的，沒有東西擋你起第二個副本~~ — 已擋（以文件）。
  `README.md` 一節、`docker-compose.yml` 服務上方一段，都寫明症狀是**無聲的**
  （沒有錯誤、沒有 log，只有舊位元組）。執行期守衛評估後不做，見上方拍板決策。

## 本輪第五段（2026-07-31）完成並移除的項目

> **KDF 換成 Argon2id**，以及**整檔 rollback 拍板不做**。
> 336 項自動化測試全過（+26），pyflakes 乾淨。
> **已上線並完成實地遷移**：image 已重建，線上那份 keystore 記錄已從
> PBKDF2 搬到 Argon2id，收尾對帳 0 孤兒、0 懸空引用。

- ~~[later] KDF 換成 Argon2id~~ — 已實作。新增 `argon2-cffi==25.1.0`，
  `KDF` / `ARGON2_TIME_COST` / `ARGON2_MEMORY_KIB` / `ARGON2_PARALLELISM` 四個新設定值，
  預設 `argon2id` 64 MiB / t=3 / p=1。理由與套件選擇見上方拍板決策。
  - **「不需要 migration」這句話這次被兌現並釘住了**：`derive_kek` 從記錄裡讀函式名與成本，
    不假設當前預設值；`test_a_pbkdf2_record_still_opens_after_the_default_moved` 釘住這條。
    另外實地確認過線上那份記錄的形狀就是 `pbkdf2-sha256 / kdf_iterations=600000`。
  - 成本參數改成「缺一個就拒絕」而不是補預設值——補預設會推出一把不同的金鑰，
    然後以「密碼錯誤」的形式浮現，那是最糟的一種報錯方式。
  - `KDF_UPGRADE`（預設關）才會把既有記錄重新包裝；覆蓋前會先驗證新記錄真的解得開。
  - **實地上線與遷移已完成（2026-07-31）**，分兩次重啟做，不合併成一次：
    1. 先只上線新程式碼（`KDF_UPGRADE=0`）。確認 `argon2-cffi` 25.1.0 在
       `python:3.11-slim` 裡直接裝得起來（manylinux wheel，不需編譯工具鏈），
       且**舊的 PBKDF2 記錄照常打得開**、服務照常起來、預期的 WARNING 有出現。
    2. 確認無誤才 `KDF_UPGRADE=1` 重啟做遷移，然後關回 `0`。
    **驗證方式是 canary**：遷移**前**用 SFTP 上傳一個 256 KiB、內容由固定種子產生的
    檔案，遷移**後**讀回來比對——位元組完全相同，證明 master key 原封不動。
    種子固定是刻意的：驗證端自己重新產生一次期望值，不去信任遷移前那一端寫下的東西。
    遷移後的記錄是 `argon2id / 64 MiB / t=3 / p=1`，`kdf_iterations` 整個消失
    （整筆置換而非合併）。收尾清掉 canary，對帳 **0 孤兒、0 懸空引用**。
    **刻意不做記錄備份**：`_replace_wrapping` 覆蓋前已先確認新記錄解得開，
    而留一份 PBKDF2 包裝的同一把金鑰會直接抵銷這次升級——攻擊者挑弱的那份打就好。
- ~~[next] 整檔 rollback~~ — **拍板不做**，改列為已接受的殘留風險，見上方拍板決策。

## 本輪第四段（2026-07-31）完成並移除的項目

> **真實環境驗證 Discord 429**（`src/ratelimit.py`），連續四輪記著、
> 一直沒被真實環境觸發過的一條，本輪補上。

- ~~仍未被真實 429 驗證過的 rate limit bucket~~ — 已驗證。用真實 bot token
  對真實 SFTP 服務灌 50 個 200 bytes 小檔案、序列上傳無延遲，兩個機制都被
  真實觸發：
  - **主動節流**（讀 `X-RateLimit-Remaining` / `X-RateLimit-Reset-After`）
    在上傳（`POST .../messages`）路徑上生效，讓大多數請求**沒有**真的撞到 429——
    可觀察到的訊號是部分檔案耗時從約 0.5s 跳到 4.5～5s（等待 bucket 重置），
    而不是錯誤或重試 log。
  - **真實 429 與重試**在清理（`DELETE .../messages/{id}`）路徑上被觸發：
    這條路由的 bucket 在迴圈一開始還沒被學到，所以前幾個刪除跑得比真實限流快，
    7 次真的收到 Discord 的 429，`retry_after`（實測 0.3s～0.602s）被正確讀出、
    log 記成 WARNING、等待後重試，最終 50 個檔案全部刪除成功。
  - 50 個檔案上傳後全數 `stat` 核對 size 正確，清理後**對帳 0 孤兒、0 懸空引用**。
  驗證腳本與對帳腳本皆為 scratchpad 一次性腳本，未留在 repo（沿用既有慣例）。
- [parked] **（本輪順帶發現，未修）SFTP 用戶端正常斷線時偶爾被記成 WARNING**：
  `src/sftp.py` 的 `connection_lost()` 只把 `None` 與 `ConnectionResetError`
  視為正常斷線；本輪驗證腳本用 `async with asyncssh.connect(...)` 正常關閉連線時
  觸發了另一種例外型別，被記成 `WARNING SSH connection error: Connection lost`。
  純粹是 log 噪音（功能本身無誤——所有上傳/清理/對帳結果都正確），本輪僅第一次
  觀察到、按規則不在第一次出現時處理，先記錄。若之後又踩到，再回來查究竟是
  哪個例外型別、決定是否要擴大 `connection_lost()` 判斷的白名單。

## 本輪第三段（2026-07-31）完成並移除的項目

> **實地驗收 10/10 通過**（真實 bot token、真 MongoDB、真 SFTP、12MB 檔案）。
> 收尾對帳：Discord 0 個附件、0 個被引用、**0 孤兒**。

- ~~[next] 跨 handle 的狀態不同步~~ — 已修。上一輪實地確認的情境
  （連線 B 截短後，連線 A 的 handle 仍回報舊 size，並在新檔尾之後**讀得到舊資料**）
  這輪實地重跑：A 的 `fstat` 回報 4096、新檔尾之後讀回 EOF 而不是 1024 bytes 舊明文。
  做法是 process 內的 `_node_versions`（node id → 最後 commit 的 `mac`）：
  handle 在每次 read / write / truncate 前先比對，**相符就什麼都不做**，
  只有真的被別人改過才付一次 `find_one` 並重新驗章、清快取。
  所以無衝突時零額外 DB 查詢（有測試釘住），這對序列上傳很重要——
  `fstat` 每個 SFTP 封包都會被呼叫。
  **這個字典刻意不設上限**：查不到會被當成「沒人改過」，所以 LRU 淘汰等於把這個
  bug 在記憶體壓力下悄悄放回來。代價是每個碰過的 node 約 100 bytes。
  殘留缺口兩條（metadata 不同步、真正的同時寫入競態）已改列 `[later]`，見上。

## 本輪後半（2026-07-31）完成並移除的項目

> **實地驗收 18/18 通過**（真實 bot token、真 MongoDB、真 SFTP）。
> 收尾對帳：Discord 0 個附件、0 個被引用、**0 孤兒**。
> 驗收過程找到**兩個單元測試結構上抓不到的 bug**，見下方兩條。

- ~~（實地驗收找到）唯一索引無法升級，服務直接起不來~~ — 已修。
  MongoDB **不會就地把既有的非唯一索引改成唯一**，會回 `IndexKeySpecsConflict`。
  單元測試對假的 collection 跑當然不會遇到，**升級既有部署時服務直接拒絕啟動**。
  `src/db.py` 現在會偵測衝突、卸掉舊索引、重建為唯一；若資料本身已有重複，
  會把舊索引放回去並給出一則說明「哪些重複要人工處理」的錯誤，而不是留下沒有索引的集合。
- ~~（實地驗收找到）SIGTERM 沒有真的 flush~~ — 已修。原本靠 asyncssh 在 session 結束時
  對每個 handle 呼叫 `close()`，**但那個 cleanup 不在 `conn.wait_closed()` 的等待範圍內**，
  所以行程在上傳還在飛的時候就結束了，客戶端最後不滿一個 chunk 的資料整個消失。
  log 看起來完全正常（「Shutdown complete」照印），只有真的去讀那個檔案才會發現。
  現在 `src/sftp.py` 會追蹤開著的 handle，`_drain()` 在關連線**之前**明確 flush 它們。
  **原本的單元測試是靠時序巧合過的**（斷言寫在後面，剛好讓 cleanup 有機會跑完），
  已改成在 `_drain()` 回傳的當下就斷言，不留巧合空間。


- ~~[next] 測試又更慢了（208 秒 / 197 項）~~ — **208 秒 → 23 秒**，302 項。
  根因不是 fixture 結構：asyncssh 預設會用 `socket.getfqdn()` 算 GSS 主機名，
  這台機器的反向 DNS 每次要 1.04 秒，而每個測試呼叫兩次（listen 一次、connect 一次）。
  設 `gss_host=None` 同時關掉了本來就不打算提供的 GSSAPI 認證路徑。
  **原本計畫的「把 server 改成 module-scoped」不需要了。**
- ~~[next] `_request` 對 5xx 不重試~~ — 已補 500/502/503/504 與傳輸層例外的重試，
  指數退避＋抖動，具名的 `DiscordAPIError`，明確的逾時設定。4xx 仍然不重試。
- ~~[next] 附件 URL 過期~~ — 依 `ex=` 快取並在過期前重新解析；真的被 CDN 以 403/404
  拒絕時再解析一次重試。順帶：下載改用不帶 bot token 的獨立 session。
- ~~[next] HMAC 未涵蓋 chunk 位置 / 洞沒有 tag~~ — chunk tag 現在綁
  (file id, index, offset, size)，另加 node tag 蓋 (file id, size, 有序 chunk tag 列表)。
  重排、跨檔搬運、刪尾端 chunk、chunk 換洞全部會被抓到。剩下的只有整檔 rollback（見上）。
- ~~[later] PBKDF2/Argon2 動態金鑰（`todo.md`）~~ — 已實作，但**改成金鑰包裝**而不是
  直接推導，理由見上方拍板決策。
- ~~[later] 斷點續傳與下載進度紀錄（`todo.md`）~~ — **不實作，前提不成立**。
  SFTP 讀取是無狀態的 offset 讀，寫入的 chunk 一上傳就寫進 Mongo，斷線時 asyncssh
  的 session cleanup 會呼叫 `close()` 把 buffer flush 掉——所以檔案大小就是續傳點。
  已用測試釘住（`test_an_interrupted_upload_can_be_resumed_by_appending`）而不是加程式碼。
- ~~（新增）POSIX metadata~~ — 權限位與 mtime/atime 現在會保存。順帶修掉兩個既有錯誤：
  `close()` 會蓋掉客戶端剛設定的 mtime（`put -p` 的實際流程），以及 rename 會重設 mtime
  （移動檔案不是修改檔案）。目錄的 mtime 現在會在內容變動時更新。
- ~~（新增）檔名唯一索引~~ — `(parent_id, filename)` 改為 unique。
- ~~（新增）優雅關機~~ — SIGTERM 會停止接受新連線、給既有 session 20 秒完成、
  再關閉它們（這會觸發 handle 的 flush）。所有等待都有上限。
  `docker-compose.yml` 的 `stop_grace_period` 一併設成 30s。
- ~~（新增）log 噪音~~ — 正常斷線不再記成 WARNING。

## 本輪前半（2026-07-31）完成並移除的項目

- ~~[next] `setstat` / `fsetstat` 的 size 變更仍被拒絕~~ — 已實作。截短為
  `_resize_node()`（丟掉超出範圍的 chunk、跨邊界那塊換新 nonce 重傳縮短版）；
  擴張改採稀疏尾端（見上方拍板決策）。`O_TRUNC` 的舊路徑併入同一個函式。
  新增 `tests/test_truncate.py` 38 項，9 個突變全部被抓到。
- ~~（本輪順帶發現）`fstat` 少報還在 buffer 裡的位元組~~ — 已修。
  asyncssh 的 `read()` 不帶長度時會先 `fstat` 決定要讀多少，所以少報的後果不是
  「數字難看」，而是**客戶端剛寫完的資料直接被回 EOF、無聲消失**。
  這個缺陷在上輪隨機寫入時就已經在了，只是沒有測試踩到它。
- ~~本輪缺實地驗收~~ — 已補（2026-07-31）。真實 bot token、DM 模式、20MB 檔案，
  **15/15 通過**：預先設定大小的上傳耗時 9.7s vs 一般上傳 10.0s（比值 0.97，
  補零實作會是數十倍）、chunk 佈局完全相同、擴張到 500MB 上傳 0 則訊息且耗時 0.01s、
  截短後被取代的附件在 Discord 上真的 404、稀疏檔案重新連線後讀回零。
  收尾對帳：Discord 9 個附件、MongoDB 引用 9 個、**0 孤兒**。

## 上輪（2026-07-29）完成並移除的項目

- ~~[now] 設定值的可達性檢查~~ — 已實作於 `src/discord_api.py` 的 `check_reachability()`，`main.py` 在開 socket 前呼叫。真實憑證與壞 token 兩條路徑都實測過。
- ~~[next] 隨機寫入~~ — 已實作於 `DiscordFile._write_random()`。含補零、跨 chunk、延伸檔尾。
- ~~[next] Discord rate limit bucket~~ — 已實作於新檔 `src/ratelimit.py`，讀 `X-RateLimit-*` 並加上並發上限 `DISCORD_MAX_CONCURRENCY`。
- ~~[next] 容器以 root 執行~~ — `Dockerfile` 改用 uid 10001 的 `appuser`，volume 權限一併處理。
- ~~[next] `docker compose logs` 會印出 SFTP 密碼？~~ — 已實測稽核：密碼、AES 金鑰、bot token、Mongo 密碼皆未出現在 log。只有 SFTP 帳號會出現（asyncssh 的 `Beginning auth for user X`，與 sshd 慣例相同，非機密）。
- ~~[now] 完整性驗證~~ — 已依拍板方案實作 per-chunk HMAC-SHA256（encrypt-then-MAC，蓋 `nonce||ciphertext`，MAC 金鑰以 HKDF 從 `AES_SECRET_KEY` 導出）。既有測試資料已清空重跑。實地驗證：竄改 MongoDB 裡的 tag → 讀取被拒並留下 `Integrity check failed` log。
- ~~（本輪順帶發現）host key 權限是 0644~~ — 已修成 0600 並會自動修復既有金鑰。
