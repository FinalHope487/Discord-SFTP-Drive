# 後端待辦（UI 第 5 步擋在這裡）

前端原型：`Discord Drive 桌面版.dc.html`，17 個狀態全部畫好了，資料是 mock。
下面每一項都是「前端已經備好介面、等後端給東西」。細節在 `handoff-frontend.md`。

排序就是建議的施工順序。

---

## A. 新端點：搜尋 — 阻塞

`web.py` 沒有搜尋路由。原型的搜尋畫面上那條 amber 橫幅就是在說這件事。

**做法先選 (a) 全樹掃描**：用 session 金鑰沿 `parent_id` 走，**每一層都驗 `dir_entries_tag`**。
不驗就開了一條「搜尋看得到、開啟看不到」的旁路，和 `scandir` 繞過 `list_dir` 那個 bug 同類。
不加欄位、不動 tag 涵蓋範圍、不需要 migration。

（(b) 另建正規化檔名索引比較快，但檔名被 `node_tag` 蓋著，多存一份沒被 tag 保護的副本
＝ 有 DB 權限的人可以改那一份而不被抓到。要做就得納入 tag → `TAG_VERSION` 3→4 → 真 migration。
現在資料量小，不值得。）

```
GET /api/search?q=<str>&limit=<int>
→ 200 {"results": [{"path": str, "is_dir": bool, "size": int, "mtime": int}],
       "truncated": bool}
→ 401 session 無效
```

- `limit` 要有伺服器上限，`truncated` 告訴前端還有更多。
- **不做正規表示式、不做內容搜尋**（內容搜尋要解密每個 chunk）。
- 結果要帶**完整路徑**，UI 就是這樣顯示的。

---

## B. 新端點：垃圾桶 4 條 — 阻塞

`vfs.py` 已經有 `trash()` / `list_trash()` / `restore()`，HTTP 那側只有
`DELETE /api/dir?recursive=true`。UI 需要：

```
GET    /api/trash
→ 200 {"items": [{"id": str, "path": str, "restore_path": str,
                  "is_dir": bool, "size": int, "trashed_at": int}]}

POST   /api/trash/restore
  {"id": str, "on_conflict": "replace"|"skip"|"keep_both"}
→ 200 {"path": str}
→ 409 {"error": "conflict", "existing": {"size": int, "mtime": int},
       "incoming": {"size": int, "mtime": int}}

DELETE /api/trash?id=<str>          永久刪除單一項目
→ 200 {"attachments_released": int}

DELETE /api/trash                   清空
→ 200 {"items_deleted": int, "attachments_released": int}
```

- `on_conflict` **預設是拒絕（回 409）**，和 `rename` 不覆蓋的語意一致。前端收到 409 才彈
  對話框（原型的 `conflict` 狀態，取代／略過／並存，兩邊的大小與時間都要顯示 → 所以 409
  要帶 `existing` 和 `incoming`）。
- **`replace` 時舊檔進垃圾桶**，不直接消失。
- 永久刪除要回報 `attachments_released`，原型的確認對話框已經在顯示這個數字。

---

## C. 上傳失敗要回報三個數字 — 阻塞

拍板決策在 `vfs.py` docstring：`_rollback()` 只刪這個 handle 建立的附件，既有檔案一律不動。
HTTP 上傳是**新的呼叫點**，要自己確認失敗後 Discord 端沒殘留。

原型的「上傳失敗 · 已回滾」狀態直接說出：已釋放 44/44 個附件、動到 0 個既有檔案、0 個孤兒。
**這三個數字要後端真的回報，前端不能猜。**

```
PUT /api/file 失敗時
→ 5xx {"error": "upload_failed", "chunks_uploaded": int,
       "attachments_released": int, "orphans": int, "detail": str}
```

`orphans != 0` 時前端會換一種說法——那是要人去對帳的狀況，不是按一下重試就好。

---

## D. `GET /api/session` 要回剩餘時間 — 阻塞

伺服器上限是閒置 10 分鐘／絕對 2 小時。底部狀態列顯示剩餘時間。

```
GET /api/session
→ 200 {"user": str, "idle_expires_in": int, "absolute_expires_in": int}
→ 401
```

**前端不會用計時器自己算**——會和伺服器真實期限漂移，而漂移方向剛好是「以為還有時間」。

