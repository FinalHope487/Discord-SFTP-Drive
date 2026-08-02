# Roadmap

新想法寫進來並標記，不當場插隊打斷目前任務。

## 標記說明

- `[now]` — 阻塞當前任務，不做就走不下去
- `[next]` — 目前模組穩定後就做
- `[later]` — 現在做屬於過早設計，等需求明確再說
- `[parked]` — 先記錄，暫不評估要不要做

---

## 待辦項目

<!-- 格式：- [標記] 說明（可附上下文/來源 session） -->

- [now] **Client UI**（2026-08-02 列入，形態已拍板；**第 1～3 步已於同日落地**）。
  唯一的對外介面仍是 SFTP 協定，`src/` 裡還沒有任何 HTTP／API 層，所以這不是「加一個前端」，
  是要先長出一層 API。
  - ~~1. `users` collection 與密碼雜湊~~ **已完成**（`src/users.py`）。Argon2id
    `PasswordHasher`，與 `derive_kek` 各走各的；帳號不存在與帳號停用都照樣跑一次假驗證。
  - ~~2. `keystore` 改 per-user~~ **已完成**。`adopt_legacy_record()` 把舊的 `"master"`
    改名到帳號 id 底下——**只動 `id` 一個欄位，不重包**，所以不需要密碼也不冒險。
  - ~~3. root 改 per-user~~ **已完成**。`DiscordVFS(key, root_id)`，`root_id` 沒有預設值；
    `extra_info` 從 `session_key` 換成帶 key／root_id／username 的 `users.Session`。
  - ~~4. HTTP API 層~~ **已完成**（`src/web.py` / `websession.py` / `webauth.py`）。
    `aiohttp` app 掛在 `main.py`，與 SFTP server 同 process、共用 `_node_versions`。
    session 持有金鑰的方案已拍板並落地，見下方決策。
  - **5. 前端**（下一步）：純靜態 SPA，完整檔案管理（瀏覽／上傳／下載／刪除／建目錄／改名）。
    API 已經齊了：`/api/login` `/api/logout` `/api/session` `/api/files` `/api/stat`
    `/api/file`（GET/PUT/DELETE）`/api/dir`（POST/DELETE）`/api/rename`。
  - **已知代價，前端要一起處理**：寫入路徑現在是兩條，`_rollback()` 的附件釋放責任
    （見下方拍板決策，寫在 docstring 裡的那條）在 HTTP 路徑上也要自己負責。

- [later] **重新產出 `BLUEPRINT.md`**。它以 `e288ff7`（Argon2id 上線那次）為準，之後 H1 與 H2 都落地了，
  §3 / §4 描述的 tag 涵蓋範圍與 `_rollback()` 行為都已不同。目前靠開頭的狀態橫幅與逐條註記
  撐著，那是權宜，不是長久做法——`/blueprint` 跑一次就好。原本的前置條件（先有 remote）
  已於 2026-08-02 滿足，但**現在應該再等**——Client UI 會動到認證、金鑰與樹根，
  現在重產出來的藍圖跑完那五步就又過期了。等 UI 落地後再跑。

- [later] **`SFTP_PASSWORD` 以明文環境變數注入，而它是唯一能開出 master key 的東西**。
  **2026-08-02 拍板：要做 docker secret，但排在 Client UI 之後，不讓它擋住 UI。**
  在那之前風險原樣存在。這條標記維持 `[later]` 只是排序，不是還在評估。
  `docker-compose.yml` 把它注入成環境變數，所以 `docker inspect`、`/proc/<pid>/environ`
  都看得到。拿到宿主的人本來就贏了，所以這不是新開的洞——但它把「讀得到容器設定」
  直接升級成「解得開所有資料」，中間沒有任何一層。
  - 做法：docker secret（掛檔案而非環境變數），compose 加 `secrets:`、
    `config.py` 支援 `*_FILE` 後綴。改動不大。
  - 當初列出的另一條路是「明確接受風險並寫進拍板決策」，**2026-08-02 沒有選它**——
    選的是要修、只是不排在 UI 前面。所以這條不會靜靜變成預設值。

