# 從零開始建置並打包

從一台什麼都沒有的機器，到一個可以複製到任何 Windows 裝置雙擊打開的 `.exe`。

先讀完「這包出來的是什麼」再動手，不然打出來的東西會不是你期待的。

---

## 先選一種：兩種打包是兩個產品

| | **標準版**（本文件主體） | **獨立單機版**（見最後一節） |
|---|---|---|
| 打出來的 | Electron 視窗，~86 MB | 一支後端執行檔，~17 MB |
| 需要 Docker | **要**（後端是 `docker compose`） | **不要** |
| metadata 存哪 | MongoDB | 一個 SQLite 檔 |
| 資料能不能多裝置共用 | **能**——都連同一台後端 | **不能**，一台裝置一份 |
| 需要網路與 Discord | 要 | 要（檔案還是存 Discord） |
| 成熟度 | 已實地驗收過 | 後端已驗證；**外殼整合還沒做** |

**兩者不能互通**：沒有遷移工具，兩邊的 metadata 格式毫無共通之處。把單機版指向
現有部署在用的 Discord 頻道**不會匯入那個 drive**，只會在旁邊開一個空的。
理由與取捨寫在 `design-standalone.md`。

---

## 這包出來的是什麼

**`.exe` 裡面沒有你的檔案，也沒有後端。** 它是一個視窗，加上第一次開啟時問你
「伺服器在哪」的設定畫面。檔案管理介面本身是後端吐出來的。

```
┌─ 任何一台 Windows 裝置 ─────────┐        ┌─ 跑後端的那台機器 ───────────┐
│                                 │        │                              │
│  DiscordDrive-portable.exe      │        │  docker compose up -d        │
│    · 視窗、最小尺寸、導覽鎖定    │──HTTP──▶│    · aiohttp（API + 前端）   │
│    · 首次設定：伺服器位址        │        │    · SFTP :2222              │
│                                 │        │    · MongoDB                 │
└─────────────────────────────────┘        └──────────────┬───────────────┘
                                                          │
                                                    加密後的分塊
                                                          ▼
                                                      Discord
```

**為什麼前端不包進 exe。** 認證是 `dd_session` cookie，帶 `HttpOnly` 與
`SameSite=Strict`。如果畫面從 `file://` 載入、再去 fetch 遠端伺服器，那是跨來源請求，
`SameSite=Strict` 的 cookie **不會被送出**——要能用就得把認證改成 Authorization header，
等於放棄「頁面裡的腳本讀不到憑證」這個保證。所以 exe 只帶那一頁設定畫面，
填完之後視窗直接 `loadURL(伺服器)`，之後全部同源。

**Discord bot token 不在 app 裡填。** token、頻道 id、MongoDB 帳密是伺服器的設定，
寫在伺服器那台機器的 `.env`。它們不從客戶端送過去：token 是伺服器的祕密，不是使用者的祕密，
讓前端寫得到它就等於讓任何登入的人讀得到它。

---

## 需要裝什麼

| 工具 | 版本 | 做什麼用 | 哪一步需要 |
|---|---|---|---|
| Docker Desktop | 任何近期版本 | 跑後端與 MongoDB | 2 |
| Node.js | 20 以上（實測 24.14） | 建前端、打包 exe | 3、4 |
| Python | 3.12 | 只有跑測試才需要 | 附錄 |

`git`、`npm` 隨附即可。**不需要** Rust、不需要 Visual Studio、不需要 Wine。

```bash
node --version && npm --version && docker --version
```

---

## 步驟 1 · 取得原始碼與設定

```bash
git clone https://github.com/FinalHope487/Discord-SFTP-Drive.git
cd Discord-SFTP-Drive
cp .env.example .env
```

打開 `.env`，把標成 `REQUIRED` 的填完。每一項在 `.env.example` 裡都寫了填錯的代價，
這裡只列最少的：

