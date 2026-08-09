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

- [done] ~~**垃圾桶的查詢是全 collection 掃描，且 `trashed_at` 沒有索引**~~（2026-08-06 完成，
  見下方拍板決策與變更紀錄）。以下保留原始描述作為背景：（2026-08-06
  `/blueprint` 掃出）。`list_trash()` 撈出**所有樹**的垃圾桶節點再向上走訪過濾，
  `purge_expired()` 每 15 分鐘（每個 live session 各一次）呼叫它並逐一驗證標籤。
  單使用者、資料量小時看不出來；**開第二個帳號時會變貴，而那正是第 4 步要做的事**。
  修法：加 `nodes.trashed_at` 索引，並讓 `purge_expired` 直接用 `trashed_at <= cutoff` 查詢。
  **2026-08-06 補**：這一輪把覆寫改成 copy-on-write 之後，**每次覆寫都會多一筆垃圾桶記錄**，
  所以餵給這條掃描的量從「使用者刪了幾個檔案」變成「使用者覆寫了幾次」。單使用者仍然看不出來，
  但它現在成長得比原本快。**沒有一起做，是因為加索引屬於改 schema，那要先問過。**

- [later] **`sweep_incoming` 只在有人登入網頁時才跑**（2026-08-06 開出，**同日實地確認**）。
  驗收時這一條是被動證實的：游離節點在資料庫裡放著不動，直到腳本登入的那一刻，六秒內就被收走。
  沒有登入就沒有金鑰，沒有金鑰就沒有 sweep。它掛在
  `trash_sweeper` 同一個迴圈上，而那個迴圈借用 session 的金鑰、只掃有 live session 的樹。
  **純 SFTP 的用法因此永遠不會觸發它**：被中斷的覆寫留下的游離節點會一直佔著 Discord 空間，
  直到有人開一次網頁 UI。這與垃圾桶「至少這麼久」是同一個取捨（背景任務不該長期持有主金鑰），
  所以不是缺陷；但垃圾桶那條使用者看得到，這條看不到。
  要嘛在 UI 上把游離節點的數量顯示出來，要嘛接受並寫進文件。

- [parked] **關掉直譯器時偶爾出現 `Task was destroyed but it is pending`**（2026-08-06 觀察到）。
  指向 `websession.sweeper` 與 `web.trash_sweeper`，非決定性——同一份程式碼連跑三次只出現一次。
  是測試把 app 拆掉時 task 的取消還沒被 loop 處理完，純輸出噪音，功能無誤。
  與上面 SFTP 正常斷線那條 WARNING 同類。

- [later] **程式碼內註解與現況的小幅漂移**（2026-08-06 `/blueprint` 掃出，純整潔）。
  `src/vfs.py:104-107` 描述 tag version 1 與 2 而 `TAG_VERSION` 已經是 3（v3 加的
  `trashed_at` 沒被寫進去）；`tests/conftest.py:6` 與 `.gitignore:1` 還提到早已不存在的
  `AES_SECRET_KEY`；`docker-compose.yml:138` 指向 `client/BUILD.md`，實際檔案在 repo 根目錄；
  `README.md:108` 寫 515 項測試，實際 578。
  **2026-08-06 補**：`i18n.js` 有 13 個 key 沒有任何元件引用（`col.location`、`col.remaining`、
  `status.search`、`status.searchTruncated`、`detail.path`、`detail.verified`、
  `detail.verifiedNote`、`search.title`、`search.reveal`、`transfer.uploading`、
  `act.copyPath`、`act.copied`、`toast.uploaded`）。改文案時逐一比對才發現。
  **`detail.verified` 那一對是死得有道理的**——列目錄不畫綠勾，所以「已通過檢查」永遠沒機會出現；
  其餘幾個看起來是做到一半或後來換了做法。**要刪之前得先確認是「文字多餘」而不是「元件漏用」。**

- [done] ~~**獨立單機版：packaged app 的密碼從哪來**~~（2026-08-07 開出同日拍板並落地，
  見下方變更紀錄）。拍板結果是 (a)：外殼跳密碼視窗，用 stdin 餵給後端子行程。

- [later] **Electron 外殼那兩頁 UI 沒有自動化驗收**（2026-08-09 開出）。`client/app` 的 SPA 已經有
  Playwright 蓋住，但 `setup.html` / `local.html` 還是只有目測。Python Playwright 不支援
  Electron，要做就得另外引進 JS 端 Playwright（`_electron` API）。`setup.html` 只有一個表單，
  等它開始長東西再說。

- [later] **`children()` 在兩個後端都是全表掃描**（2026-08-07 量到）。
  `{"parent_id": x}`（含垃圾桶項，`list_dir` 每次都會呼叫）在 MongoDB 是 COLLSCAN、
  在 SQLite 是 `SCAN nodes`，因為 `(parent_id, filename)` 是部分索引而這個查詢沒帶
  `trashed_at` 條件，兩邊的 planner 都無法證明它是子集。`live_children()` 有帶，兩邊都走索引。
  **不是這一輪引入的退化，兩個後端一致**。修法是加一個 `parent_id` 單欄索引，
  **但那是改 schema，要先問**；而且只加在單機版會製造「兩邊索引集合不一致」，
  那正是這次刻意用同一份宣告去避免的事。

- [later] **標準版 exe 仍需要 Docker**。方案一（exe 只是視窗、後端是 compose）與
  方案二（單機版）現在並存，`BUILD.md` 兩邊都寫了。兩者是不同產品線不是替代關係，
  維護成本是雙份，日後若要收斂成一個要重新拍板。

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
  **2026-08-06 補**：它同時是最上面那條 `[now]`（並行 `_stage_entries`）的根因。
  差別在後果：這一條是「你的寫入被蓋掉」，那一條是「目錄從此列不出來」，所以那一條不能等。

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

