# Roadmap

新想法寫進來並標記，不當場插隊打斷目前任務。

## 標記說明

- `[now]` — 阻塞當前任務
- `[next]` — 目前模組穩定後就做
- `[later]` — 現在做屬於過早設計，等需求明確再說
- `[parked]` — 先記錄，暫不評估

---

## 待辦項目

<!-- 格式：- [標記] 說明（可附上下文/來源 session） -->

- [next] **上傳失敗要回報三個數字**（2026-08-05 從 `client/backend-todo.md` 移入）。
  `PUT /api/file` 失敗時目前只回一個訊息。`_rollback()` 只刪這個 handle 建立的附件，
  而 HTTP 是新的呼叫點，所以「Discord 那側沒有殘留」這件事要能被證明而不是被相信：
  ```
  → 5xx {"error": "upload_failed", "chunks_uploaded": int,
         "attachments_released": int, "orphans": int, "detail": str}
  ```
  `orphans != 0` 是要人去對帳的狀況，不是按一下重試就好，前端要換一種說法。
  **前端目前不假造這些數字**——它顯示伺服器真的說了什麼，所以這是補強而不是修 bug。

- [next] **批次刪整棵樹撞 429 要能中斷續跑**（2026-08-05 從 `client/backend-todo.md` 移入）。
  刪大目錄會撞 Discord 的速率限制。需要伺服器在過程中吐出進度（SSE 或輪詢一個 job id），
  前端才畫得出可中斷的進度條。**已刪掉的不會回來**，這句話要在介面上說。
  原型畫過這個狀態，這一版**沒有做進去**——沒有後端支撐的進度條是動畫，不是進度。
  多使用者第 4 步（刪帳號）會再掀出這一條。

- [later] **全包版 .exe：把後端也塞進去**（2026-08-05 列入，方案二）。
  這一輪選的是方案一：exe 只是視窗，後端仍是 `docker compose`。方案二是 PyInstaller 打包
  Python、換掉 MongoDB 改用內嵌資料庫，雙擊即用、不需要 Docker。
  **代價已知**：`docker-compose.yml` 的 `127.0.0.1:8080:8080` 是目前唯一擋住外部連線的邊界，
  塞進 app 之後那條邊界不存在；每台裝置各自一份資料互不相通；等於重寫儲存層。
  **要做的前提**是先想清楚「一台裝置一份資料」到底是不是想要的語意——如果是，那它其實是
  另一個產品，不是這個服務的打包方式。

- [now] **Client UI**（2026-08-02 列入）。原本唯一的對外介面是 SFTP，`src/` 裡沒有 HTTP 層，
  所以這不是「加一個前端」，是先長出一層 API。
  - ~~1. `users` collection 與密碼雜湊~~ **已完成**（`src/users.py`）。Argon2id `PasswordHasher`，
    與 `derive_kek` 各走各的；帳號不存在與帳號停用都照樣跑一次假驗證。
  - ~~2. `keystore` 改 per-user~~ **已完成**。`adopt_legacy_record()` 只動 `id` 一個欄位、不重包。
  - ~~3. root 改 per-user~~ **已完成**。`DiscordVFS(key, root_id)`，`root_id` 無預設值；
    `extra_info` 改為帶 key／root_id／username 的 `users.Session`。
  - ~~4. HTTP API 層~~ **已完成**（`src/web.py` / `websession.py` / `webauth.py`）。
    `aiohttp` app 掛在 `main.py`，與 SFTP server 同 process、共用 `_node_versions`。
  - ~~5. 前端~~ **已完成**（2026-08-05，`client/app/`）。Vite + React，由 `web.py` 的
    static route 吐出來。同一輪加了 `GET /api/search`、`/api/session` 的雙期限與連線數、
    `POST /api/sessions/revoke-others`，以及可辨識的 `integrity_failure` 錯誤碼。
  - ~~6. 桌面外殼~~ **已完成**（2026-08-05，`client/shell/`）。Electron，產出可攜版與安裝檔
    兩種 `.exe`。打包步驟在 `BUILD.md`。

- [later] **重新產出 `BLUEPRINT.md`**。它以 `e288ff7` 為準，之後 H1 與 H2 都落地了，
  §3 / §4 的 tag 涵蓋範圍與 `_rollback()` 行為都已不同，目前靠開頭橫幅與逐條註記撐著。
  **等 Client UI 落地後再跑** `/blueprint`——UI 會動到認證、金鑰與樹根，現在重產出會立刻過期。