| 變數 | 從哪來 |
|---|---|
| `DISCORD_BOT_TOKEN` | Discord Developer Portal → 你的 application → Bot → Reset Token |
| `DISCORD_USER_ID` 或 `DISCORD_CHANNEL_ID` | 至少填一個。兩個都填時 DM 優先 |
| `SFTP_USER` / `SFTP_PASSWORD` | 自己決定。密碼至少 12 bytes，會被強制檢查 |
| `MONGO_ROOT_USERNAME` / `MONGO_ROOT_PASSWORD` | 自己決定，只在第一次啟動時生效 |

產生一組夠長的密碼：

```bash
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

> **`SFTP_PASSWORD` 要另外備份。** 它包裝著所有檔案的加密金鑰。弄丟它是弄丟檔案，
> 不只是弄丟登入。

---

## 步驟 2 · 起後端

```bash
docker compose up -d --build
```

第一次會建 image、拉 MongoDB，大約兩三分鐘。確認它活著：

```bash
curl http://127.0.0.1:8080/api/health
```

看到 `{"ok": true}` 就成了。這時候用瀏覽器打開 `http://127.0.0.1:8080` 會看到一頁
「前端還沒有建置」——那是對的，前端在下一步。

出問題先看 log，設定錯誤會是最後一行：

```bash
docker compose logs --tail 40 sftp-discord-server
```

---

## 步驟 3 · 建前端

```bash
cd client/app
npm install
npm run build
cd ../..
```

產出在 `client/app/dist/`（約 280 KB，三個檔案）。

**不需要重建 image。** `docker-compose.yml` 把 `client/app/dist` 以唯讀方式掛進容器的
`/app/web`，所以改前端只要重跑 `npm run build` 再重新整理瀏覽器。第一次建完之後要讓容器
看見這個目錄，重啟一次：

```bash
docker compose restart sftp-discord-server
```

現在 `http://127.0.0.1:8080` 是真的檔案管理介面，用 `.env` 裡那組帳密登入。

開發時想要熱更新可以改跑 `npm run dev`（<http://127.0.0.1:5173>），它把 `/api` 代理到
8080，所以 cookie 仍然同源——這是刻意的，直接打 8080 會需要一組 production 沒有的 CORS 設定。

---

## 步驟 4 · 打包 .exe

```bash
cd client/shell
npm install
npm run dist
```

第一次會下載 Electron 執行檔（約 100 MB），之後有快取。產出在專案根目錄的 `dist-desktop/`：

| 檔案 | 大小 | 用途 |
|---|---|---|
| `DiscordDrive-0.1.0-portable.exe` | ~86 MB | **單檔可攜版。複製到任何 Windows 裝置雙擊就開**，不用安裝、不用管理員權限 |
| `DiscordDrive-0.1.0-setup.exe` | ~86 MB | NSIS 安裝檔，會建捷徑、可選安裝路徑、可解除安裝 |
| `win-unpacked/` | ~250 MB | 沒有壓縮的目錄版，除錯用 |

只要其中一種就加參數：

```bash
npm run dist:portable     # 只出可攜版
npm run dist:installer    # 只出安裝檔
npm run pack              # 只出 win-unpacked/，最快，用來試跑
```

打包前先試跑不打包的版本：

```bash
npm start
```

### 換圖示

圖示是 `client/shell/icon.png`，由 `make-icon.py` 產生（只用標準函式庫，沒有影像套件相依）。
改了裡面的常數之後：

```bash
cd ../..
./venv/Scripts/python.exe client/shell/make-icon.py
```

或直接換掉 `icon.png`，最小 256×256。

### 其他平台

`electron-builder` **只能打自己平台的包**：Windows 的 NSIS 需要 Windows（或 Wine），
macOS 的 dmg 需要 macOS。三個平台就開 CI matrix，或在各自機器上各跑一次。
`package.json` 裡 mac 與 linux 的 target 已經寫好了。