- **使用者層的驗收用 Playwright，測試檔放 `tests/`**（2026-08-09）。真瀏覽器 → 真 aiohttp
  程序 → 真 VFS / 真資料庫 → `fake_discord`，只有最外層的外部服務是假的。
  否決 vitest + jsdom：jsdom 是第二套假件，而這個專案已經被「假件不模擬的那一半」咬過三次
  （見 `SOP.md`），不需要第四次。放 `tests/` 而不是 `client/app/` 的理由是它因此直接繼承
  `fake_db` / `fake_discord` / `account` 三個既有 fixture 與 `--db=sqlite`，
  測試總數維持單一數字。
  **Electron 外殼的兩頁 UI 不做**——Python Playwright 不支援 Electron，要另外引進 JS 端
  Playwright，而 `setup.html` 只有一個表單，投報率遠低於 SPA。列在下方 `[later]`。

- **`push main` 由 PreToolUse hook 實際攔阻，不再只是 `CLAUDE.md` 的行為引導**（2026-08-09）。
  `.claude/hooks/block-push-main.py` 檢查兩條路徑：refspec 指名的目標，以及沒有 refspec 時的
  當前分支。`.claude/settings.json` 的 `Bash(git push:*)` allow **保留**——功能分支不該每次
  跳確認，該擋的只有 main。`deny` 字串比對做不到這件事，涵蓋不了 `git push -u origin HEAD`
  與在 main 上不帶參數的 `git push`。

- **獨立單機版：packaged app 的密碼走 (a)——外殼跳密碼視窗、用 stdin 餵給後端子行程**
  （2026-08-07）。另外兩個選項——寫進 `drive.env`（鎖跟鑰匙放一起）、OS 憑證保管庫
  （新相依套件）——都否決。**同一條 stdin 管線也拿來當關機訊號**：外殼想關閉時直接
  `child.stdin.end()`，不需要另外設計一套協定。理由是 Windows 上量到的事實，不是猜的——
  `child.kill()` 在 Windows 一律是強制 TerminateProcess，不管傳哪個訊號名稱都一樣；
  `taskkill` 不加 `/f` 對一個沒有視窗的 console 行程會直接拒絕（「這個處理程序只能強制終止」）。
  關閉 stdin 兩邊都測過會動：`main.py` 的 `_wait_for_shutdown` 現在接受 `extra_stop`，
  跟原本的訊號等待賽跑；`standalone.py` 在被外殼餵密碼的模式下（`DISCORD_DRIVE_STDIN_LIFECYCLE=1`）
  把「stdin 讀到 EOF」接上那個 `extra_stop`。

- **獨立單機版：接受「一台裝置一份資料」語意、換掉 MongoDB 改用內嵌資料庫、仍以 Discord 作為
  儲存後端**（2026-08-07）。這是下方 `全包版 .exe` 那條 `[later]` 卡住的前提——方案二
  （PyInstaller 打包、免 Docker）需要「一台裝置一份資料、彼此不相通」是可接受的語意，因為
  現有唯一擋外部連線的邊界（`docker-compose.yml` 的 `127.0.0.1:8080:8080`）塞進 app 之後
  就不存在了。使用者確認接受：換機器/重灌要重新設定，不再是「隨處連同一份雲端硬碟」——
  這其實是把它變成另一個產品線，**與現有多裝置共用版本並存，不是取代**。
  三個子決定一併拍板：
  1. **資料語意**——單機各自一份，不跨裝置同步。
  2. **儲存後端**——MongoDB 換成內嵌資料庫（SQLite 為首選：免額外執行檔、Python 標準庫內建）。
     這是移除/新增相依套件等級的改動，**不是遷移**，兩份資料庫格式不相容。
  3. **檔案儲存仍是 Discord**——沿用現有加密與 tag 完整性設計，仍需要網路與 bot token/頻道
     設定，**不是純離線 app**，只是不需要 Docker 了。
  尚未拍板、留給實作分支決定的：SQLite schema 怎麼對應現有 MongoDB 文件結構、要不要提供
  從現有部署匯入的工具、殼與後端合併打包後的啟動/關閉生命週期怎麼管理。

- **垃圾桶掃描走 `nodes.trashed_at` 的部分索引，兩條查詢都改用 `$gt: 0`**（2026-08-06）。
  索引是 partial（`{"trashed_at": {"$gt": 0}}`）而不是整欄索引：活節點根本不帶這個欄位，
  為了找出少數被刪的而把所有樹的所有節點都索引起來，正是這個索引要省掉的成本。
  **`$ne: None` 必須一起換掉**——它描述同一組文件，但不能出現在 partialFilterExpression 裡，
  而且 MongoDB 不會用索引服務它。實測（真 mongod，四份文件）：`$ne: null` 是 COLLSCAN 掃四份，
  `{$gt: 0, $lte: cutoff}` 是 IXSCAN 只檢查一份。
  **查詢裡的 `$gt: 0` 不是多餘的**：partial index 只服務「planner 能證明是該 filter 子集」的查詢，
  拿掉它索引照樣建成、照樣正確、而且**永遠不會被用到**——一個沒有任何症狀的退化，
  所以由 `tests/test_trash.py` 釘住。
  **順帶釐清一件容易記反的事**：`{$lte: 數字}` 不會匹配缺欄位或 null，因為比較運算子有型別分隔；
  BSON 的排序把 null 排在數字前面，那是排序不是查詢。記反的後果是把活節點餵給 `purge()`，
  所以假件的版本也照真實語意寫，並單獨釘住。
  **驗證方式**：對真 mongod 重啟（索引規格只有真伺服器會檢查，2026-08-05 就是在這裡炸的），
  確認索引建成、兩條查詢都走 IXSCAN；四個突變各自被對應的測試抓到。