- [later] **`SFTP_PASSWORD` 走 docker secret**。**2026-08-02 拍板要做，排在 Client UI 之後**；
  維持 `[later]` 只是排序，不是還在評估。現在它是明文環境變數，`docker inspect` 與
  `/proc/<pid>/environ` 都看得到，等於把「讀得到容器設定」直接升級成「解得開所有資料」。
  做法：compose 加 `secrets:`、`config.py` 支援 `*_FILE` 後綴。

- [later] **多使用者的第 4 步：真正開放第二個帳號**——管理 CLI、建帳號流程、per-user 配額。
  第 1～3 步已隨 Client UI 落地。方案見 `design-multi-user.md`，三個決策點已於 2026-08-02
  全部拍板（見下方）。這一步會同時掀出配額與刪帳號（批次刪整棵樹會撞 429，要走既有 rate limit
  路徑且可中斷續跑），且**必須先有密碼救援路徑**。**分享明確不做。**

- [later] **跨 handle 的 metadata 變更不同步**。`_node_versions` 比對 `mac`，而 `mac` 不涵蓋
  權限位與時間戳，所以另一條連線的 `chmod` / `utimes` 不會觸發重新抓取。內容（size / chunks）
  已同步。要涵蓋就得替 metadata 另開版本欄位，或讓 `mac` 蓋住 metadata——後者已被否決。

- [later] **真正同時寫入的競態是後寫的贏**。跨 handle 同步保證的是「每次操作前看得到別人
  已 commit 完的狀態」，不是寫入互斥。要擋需要 node 層級的樂觀鎖（`update_one` 帶舊 `mac`
  當條件）。**這也是「只能跑一個副本」的根因**，見 `README.md`。

- [later] **路徑版 `stat` 看不到別的 handle 還在 buffer 裡的位元組**。同一 handle 的 `fstat` 已修；
  跨 handle 的不修——那些位元組還沒上傳，本來就不該對別人可見。

- [later] **權限位與時間戳不受完整性保護**（見下方決策）。能改 MongoDB 的人可以改它們，
  這是 tag 明確沒有涵蓋的唯一一類 metadata。

- [later] **不支援符號連結**。`symlink` / `readlink` / `link` 回 FX_OP_UNSUPPORTED。

- [parked] **SFTP 用戶端正常斷線偶爾被記成 WARNING**。`src/sftp.py` 的 `connection_lost()`
  只把 `None` 與 `ConnectionResetError` 視為正常斷線；`async with asyncssh.connect(...)`
  正常關閉會觸發另一種例外型別。純 log 噪音，功能無誤。2026-07-31 第一次觀察到。

- [parked] **chunk metadata 的 `index` 與 `offset` 互為冗餘**。**評估結論是不動**：冗餘已被
  `chunk_tag` 保護，拿掉 `index` 要改 tag 涵蓋範圍，等於觸發全檔重算。記在這裡是為了不再想一遍。

- [parked] chunk 壓縮與去重。

---

## 已拍板的長期決策

<!-- 這些是問過使用者、往後都適用的決定，不要下一輪又拿出來重問 -->

- **垃圾桶：標記在節點上，且納入完整性標籤**（2026-08-03）。節點加 `trashed_at`、原地不動，
  所以還原沿 `parent_id` 往上走就有原始路徑。`node_tag` 與 `dir_tag` 都涵蓋這個欄位，
  網域分隔字串改為 `node3` / `dir2`，`TAG_VERSION` 2→3。理由：放在標籤外面等於讓有 DB 權限的人
  無聲隱藏任意檔案。當時線上只有 root，重算成本近乎零。
  - **被過濾掉的子項必須當場驗自己的標籤**（`entries_of`）。已刪除的節點永遠不會再被路徑解析
    取出，標籤就永遠沒機會被檢查。活著的子項不需要——它開啟時會驗。
  - **(parent_id, filename) 的唯一索引改為部分索引**，只涵蓋活著的節點，
    否則「刪掉再建同名檔案」會撞 duplicate key。因此**活著的節點不帶這個欄位**（還原走 `$unset`）。
    - **2026-08-05 更正**：條件原本寫成 `{"trashed_at": {"$exists": False}}`，**MongoDB 直接拒絕**
      ——`$exists: false` 不在 partialFilterExpression 允許的文法內（回
      `Expression not supported in partial index: $not`），索引根本沒建成，伺服器起不來。
      改成 `{"trashed_at": None}`：對 null 的等值比對**同時匹配 null 與缺欄位**，是同一組文件，
      而且在文法內。**寫入格式完全沒變**，活著的節點仍然不帶這個欄位。
      這個 bug 從 2026-08-03 存在到今天沒被發現，因為 `FakeDB` 不驗證索引規格
      ——見 `SOP.md`。
