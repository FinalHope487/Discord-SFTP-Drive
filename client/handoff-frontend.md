# 前端交接（Client UI 第 5 步）

原型：`Discord Drive 桌面版.dc.html`，17 個狀態，右下角的狀態列表可以逐個跳。
資料是寫死的 mock。這份文件寫的是「換成真 API 要動哪裡」，以及**一個現在還不存在的端點**。

---

## 1. 缺一個端點：搜尋

`web.py` 沒有搜尋路由。原型的搜尋畫面上有一條 amber 橫幅寫著這件事，不是忘了接。

跨整棵樹比對名稱有兩條路，成本差很多：

**(a) 全樹掃描**。用 session 的金鑰沿 `parent_id` 走，邊走邊驗 `dir_entries_tag`。
不加任何欄位、不動 tag 涵蓋範圍。代價是 O(節點數) 的 Mongo 查詢，而且**每一層都要驗標籤**
——不驗就等於開一條「搜尋看得到、開啟看不到」的旁路，和 `scandir` 繞過 `list_dir` 那個
bug 同一類（`SOP.md` 有記）。

**(b) 另建索引**。`nodes` 加一個正規化過的檔名欄位下 index。快，但**檔名是要保護的資料**：
`node_tag` 涵蓋 `filename`，多存一份沒被 tag 蓋住的副本，等於讓有 DB 權限的人改那一份而
不被抓到——除非把它也納入 tag，而那會觸發 `TAG_VERSION` 3→4 與一次真正的 migration
（2026-08-01 那次沒寫 migration 的代價已經記在 ROADMAP 了）。

**建議先做 (a)**，因為現在資料量小，而且它不需要任何決策。介面已經照 (a) 設計：搜尋結果
帶完整路徑、有掃描進度的位置。真的慢到不能用再回來評估 (b)。

建議簽名：

```
GET /api/search?q=<str>&limit=<int>
→ 200 {"results": [{"path": str, "is_dir": bool, "size": int, "mtime": int}], "truncated": bool}
→ 401 session 無效
```

`limit` 有伺服器上限，`truncated` 告訴前端還有更多。**不做正規表示式、不做內容搜尋**
——內容搜尋要解密每一個 chunk。

---

## 2. mock 換成真呼叫

原型裡的假資料集中在三個地方，換掉這三處就接上了：

| 原型 | 真來源 |
| --- | --- |
| `FS` / `TREE`（模組頂層常數） | `GET /api/files?path=` |
| `s.trash` | `GET /api/trash`（若尚未存在，見下方 §4） |
| `this.entries()` | 上面兩者 |

| 動作 | 端點 |
| --- | --- |
| 登入／登出／查連線 | `POST /api/login`、`POST /api/logout`、`GET /api/session` |
| 列目錄 | `GET /api/files?path=` |
| 單一節點資訊 | `GET /api/stat?path=` |
| 下載 | `GET /api/file?path=` |
| 上傳 | `PUT /api/file?path=` |
| 刪除檔案 | `DELETE /api/file?path=` |
| 建目錄 | `POST /api/dir` |
| 刪目錄（整棵） | `DELETE /api/dir?path=&recursive=true` |
| 改名／搬移 | `POST /api/rename` |

### 上傳進度是假的

原型的分塊進度條是動畫。`PUT /api/file` 是**整檔上傳**，瀏覽器只知道自己送出多少 bytes，
不知道伺服器切到第幾塊。兩個選項：

- **維持整檔**，進度條改成顯示 `XMLHttpRequest.upload.onprogress`（送出的 bytes / 總 bytes），
  把「45/217 塊」那行拿掉。**改動最小，建議先這樣。**
- **開分塊端點**（`PUT /api/file/chunk`），前端自己切 9 MiB 送。真進度、可續傳，但寫入路徑
  變第三條，`_rollback()` 的附件釋放責任要再驗一次。

### `_rollback()` 的責任在 HTTP 這條路上要自己負責

拍板決策寫在 `vfs.py` 的 docstring 裡：`_rollback()` 只刪這個 handle 建立的附件，既有檔案
一律不動。HTTP 上傳是**新的呼叫點**，所以要自己確認上傳失敗後 Discord 端沒有殘留。