- **UI 文案只講使用者要決定或要做的事，不講實作**（2026-08-06）。`i18n.js` 的長說明原本寫著
  9 MiB 分塊、AES-256-CTR、HMAC-SHA256、Argon2id 64 MiB、`parent_id`、MongoDB、HTTP 狀態碼，
  **沒有一項會改變任何人按哪個按鈕**。全部改成白話，畫面結構一行都沒動。
  **刻意留下來的三條例外，是機制本身就是那個決定**：(1) 密碼弄丟＝檔案永遠打不開
  （`login.passwordNote`）；(2) 清單上的大小**沒有**被驗過，下載才會驗（`detail.unverifiedNote`，
  這是 2026-08-05「盾牌空心不畫綠勾」那條的文字面）；(3) 完整性失敗不是網路問題、重試沒有用
  （`integrity.body`）。**這三條變短了但沒有變軟。**
  順帶修掉一個渲染瑕疵：詳細資訊面板的 `VerifyNote` 拿不到路徑，一直借用 `integrity.body`
  並把 `{path}` 傳空字串，畫面上多出一對空引號；改成它自己的 `integrity.bodyShort`。

- **同目錄的結構性寫入用 per-`dir_id` 的行程內鎖串起來，不做 node 層級樂觀鎖**（2026-08-06）。
  `_locked_dirs()` 把 stage→改動→commit 整段圈起來，六個呼叫點全部包住
  （`makedir` / `_create_file` / `purge` / `rename` / `restore` / `_rollback`），
  並且 `_stage_entries` 會**檢查呼叫端真的持有那把鎖**，沒有就 `RuntimeError`
  ——漏掉一個呼叫點的症狀是「某個目錄從此列不出來」，離出事的那一行非常遠。
  鎖是**模組層級**的，理由和 `_node_versions` 一樣：互相競爭的協程在不同連線上，
  而連線各有自己的 `DiscordVFS`，掛在實例上等於只防自己。多把鎖依 id 排序取得，
  並且**對同一個 task 可重入**，那是 `rename` 能在持有兩端的情況下呼叫 `purge` 的原因。
  **選 (a) 不選 (b) 的理由**：這個服務已拍板只能跑一個副本，行程內的鎖在這個範圍下就是完整解；
  樂觀鎖真正的價值（跨行程）現在沒有人要用，而在急件上改每一條寫入路徑，寫壞的代價是資料讀不出來。
  **代價**：不解「後寫的贏」與「只能跑一個副本」，那兩條留在 `[later]`；
  `purge` 會在整段 Discord 往返期間持有父目錄的鎖（大量刪除時該目錄會等），
  這是刻意的——中間放掉鎖正是要防的那個交錯。

- **覆寫改成 copy-on-write：寫游離節點，close 才換名，舊節點進垃圾桶**（2026-08-06）。
  `open(truncate=True)` 不再原地清空節點。新位元組寫進一個 `parent_id` 與 `filename`
  都是 `None` 的**游離節點**，`close()` 時才把佔用該名字的節點丟進垃圾桶、讓游離節點接手。
  HTTP 與 SFTP 走同一條路（`FXF_TRUNC` 也是）。
  **選游離而不是「保留檔名的暫存節點」**：後者要嘛出現在列目錄裡，要嘛得在列目錄時濾掉——
  而「這裡看得到、那裡看不到」正是 `scandir` 繞過 `list_dir`、搜尋繞過 `entries_of` 那兩個 bug 的形狀。
  游離節點不在任何目錄裡，所以沒有任何一處需要記得隱藏它。它的 tag 照樣蓋住空的 parent 與空的名字。
  **可行的關鍵是那個部分唯一索引**：被換掉的節點進垃圾桶後仍保有原本的 `(parent_id, filename)`，
  而索引只涵蓋活著的節點，所以同一個名字下同時存在一個垃圾桶節點與一個新的活節點是合法的。
  **這一點假件不模擬**（`FakeCollection` 明講唯一性是 MongoDB 的職責），所以另外對真的 mongod
  驗過 16 項，包含「再插入第二個活節點會拿到 DuplicateKeyError」。
  **代價，三項，都是刻意接受的**：(1) 舊版本會在垃圾桶裡佔 Discord 空間到保留期滿，
  所以**反覆覆寫同一個大檔案會累積多份**；(2) 上傳期間佔兩份；
  (3) 行程被砍會留下游離節點，由 `sweep_incoming` 收（`INCOMING_MAX_AGE_HOURS`，預設 24 小時，
  以 `modified_at` 計算所以還在進度中的上傳永遠不算舊，且**刪之前一定先驗 tag**
  ——它是照節點自己的 `chunks` 刪的，不驗就等於讓有 DB 權限的人指定別的檔案的附件去死）。
  **SFTP 斷線仍然 commit，而那是對的**：SFTP 沒有宣告長度，每個 write 都被回覆成功，
  所以「檔案就是被確認過的那麼長」是誠實的結果，也是 `test_session.py` 的續傳測試依賴的前提。
  HTTP 不同是因為 `Content-Length` 讓伺服器知道身體短了。差別寫在 `tests/test_sftp_disconnect.py`。

- **`config.py` 讀得到的每個變數都必須出現在 `docker-compose.yml`，用測試釘住**（2026-08-06）。
  `tests/test_compose_coverage.py` 用 `ast` 解析 `config.py` 找出所有 `os.getenv` / `_setting`
  的變數名，比對 compose 的 `environment:` 與 `.env.example`。
  **理由是這個形狀犯了兩次**（`DISCORD_MAX_CONCURRENCY`、`TRASH_*`），而且第二次發生時
  第一次的五行註解就在同一個檔案裡幾行之外。補上缺的幾行不算修好，會斷言涵蓋率的東西才算。
  秘密以 `NAME_FILE` 形式出現也算數。**在 image 內整份 skip**——那兩個檔案不在 image 裡，
  而在 image 內跑整份 suite 是這個專案的慣例；skip 說的是「這題沒問」，pass 會是「答錯了」。