- **保留期 30 天，到期由背景 sweeper destroy，可中斷**（2026-08-03）。
  `TRASH_RETENTION_DAYS` / `TRASH_SWEEP_SECONDS` / `TRASH_SWEEP_BATCH`。
  **sweeper 借用 session 的金鑰，不自己持有**——為了背景任務讓進程長期持有主金鑰是安全上的倒退。
  代價：保留期的語意是「至少這麼久」，沒人登入時不會清。
- **SFTP 的 `rm` 進垃圾桶，`rmdir` 仍要求空目錄**（2026-08-03）。ENOTEMPTY 是 POSIX 契約，
  有垃圾桶不是默默吞掉整棵樹的理由。非空目錄整棵進垃圾桶的能力放在 `vfs.trash()`，
  只從 web UI 走（`DELETE /api/dir?recursive=true`）。
- **還原撞名採 Windows 的對話框行為**（2026-08-03）：取代／略過／比較兩個檔案資訊。
  **「取代」時舊檔進垃圾桶**，不直接消失。`keep_both` 產生 `name (2).ext`。預設是拒絕，
  與 `rename` 不覆蓋的語意一致。

- **完整性驗證採 per-chunk HMAC，不採 AES-GCM**（2026-07-29）。加密層維持 AES-256-CTR；
  HMAC-SHA256 存在 MongoDB 的 chunk metadata。理由：改動面積最小，且**竄改者即使控制 Discord
  也改不到 tag**。

- **不做向後相容**（2026-07-29）。沒有 HMAC 欄位的 chunk 一律拒絕（fail closed）——
  「舊檔跳過驗證」那條路徑本身就是降級攻擊面。既有測試資料直接清掉重跑。

- **主金鑰隨機產生、以密碼包裝，不由密碼直接推導**（2026-07-31）。`.env` 不再有 `AES_SECRET_KEY`；
  主金鑰用 SFTP 密碼推導的 KEK 包裝後存在 MongoDB 的 `keystore`。理由：直接推導的話
  **改密碼＝所有資料永久讀不出來**；包裝後改密碼只是重寫 32 bytes。包裝的 MAC 也讓
  「密碼錯」與「資料壞了」可以區分。

- **KDF 用 Argon2id，套件用 `argon2-cffi`**（2026-07-31，取代 PBKDF2-HMAC-SHA256）。
  新包裝一律 Argon2id（64 MiB / t=3 / p=1）；**既有 PBKDF2 記錄照樣打得開**，因為每份記錄
  自帶 `kdf` 欄位，一行 migration 都沒有。不選 `cryptography` 44 內建的 Argon2id 是因為
  要把 cryptography 跨兩個 major 升上去，而 asyncssh 整個傳輸層坐在它上面。
  實測 125ms，比 PBKDF2 600k 的 214ms 還快。

- **既有記錄的 KDF 升級是 opt-in**（2026-07-31）。`KDF_UPGRADE=0` 是預設。理由：這是系統裡
  最危險的一次寫入——寫壞是**所有位元組永遠讀不出來**。升級會先確認新記錄解得開才覆蓋舊的，
  但時機留給操作者決定。

- **整檔 rollback 不做，列為已評估並接受的殘留風險**（2026-07-31）。擋它需要一個持有資料庫的人
  碰不到的單調版本計數器；釘在 Discord、本機 append-only 檔、外部 KMS/TPM 三條路都評估過，
  都不做。能寫 MongoDB 的攻擊者可以把某個檔案連同其父目錄換回舊版本並驗得過；其他所有竄改
  （重排、跨檔搬運、刪尾端 chunk、chunk 換洞、改內容、改名、搬移、交換檔名、刪節點）都擋得住。

- **金鑰是每連線的，不是每行程的**（2026-07-31）。`validate_password` 解開主金鑰後放在該連線上，
  `DiscordVFS` 每條連線各一個。連線結束即釋放參照（Python 無法真的抹除 bytes，這是盡力而為）。