macOS 上要能雙擊打開而不被 Gatekeeper 擋，需要簽章加公證（`CSC_LINK`、`CSC_KEY_PASSWORD`、
`APPLE_ID`、`APPLE_APP_SPECIFIC_PASSWORD`、`APPLE_TEAM_ID`）。自己用的話按住 Control 點
「打開」就過了。

Windows 上沒有簽章憑證時，SmartScreen 第一次會跳「不明的發行者」，點「其他資訊 → 仍要執行」。
這不是打包壞了，是沒有付費的程式碼簽章憑證。

---

## 步驟 5 · 在別的裝置上打開

把 `DiscordDrive-0.1.0-portable.exe` 複製過去，雙擊。

第一次會出現設定畫面問伺服器位址。**預設的 `http://127.0.0.1:8080` 只有在後端跑在同一台
機器上時才對。** 從別台連過來要先讓後端連得到——`.env.example` 寫了三條路與各自的代價：

1. **私人網路（Tailscale / WireGuard）**——兩邊各裝一個軟體，`WEB_BIND` 設成那張網路卡的位址，
   其他都不用改。流量不經過你不控制的網路，**這是唯一不新增出錯方式的選項**。
2. **反向代理配真憑證**（Caddy 兩行）——`WEB_BIND` 維持 `127.0.0.1`，改成發佈代理。
   代價是一個公開網域名稱、一張要續期的憑證、compose 多一個服務。
3. **LAN 明文**——要同時改 `WEB_BIND=0.0.0.0` 與 `WEB_COOKIE_SECURE=0`。**不建議。**
   第二個變數存在的理由就是讓你不會不小心走到這裡。

填好位址按「測試連線」，它會打 `/api/health`，所以「沒有東西在聽」和「有東西在聽但不是這個服務」
會分開回報——這兩件事的修法完全不同。

之後要換伺服器：功能表 → 切換伺服器，或 `Ctrl+Shift+S`。位址存在
`%APPDATA%\Discord Drive\config.json`，刪掉它就會回到首次設定畫面。

---

## 一次跑完（前端 + 打包）

前端或外殼改過之後：

```bash
cd client/app   && npm run build && cd ../..
cd client/shell && npm run dist  && cd ../..
```

只改前端的話**不用重打包 exe**——exe 每次開啟都是向伺服器要最新的前端。
只有改到 `client/shell/` 底下的東西才需要重打包。

---

## 附錄 · 跑測試

```bash
python -m venv venv
./venv/Scripts/python.exe -m pip install -r requirements.txt -r requirements-dev.txt
./venv/Scripts/python.exe -m pytest
```

511 項，約 25 秒，不需要憑證也不需要網路——MongoDB 與 Discord API 都是假的。

外殼那邊的單元測試（伺服器位址的正規化）：

```bash
cd client/shell && node --test
```

真正貼近上線的一輪，是在 production image 裡跑同一份測試：

```bash
docker run --rm --user root -v "$PWD:/repo" -w /repo discord-drive-sftp-discord-server \
  sh -c "pip install -q -r requirements-dev.txt && python -m pytest -q"
```

---

## 常見狀況

| 症狀 | 原因 | 怎麼修 |
|---|---|---|
| 瀏覽器顯示「前端還沒有建置」 | `client/app/dist` 是空的，或掛載還沒生效 | 跑步驟 3，然後 `docker compose restart sftp-discord-server` |
| exe 開起來停在設定畫面說「連不上」 | 後端沒起來，或位址／埠號打錯 | `docker compose ps`；「測試連線」的訊息會分辨是哪一種 |
| exe 說「有東西在聽，但不是 Discord Drive」 | 埠號打到別的服務 | 確認是 8080，或 `docker-compose.yml` 裡 `WEB_PORT` 改過的值 |
| 從別台裝置連不上，同一台可以 | `WEB_BIND` 仍是 `127.0.0.1` | 見步驟 5 的三條路 |
| 從別台連得上但登入後每個動作都 401 | 明文 HTTP 上 `WEB_COOKIE_SECURE=1`，瀏覽器不存 cookie | 走路線 1 或 2；真的要明文就把它設成 0 |
| `npm run dist` 抱怨圖示尺寸 | `icon.png` 小於 256×256 | 重跑 `make-icon.py` 或換一張夠大的 |
| SmartScreen 擋住 | 沒有程式碼簽章憑證 | 「其他資訊 → 仍要執行」，或去買一張憑證 |
| 上傳大檔到一半停住 | Discord 的速率限制 | 伺服器會退避重試；`docker compose logs` 會看到 429 |