- **覆寫式 `rename` 的目標走 `purge()`，不是刪一列**（2026-08-06）。先 `_set_trashed` 再
  `purge()`，所以整棵子樹（含已在垃圾桶的子項）都被銷毀。`purge` 拒絕活節點的規則保留下來，
  順帶讓兩步之間崩潰的結果是「目標在垃圾桶裡」而不是「刪到一半」。
  **`gone` 因此從 rename 自己的 staging 拿掉**：`purge` 已經 stage 並 promote 過那次移除，
  同一個操作裡對同一個目錄 stage 兩次會讓後者覆蓋前者。

- **`scripts/find_orphans.py` 只列不刪，而且沒有開關可以讓它刪**（2026-08-06）。
  刪除要能安全，前提是「還被引用的 message id」這個集合是完整的，而它是直接從 `nodes.chunks`
  讀出來、沒有驗任何節點的 tag——所以能改資料庫的人只要拿掉一個 chunk 引用，就能讓這支工具
  替他把附件銷毀。要開放刪除就得先驗過每一個節點的 tag，那需要主金鑰，也就是需要密碼，
  那是另一支工具、另一種風險範圍。順帶：**它看不到被中斷的覆寫留下的附件**，那些仍被游離節點
  引用著，不是孤兒，歸 `sweep_incoming` 管。

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

- **上傳失敗回報四個欄位，不是三個**（2026-08-06）。拍板的形狀是三個數字
  （`chunks_uploaded` / `attachments_released` / `orphans`），實作多了一個布林 `stale_node`，
  且不動任何既有欄位。**理由是那三個數字會說謊**：`_rollback()` 先刪 Discord 附件、後刪
  node 那一列，而兩件事需要的依賴不同。上傳 30 MiB 途中停掉 MongoDB 的實測結果是附件全部
  收回（`orphans=0`）但那一列刪不掉——**清單上是一個 9 MiB、讀起來 `404 Unknown Message`
  的檔案**，而只看三個數字的介面會說「這個檔案不存在，可以直接重試」。
  刪不掉那一列的失敗仍然被吞掉（unwinding 的寫入路徑沒有更好的事可做），改變的只是它
  不再對呼叫端沉默。`code` 欄位也一併補上，讓前端用與 `integrity_failure` 同一套機制判斷，
  而不是比對字串。**已有意義的錯誤保持原意**：名稱衝突仍然是 409，不會被壓平成 `upload_failed`。

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

**2026-08-09 · 協作規則長出強制層與使用者層** — 737 項測試（+23），`--db=sqlite` 734 過 3 skip，
`node --test` 16 過。新增的 23 項是 `test_push_guard.py`（18）與 `test_ui_login.py`（5）。

- **`client/app` 從此有瀏覽器層驗收。** 架構見上方決策。值得記的是它被證明有效的方式：
  把建好的 bundle 裡 20 處 `/api/` 全部改壞、伺服器一行不動，**`test_ui_login.py` 5 條紅了 3 條，
  `test_web.py` 60 條全綠**。紅的正好是依賴 API 接線的那三條（登入、reload 後仍在線、
  建資料夾），綠的那兩條本來就不該紅（表單有沒有 render、錯密碼留在原地）。
  這個數字是「內層測試對沒接上外層完全免疫」第一次有可複現的證據，不再只是 `SOP.md` 的論述。
- **`built_client` fixture 會擋住過期的 bundle**：`client/app/dist` 比 `src` 舊就直接 fail 並印出
  `npm run build`，不自動幫忙建。自動建會把「建置壞了」藏成十秒後的測試失敗。
- **push 攔阻的 hook 自己壞了一次，而且是掛上去之後才壞的**——單元測試 18 項全綠，
  真的接進 `PreToolUse` 之後擋掉了下一個無關的指令。根因與修法進 `SOP.md`。
  教訓歸納成一句：**跑在別人程序裡、吃別人餵的 stdin 的東西，測試餵什麼就只驗到什麼。**
- **`.claude/settings.json` 在 `.gitignore` 裡**，所以 hook 腳本進版控、啟用它的設定不會。
  換一台機器要自己補 `hooks.PreToolUse` 那段，`.claude/hooks/block-push-main.py` 的檔頭有說明。

**2026-08-06 · 垃圾桶索引，以及 `sweep_incoming` 對真依賴的驗收** — 662 項測試（+5），
pyflakes 乾淨，四個突變全被抓到，production 重啟後乾淨啟動。
- **索引那半見上方決策，不複述。** 值得記的是驗證順序：先對真 mongod 問語意（`$lte` 到底匹不匹配
  null）、再寫程式碼，而不是寫完再驗。這個專案在同一個位置錯過一次（`$exists: false`），
  而那次的成本是三天綠燈加上一台起不來的伺服器。
- **`sweep_incoming` 的附件釋放那一半，這次是真的驗了**：dev stack、真 Mongo、真 Discord，
  12 MiB 兩塊的固定種子 payload。流程是上傳→覆寫到第一塊提交→**SIGKILL 掉行程**→重啟。
  **必須用 kill 而不是斷線**：斷線那條路會自己 unwind 並清乾淨（那正是上一輪修的），
  `sweep_incoming` 存在的理由是行程沒機會善後。結果：附件在 Discord 上從 `exists: true`
  變成 **404 Unknown Message (10008)**，原檔 12,582,912 bytes、sha256 相符，
  收尾對帳 2 個活節點 / 0 垃圾桶 / 0 游離 / 2 個 chunk 引用。
- **時間是用改 `modified_at` 跳過的，不是等一小時。** `INCOMING_MAX_AGE_HOURS` 的下限是 1
  且理由正當（0 會收掉此刻正在進行的上傳），而時間戳刻意不在 tag 涵蓋範圍內——所以往前調
  兩小時不會偽造任何被保護的東西，sweep 之前仍然驗過那個節點的 tag。
