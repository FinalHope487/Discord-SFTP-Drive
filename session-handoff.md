# Session Handoff

依 `.claude/templates/session-handoff.md` 產出。**最後更新 2026-08-01。**

> **`ROADMAP.md` 現在沒有 `[now]`，唯一的 `[next]` 是 Client UI（2026-08-02 列入）。**
> 371 項自動化測試全過，pyflakes 乾淨，production image 內同樣 371 過。
> 實地驗收 17/17，收尾對帳 0 孤兒、0 懸空引用。

---

## 目前狀態

**服務跑在 Python 3.12 上，帶著新的完整性 tag，已上線並實地驗收過。**
線上 `nodes` 只剩 root（驗收用的檔案已清乾淨），Discord 上 0 個附件。

H2 那一段已入庫：`5392704`（程式碼）、`881d0d5`（文件）。
**注意：git 歷史在那之後被改寫過**（作者改成 `AllenOuO`、commit 訊息改英文），
所以任何舊筆記裡的 commit hash 都指向 dangling 物件，對照要用日期與訊息而不是 hash。

改動內容與決策理由不在這裡複述——**看 `ROADMAP.md` 的「已拍板的長期決策」與「變更紀錄」**。
方案與實作的四處出入在 `design-node-identity-integrity.md` 開頭的橫幅。

### 驗證方式（四層，缺一條就會漏掉東西）

```bash
./venv/Scripts/python.exe -m pytest        # 371 passed, ~30s
```

1. **371 項單元/整合測試**，`tests/test_node_identity.py` 24 項是 H2 新增的。
2. **突變測試**：把九個保護逐一拿掉，確認每一個都有測試會失敗——包含
   「`scandir` 改回直接呼叫 `children()`」這一條。腳本在 scratchpad，未留在 repo。
3. **production image 內跑同一份 suite**：371 passed（3.12.13 / Linux）。
4. **實地驗收 17/17**（真實 bot token、真 MongoDB、12MB 檔案）：直接竄改 MongoDB
   ——改檔名、搬到別的目錄、交換兩個檔名、改目錄名、刪掉一個節點——五種全部被拒。

---

## 未完成待辦

1. **Client UI**（2026-08-02 起是 `ROADMAP.md` 唯一的 `[next]`）。**形態已拍板**：同 process
   的 aiohttp API + 靜態 SPA、完整檔案管理、先跑多使用者 §5 前三步且金鑰按模型 B 鋪路。
   五步的實作順序寫在 `ROADMAP.md` 該條裡。**唯一還沒拍板的是 session 怎麼持有 master key**
   ——那要在第 4 步（HTTP API 層）動工前先出方案。
   ~~repo 沒有 remote~~ — 已於 2026-08-02 用 GitHub CLI 推上 `origin`，這條關閉。
2. **`BLUEPRINT.md` 該重新產出**。它以 `e288ff7` 為準，H1 與 H2 落地後 §4.3 / §4.4
   描述的 tag 涵蓋範圍已明顯落後。排在 remote 之後（它會產生一份大檔）。
3. ~~**仍未拍板的 `[later]`**~~ — 2026-08-02 全部收掉：`SFTP_PASSWORD` **要做 docker secret，
   但排在 Client UI 之後**；`design-multi-user.md` §6 三個決策點全部拍板（模型 B／
   `users` collection／分四步）。其餘 `[later]` 見 `ROADMAP.md`，都是已評估的殘留項。
4. **仍未被真實環境驗證的東西**：5xx 與傳輸層重試（要 Discord 自己故障）、
   附件 URL 真的過期（要等 24 小時）、`_rollback` 的保護（要 Discord 連續失敗五次以上）。

---

## 資料狀態

- **root 節點已就地升級**成帶 tag 的形狀（升級當下它是空的，所以是安全的；`ensure_root`
  對**非空**的 pre-tag root 會拒絕啟動——對有內容的目錄重算 tag 等於把已經發生的刪除簽成合法）。
- **驗收期間建立的檔案與目錄已全部刪除**，Discord 附件一併釋放，對帳 0 孤兒、0 懸空。
- **`keystore` 未動**，master key 沒有變。`chunk_tag` 沒改，所以一個 chunk 都不需要重傳。
- `mongo_data` / `host_key_data` volume 未刪除，host key 沒換。
- **從這一刻起寫入的資料回不去舊格式**——沒有 migration，這是 D4 拍板的內容。

---

## 環境備忘

- venv 在 `venv/`（3.12.7）；上線 image 也是 3.12（3.12.13）。一律用
  `./venv/Scripts/python.exe -m pytest`（見 `SOP.md`；裸 `python` 是 Store 的 stub，exit 49 且無輸出）。
- 程式碼是烤進 image 的，改完 `src/` 一定要 `docker compose up -d --build`。
- **重啟服務目前不必先問**，見 `CLAUDE.md`「臨時例外」。那是一條有到期日的規則。
- MongoDB 的埠沒有對宿主開放，一次性腳本要走
  `docker compose exec -T sftp-discord-server python - < 腳本`。
  容器裡有 compose 注入的環境變數，**不需要也讀不到 `.env`**。
- 在容器裡跑測試要 `MSYS_NO_PATHCONV=1` 前綴、`--user root`、`-p no:cacheprovider`。

### 寫實地竄改腳本的兩個坑（都踩過一次，按規則還沒進 `SOP.md`）

- **真實 MongoDB 有 `(parent_id, filename)` 唯一索引**：直接對調兩個檔名會撞
  DuplicateKeyError，要經過一個暫時名稱。in-memory fake 不模擬唯一性，所以單元測試不會遇到。
- **「還原」要用明確的目標值，不要「再做一次同樣的操作來還原」**——對調兩次讀的是同一份快照，
  等於再對調一次。而且**只檢查檔名的斷言驗不出來**（名字的集合兩種情況下一模一樣，
  那正是被測的攻擊）。斷言要讀位元組。