- **完整性 tag 的涵蓋範圍：內容與身分，不含權限位與時間戳**（2026-07-31 起，2026-08-01 擴充）。
  - `chunk_tag` 綁 (file id, index, offset, size)；`node_tag` 蓋 (file id, size, 有序 chunk tag
    列表, `parent_id`, `filename`)；目錄有身分 tag（`dir_tag`）與蓋住子項集合的 tag
    （`dir_entries_tag`，**重算式**）。所有節點帶 `tag_version`，舊格式一律拒絕。
  - **權限位與時間戳刻意不納入**——它們不是內容，納入會讓每次 chmod 都要重算一份蓋在
    完全沒動過的位元組上的 tag。
  - 2026-08-01 的四個決策點：D1=(b1) 納入子項集合／重算式、D2 同前、D3 加 `tag_version`、
    D4 不寫 migration。**不做 migration 是因為時機**：當時線上 `nodes` 只有 root、0 個檔案。
    **代價：從那一刻起寫入的資料就回不去了，下次再改 tag 涵蓋範圍就真的需要 migration。**
  - **`ensure_root()` 從啟動移到認證之後**——它要金鑰，而金鑰是每連線的。root 因此不需要任何
    豁免。既有的 pre-tag root 只在**空的**時候就地升級；非空就拒絕啟動，因為對有內容的目錄
    重算 tag 等於用真金鑰把「已經發生的刪除」簽成合法。

- **「完整性檢查不涵蓋列目錄」被收窄而非推翻**（2026-08-01）。`list_dir` / `entries_of` 會驗
  **子項集合**（誰在裡面），但不驗每個子項自己的 tag。所以一個被竄改的檔案不會讓整個目錄列不出來，
  而「有人從資料庫刪掉一個節點」會被抓到。代價是 `ls -l` 顯示的大小未經驗證，但讀或 stat 它會失敗。

- **`_rollback()` 只負責「本 handle 建立的檔案」，既有檔案一律不刪任何東西**（2026-08-01）。
  兩個呼叫點本來就各自把自己那顆附件收乾淨了。原本評估的「還原成開啟時的快照」在實作前
  被否決：`_replace_chunk` 在 commit 成功之後才刪舊附件，所以快照可能指向已不存在的訊息，
  還原它等於寫進懸空引用。**代價**：日後新增 `_rollback()` 呼叫點的人必須自己負責釋放附件，
  這條寫在 docstring 裡。

- **檔案擴張採「稀疏尾端」，不實際補零**（2026-07-31）。`size` 可以大於所有 chunk 長度總和，
  中間那段讀回零、不佔 Discord 空間。**洞只會在尾端**；寫入落在 chunk 之後仍然實際補零。
  理由是效能：真的補零會讓「先設定大小再上傳」的客戶端每個 SFTP 封包都落在檔案中間、
  各重傳一整塊 chunk，9MB 的 chunk 會被重傳數百次。由
  `tests/test_truncate.py::test_presetting_the_size_does_not_change_the_upload_count` 釘住。

- **上線與測試跑同一個 Python 版本（3.12）**（2026-08-01）。理由不是「版本不同」本身，
  是 `pytest.ini` 把 `src.*` 的 `DeprecationWarning` 設成 error——那最容易在 minor 版本之間漂移，
  而它只會在容器裡浮現。**沒有另外加 3.11 的測試環境。**

- **此服務不可水平擴展，且只用文件擋，不加執行期守衛**（2026-08-01）。`README.md` 與
  `docker-compose.yml` 各寫明一段。「Mongo 單例標記硬擋」與「心跳租約只記 WARNING」都不做：
  硬擋會在 SIGKILL／斷電之後被自己的殘留記錄擋住，配上 `restart: on-failure:5` 就是重試五次
  然後放棄。**若日後搬上 k8s／Swarm（副本數是宣告式的），這條要重新評估。**

- **Client UI 的 API 與 SFTP server 同 process，前端是純靜態 SPA**（2026-08-02）。
  **理由是「第二個副本」那條**：獨立 process 連同一個 MongoDB，`_node_versions` 這個 process 內
  字典就會對 UI 說謊——查不到會被當成「沒人改過」，UI 拿著過期的 chunk layout 讀檔，
  **沒有錯誤、沒有 log，只是舊位元組**。`aiohttp` 本來就是相依。**代價**：UI 出問題會拖到 SFTP，
  兩者不能分開重啟——但這個服務本來就只能跑一個副本。**若日後做了 node 層級樂觀鎖可重新評估。**

- **多使用者採模型 B：每個使用者一把 master key**（2026-08-02，`design-multi-user.md` §6-1）。
  A 的密碼在密碼學上就解不開 B 的 chunk。選 B 的關鍵是**不可逆性**：A 改 B 要把所有人的資料
  重新加密，而現在線上幾乎沒有資料。**代價**：跨使用者分享會變成真正的金鑰交換問題，
  所以**分享明確不做**；忘記密碼＝那個使用者的資料真的救不回來，開放第二個使用者前必須先有
  等價於 `SFTP_PASSWORD_OLD` 的救援路徑。

- **帳號存 `users` collection，不存設定檔**（2026-08-02，`design-multi-user.md` §6-2）。
  設定檔會讓「刪掉一行就等於刪掉一個人的全部資料」太容易發生。代價是需要一支管理 CLI，
  排在第 4 步。