- [later] **多使用者與各自獨立的 VFS 樹**；目前是單一帳號共用同一棵樹。
  方案在 `design-multi-user.md`，**§6 的三個決策點已於 2026-08-02 全部拍板**（見下方）。
  **§5 的第 1～3 步已併進上面的 Client UI 那條**，會在 UI 之前先跑掉；留在這裡的是**第 4 步**
  ——管理 CLI、建帳號流程、per-user 配額，也就是真正開放第二個使用者的那一步。
  前三步跑完之後，「要不要真的多開」還可以再決定一次，這正是分四步的理由。
  第 4 步同時會掀出 §4 的配額與刪帳號（批次刪一整棵樹會撞 429，要走既有 rate limit 路徑
  且可中斷續跑）。**分享明確不做**，模型 B 之下那是真正的金鑰交換問題。

- [later] **跨 handle 的 metadata 變更仍不同步**。`_node_versions` 比對的是 `mac`，
  而 `mac` 刻意不涵蓋權限位與時間戳（見下方拍板決策），所以另一條連線的 `chmod` / `utimes`
  **不會**觸發重新抓取，開著的 handle 會繼續回報舊的 mode 與 mtime。內容（size / chunks）
  已經同步了。要一併涵蓋就得替 metadata 另開一個版本欄位，或讓 `mac` 蓋住 metadata——
  後者已經被拍板否決過。實務衝擊小：SFTP 客戶端不會邊開著檔案邊等別人改權限。

- [later] **真正同時寫入的競態仍是後寫的贏**。跨 handle 同步的保證是「單一 handle
  在每次操作前看得到別人**已經 commit 完**的狀態」，不是寫入互斥。兩條連線同時
  對同一個檔案寫，後寫的仍會蓋掉先寫的——POSIX 對此本來也不保證原子性。
  要真的擋需要 node 層級的樂觀鎖（`update_one` 帶上舊 `mac` 當條件）。
  **這條也是「只能跑一個副本」的根因**，見 `README.md`。

- [later] **路徑版 `stat` 看不到別的 handle 還在 buffer 裡的位元組**。同一 handle 的
  `fstat` 已修；跨 handle 的沒修，也修不乾淨——那些位元組還沒上傳，本來就不該對別人可見。

- [later] **權限位與時間戳不受完整性保護**（見下方拍板決策）。能改 MongoDB 的人可以改它們。
  這是目前 tag 明確沒有涵蓋的唯一一類 metadata。

- [later] **不支援符號連結**（你選擇不做）。`symlink` / `readlink` / `link` 回 FX_OP_UNSUPPORTED。

- [parked] **SFTP 用戶端正常斷線時偶爾被記成 WARNING**。`src/sftp.py` 的 `connection_lost()`
  只把 `None` 與 `ConnectionResetError` 視為正常斷線；用 `async with asyncssh.connect(...)`
  正常關閉連線時會觸發另一種例外型別，被記成 `WARNING SSH connection error: Connection lost`。
  純粹是 log 噪音，功能本身無誤。2026-07-31 第一次觀察到，按規則不在第一次出現時處理。
  若之後又踩到，再回來查究竟是哪個例外型別、決定要不要擴大白名單。

- [parked] **chunk metadata 的 `index` 與 `offset` 互為冗餘**——chunk 從 0 連續、
  大小固定（末塊除外），兩者可互推。**評估結論是不動**：冗餘已經被 `chunk_tag` 保護
  （不一致會被抓到），而拿掉 `index` 要改 tag 的涵蓋範圍，那等於觸發一次全檔重算，
  代價遠大於收益。記在這裡是為了下次不要再想一遍。

- [parked] chunk 壓縮與去重。

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

- **KDF 用 Argon2id，套件用 `argon2-cffi`**（2026-07-31 決定，取代原本的 PBKDF2-HMAC-SHA256）。
  新的包裝一律用 Argon2id（預設 64 MiB / t=3 / p=1）；**既有的 PBKDF2 記錄照樣打得開**，
  因為每份記錄自己帶著「是哪個函式、什麼成本做出來的」——這正是當初把 `kdf` 欄位寫進格式的
  理由，這次兌現了，一行 migration 都沒有。
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
  威脅模型邊界：能寫 MongoDB 的攻擊者可以把某個檔案（連同其父目錄）換回它自己的舊版本，
  而且驗得過。其他所有竄改（重排、跨檔搬運、刪尾端 chunk、chunk 換洞、改內容、改名、
  搬移、交換檔名、刪節點）都擋得住。