- **dev stack 被順手搬到現行設定上**：它先前跑的還是 docker secret 之前的那一版
  （`SFTP_PASSWORD` 還是環境變數，也沒有 `TRASH_*`），等於從來沒載入過這些程式碼。
  現在有自己的 `secrets/sftp_password.dev`。**`WEB_COOKIE_SECURE=0` 是為了這次驗收關的**
  ——aiohttp 不會把 Secure cookie 送到 http，而瀏覽器對 localhost 的例外不適用於它。
- **dev stack 與 production 共用同一個 Discord DM 頻道。** 這次無所謂（追的是特定 message id），
  但**拿 `find_orphans.py` 對 dev 資料庫跑會把 production 的附件全部報成孤兒**。

**2026-08-07 · 獨立單機版的後端落地** — 702 項測試（+38），**外加整套測試在真 SQLite 上再跑一次**
（699 過、3 skip），pyflakes 乾淨，`dist-standalone/discord-drive.exe`（17 MB）建得出來也跑得起來。
`vfs.py` **一行都沒改**。

- **做法是「寫一個長得跟 Motor 一樣的 SQLite 後端」，不是把 `vfs.py` 改寫成 SQL。**
  整個專案對資料庫的呼叫只有三個 collection、六個方法、四個查詢運算子、兩個更新運算子——
  那已經是一個介面，只是沒被命名。方案與被否決的替代方案寫在 `design-standalone.md`。
- **驗證方式才是這一輪的重點**：既有測試套件本身就是一致性測試。`pytest --db=sqlite`
  把 `fake_db` fixture 換成真的 SQLite，其餘一律不動。**這比另外寫一份 SQLite 專用測試強得多**，
  因為它涵蓋的是真正會跑的路徑，用的是為 MongoDB 語意寫的斷言。
- **抓到兩個只有這樣才會現形的 bug：**
  1. **SQLite 的索引名是 per-database 而不是 per-collection**，所以 `nodes` / `keystore` /
     `users` 各自要的 `id_1` 互相覆蓋，啟動後**只有最後一個真的存在**，另外兩個唯一索引
     無聲消失。已改成在 SQL 層加表名前綴。**在那之前 664 項測試全綠**——因為沒有任何一項
     測試曾經證明過重複鍵會被拒（`fakes.py` 明講它不強制唯一性）。教訓進 `SOP.md`。
  2. **`test_swapping_two_filenames_is_caught_on_both` 描述的竄改真實資料庫會拒絕。**
     直接對調兩個檔名，中間會有一瞬間兩個活節點同名，唯一索引不允許——MongoDB 也不允許。
     那條測試從來沒有在真環境重現過的可能。已改走一個暫用名，**斷言與結果完全不變，
     只有路徑換成攻擊者真的走得通的那條**。
- **`PRAGMA table_info` 不列 generated column**，要用 `table_xinfo`；用錯的症狀是
  「第一次啟動正常，之後每一次都失敗」。有測試釘住。
- **密碼刻意不寫進設定檔**：`drive.env` 與資料庫同一個目錄，寫進去等於鎖跟鑰匙放一起。
  沒有 `SFTP_PASSWORD` 時在終端機問，不落地；`SFTP_PASSWORD_FILE` 與環境變數照舊可用。
  **Electron 外殼沒有終端機，所以 packaged app 的密碼來源是上方那條 `[now]`，我停在那裡沒做。**
- 索引宣告仍然只有 `db.py` 那一份，兩個後端共用；`sqlitedb.py` 把同樣的請求翻譯成 DDL，
  所以不可能漂移。`test_db_indexes.py` 釘住那一份宣告，因此同時釘住兩邊。

**2026-08-07 · 獨立單機版：Electron 外殼接上後端，雙擊開啟這條路打通** — 714 項測試（+12），
`--db=sqlite` 711 過 3 skip，`node --test`（含拿真的 `discord-drive.exe` 跑的整合測試）16 過，
pyflakes 乾淨。三個產物都重建過並各自驗證：`discord-drive.exe`、`DiscordDrive-*-portable.exe`、
打包後的 `win-unpacked/`。

- **`src/main.py` 的 `_wait_for_shutdown` 加了 `extra_stop` 參數**，預設 `None`——對容器那條路
  完全沒改變行為，只有單機版會傳東西進來。**這是把 Windows 上「GUI 母行程殺不掉自己開的
  console 子行程」這件事量出來之後，才寫的**：`child.kill()` 不管傳哪個訊號名稱，在 Windows
  一律是強制終止；`taskkill` 不加 `/f` 對沒有視窗的 console 行程直接拒絕（「這個處理程序只能
  強制終止」）。**唯一測出來有效的是關閉子行程自己的 stdin**——`main.py` 的 drain 邏輯就會照
  正常路徑跑完，這是拿一支小的 asyncio 探針腳本，在 Node 這邊分別試過四種 kill 方式量出來的。
- **`src/standalone.py` 因此多了 `DISCORD_DRIVE_STDIN_LIFECYCLE` 這個環境變數**：設了它，密碼
  改成從 stdin 讀一行（外殼在密碼視窗按下確認後餵進去），而不是 `getpass`；且會在阻塞讀密碼
  之前先印一行 `AWAITING_PASSWORD`。**這一行是特地為了避免用「猜多久算逾時」去分辨「還沒設定」
  跟「正在等密碼」**——新解壓的 exe 第一次執行常被防毒軟體掃描拖慢，固定的短逾時會把「還在
  啟動」誤判成「這是第一次執行」。
- **`client/shell/backend.js`（新）**：擁有子行程整個生命週期——`status()` 判斷首次執行／等密碼／
  執行檔不存在／出錯，`start()` 送密碼並輪詢 `/api/health`，`stop()` 關閉 stdin、給寬限時間、
  逾時才強制終止。`readWebPort()` 直接讀 `drive.env` 裡的 `WEB_PORT`，不去解析 log 行——
  這樣 `main.py` 改 log 格式不會弄壞這裡。