- **既有的單一使用者分四步過渡**（2026-08-02，`design-multi-user.md` §6-3）。前三步
  （`users` collection／per-user keystore／per-user root）**行為完全不變**。理由是可診斷性：
  一次到位會同時動認證、金鑰與樹根，而其中兩層寫壞的後果是**所有位元組永遠讀不出來**。
  **第 4 步是獨立決定，前三步不預先承諾它。**

- **HTTP session 把 master key 留在 process 記憶體，瀏覽器只拿到不透明 id**（2026-08-02）。
  兩條被否決的路：**把金鑰加密塞進 cookie**——偷到 cookie 就是偷到金鑰本身，而且金鑰每個 request
  都過一次網路；**每個 request 重新推導**——每次兩輪 Argon2、約 250ms。**重啟後所有 session 失效
  是刻意的**：要讓 session 活過重啟，就得把 master key 寫在某個比密碼更弱的東西底下。

- **session 存活時間是伺服器定的上限，client 只能往短調**（2026-08-02）。`.env` 定 10 分鐘 idle /
  2 小時絕對上限。**這個不對稱就是重點**：瀏覽器能延長的期限等於是被偷走 cookie 的人在控制。
  兩個期限缺一不可——idle 每次請求重設，絕對上限不會，沒有後者的話一個背景輪詢的分頁
  可以讓金鑰無限期留在記憶體裡。

- **登入鎖來源，永遠不鎖帳號**（2026-08-02）。只有一個帳號，帳號鎖定等於「任何人打錯幾次密碼
  就能把擁有者鎖在門外」的 DoS。鎖定的鍵是 (來源位址 + 裝置 id)。**裝置 id 不是安全邊界**
  ——cookie 誰都能清掉，所以底下疊了一層純位址的計數。登入成功只清該裝置的計數，不清位址的。

- **登入端點有並發上限與佇列上限**（2026-08-02）。一次登入跑兩輪 Argon2id、各 64 MiB；
  asyncssh 有連線上限所以 SFTP 那條路碰巧有界，HTTP 沒有——100 個並發登入就是 6.4 GB，
  而死掉的行程會把 SFTP 一起帶走。超過佇列深度回 503，不無上限排隊。**Argon2 移出 event loop**
  （`asyncio.to_thread`）：它是不讓步的 memory-hard C，在 loop 上跑等於一次登入凍住所有連線 125ms。

- **Client UI 第一版就做完整檔案管理，不只唯讀**（2026-08-02）。瀏覽／上傳／下載／刪除／
  建目錄／改名。**代價是寫入路徑從一條變兩條**：`_rollback()` 的附件釋放責任與跨 handle 同步的
  邊角都要在新路徑再驗一次。

- **桌面 app 只是視窗，SPA 由後端吐出來，不包進 exe**（2026-08-05）。
  **理由是 cookie**：認證是 `dd_session`，帶 `HttpOnly` 與 `SameSite=Strict`。從 `file://`
  載入的頁面去 fetch 遠端伺服器是跨來源請求，`SameSite=Strict` 的 cookie 不會被送出——
  要能用就得改成 Authorization header，等於推翻 2026-08-02 那條「瀏覽器只拿到不透明 id」
  的設計。exe 只帶一頁「填伺服器位址」的設定畫面，填完 `loadURL(伺服器)`，之後全部同源。
  **代價**：exe 一定要有一台跑得起來的後端。**好處**：改前端不必重打包，也不必重建 image
  （`dist/` 是掛進容器的），因此不會掉光所有 session。

- **Discord bot token 留在伺服器的 `.env`，客戶端只填連線設定**（2026-08-05）。
  被否決的是「app 內建設定精靈，填完寫進 DB 並自我重啟」。**理由**：token 是伺服器的祕密，
  不是使用者的祕密；讓客戶端寫得到它就等於讓任何登入的人讀得到它，而且設定精靈必然帶一段
  「還沒有帳號所以不能驗證」的無認證視窗期。設定畫面改成把三條連線路徑講清楚。

- **「多人共用」先做成「同一帳號多連線」，不是多帳號**（2026-08-05）。
  後端本來就允許（`SessionStore` 沒有單一 session 限制），這一輪把它變成看得見的：
  `GET /api/session` 回 `connections`，狀態列顯示，並加 `POST /api/sessions/revoke-others`
  結束其他連線而**不把呼叫者自己登出**——會把自己一起登出的按鈕，沒有人敢在有入侵者時按。
  真正的多帳號仍卡在密碼救援路徑，見 `[later]` 的第 4 步。