- **金鑰是每連線的，不是每行程的**（2026-07-31 決定）。`validate_password` 解開主金鑰
  後放在該連線上，`DiscordVFS` 每條連線各一個。連線結束即釋放參照
  （Python 無法真的抹除 bytes，這是盡力而為，不是安全抹除）。

- **完整性 tag 的涵蓋範圍：內容與身分，不含權限位與時間戳**（2026-07-31 起，2026-08-01 擴充）。
  - `chunk_tag` 綁 (file id, index, offset, size)；`node_tag` 蓋 (file id, size, 有序 chunk tag
    列表, `parent_id`, `filename`)；目錄有自己的身分 tag（`dir_tag`）與一個蓋住「子項集合」
    的 tag（`dir_entries_tag`，採**重算式**）。所有節點帶 `tag_version`，舊格式一律拒絕。
  - **權限位與時間戳刻意不在裡面**——它們不是內容，把它們納入會讓每次 chmod 都要重算
    一份蓋在完全沒動過的位元組上的 tag。
  - 2026-08-01 的四個決策點：D1=(b1) 納入子項集合／重算式、D2 同前、D3 加 `tag_version`、
    D4 不寫 migration。**不做 migration 是因為時機**：拍板當下線上 `nodes` 只有 root、
    0 個檔案，沒有東西要回填。**代價講明白：從這一刻起寫入的資料就回不去了，下次再改 tag
    涵蓋範圍就真的需要 migration。**`tag_version` 欄位就是為了讓那一次便宜——與 `kdf` 欄位
    讓 Argon2id 遷移「一行 migration 都沒有」是同一個設計。
  - **`ensure_root()` 從啟動移到認證之後**。它要金鑰，而金鑰是每連線的，啟動時根本沒有。
    順帶讓 root 不需要任何豁免——它就是一個普通的有 tag 的目錄。對既有的 pre-tag root，
    只在**它是空的**時候就地升級；非空就拒絕啟動並說明原因，因為對有內容的目錄重算 tag
    等於用真金鑰把「已經發生的刪除」簽成合法。

- **「完整性檢查不涵蓋列目錄」這條決策沒有被推翻，而是被收窄**（2026-08-01）。
  `list_dir` / `entries_of` 現在會驗**子項集合**（誰在裡面），但**不驗每個子項自己的 tag**。
  所以原本的理由仍然成立——一個被竄改的檔案不會讓整個目錄列不出來，你還是看得到它、
  刪得掉它——而「有人從資料庫刪掉一個節點」現在會被抓到。代價是 `ls -l` 顯示的大小
  未經驗證，但真的去讀或 stat 它就會失敗。

- **`_rollback()` 只負責「本 handle 建立的檔案」，既有檔案一律不刪任何東西**（2026-08-01 決定）。
  兩個呼叫點本來就各自把自己那顆附件收乾淨了，所以對既有檔案而言 rollback 該做的清理**是零**。
  原本評估的做法是「記下開啟時的 message id 集合、rollback 還原成開啟時的快照」，
  **實作前發現那條會生出新的資料損壞路徑**：`_replace_chunk` 在 commit 成功之後才刪舊附件，
  所以開啟時的快照可能指向已經不存在的訊息，還原它等於寫進一個懸空引用、那個 chunk 永久
  讀不出來。**代價**：日後新增 `_rollback()` 呼叫點的人必須自己負責釋放附件，這條寫在
  docstring 裡。

- **檔案擴張採「稀疏尾端」，不實際補零**（2026-07-31 決定）。
  `size` 可以大於所有 chunk 的長度總和，中間那段是洞、讀回零、不佔 Discord 空間。
  **洞只會在尾端**；寫入落在 chunk 之後仍然實際補零（中間的洞沒有表示法）。
  理由是效能而不是省空間：真的補零會讓「先設定大小再上傳」的客戶端每個 SFTP 封包
  都落在檔案中間、各重傳一整塊 chunk，9MB 的 chunk 會被重傳數百次。
  這條由 `tests/test_truncate.py::test_presetting_the_size_does_not_change_the_upload_count`
  釘住，改回補零會直接讓它失敗。

