# Roadmap：later

狀態標為 `[later]` 的待辦項目。格式、標籤與規則見 [`ROADMAP.md`](../ROADMAP.md)；狀態改了就把條目搬到對應的檔。

---

### [later] `sweep_incoming` 只在有人登入網頁時才跑
**具體細節**：2026-08-06 實地確認。它掛在 `trash_sweeper` 的迴圈上，借 session 的金鑰、只掃有 live session 的樹。純 SFTP 用法永遠不觸發，被中斷的覆寫留下的游離節點會一直佔 Discord 空間。與垃圾桶「至少這麼久」是同一個取捨（背景任務不長期持有主金鑰），但這條 user 看不到
**怎麼做**：二選一：UI 顯示游離節點數量，或接受並寫進 `docs/OPERATIONS.md`
**會改變什麼**：前者動 `src/web.py` 與 SPA；後者只動文件
**做後回退代價**：`git revert`；沒有資料格式變動

### [later] 程式碼註解與現況的漂移，加 `i18n.js` 13 個無人引用的 key
**具體細節**：`src/vfs.py:107-110` 只描述 tag version 1、2，`TAG_VERSION` 已是 3（v3 加的 `trashed_at` 沒寫進去）；`tests/conftest.py:5` 仍提到已不存在的 `AES_SECRET_KEY`。`i18n.js` 無人引用：`col.location`、`col.remaining`、`status.search`、`status.searchTruncated`、`detail.path`、`detail.verified`、`detail.verifiedNote`、`search.title`、`search.reveal`、`transfer.uploading`、`act.copyPath`、`act.copied`、`toast.uploaded`
**怎麼做**：註解直接改。13 個 key 逐個判斷是「文字多餘」還是「元件漏用」再動；`detail.verified` 那對是刻意不用的（列目錄不畫綠勾）
**會改變什麼**：註解與 `client/app/src/i18n.js`；刪 key 不改畫面，補元件會改畫面
**做後回退代價**：`git revert`；刪錯的 key 要從歷史撈回並重建 SPA

### [later] `children()` 在兩個後端都是全表掃描
**具體細節**：2026-08-07 量到。`{"parent_id": x}` 在 MongoDB 是 COLLSCAN、SQLite 是 `SCAN nodes`；`(parent_id, filename)` 是部分索引，這個查詢沒帶 `trashed_at` 條件。`live_children()` 兩邊都走索引
**怎麼做**：兩個後端同時加 `parent_id` 單欄索引，用同一份索引宣告
**會改變什麼**：改 schema（加索引），要先問 user
**做後回退代價**：`git revert` 加上對既有資料庫手動 drop 該索引

### [later] 標準版 exe 仍需要 Docker
**具體細節**：方案一（exe 只是視窗、後端是 compose）與方案二（單機版）並存，是不同產品線，維護成本雙份
**怎麼做**：未知；要收斂成一個得重新拍板
**會改變什麼**：打包流程、`README.md` 的安裝說明
**做後回退代價**：收斂後被拿掉的那條產品線要從歷史重建打包設定

### [later] 多使用者第 4 步：真正開放第二個帳號
**具體細節**：管理 CLI、建帳號流程、per-user 配額。專案定位是開源自架（見 `docs/decisions/2026-08-10-positioning-self-hosted-open-source.md`），這步只在要營運公開服務時才復活。必須先有密碼救援路徑；刪帳號會撞 429，要走既有 rate limit 且可中斷續跑。分享明確不做
**怎麼做**：方案見 `design-multi-user.md`
**會改變什麼**：認證流程、`users` collection 的寫入路徑
**做後回退代價**：已建立的第二個帳號與其資料要另外清；帳號資料無法用 `git revert` 收回

### [later] 跨 handle 的 metadata 變更不同步
**具體細節**：`_node_versions` 比對 `mac`，`mac` 不涵蓋權限位與時間戳，所以別條連線的 `chmod`／`utimes` 不觸發重新抓取。內容已同步
**怎麼做**：替 metadata 另開版本欄位（讓 `mac` 蓋住 metadata 已被否決）
**會改變什麼**：node 文件多一個欄位
**做後回退代價**：`git revert`；已寫入的版本欄位留在資料裡，舊程式會忽略

### [later] 真正同時寫入時後寫的贏
**具體細節**：跨 handle 同步保證「看得到別人已 commit 的狀態」，不是寫入互斥。這也是「只能跑一個副本」的根因
**怎麼做**：node 層級樂觀鎖（`update_one` 帶舊 `mac` 當條件）
**會改變什麼**：每一條寫入路徑
**做後回退代價**：`git revert`；寫壞的代價是資料讀不出來，要先在真 MongoDB 驗過

### [later] 路徑版 `stat` 看不到別的 handle 還在 buffer 裡的位元組
**具體細節**：同 handle 的 `fstat` 已修；跨 handle 不修，那些位元組還沒上傳，本來就不該對別人可見
**怎麼做**：維持不修，除非出現需要它的用戶端
**會改變什麼**：無
**做後回退代價**：若改成可見，`git revert` 即可

### [later] 權限位與時間戳不受完整性保護
**具體細節**：能改 MongoDB 的人可以改它們，是 tag 明確沒涵蓋的唯一一類 metadata（見 `docs/decisions/2026-07-31-integrity-tag-scope.md`）
**怎麼做**：納入要改 tag 涵蓋範圍，需要真的 migration
**會改變什麼**：`TAG_VERSION`、所有既有節點的 tag
**做後回退代價**：tag 格式升版後舊版本讀不起新資料，回退要再跑一次反向 migration

### [later] 不支援符號連結
**具體細節**：`symlink`／`readlink`／`link` 回 `FX_OP_UNSUPPORTED`
**怎麼做**：未知
**會改變什麼**：VFS 要多一種節點型別
**做後回退代價**：已建立的連結節點要另外清，否則舊程式讀到未知型別