- **前端建置鏈是 Vite + React；執行時零外部來源**（2026-08-05）。
  被取代的原型是設計工具的產物，執行時從 unpkg 抓 React、ReactDOM 與 Babel，
  **離線時整頁空白**（原本的 `client/README.md` 寫「只有圖示會不見」，那句是錯的）。
  圖示改成內嵌 SVG、字型改用系統字、`index.html` 用 CSP 把 `default-src 'self'` 寫死，
  所以以後誰加了 CDN 會直接壞掉，而不是「剛好在線上的人看起來正常」。

- **倒數計時一律來自伺服器，前端只做真實時間的內插**（2026-08-05）。
  `GET /api/session` 每 10 秒同步一次，兩次之間用 `Date.now()` 的差值往下算，
  所以它可能**慢**（少報剩餘時間，無害）但不可能**快**。原型是每 700ms 的 `setInterval`
  扣一秒，時鐘快 43%，而快的方向正好是「以為還有時間」。

- **清單裡的盾牌是空心的，不畫綠勾**（2026-08-05）。列目錄只驗子項集合，不驗每個子項自己的
  標籤（2026-08-01 那條決策的直接結果），所以清單上的大小是未經驗證的。畫一個勾等於介面
  替伺服器說了它沒說過的話。**完整性失敗也不能用 × 關掉**，要明確確認，事件留在狀態列的計數裡。

- **搜尋走全樹掃描，不另建檔名索引**（2026-08-05，`GET /api/search`）。
  沿 `parent_id` 廣度優先，**每一層都經過 `entries_of`**，所以membership 標籤逐層驗證——
  不驗就開了一條「搜尋看得到、開啟看不到」的旁路，和 `scandir` 繞過 `list_dir` 那個 bug 同類。
  另建正規化檔名索引比較快，但 `node_tag` 蓋著 `filename`，多存一份沒被 tag 保護的副本
  等於讓有 DB 權限的人改那一份而不被抓到；要納入 tag 就是 `TAG_VERSION` 3→4 與一次真 migration。
  **不做正規表示式、不做內容搜尋**（內容搜尋要解密每一個 chunk）。結果有伺服器端上限，
  `truncated` 明說是否被截斷——短清單看起來就像「只有這些」，被信任為「不存在」的搜尋會藏檔案。

---

## 開案問題與最終結論

<!-- 原 missing_info.md（2026-08-03 併入）。只寫結論並指向權威來源，不複述內容。 -->

1. **SFTP 認證：憑證存哪** → 單一使用者、`.env` 注入 `SFTP_USER` / `SFTP_PASSWORD`，
   比對用 `hmac.compare_digest`。兩者皆無預設值，未設定會在啟動時直接失敗。
   密碼同時是登入憑證與包裝主金鑰的密碼來源，所以有 12 bytes 的長度下限。
   帳號現已是 `users` 的一列，但仍由 env 決定；多開帳號是上面的第 4 步。

2. **檔案刪除：只刪 metadata 還是也刪 Discord 訊息** → 兩者都做。涵蓋三條會產生孤兒附件的
   路徑：刪除、truncate 覆寫、`posix_rename` 覆寫既有目標，各有測試盯著「操作後 Discord 端
   不得殘留附件」（`tests/test_sftp_e2e.py`、`tests/test_rename.py`）。
   2026-08-03 起 SFTP 的 `rm` 改為進垃圾桶，實際刪除由 sweeper 執行。

3. **AES-CBC 的 IV 存哪** → **前提已作廢**。演算法是 AES-256-CTR，沒有 file-level IV，
   改為每個 chunk 各帶一個 16 bytes nonce 存在該 chunk 的 metadata。理由是 SFTP 是 offset-based，
   CBC 必須從頭依序解，per-chunk nonce 才讓隨機讀取成立；附帶好處是密文長度等於明文長度，
   不需要 padding。完整性另疊兩層 HMAC-SHA256（`chunk_tag` / `node_tag`），
   涵蓋範圍與明確沒涵蓋的部分見上方拍板決策。

4. **Discord 附件檔名** → `{file_id}_chunk_{index}.bin`。`file_id` 是內部 UUID，
   不會從附件名洩漏原始檔名。

5. **上傳失敗要不要 rollback** → 要。`DiscordFile._rollback()`（`src/vfs.py`），
   範圍與代價見上方拍板決策，覆蓋見 `tests/test_write_failures.py`。
   重試涵蓋 429 / 500 / 502 / 503 / 504 與傳輸層例外，指數退避加抖動、最多 5 次；4xx 不重試。
   每次 attempt 重建 request body（`aiohttp.FormData` 是一次性的）。
   主動節流在 `src/ratelimit.py`，讀 `X-RateLimit-*` 在被告知之前就先等。
   孤兒附件的原則是**一律先寫 metadata 才刪舊附件**——反過來會把一次失敗的更新變成真的資料遺失。