- **`client/shell/local.html`（新）**：在這台電腦上執行的畫面，跟 `setup.html`
  共用同一個視窗與同一份 preload（`window.dd` 管遠端、`window.ddLocal` 管本機，
  因為 Electron 的 preload 是視窗建立時就定了、換頁不能換）。
- **驗證分三層，一層比一層真**：(1) 純邏輯的 Node 單元測試；(2) 對著真的
  `dist-standalone/discord-drive.exe` 跑的整合測試，包含刻意填一個看起來像真的、其實無效的
  Discord token，確認密碼真的透過管線送達、真的解開了 keystore、真的一路跑到 Discord 那一步
  才失敗——這比「跑起來沒有 crash」強得多；(3) 真的透過 Chrome DevTools Protocol 連進**正在跑的
  Electron app**（先開發模式、後來是完整打包出來的 `win-unpacked/`），點擊真正的連結、呼叫真正
  的 `window.ddLocal.status()`，來回走過完整的 renderer → preload → IPC → 子行程這條路。
  第 (3) 層驗到一個真的 bug：**沒有它就不會發現**——見下方。
- **驗證途中連到使用者自己先前留下的真實 `drive.env`**（真 bot token、真使用者 ID、
  真 `SFTP_USER`，是使用者自己那輪「測好 1~7」驗收留下的）。發現後立刻停手、
  換成 `--user-data-dir` 隔離出乾淨目錄重測，**沒有送出密碼、沒有動到 `drive.sqlite3`**——
  它只會在密碼真的解開 keystore 之後才被讀寫，而這一步從沒發生。時間戳可查證。
- **打包設定加了 `extraResources`**：`discord-drive.exe` 現在會被複製進
  `resources/backend/discord-drive.exe`，跟 `app.asar` 平行放，因為它要能被當成真正的檔案
  執行、不能被封進封存檔。**這件事有先後順序**：`discord-drive.spec` 要先建過，
  `npm run dist` 才找得到東西可以複製；`BUILD.md` 已經補上這個順序。

**2026-08-07 · 獨立單機版用真 bot token 實地驗收，通過** — 建 bot、上傳、讀回比對、覆寫、
**關閉行程再重開後資料仍在**、SFTP 連線、網頁登入操作，全部過。這是唯一我這邊驗證不到的一段
（我手上只有假 token，卡在 401）。**「關閉再重開」這一項特別重要**：SQLite 用 WAL 模式，
`-wal` 檔沒 flush 乾淨的話症狀是「這次跑起來正常、重開後資料不完整」，只有真的走過一次
process 生命週期才驗得到，單元測試不會告訴你答案。**單機版的後端到這裡算是完整可用**，
剩下唯一的缺口是 packaged GUI 的密碼決定（見上面 `[now]`），不影響現在這個終端機版本能不能用。

**2026-08-06 · 用真密碼在 UI 上手動驗收，掛了四輪的 `[next]` 關閉** — 覆寫中途關掉分頁，
舊檔仍活在原本的路徑上、內容完好，垃圾桶是空的。**這個結果是通過，不是失敗**：
舊版進垃圾桶只發生在覆寫**成功**時（`close()` 才換名），中斷的覆寫從頭到尾沒有 commit，
所以什麼都沒被取代。交接文件當初把驗收語句寫成「確認舊的還在垃圾桶裡」，那句描述的是
另一條路徑，照著念會把正確結果讀成缺陷。**驗收語句要寫成「舊位元組有沒有活下來」，
不是「舊節點跑到哪裡去」**——後者是實作細節，而且會隨成功與否而不同。

**2026-08-06 · 清掉 `/blueprint` 開出的兩個 `[now]` 與四個 `[next]`** — 657 項測試（+79），
image 內 624 過 33 skip（那 33 項讀的是 repo 的部署描述檔，image 裡沒有），pyflakes 乾淨，
production stack 重建後乾淨啟動，另對真的 mongod 跑過 16 項專門驗證。決策五條見上方，不複述。
- **並行 `_stage_entries` 與覆寫毀舊檔兩條 `[now]` 都修掉了**，各自附回歸測試，
  兩者都做過突變測試（把鎖換成 no-op：8 項裡 5 項變紅，紅的原因正是
  `directory entries failed integrity verification`）。
- **最重要的產出不是修法，是假件先被改了。** `tests/fakes.py` 的每個資料庫方法現在都
  `await asyncio.sleep(0)`，因為真的 MongoDB 每次呼叫都會讓出協程。
  在那之前**這個 bug 連測都測不出來**：兩個協程會一前一後各自跑完，任何交錯都到不了，
  578 項綠燈對它一句話都沒說。這與 08-06 上一輪「假件把前提也一起假掉了」是同一課，
  只是上次是 URL 永不過期，這次是資料庫呼叫不會讓出。
- **`_stage_entries` 現在會檢查呼叫端持有鎖，沒有就 `RuntimeError`。** 漏掉一個呼叫點的
  症狀是「某個目錄從此列不出來」，發生在很久以後、發生在剛好同時傳兩個檔案的人身上，
  離出事那一行非常遠。這種距離只能用當場爆炸來縮短。
- **修覆寫那條時，兩個既有測試必須改寫，因為它們斷言的正是被推翻的行為**
  （`test_truncating_overwrite_releases_old_attachments`、
  `test_a_truncated_file_that_then_fails_stays_empty`）。後者的 docstring 寫著
  「`O_TRUNC` 已經 commit 空的了，沒有東西可以還原」——那句話從頭到尾是對的，
  它描述的就是這個 bug，只是被當成規格寫下來。**測試會把缺陷寫成契約，然後保護它。**
- **`sweep_incoming` 是這條修法的必要配套，不是附加功能。** 游離節點的附件
  `find_orphans` 看不到（Discord 那側確實還有節點指著它），所以沒有這支 sweep
  就是一條沒有任何工具能發現的洩漏。