- **上線與測試跑同一個 Python 版本（3.12）**（2026-08-01 決定）。`Dockerfile` 從
  `python:3.11-slim` 升到 `python:3.12-slim`，對齊本機 venv 的 3.12.7。
  理由不是「版本不同」本身，是 `pytest.ini` 把 `src.*` 的 `DeprecationWarning` 設成 error
  ——那正是最容易在 minor 版本之間漂移的東西，而它只會在容器裡浮現、容器裡又沒有測試可跑。
  **沒有另外加 3.11 的測試環境**——兩邊同版之後那件事的價值就消失了。

- **此服務不可水平擴展，且只用文件擋，不加執行期守衛**（2026-08-01 決定）。
  `README.md` 與 `docker-compose.yml` 各寫明一段。評估過「Mongo 單例標記硬擋」與
  「心跳租約只記 WARNING」，**都不做**：硬擋會在 SIGKILL／斷電之後被自己的殘留記錄擋住，
  錯誤訊息還會指向一個不存在的副本，配上 `restart: on-failure:5` 就是重試五次然後放棄
  ——用一個新的失敗模式去換一個需要刻意觸發的誤用，不划算。
  **若日後搬上 k8s／Swarm（副本數是宣告式的、手滑成本低很多），這條要重新評估。**

- **Client UI 的 API 與 SFTP server 同 process，前端是純靜態 SPA**（2026-08-02 決定）。
  **理由是「第二個副本」那條，不是省事**：獨立跑一個 process 連同一個 MongoDB，
  `_node_versions` 這個 process 內字典就會對 UI 說謊——查不到會被當成「沒人改過」，
  UI 拿著過期的 chunk layout 讀檔，**沒有錯誤、沒有 log，只是舊位元組**。同 process
  讓這個問題整個消失，而且 `aiohttp` 已經是相依，一個新套件都不用加。
  前端切成靜態資源是為了改版不必重建 image，那不影響上面的保證。
  **代價**：UI 出問題會拖到 SFTP（同一個 event loop），兩者不能分開重啟——但這個服務
  本來就只能跑一個副本，所以代價幾乎是零。**若日後做了 node 層級樂觀鎖，這條可以重新評估。**

- **多使用者採模型 B：每個使用者一把 master key**（2026-08-02 決定，`design-multi-user.md` §6-1）。
  A 的密碼在密碼學上就解不開 B 的 chunk，即使拿到整個資料庫。選 B 而不是 A 的關鍵在
  **不可逆性**：A 改 B 要把所有人的資料重新加密一次，而現在線上幾乎沒有資料，是成本最低的時機。
  **代價講明白**：跨使用者分享會變成真正的金鑰交換問題（現有架構沒有「每個檔案一把金鑰」
  這層），所以**分享明確不做**；忘記密碼＝那個使用者的資料真的救不回來，開放第二個使用者前
  必須先有等價於 `SFTP_PASSWORD_OLD` 的救援路徑。

- **帳號存 `users` collection，不存設定檔**（2026-08-02 決定，`design-multi-user.md` §6-2）。
  設定檔會讓「刪掉一行就等於刪掉一個人的全部資料」這種操作變得太容易。代價是需要一支
  管理 CLI，那排在第 4 步。

- **既有的單一使用者分四步過渡，不一次到位**（2026-08-02 決定，`design-multi-user.md` §6-3）。
  前三步（`users` collection／per-user keystore／per-user root）**行為完全不變**，
  跑完之後系統仍是單一使用者但結構已經是多使用者的了。理由是可診斷性：一次到位會同時動
  認證、金鑰與樹根，出事時分不出是哪一層——而這三層裡有兩層寫壞的後果是**所有位元組
  永遠讀不出來**。**第 4 步（真正開放第二個帳號）是獨立決定，前三步不預先承諾它。**

- **HTTP session 把 master key 留在 process 記憶體，瀏覽器只拿到不透明 id**（2026-08-02 決定）。
  兩條被否決的路要記下來免得再想一遍：**把金鑰加密塞進 cookie**，等於偷到 cookie 就是偷到
  金鑰本身，而且金鑰每個 request 都過一次網路；**每個 request 重新推導**是每次兩輪 Argon2、
  約 250ms，那不是設計而是故障。**重啟後所有 session 失效也是刻意的**——要讓 session 活過重啟，
  就得把 master key 寫在某個比密碼更弱的東西底下，而那正是 keystore 存在的理由。

