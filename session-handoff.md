# Session Handoff

跨多輪任務結束前，依此模板產出交接摘要。

> 2026-08-07 · 獨立單機版：Electron 外殼接上後端，雙擊開啟打通（分支 `feat/standalone-app`）

---

## 目前狀態

**單機版現在有兩種開法，都驗證過。** 終端機版（真 bot token 實地驗收過，見上一輪）與
Electron 外殼版（今天接上，密碼改用視窗輸入，關閉視窗會把後端一起收掉）。三個產物都
重建過：`dist-standalone/discord-drive.exe`（17 MB）、`DiscordDrive-0.1.0-portable.exe`／
`-setup.exe`（106 MB，含後端）。

- 714 項測試（+12）；`--db=sqlite` 711 過 3 skip；`node --test` 16 過（含拿真 exe 跑的整合測試）；
  pyflakes 乾淨。
- **已 commit 到上一輪為止（5 個 commit）並推上 `origin/feat/standalone-app`；這一輪的變更
  （main.js/backend.js/local.html/main.py/standalone.py 等）尚未 commit。**
- `master` 仍未合併，PR 沒開。

## 已完成

**這一輪的核心是「Windows 上 GUI 母行程殺不掉自己開的 console 子行程」這件事——量出來，
不是猜的**，然後照量到的結果設計：

- 拿一支小的 asyncio 探針腳本，在 Node 這邊分別測了 `child.kill()`（預設）、
  `child.kill('SIGINT')`、detached + 訊號、`taskkill` 不加 `/f`——**全部都不優雅**，
  `taskkill` 直接拒絕（「這個處理程序只能強制終止」）。**唯一測出來有效的是關閉子行程
  自己的 stdin**，於是設計走這條路。
- `src/main.py`：`_wait_for_shutdown` 加 `extra_stop` 參數，跟原本的訊號等待賽跑，
  預設 `None`——對容器那條路完全沒改變行為。
- `src/standalone.py`：`DISCORD_DRIVE_STDIN_LIFECYCLE=1` 時，密碼改成從 stdin 讀一行
  （不是 getpass），且會先印 `AWAITING_PASSWORD` 這個標記——**特地避免用猜的逾時去分辨
  「還沒設定」跟「正在等密碼」**，因為新解壓的 exe 第一次執行常被防毒軟體掃描拖慢。
  同一條 stdin 讀到 EOF 接上 `extra_stop`，當關機訊號。
- `client/shell/backend.js`（新）：擁有子行程整個生命週期（`status`/`start`/`stop`）。
- `client/shell/local.html`（新）：「在這台電腦上執行」畫面，跟 `setup.html` 共用視窗與 preload。
- `client/shell/main.js`：`mode: "local"` 存進 `config.json`，下次開啟直接回到本機流程；
  `before-quit` 攔截第一次請求、等後端收乾淨才真的關閉。

**驗證分三層，一層比一層真**：
1. 純邏輯 Node 單元測試（`readWebPort` 等）。
2. 對著真的 `discord-drive.exe` 跑整合測試——填一個看起來像真的、其實無效的 token，
   確認密碼真的透過管線送達、真的解開 keystore、真的到 Discord 那一步才失敗。
3. **真的用 Chrome DevTools Protocol 連進正在跑的 Electron app**（開發模式與完整打包後
   的 `win-unpacked/` 都測過），點擊真正的連結、呼叫真正的 IPC，走完整條
   renderer → preload → main → 子行程的路。第 3 層驗到一個真的 bug（見下）。

**這一輪抓到的 bug**：`extraResources` 之前沒設，第 3 層驗證會抓到（但其實是設計階段就
處理了，先寫好 `package.json` 才測的）。**驗證途中意外連到你自己先前留下的真實
`drive.env`**（真 bot token、真 `SFTP_USER`，是你「測好 1~7」那輪的殘留）。
**發現後立刻停手**，改用 `--user-data-dir` 隔離出乾淨目錄重測。密碼沒有送出、
`drive.sqlite3` 沒有被動過——它只在密碼真的解開 keystore 後才會被讀寫，這一步從沒發生，
時間戳可查證（`drive.sqlite3` 最後修改時間停在你那輪測試結束的當下）。

## 未完成待辦

- **這一輪的變更尚未 commit。** 建議怎麼拆看你，我這邊沒有先拆好——這次改動集中在
  「外殼生命週期」這一個主題，可能適合一個 commit，但你可能想跟其他部分分開，你決定。
- **Linux 版仍沒建過**（後端與外殼都是）。
- **`master` 未合併，PR 未開。**
- `[later]` `children()` 在兩個後端都是全表掃描（不是這輪或上輪引入的，兩邊一致，
  修法要改 schema，要先問）。
- `[later]` 標準版 exe 仍需要 Docker，跟單機版是並存的兩個產品線，維護成本雙份。

## 本輪不可碰的範圍

- **加密、tag 完整性、認證流程、`vfs.py`**：一行都沒動。
- **既有 MongoDB 部署**：`DB_BACKEND` 預設仍是 `mongo`，行為不變。
- **你的真實 drive 資料**：意外連到後立刻停手，見上方說明；沒有寫入、沒有送出密碼。
- **`BLUEPRINT.md`**：沒讀。

## 下一步建議任務

1. **實地點一輪**：填真的 Discord 憑證進 `%APPDATA%\Discord Drive\drive.env`
   （或用外殼的「開啟資料夾」按鈕），雙擊 `DiscordDrive-0.1.0-portable.exe`，
   選「在這台電腦上執行」，走一次完整流程——這是我驗證不到的最後一段
   （真密碼、真視窗點擊、真的關閉再確認後端真的收掉）。
2. **決定要不要 commit、怎麼拆。**
3. Linux 版與 `master` 合併，視你的優先順序排。