- **踩到一個測試互相污染、症狀完全不指向原因的坑**，已寫進 `SOP.md`：
  模組層級的 `asyncio.Lock` 屬於它被 await 的那個 loop，而 pytest 每支測試換一個 loop。
  一支測試結束時若還有 task 停在 `async with` 裡（被 abort 的 SSH 連線），
  鎖會以「已鎖住」留在字典裡，鍵是跨測試不變的 `root`——**之後每一支測試都卡死**。
  單獨跑全過、一起跑就掛。抓法是 autouse fixture 印出 registry 與持有者 task。
  真正的錯是我等錯事件：共用的 `closed` Event 被前一個連線先 set 過而忘了 clear。
- **在 image 內跑整份 suite 這個慣例，當場就抓到新測試的一個設計錯誤**：
  `test_compose_coverage.py` 讀 repo 根目錄的檔案，而 image 只有 `src/`，32 項全紅。

**2026-08-06 · 重新產出 `BLUEPRINT.md`（第三輪）** — 578 項測試（±0，全掃過程沒有改任何程式碼），
基準 `57f2779`，舊版備份在 `BLUEPRINT.md.bak`。掛了四輪的那條 `[next]` 就此關掉。
- **掃出五個新問題，兩個是「高」**：並行 `_stage_entries` 的競態、被中斷的覆寫上傳毀掉舊內容，
  兩條都已列在上方待辦並標明**需要拍板**。另外三個是 `TRASH_*` 在 compose 下無效、
  覆寫式 `rename` 孤兒化垃圾桶子項、孤兒附件沒有回收工具。
- **兩個高嚴重度問題有同一個特徵：它們都是已修缺陷的另一個入口。**
  目錄鎖死那條在 08-06 修的是「上傳失敗」這個觸發源，但同一個 `entries_mac_pending`
  欄位在並行下會被覆蓋，故障結果一模一樣；覆寫毀舊檔那條則是 `_rollback()`
  「既有檔案一律不刪」這條正確規則的另一面——truncate 已經先 commit 過了。
  **教訓：修掉一個觸發源不等於修掉那個故障模式，要回頭問「還有誰能走到同一個狀態」。**
- **`DISCORD_MAX_CONCURRENCY` 那個形狀犯了第二次**（`TRASH_*`）。compose 檔案裡還留著
  第一次的五行註解。**只補三行不算修好**，要有東西去斷言 `config.py` 與 compose 的涵蓋率。

**2026-08-06 · docker secret、批次刪除的進度與中斷，以及兩個被驗出來並修掉的缺陷** —
578 項測試（+55），pyflakes 乾淨，前端建置通過，docker secret 已在 production 上
驗過（乾淨啟動、沒有重建金鑰、`docker inspect` 只剩路徑）。
- **`SFTP_PASSWORD` 走 docker secret**。`config.py` 支援 `*_FILE` 後綴（`DISCORD_BOT_TOKEN` /
  `MONGO_URI` / `SFTP_PASSWORD` / `SFTP_PASSWORD_OLD`），compose 加 `secrets:`。
  **同時設變數與 `_FILE` 是拒絕，不是取捨優先權**——兩種順序都是在猜操作者的意思，
  猜錯的代價是伺服器用錯的密碼起來。**只剝掉一個結尾換行**：`echo pw > file` 會加一個，
  但貪心地剝掉所有空白會毀掉合法結尾是空格的密碼，而這個密碼差一個 byte 在啟動時
  跟完全錯誤沒有區別。遷移用 `scripts/adopt_password_secret.py`，**它從執行中的容器
  搬密碼而不是重新解析 `.env`**——`.env` 的解析權在 compose 手上，用第二個 parser
  去猜第一個 parser 的輸出是沒有必要的風險。
- **批次刪除變成可輪詢、可中斷的 job**（`src/jobs.py`）。選 job id + 輪詢而不是 SSE：
  工作活得比請求久，也應該活得比分頁久。**分母在動工之前就算完**（`purge_cost`），
  否則進度條會跑到 90% 然後停住。**取消只落在附件與附件之間**——那是唯一一個
  「節點還在垃圾桶、可以再刪一次」的狀態，再往後停就會產生沒有人叫得出名字的孤兒。
  **一棵樹同時只准一個 job**，這是正確性不是政策：兩個並行的 purge 會互相蓋掉
  父目錄的 `entries_mac_pending`。
- **兩個缺陷是這一輪最重要的產出**，都是去驗「上傳中途斷線的清理路徑」時掉出來的，
  重現在 `tests/test_rollback_leaves_the_directory.py` 與 `tests/test_upload_disconnect.py`。
  - **一次失敗的上傳會讓所在目錄永久列不出來**。**既有缺陷**，重現只用
    `FakeDiscord.fail_uploads_from`（既有失敗測試本來就在用的開關），
    在根目錄發生就是整個硬碟。機制：建節點時把子項寫進父目錄的 `entries_mac` 並 promote，
    `_rollback()` 刪掉節點卻沒有把那個 tag 還原，於是 tag 永遠涵蓋一個不存在的子項。
    修法照 `purge()` 的 stage → 改動 → commit 三步；staging 自己失敗時**不刪節點**，
    改回報 `stale_node`——那代表目錄本來就已經對不上，刪下去只會更難救。
  - **斷線會把截斷的檔案當成完整的存下來**。aiohttp 在客戶端消失時**取消 handler task**，
    而 `CancelledError` 是 `BaseException`，`except Exception` 接不到，`finally` 照跑
    `close()`，而 close 就是 commit。新增 `DiscordFile.abort()` 把「不 commit 地收尾」
    變成可以說出口的東西，unwind 用 `asyncio.shield` 包住——這個 task 本來就在被拆掉，
    unwind 做到一半被打斷正是孤兒的來源。
  - **順序不能反**。先修目錄那條再修斷線：只修斷線會把「一個被截斷的檔案」
    升級成「它所在的資料夾整個不能用」。