原型的「上傳失敗 · 已回滾」狀態就是這條的介面：明確說出**已釋放 44/44 個附件、動到 0 個
既有檔案、0 個孤兒**。這三個數字要從後端真的回報，不要前端猜。建議 `PUT /api/file` 失敗時回：

```
→ 5xx {"error": "upload_failed", "chunks_uploaded": int,
       "attachments_released": int, "orphans": int, "detail": str}
```

`orphans` 不為 0 的時候前端要換一種說法——那是要人去對帳的狀況，不是按一下重試就好。

### session 過期

伺服器上限是閒置 10 分鐘 / 絕對 2 小時，client 只能往短調。任何 401 都要跳「連線到期」
遮罩（原型的 `expired` 狀態），**不要靜默重新登入**——重新登入要跑兩輪 Argon2、約 250ms，
而且需要密碼，沒有密碼就沒有主金鑰。

底部狀態列顯示剩餘時間，資料來自 `GET /api/session`。**不要用前端計時器自己算**：那會和
伺服器的真實期限漂移，而漂移的方向剛好是「以為還有時間」。

### 429 / 503

登入端點有並發上限與佇列上限，超過回 503。原型有兩個狀態（`locked` = 登入鎖定、
`busy` = 排隊已滿），文案已經寫好：鎖的是來源加裝置，**永遠不鎖帳號**。

批次刪除撞 Discord 的 429 是另一件事，走原型的 `throttled` 狀態：顯示進度、429 次數、
可中斷。續跑從剩下的接著做，**已刪掉的不會回來**——這句話在介面上就要說。

---

## 3. 完整性失敗是終局，不是錯誤

驗證失敗**不重試、不降級、不回傳任何位元組**。原型的橫幅明確說「不是網路問題，也不是
暫時性錯誤」，這個語氣要保留：它代表有人在沒有金鑰的情況下改了資料庫或 Discord 那一側。

列目錄只驗**子項集合**（誰在裡面），不驗每個子項自己的 tag。所以清單裡的檔案大小是未經
驗證的，開啟或 stat 才會失敗——原型的清單裡每一列都有一個盾牌圖示，這是它存在的理由。

---

## 4. 垃圾桶端點

`vfs.py` 有 `trash()` / `list_trash()` / `restore()`，HTTP 那一側目前只有
`DELETE /api/dir?recursive=true`。UI 需要的至少還有：

```
GET    /api/trash                         → 列出，含 trashed_at 與還原後的路徑
POST   /api/trash/restore                 {"id": str, "on_conflict": "replace"|"skip"|"keep_both"}
DELETE /api/trash?id=<str>                永久刪除單一項目
DELETE /api/trash                         清空
```

`on_conflict` 預設是**拒絕**，和 `rename` 不覆蓋的語意一致；前端收到衝突才彈對話框
（原型的 `conflict` 狀態，Windows 式的取代／略過／比較兩個檔案資訊）。**「取代」時舊檔進
垃圾桶**，不直接消失。

永久刪除要回報釋放了幾個附件，原型的確認對話框已經在顯示這個數字。

---

## 5. 排在第 5 步後面的（都不是前端）

- **`SFTP_PASSWORD` 走 docker secret**。2026-08-02 拍板要做，排在 UI 之後。
  compose 加 `secrets:`、`config.py` 支援 `*_FILE` 後綴。
- **重新產出 `BLUEPRINT.md`**。等 UI 落地再跑 `/blueprint`。
- **多使用者第 4 步**：管理 CLI、建帳號、per-user 配額、刪帳號。
  **必須先有密碼救援路徑**——模型 B 之下忘記密碼就是那個使用者的資料真的救不回來。
  這一步也會掀出「批次刪整棵樹撞 429」，前端的 `throttled` 狀態已經先把介面備好了。

已知且接受、**不要再當成待辦**的：整檔 rollback、真正同時寫入是後寫的贏、跨 handle 的
metadata 不同步、權限位與時間戳不受完整性保護、不支援符號連結、跨使用者分享。