6. **Host key 從哪來** → `SFTP_HOST_KEY_PATH`（預設 `host_key`），檔案不存在才產生。
   `docker-compose.yml` 有 `host_key_data:/app/keys`，容器重建不會換金鑰（否則客戶端會跳
   mismatch，而那個警告與真實中間人攻擊的警告長得一模一樣）。金鑰權限強制 `0600`，
   既有金鑰也會被自動修復。**例外是舊的 root-running build 留下的 volume**，
   `ensure_host_key()`（`src/main.py`）會偵測並在錯誤訊息裡說明一次性遷移做法。

---

## 變更紀錄

<!--
只記「日期 / 做了什麼 / 測試數」，加上不在別處的教訓。
決策與理由在上面那一節，重複問題在 SOP.md，逐檔改動在 git log。這裡不複述。
-->

**2026-08-02 · HTTP API 層（Client UI 第 4 步）** — 455 項測試（+59），突變 15/15，
image 內同樣 455 過，**實地驗收 25/25，已上線**。`src/web.py`、`websession.py`、`webauth.py`。
- **loopback 是靠 host 端的 publish 做的，不是靠容器內的 bind**。容器內必須綁 0.0.0.0，
  所以 `WEB_HOST` 保護不了任何東西；`docker-compose.yml` 的 `127.0.0.1:8080:8080` 才是邊界。
  啟動時**刻意不對 bind 位址發警告**，改為在 `WEB_COOKIE_SECURE` 被關掉時警告。
- **aiohttp 的 Application 在 startup 之後是凍結的**，所以 sweeper task 放在啟動前就建好的 dict 裡。
  key 一律用 `web.AppKey`——裸字串 key 已被 aiohttp 標為 deprecated，而 `pytest.ini` 把
  `src.*` 的 DeprecationWarning 設成 error。
- **手機怎麼連**寫在 `.env.example` 末段。**自簽憑證刻意不提供**——它會訓練使用者按掉那個
  本來應該有意義的警告。

**2026-08-02 · 多使用者結構的前三步** — 396 項測試（+25），突變 10/10，image 內同樣 396 過，
**實地驗收 20/20，已上線並完成遷移**。**行為完全不變**：仍是一個使用者、仍由 `SFTP_USER` /
`SFTP_PASSWORD` 決定，只是憑證從模組常數變成一列資料。
- **遷移做法值得複用**：遷移前先對 `keystore` 記錄的密文／salt／nonce／MAC／KDF 參數取一個
  fingerprint，遷移後比對——`263b893e0206501d` 前後相同，這才是「只改了 id」的證據。
  fingerprint 是雜湊而不是原值，因為要驗的是它有沒有變。
- 驗收涵蓋兩條新的拒絕路徑（密碼錯、帳號不存在），以及 12 MiB 跨兩 chunk 的上傳／讀回／
  改名／刪除，收尾對帳 1 個節點、2/2 附件已釋放。payload 由固定種子產生，驗證端自己重算期望值。
- **第二次重啟確認兩條遷移都是 no-op。**
- **複查時才發現的一條**：帳號變成一列資料之後，「這個部署對應哪把 master key」的連結改走 `users`。
  所以**只清掉 `users` 而 `keystore` 還在**，會產生新的帳號 id、底下沒有記錄，然後 `ensure_usable`
  會若無其事地 bootstrap 一把新 master key **蓋在只有舊金鑰讀得懂的資料上**——不會報錯，只是從此
  解不開。已加守衛：keystore 非空但這個帳號沒有記錄時直接拒絕啟動。`bootstrap` 本身刻意不加守衛。
- 登入現在跑**兩次 Argon2**（驗密碼雜湊一次、解 KEK 一次），照 `design-multi-user.md` §3.2。
  成本約 125ms → 250ms。**這是照方案實作**；要合併成一次是偏離已拍板的方案，要先問。
- 既有 root 節點沿用 `"root"` 這個 id 指派給 env 帳號，所以**一個 tag 都不用重算**。

**2026-08-02 · repo 有 remote 了** — `origin` 指向 `github.com/FinalHope487/Discord-SFTP-Drive`，
`master` 已追蹤並推上去。連續四輪掛在 `[next]` 的備份缺口就此關閉。