- **既有測試為什麼沒抓到目錄那條**：它們斷言失敗上傳回傳的 500 形狀
  （`chunks_uploaded` / `orphans` / `stale_node` 全是對的），然後就結束了，
  **從來沒有人在那之後列一次目錄**。被測的命題是「上傳會回報它留下什麼」，
  而不是「硬碟還能用」。
- **教訓**：`_url_cache` 與斷線這兩項之所以是「只有真依賴才驗得了」，不是因為缺少
  真依賴，是因為**假件把前提也一起假掉了**——FakeDiscord 的 URL 永不過期，
  於是「過期後會怎樣」連問題都問不出來。換成一個會真的拒絕過期簽章的 stub 之後，
  用不到 Discord 也驗得到兩條分支。真正需要真依賴的，是「Discord 是否還是這個格式」。

**2026-08-06 · 上傳失敗的回報，與 UI 對真依賴的第一輪驗收** — 523 項測試（+8），pyflakes 乾淨，
**實地驗收 14/14**（真 MongoDB、真 Discord），**已上線**。`vfs.py` 的 `failure_tally`、
`web.py` 的 `_upload_failed()`、前端的 `UploadFailedDialog`。
- **驗收方式本身是這一輪的產出**：UI 那一輪寫成可重跑的腳本而不是一次性操作，跑在一個獨立的
  dev stack 上（自己的 Mongo volume、自己的主金鑰、2223/8081）。**真密碼因此完全沒有進入
  任何紀錄**，而驗到的仍然是真依賴。腳本與 stack 都不進版本控制（`.gitignore` 的 `dev-stack/`）。
- **三個數字會說謊，是實地才發現的**——見上方決策。這一條的教訓不是「多加一個欄位」，是
  **回報「我清乾淨了」的程式碼，它自己的清理動作也可能失敗**，而失敗的那半正好是沉默的那半。
- **兩個 bug 都只在非典型路徑上**：裸 token `upload_failed` 漏進傳輸清單（測試斷言的是 JSON，
  不是使用者看到的字）；殘留節點只在「Discord 活著但 MongoDB 死了」時出現。
  單一依賴故障的測試造不出來，要兩個依賴的狀態不一致。
- **`FakeDiscord.fail_deletes` 與 `FakeCollection.fail_deletes` 是新的注入點**。沒有它們，
  `orphans` 與 `stale_node` 這兩條路徑在假件上永遠是 0，而那正是它們存在的意義。

**2026-08-05 · 前端與桌面外殼（Client UI 第 5、6 步），以及垃圾桶索引的修正** — 515 項測試（+24），
image 內同樣 515 過，**實地驗收：檔案操作矩陣 61 項全過**，另加雙 session 的一組與收尾對帳
（`nodes=1 live=1 users=1 keystore=1 chunk_records=0`，前後一致），**已上線**。
`client/app/`（Vite + React）、`client/shell/`（Electron）、`vfs.search()`、`web.py` 的 static route。
- **起容器時就撞到一個真 bug：垃圾桶的部分唯一索引從 2026-08-03 到這天從來沒建成功過**，
  伺服器根本起不來。文法限制與判別方式在 `SOP.md`，修法在上方決策。這裡只記時間軸：
  **綠色的 491 項測試撐了三天，因為那三天沒有在真環境重啟過。**
- **UI 那半刻意不用真密碼驗**——那組密碼是主金鑰的包裝，不該進入任何對話或指令紀錄。
  改用 fakes 後端跑同一份 `dist/` 與同一份 `web.py`，雙連線的行為逐項驗過。
  **代價是「用真密碼在 UI 上手動點一輪」至今沒做過**，見上方 `[next]` 的驗證缺口那一條。
- **證明 exe 真的載到 SPA 的方法**：啟動後去 Electron 的 cache 裡撈一段只存在於我們 bundle 的
  字串。與 2026-08-02 那條 keystore fingerprint 同類——**要驗的是「它有沒有載到我們的東西」，
  不是「它有沒有畫出東西來」**，後者連載到一頁快取的舊版都會通過。
- **`electron-builder` 的 `files` 白名單漏一個檔案就是打包後 crash**（`server-url.js` 漏過一次）。
  本機 `npm start` 一切正常，因為那時讀的是原始目錄，不是 asar。加檔案後用
  `npx @electron/asar list` 對一次。
- 驗收抓到的兩個 bug 有同一個形狀，**都只在「第一次」或非典型輸入時發生，happy path 一次都碰不到**：
  初次量測到的 `0 × 0` 被當成「視窗太小」而永久蓋上遮罩（之後不會再有 `resize` 事件來修正）；
  伺服器位址的正規化把 `http://` 硬黏在前面，於是 `file:///etc/passwd` 變成一個 host 叫 `file`
  的合法 origin。後者抽成 `server-url.js` 並補了測試。

**2026-08-03 · 垃圾桶** — 491 項測試（+36），`TAG_VERSION` 2→3，附一支 `scripts/migrate_tag_v3.py`。
`src/vfs.py` 的 `trash()` / 還原、`db.py` 的部分唯一索引、背景 sweeper、`DELETE /api/dir?recursive=true`。
四條設計決策（標記在節點上並納入標籤／保留期 30 天由 sweeper 執行／`rm` 進垃圾桶而 `rmdir` 契約不變／
還原撞名走 Windows 對話框行為）見上方，不複述。
- **這一輪沒有實地驗收，也沒有在真環境重啟過。** 上面每一條變更紀錄都寫著「實地驗收 N/N」，
  唯獨這一輪沒有——而那個空白正好就是後果：一個 MongoDB 會直接拒絕的 `partialFilterExpression`
  就這樣上線，兩天後才在 2026-08-05 起容器時炸出來。**這一條補記於 2026-08-06。**

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
