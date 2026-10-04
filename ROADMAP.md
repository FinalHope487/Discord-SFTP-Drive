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

---

## 基線

**只有一份，覆寫不追加，≤40 行。** 數字取自本輪實跑；沒跑的層寫「未跑」，
不得沿用上一輪的數字。

**commit**：`main` 上本輪最後一個 commit（預設英文 UI＋外殼測試視窗移到螢幕外）
**CI run**：推 `main` 後的那一次，見 `gh run list --limit 3`

| 層 | 指令 | 本輪實跑（2026-10-04） |
|---|---|---|
| Python 全套（含瀏覽器與桌面視窗 user 層） | `./venv/Scripts/python.exe -m pytest` | **776 passed / 0 skipped** |
| 同一套換真 SQLite | `./venv/Scripts/python.exe -m pytest --db=sqlite` | **773 passed / 3 skipped** |
| Electron 外殼 | `cd client/shell && node --test` | **23 passed** |
| lint | `./venv/Scripts/python.exe -m pyflakes src tests` | exit 0 |
| 真 MongoDB | `docker compose up -d` | 未跑 |

---

## 待辦項目

### [next] aiohttp 3.14 對 `request["session"]` 發 `NotAppKeyWarning`
**具體細節**：2026-08-11 隨相依升級出現。aiohttp 要的是 `web.RequestKey`；`src/web.py` 有 18 處 `request["session"]`，另有 `request["device_id"]`。目前兩條警告，沒有東西壞掉
**怎麼做**：定義一個 `web.RequestKey`，一次把所有寫入與讀取換掉；不要改一半，兩種寫法並存會失去「拼錯就壞」的價值
**會改變什麼**：只動 `src/web.py`，消掉警告；行為不變
**做後回退代價**：`git revert` 該 commit 即可；沒有資料或格式受影響

### [next] user 手動清掉 agy 評測的殘留
**具體細節**：user 於 2026-10-04 批准（原 Q2），但 agent 執行時被權限系統擋下。`.claude/settings.local.json` 的 allow 清單仍有 `Bash(powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\sword\AppData\Local\Temp\r6ws\launch.ps1 *)`；`%LOCALAPPDATA%\Temp\r6ws`（44 KB）、`r7ws`（12 MB）仍在，venv 複本已不在
**怎麼做**：user 自己從 `settings.local.json` 刪那一行，刪 `r6ws`、`r7ws`，留 `r6verify`、`r7verify`
**會改變什麼**：之後再跑那支腳本會跳權限確認；釋放約 12 MB
**做後回退代價**：權限規則照上面原字串加回去；刪掉的工作區回不來，證據檔在 `*verify` 所以結論不受影響