**2026-08-01 · H2：完整性 tag 涵蓋身分與位置** — 371 項測試（+28），實地驗收 17/17。
`node_tag` 加 `parent_id` / `filename`，新增 `dir_tag` 與 `dir_entries_tag`，全節點帶 `tag_version`。
順帶掃掉三條 `[later]`：失敗 handle 的讀取語意、`_url_cache` 加 LRU 上限、`_to_sftp_error` 的死分支。
方案與實作的四處出入列在 `design-node-identity-integrity.md` 的橫幅裡。
- **最值得記的一條**：`scandir` 繞過 `list_dir`，讓子項集合的保護「對直接呼叫 VFS 的人有效、
  對真正的 SFTP 客戶端無效」，而 371 項測試全綠——實地驗收才抓到，已寫進 `SOP.md`。

**2026-08-01 · BLUEPRINT 全掃的 H1 / M2 / M3 / M4** — 343 項測試（+7），首次在上線用的 image 內
跑過整份 suite。修掉 `_rollback()` 會刪光既有檔案（H1）、補上失敗路徑的測試覆蓋與
`FakeDiscord.fail_uploads_from`、修掉 `DISCORD_MAX_CONCURRENCY` 在 compose 下無效、對齊 Python 3.12、
補 `README.md`、把「只能跑一個副本」寫進文件。
- H1 的修法**推翻了原本評估的方案**（理由見上方決策）。迴歸測試在修好前後各跑一次。
- `FakeDiscord` 的失敗注入：一個上傳失敗代表整組重試預算已耗盡，這寫在 fake 的註解裡。

**2026-07-31 · KDF 換成 Argon2id** — 336 項測試（+26），已上線並完成實地遷移。
**遷移做法值得複用**：分兩次重啟（先只上線新程式碼確認舊記錄仍打得開，確認無誤才開
`KDF_UPGRADE=1`，然後關回 0），驗證用 **canary**——遷移前上傳一個由固定種子產生的 256 KiB 檔案，
遷移後讀回來比對；種子固定是為了讓驗證端自己重算期望值。**刻意不做記錄備份**：留一份 PBKDF2
包裝的同一把金鑰會直接抵銷這次升級。收尾對帳 0 孤兒、0 懸空引用。

**2026-07-31 · 用真實流量驗證 Discord rate limit** — 連續四輪記著、一直沒被真實環境觸發過的一條。
50 個小檔案序列上傳，兩個機制都被真實觸發：主動節流在上傳路徑生效（訊號是耗時從約 0.5s 跳到
4.5～5s，而不是錯誤 log），真實 429 與重試在刪除路徑被觸發（7 次收到 429，`retry_after` 實測
0.3～0.602s）。

**2026-07-31 · 跨 handle 狀態同步** — process 內的 `_node_versions`（node id → 最後 commit 的 `mac`），
handle 在每次 read / write / truncate 前先比對。無衝突時零額外 DB 查詢（有測試釘住），這對序列上傳
很重要——`fstat` 每個 SFTP 封包都會被呼叫。**這個字典刻意不設上限**：查不到會被當成「沒人改過」，
所以 LRU 淘汰等於把這個 bug 在記憶體壓力下悄悄放回來。（`_url_cache` 可以淘汰——那裡查不到
只是多打一次 API。）

**2026-07-31 · 實地驗收找到兩個單元測試結構上抓不到的 bug** — MongoDB 不會就地把既有的非唯一
索引改成唯一（`IndexKeySpecsConflict`）；SIGTERM 的 flush 不在 `conn.wait_closed()` 的等待範圍內
（log 一切正常，只有客戶端最後不滿一個 chunk 的資料無聲消失）。兩者都已修，教訓在 `SOP.md`。

**2026-07-31 · 測試從 208 秒降到 23 秒** — 根因不是 fixture 結構而是 asyncssh 的 `socket.getfqdn()`
（宿主反向 DNS 每次 1.04 秒）。**原本 ROADMAP 上的計畫完全打錯地方**，教訓「先量再修」在 `SOP.md`。

**2026-07-31 · truncate / 稀疏尾端、POSIX metadata、優雅關機、隨機寫入、5xx 重試、附件 URL 過期處理、
chunk 位置納入 HMAC**。斷點續傳**不實作、前提不成立**：SFTP 讀取是無狀態的 offset 讀，寫入的 chunk
一上傳就寫進 Mongo，所以檔案大小就是續傳點；已用
`tests/test_session.py::test_an_interrupted_upload_can_be_resumed_by_appending` 釘住而不是加程式碼。

**2026-07-29 · 完整性驗證（per-chunk HMAC）、rate limit bucket、容器改非 root 執行、
設定值可達性檢查、log 洩密稽核**（實測密碼／金鑰／token／Mongo 密碼皆未出現在 log）。
