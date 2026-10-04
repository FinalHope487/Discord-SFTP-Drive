# Roadmap

制定在建立目標時不可跨越的準則寫在〈硬約束〉。
commit 與 CI run 的 SHA 與狀態寫在〈基線〉。
新想法寫進〈待辦項目〉並標記，不打斷目前任務。

此檔不准自己寫新欄位，不准寫已拍板的決策（寫 `docs/decisions/`）。

- `[now]` — 阻塞當前任務
- `[next]` — 目前模組穩定後就做
- `[blocked]` — 需要 user 先拍板，見 `QUESTIONS.md`
- `[later]` — 現在做屬於過早設計，等需求明確再說
- `[parked]` — 先記錄，暫不評估

這五個以外的標籤不准用。

`[later]` 條目寫在 `ROADMAP/later.md`，`[parked]` 寫在 `ROADMAP/parked.md`，其餘留在本檔〈待辦項目〉。狀態改了就把條目搬到對應的檔。

格式如下:

### [狀態] {一句話描述}
**具體細節**：{描述問題狀態}
**怎麼做**：{描述解決問題的方法，僅限已知解法的事項}
**會改變什麼**：{明確做了這件事會改到什麼}
**做後回退代價**：{假設這件事做完了，要改回來得額外做什麼——`git revert` 哪個 commit、哪些資料或產出要另外清、有沒有東西回不來。「還沒做所以是零」不算答案}

範例如下:

### [next] 高負載時介面頓不頓，只有人工才測得出來
**具體細節**：2026-xx-xx 已驗證三個背景工作同時跑不逾時（N passed），手感部分 user 還沒實際操作過
**怎麼做**：在背景工作執行期間手動觀察介面的響應時間
**會改變什麼**：確認頓的話要把並行上限設定調成 1；這一步無法自動化，需要 user 實際操作時觀察
**做後回退代價**：結論若是要把並行上限設成 1，回退就是把它改回原值，一行設定；沒有資料或產出受影響

---

## 硬約束

不管在做什麼，這些不能跨過。每條 ≤2 行，且必須寫明誰在強制它。

- **`config.py` 讀得到的每個變數都出現在 `docker-compose.yml` 與 `.env.example`** **強制**：`tests/test_compose_coverage.py`
- **公開文件與設定檔指向的 `.md` 必須存在** **強制**：`tests/test_doc_references.py`
- **不推 `main`／`master`**（與新版規則檔衝突，見 `QUESTIONS.md` Q1） **強制**：`.claude/hooks/block-push-main.py`，回歸測試 `tests/test_push_guard.py`

---

## 基線

**只有一份，覆寫不追加，≤40 行。** 數字取自本輪實跑；沒跑的層寫「未跑」，
不得沿用上一輪的數字。

**commit**：本輪 commit 的父 `f5ae868`（工作目錄＝本輪改動，數字對應本輪 commit 的樹）
**CI run**：未跑（本輪未推）

| 層 | 指令 | 本輪實跑（2026-10-04） |
|---|---|---|
| Python 全套 | `./venv/Scripts/python.exe -m pytest` | **774 passed / 18 skipped**（792 項） |
| 同一套換真 SQLite | `./venv/Scripts/python.exe -m pytest --db=sqlite` | **771 passed / 21 skipped** |
| Electron 外殼 | `cd client/shell && node --test` | **23 passed** |
| lint | `./venv/Scripts/python.exe -m pyflakes src tests` | exit 0 |
| 桌面視窗 user 層 | `tests/test_ui_shell.py` | 未跑：本機 `client/shell/node_modules` 沒裝 Electron，18 項 skip |
| 真 MongoDB | `docker compose up -d` | 未跑 |

---

## 待辦項目

### [next] aiohttp 3.14 對 `request["session"]` 發 `NotAppKeyWarning`
**具體細節**：2026-08-11 隨相依升級出現。aiohttp 要的是 `web.RequestKey`；`src/web.py` 有 18 處 `request["session"]`，另有 `request["device_id"]`。目前兩條警告，沒有東西壞掉
**怎麼做**：定義一個 `web.RequestKey`，一次把所有寫入與讀取換掉；不要改一半，兩種寫法並存會失去「拼錯就壞」的價值
**會改變什麼**：只動 `src/web.py`，消掉警告；行為不變
**做後回退代價**：`git revert` 該 commit 即可；沒有資料或格式受影響

### [blocked] 刪掉 agy 評測留下的 Bash 權限規則
**具體細節**：`.claude/settings.local.json` 裡有 `Bash(powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\sword\AppData\Local\Temp\r6ws\launch.ps1 *)`。評測已結束，它指向 temp 目錄裡一支可被改寫的腳本。見 `QUESTIONS.md` Q2
**怎麼做**：從 `settings.local.json` 的 allow 清單刪掉那一行
**會改變什麼**：之後再跑那支腳本會跳權限確認
**做後回退代價**：把那一行加回去；`settings.local.json` 不在版控，要自己記得原字串（就在上面）

### [blocked] 清掉 agy 評測的 temp 工作區（約 1.5 GB）
**具體細節**：`%LOCALAPPDATA%\Temp\r6ws`、`r6verify`、`r7ws`、`r7verify`。`r7ws` 有 5 份各 195 MB 的 venv 複本；證據檔在 `r6verify`／`r7verify`。見 `QUESTIONS.md` Q2
**怎麼做**：刪 `r6ws`、`r7ws`，留兩個 `*verify`
**會改變什麼**：釋放約 1.5 GB；評測工作區無法重跑
**做後回退代價**：刪掉的 venv 複本回不來，要重建就照評測流程重新複製；證據檔保留所以結論不受影響

### [blocked] dependabot PR #17／#18
**具體細節**：#17 是 python group 2 個 dev 相依更新，#18 是 `client/shell` electron 43.3.0 → 43.4.0。沒跑過它們的 CI、沒審過內容。屬改相依，見 `QUESTIONS.md` Q3
**怎麼做**：看兩個 PR 的 CI log 裡的 passed 數字（不只看綠勾），再合併
**會改變什麼**：dev 相依版本；electron 升級會影響 `tests/test_ui_shell.py` 那 18 項
**做後回退代價**：`git revert` 合併 commit 並重跑 `npm ci`／`pip install`；沒有資料受影響

### [blocked] 關掉 GitHub repo 的 Wiki 與 Projects
**具體細節**：兩者都沒在用，問過四輪沒有答案。見 `QUESTIONS.md` Q4
**怎麼做**：`gh repo edit FinalHope487/Discord-SFTP-Drive --enable-wiki=false --enable-projects=false`
**會改變什麼**：repo 頁面少兩個分頁
**做後回退代價**：同一指令改成 `=true`；未查 Wiki 有沒有內容，關閉前先看一次