- **session 存活時間是伺服器定的上限，client 只能往短調**（2026-08-02 決定）。
  `.env` 定 10 分鐘 idle / 2 小時絕對上限；client 可以要求更短並且拿得到，要求更長會被夾回來。
  **這個不對稱就是重點**：瀏覽器能延長的期限，等於是被偷走那個 cookie 的人在控制。
  兩個期限缺一不可——idle 每次請求重設，絕對上限不會，沒有後者的話一個開著背景輪詢的分頁
  可以讓金鑰無限期留在記憶體裡。

- **登入鎖來源，永遠不鎖帳號**（2026-08-02 決定）。只有一個帳號，所以帳號鎖定等於
  「任何人打錯幾次密碼就能把擁有者鎖在門外」的 DoS。鎖定的鍵是 (來源位址 + 裝置 id)：
  裝置 id 讓鎖定夠精準，不會因為一個瀏覽器出事就鎖掉整個住處。**但裝置 id 不是安全邊界**
  ——cookie 誰都能清掉，所以底下疊了一層純位址的計數，清 cookie 只會讓它更快到達而不是逃掉。
  登入成功只清該裝置的計數，不清位址的：共用位址的攻擊者不該靠登入自己的帳號拿到免費重置。

- **登入端點有並發上限與佇列上限**（2026-08-02 決定）。一次登入跑兩輪 Argon2id、各 64 MiB。
  asyncssh 有自己的連線上限，所以 SFTP 那條路是**碰巧**有界的；HTTP 沒有，100 個並發登入
  就是 6.4 GB，而攻擊者不需要猜對任何東西，死掉的行程還會把 SFTP 一起帶走。
  超過佇列深度回 503 而不是繼續排——無上限的佇列是同一個故障多繞幾步。
  **順帶把 Argon2 移出 event loop**（`asyncio.to_thread`）：它是不讓步的 memory-hard C，
  在 loop 上跑等於一次登入凍住所有連線 125ms。這條同時修好了 SFTP 那邊本來就有的問題。

- **Client UI 第一版就做完整檔案管理，不只唯讀**（2026-08-02 決定）。
  瀏覽／上傳／下載／刪除／建目錄／改名。**代價是寫入路徑從一條變兩條**：`_rollback()`
  的附件釋放責任（見上方拍板決策）在 HTTP 路徑上要自己負責，跨 handle 同步的邊角也要在
  新路徑再驗一次——唯讀版本可以完全迴避這些，這裡是明知而選擇承擔。

---

## 變更紀錄

<!--
只記「日期 / 做了什麼 / 測試數」，加上不在別處的教訓。
決策與理由在上面那一節，重複問題在 SOP.md，逐檔改動在 git log。這裡不複述。
-->

**2026-08-02 · HTTP API 層（Client UI 第 4 步）** — 455 項測試（+59），突變 15/15，
image 內同樣 455 過，**實地驗收 25/25，已上線**。`src/web.py`（aiohttp app）、
`websession.py`（session store）、`webauth.py`（登入節流與鎖定）。
- 決策與理由在上面那一節。這裡只記三件不在別處的：
- **loopback 是靠 host 端的 publish 做的，不是靠容器內的 bind**。容器內必須綁 0.0.0.0 才
  可能被連到，所以 `WEB_HOST` 保護不了任何東西；`docker-compose.yml` 的
  `127.0.0.1:8080:8080` 才是邊界。因此啟動時**刻意不對 bind 位址發警告**——那會在每個
  正確部署上都叫一次。改為在 `WEB_COOKIE_SECURE` 被關掉時警告，那是伺服器真的看得到的決定。
- **aiohttp 的 Application 在 startup 之後是凍結的**，所以 sweeper task 不能寫回 app；
  它放在一個啟動前就建好的 dict 裡。key 一律用 `web.AppKey`，因為 `pytest.ini` 把
  `src.*` 的 DeprecationWarning 設成 error，而裸字串 key 已經被 aiohttp 標為 deprecated。
- **手機怎麼連**寫在 `.env.example` 末段，三條路各自標明伺服器端要吞下什麼。
  **自簽憑證刻意不提供**——它會訓練使用者按掉那個本來應該有意義的警告。