---

## 獨立單機版：不需要 Docker 的那一種

**狀態：後端可用並已驗證，桌面外殼還沒接上。** 現在打出來的是一支 console 執行檔——
它就是完整的 drive（SFTP + 網頁 UI），只是要從終端機啟動。雙擊打開的視窗版
卡在一個還沒拍板的決定，見 `ROADMAP.md` 最上面那條 `[now]`。

### 建置

前端先建好（跟標準版同一步），然後：

```bash
./venv/Scripts/python.exe -m pip install -r requirements-dev.txt
./venv/Scripts/python.exe -m PyInstaller discord-drive.spec --noconfirm --distpath dist-standalone --workpath build-standalone
```

產出 `dist-standalone/discord-drive.exe`，約 17 MB，裡面有 Python、伺服器、
以及那份前端。**不需要 Docker、不需要 MongoDB。**

`PyInstaller` 跟 `electron-builder` 一樣**只能打自己平台的包**：Linux 版要在 Linux 上建。

### 第一次啟動

```bash
./dist-standalone/discord-drive.exe
```

第一次會在你的使用者目錄寫一份設定檔然後停下來，並印出路徑：

| 平台 | 位置 |
|---|---|
| Windows | `%APPDATA%\Discord Drive\` |
| macOS | `~/Library/Application Support/Discord Drive/` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/discord-drive/` |

裡面是 `drive.env`（設定）與之後的 `drive.sqlite3`（metadata）。
填完 `drive.env` 裡標 REQUIRED 的幾項再跑一次。

`DISCORD_DRIVE_HOME` 可以指定別的目錄——同一台機器上跑兩個各自獨立的 drive 就用它。

### 密碼刻意不在設定檔裡

`drive.env` **沒有 `SFTP_PASSWORD=` 這一行，是故意的**。那個密碼包著所有檔案的加密金鑰，
而它會跟 `drive.sqlite3` 放在同一個目錄——寫進去等於把鎖跟鑰匙放在一起，
複製那個資料夾就等於複製整個 drive。

所以預設是**啟動時在終端機問**，密碼不落地。要無人值守啟動就用環境變數
`SFTP_PASSWORD`，或把 `SFTP_PASSWORD_FILE` 指向一個你自己設好權限的檔案。

> **兩樣東西都要備份，而且要分開放**：`SFTP_PASSWORD`（弄丟＝檔案永遠打不開）
> 與 `drive.sqlite3`（弄丟＝不知道哪些 chunk 組成哪個檔案）。**兩者互相不能替代。**

---

## 相關文件

| 檔案 | 內容 |
|---|---|
| [`GUIDE.md`](GUIDE.md) | 從零到會用：Discord 那一側怎麼設、兩個版本各自怎麼跑、備份與疑難排解 |
| [`README.md`](README.md) | 這個服務是什麼、怎麼跑、已知限制 |
| [`design-standalone.md`](design-standalone.md) | 單機版怎麼換掉 MongoDB 而不動 `vfs.py`，以及為什麼不走另一條路 |
| [`.env.example`](.env.example) | 每一個設定與填錯的代價 |
| [`client/README.md`](client/README.md) | 前端與外殼兩個套件的結構 |
| [`ROADMAP.md`](ROADMAP.md) | 已拍板的長期決策與待辦 |
| [`SOP.md`](SOP.md) | 重複踩到的坑與檢查順序 |