任何端點回 401，前端一律跳「連線到期」遮罩（`expired` 狀態）並要求重新輸入密碼。
**不會靜默重新登入**：那要跑兩輪 Argon2（約 250ms）而且沒密碼就沒有主金鑰。

---

## E. 批次刪除撞 429 要能中斷續跑 — 阻塞 `throttled` 狀態

刪整棵樹會撞 Discord 的 429。原型的 `throttled` 狀態顯示進度、429 次數、可中斷。
需要伺服器在過程中把狀態吐出來（SSE 或輪詢一個 job id 都可以，前端配合）：

```
DELETE /api/dir?path=&recursive=true
→ 進行中回報 {"deleted": int, "total": int, "rate_limited": int, "state": "running"}
→ 可中斷；中斷後 {"state": "interrupted", "deleted": int, "remaining": int}
```

- 續跑**從剩下的接著做**。
- **已刪掉的不會回來**——這句話原型的介面上就有寫，語意要對得上。

---

## F. 上傳進度：選一個

`PUT /api/file` 是整檔上傳，瀏覽器只知道自己送出多少 bytes，不知道伺服器切到第幾塊。
原型現在的分塊進度條是動畫。

- **維持整檔**（建議）。前端改成顯示 `XMLHttpRequest.upload.onprogress`，把「45/217 塊」那行
  拿掉。**後端不用動**，前端自己改。
- **開分塊端點** `PUT /api/file/chunk`，前端切 9 MiB 送。真進度、可續傳，但寫入路徑變成第三條，
  `_rollback()` 的附件釋放責任要**再驗一次**。

→ **請回覆選哪個。** 沒回覆就走前者。

---

## G. 已有端點，只要確認語意沒變

前端會直接接上，不需要改動：

| 動作 | 端點 |
| --- | --- |
| 登入／登出 | `POST /api/login`、`POST /api/logout` |
| 列目錄 | `GET /api/files?path=` |
| 單一節點資訊 | `GET /api/stat?path=` |
| 下載 | `GET /api/file?path=` |
| 上傳 | `PUT /api/file?path=` |
| 刪除檔案 | `DELETE /api/file?path=` |
| 建目錄 | `POST /api/dir` |
| 刪目錄（整棵） | `DELETE /api/dir?path=&recursive=true` |
| 改名／搬移 | `POST /api/rename` |

登入端點的並發上限／佇列上限超過回 503：原型有 `locked`（登入鎖定）與 `busy`（排隊已滿）
兩個狀態，文案已寫好——**鎖的是來源加裝置，永遠不鎖帳號**。請確認後端行為一致。

---

## H. 完整性失敗的語氣不要動

驗證失敗**不重試、不降級、不回傳任何位元組**。原型的橫幅明確寫「不是網路問題，也不是暫時性
錯誤」，因為它代表有人在沒有金鑰的情況下改了 DB 或 Discord 那一側。錯誤碼要能和一般 5xx 分開：

```
→ 5xx {"error": "integrity_failure", "path": str, "tag": "node_tag"|"dir_entries_tag"|"chunk_tag"}
```

也請確認：**列目錄只驗子項集合**（誰在裡面），不驗每個子項自己的 tag。所以清單裡的檔案大小
是未經驗證的，開啟或 stat 才會失敗——原型清單裡每一列的盾牌圖示就是為了這件事存在。

---

## I. 不在第 5 步、但排在後面

- **`SFTP_PASSWORD` 走 docker secret**（2026-08-02 拍板）。compose 加 `secrets:`、
  `config.py` 支援 `*_FILE` 後綴。
- **重新產出 `BLUEPRINT.md`**：等 UI 落地後跑 `/blueprint`。
- **多使用者第 4 步**：管理 CLI、建帳號、per-user 配額、刪帳號。
  **必須先有密碼救援路徑**——模型 B 之下忘記密碼＝那個使用者的資料真的救不回來。
  這一步也會掀出 E（批次刪整棵樹撞 429）。

---

## 已知且接受，不要再當成待辦

整檔 rollback、真正同時寫入是後寫的贏、跨 handle 的 metadata 不同步、權限位與時間戳不受
完整性保護、不支援符號連結、跨使用者分享。