**2026-08-02 · 多使用者結構的前三步（Client UI 的前置）** — 396 項測試（+25），突變 10/10，
image 內同樣 396 過，**實地驗收 20/20，已上線並完成遷移**。
`users` collection、per-account keystore 記錄、per-account root。**行為完全不變**：仍是一個
使用者、仍由 `SFTP_USER` / `SFTP_PASSWORD` 決定，只是憑證從模組常數變成一列資料。
- **遷移做法值得複用**：遷移前先對 `keystore` 記錄的密文／salt／nonce／MAC／KDF 參數
  取一個 fingerprint，遷移後比對——`263b893e0206501d` 前後相同，這才是「只改了 id」的證據，
  而不是「看起來還能用」。fingerprint 是雜湊而不是原值，因為要驗的是它有沒有變，
  不是它是什麼，而被包裝的金鑰沒有理由出現在終端機捲動紀錄裡。
- 驗收也涵蓋兩條新的拒絕路徑（密碼錯、帳號不存在），以及 12 MiB 跨兩個 chunk 的
  上傳／讀回／改名／刪除，收尾對帳 1 個節點、2/2 附件已釋放。
  payload 由固定種子產生，驗證端自己重算期望值，不去信任寫入端記下的東西。
- **第二次重啟確認兩條遷移都是 no-op**：既沒有再建帳號，也沒有再動 keystore。
- **最值得記的一條，是複查時才發現的**：帳號變成一列資料之後，「這個部署對應哪把 master key」
  的連結改走 `users`。所以**只清掉 `users` 而 `keystore` 還在**，會產生新的帳號 id、底下沒有記錄，
  然後 `ensure_usable` 會若無其事地 bootstrap 一把新的 master key **蓋在只有舊金鑰讀得懂的資料上**
  ——不會報錯，只是從此再也解不開。已加守衛：keystore 非空但這個帳號沒有記錄時直接拒絕啟動。
  `bootstrap` 本身刻意不加守衛，因為「在既有記錄旁邊多一筆」正是第 4 步建帳號要做的事。
- 舊 `"master"` 記錄的遷移**只改 `id` 一個欄位**。密文、salt、nonce、MAC、KDF 參數全部原樣搬過去，
  所以不需要密碼、也不可能讓記錄變得打不開——這才是它敢在啟動時自動跑的全部理由。
  有一條測試專門釘住「它沒有重包」。
- 登入現在跑**兩次 Argon2**（驗密碼雜湊一次、解 KEK 一次），照 `design-multi-user.md` §3.2 的
  三步走。上線成本約從 125ms 變 250ms。**這是照方案實作，不是疏忽**；要合併成一次的話那是
  偏離已拍板的方案，要先問。
- 既有的 root 節點沿用 `"root"` 這個 id 指派給 env 帳號，所以**一個 tag 都不用重算**——
  目錄的 tag 蓋住自己的 id，換 id 就等於整棵樹重簽。

**2026-08-02 · repo 有 remote 了** — `origin` 指向 `github.com/FinalHope487/Discord-SFTP-Drive`
（GitHub CLI 建立），`master` 已追蹤並推上去。連續四輪掛在 `[next]` 的那條備份缺口就此關閉；
`README.md` 結尾與 `session-handoff.md` 的「沒有異地備份」都已一併改掉。

**2026-08-01 · H2：完整性 tag 涵蓋身分與位置** — 371 項測試（+28），實地驗收 17/17。
`node_tag` 加 `parent_id` / `filename`，新增 `dir_tag` 與 `dir_entries_tag`，全節點帶
`tag_version`。順帶掃掉三條 `[later]`：失敗 handle 的讀取語意、`_url_cache` 加 LRU 上限、
`_to_sftp_error` 的死分支。方案（`design-node-identity-integrity.md`）與實作有四處出入，
列在該文件的橫幅裡。**最值得記的一條**：`scandir` 繞過 `list_dir`，讓子項集合的保護
「對直接呼叫 VFS 的人有效、對真正的 SFTP 客戶端無效」，而 371 項測試全綠——實地驗收才抓到，
已寫進 `SOP.md`。

**2026-08-01 · BLUEPRINT 全掃的 H1 / M2 / M3 / M4** — 343 項測試（+7），首次在上線用的
image 內跑過整份 suite。修掉 `_rollback()` 會刪光既有檔案（H1）、補上失敗路徑的測試覆蓋
與 `FakeDiscord.fail_uploads_from`、修掉 `DISCORD_MAX_CONCURRENCY` 在 compose 下無效、
對齊 Python 3.12、補 `README.md`、把「只能跑一個副本」寫進文件。
- H1 的修法**推翻了原本評估的方案**，理由見上方拍板決策。迴歸測試在修好前後各跑一次：
  舊程式碼下三個測試失敗，新程式碼下全過。
- `FakeDiscord` 的失敗注入：一個上傳失敗代表整組重試預算已耗盡（真實 client 內部重試
  5xx 與傳輸層例外後才拋），這寫在 fake 的註解裡，免得下一個人以為它模擬的是單次失敗。

**2026-07-31 · KDF 換成 Argon2id** — 336 項測試（+26），已上線並完成實地遷移。
**遷移做法值得複用**：分兩次重啟（先只上線新程式碼確認舊記錄仍打得開，確認無誤才開
`KDF_UPGRADE=1` 做遷移，然後關回 0），驗證用 **canary**——遷移前上傳一個由固定種子產生的
256 KiB 檔案，遷移後讀回來比對；種子固定是刻意的，讓驗證端自己重新產生期望值，
不去信任遷移前那一端寫下的東西。**刻意不做記錄備份**：留一份 PBKDF2 包裝的同一把金鑰
會直接抵銷這次升級，攻擊者挑弱的那份打就好。收尾對帳 0 孤兒、0 懸空引用。

**2026-07-31 · 用真實流量驗證 Discord rate limit** — 連續四輪記著、一直沒被真實環境觸發過的
一條。50 個小檔案序列上傳，兩個機制都被真實觸發：主動節流在上傳路徑生效（訊號是耗時從
約 0.5s 跳到 4.5～5s，而不是錯誤 log），真實 429 與重試在刪除路徑被觸發（該 bucket 迴圈
一開始還沒被學到，7 次收到 429，`retry_after` 實測 0.3～0.602s）。

**2026-07-31 · 跨 handle 狀態同步** — process 內的 `_node_versions`（node id → 最後 commit
的 `mac`），handle 在每次 read / write / truncate 前先比對，相符就什麼都不做。無衝突時零額外
DB 查詢（有測試釘住），這對序列上傳很重要——`fstat` 每個 SFTP 封包都會被呼叫。
**這個字典刻意不設上限**：查不到會被當成「沒人改過」，所以 LRU 淘汰等於把這個 bug 在記憶體
壓力下悄悄放回來。（對照 `_url_cache` 可以淘汰——那裡查不到只是多打一次 API。）

**2026-07-31 · 實地驗收找到兩個單元測試結構上抓不到的 bug** — MongoDB 不會就地把既有的
非唯一索引改成唯一（`IndexKeySpecsConflict`，升級既有部署時服務直接拒絕啟動）；SIGTERM 的
flush 不在 `conn.wait_closed()` 的等待範圍內（log 一切正常，只有客戶端最後不滿一個 chunk
的資料無聲消失）。兩者都已修，教訓寫進 `SOP.md`。

**2026-07-31 · 測試從 208 秒降到 23 秒** — 根因不是 fixture 結構而是 asyncssh 的
`socket.getfqdn()`（宿主反向 DNS 每次 1.04 秒）。**原本 ROADMAP 上的計畫完全打錯地方**，
教訓「先量再修」已寫進 `SOP.md`。

**2026-07-31 · truncate / 稀疏尾端、POSIX metadata、優雅關機、隨機寫入、5xx 重試、
附件 URL 過期處理、chunk 位置納入 HMAC**。斷點續傳**不實作、前提不成立**：SFTP 讀取是無狀態
的 offset 讀，寫入的 chunk 一上傳就寫進 Mongo，所以檔案大小就是續傳點；已用
`tests/test_session.py::test_an_interrupted_upload_can_be_resumed_by_appending` 釘住而不是
加程式碼。

**2026-07-29 · 完整性驗證（per-chunk HMAC）、rate limit bucket、容器改非 root 執行、
設定值可達性檢查、log 洩密稽核**（實測密碼／金鑰／token／Mongo 密碼皆未出現在 log）。
