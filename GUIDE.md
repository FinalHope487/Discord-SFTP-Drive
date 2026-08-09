# Discord Drive 完整教學

從什麼都沒有，到一個能用的雲端硬碟。

> **這份文件的定位**：`BUILD.md` 講「怎麼建置」，這份講「怎麼從零到會用」。
> 兩者有重疊，重疊的地方以這份為準。

---

## 0. 先搞清楚這是什麼

一個 **SFTP 伺服器 + 網頁檔案管理員**，但檔案實際存在 Discord 上。

- 檔案被切成 9 MB 的塊，**每一塊各自用 AES-256-CTR 加密**，再當成附件傳到 Discord。
- 加密金鑰**不在 Discord 上**，在你自己的資料庫裡。所以拿到 Discord 那一側的人
  既讀不到內容、也偽造不出東西。
- 主金鑰是隨機產生的，用你的密碼包起來（Argon2id）。**換密碼只要重寫 32 bytes，
  不用把所有檔案重傳一次。**

**不是什麼**：不是離線工具（一定要有網路跟 Discord）、不是多人共享空間
（目前只有一個帳號）、不能同時跑兩份（見第 8 節）。

---

## 1. 先選一種：兩個版本是兩個產品

| | **A. 獨立單機版** | **B. 標準版** |
|---|---|---|
| 打出來的檔案 | `discord-drive.exe`，**17 MB** | `DiscordDrive-0.1.0-portable.exe`，**89 MB** |
| 要不要裝 Docker | **不用** | **要** |
| metadata 存哪 | 一個 SQLite 檔 | MongoDB（compose 幫你起） |
| 長相 | 終端機視窗 + 瀏覽器開網頁 | 獨立視窗 App |
| 多裝置共用同一份資料 | **不行**，一台一份 | **可以**，都連同一台後端 |
| 成熟度 | 後端已驗證，**桌面外殼還沒接上** | 已實地驗收過 |

### 怎麼選

- **自己一台電腦用、不想碰 Docker** → **A**
- **想在手機／筆電／桌機看同一份檔案** → **B**
- 兩個都想試 → 先 A，它五分鐘就能跑起來

> ⚠️ **兩者不能互通。** 沒有遷移工具，兩邊的 metadata 格式毫無共通之處。
> 把 A 指向 B 正在用的 Discord 頻道**不會匯入那份 drive**，只會在旁邊開一個空的。

---

## 2. 步驟一：準備 Discord（兩條路線都要做）

這一步跟你選哪個版本無關，而且是最容易卡住的一步。

### 2.1 建一個 Bot

1. 打開 <https://discord.com/developers/applications>
2. 右上角 **New Application** → 取個名字 → Create
3. 左邊選單 **Bot** → **Reset Token** → 複製那串 token

> 🔑 **這串 token 就是 `DISCORD_BOT_TOKEN`。** 它只會顯示一次，關掉就要重設。
> 先貼到記事本。

**不需要開任何 Intents。** 這個服務只用 REST API，不連 gateway。

### 2.2 決定檔案要存在哪：DM 還是頻道

**兩種擇一。兩個都填的話 DM 優先。**

#### 選項一：存在跟自己的私訊（DM）裡 — 較簡單

需要 `DISCORD_USER_ID`（**你自己的**使用者 ID，不是 bot 的）：

1. Discord 設定 → 進階 → 打開 **開發者模式**
2. 對自己的頭像右鍵 → **複製使用者 ID**

**前提**（少一個就會啟動失敗，錯誤訊息會直說）：
- Bot 必須跟你**在同一個伺服器裡**（Discord 不准 bot DM 陌生人）
- 你的隱私設定要**允許伺服器成員傳私訊給你**

所以就算走 DM，也還是要把 bot 拉進一個伺服器——隨便建一個只有自己的伺服器就行。

#### 選項二：存在某個頻道裡

需要 `DISCORD_CHANNEL_ID`：對頻道右鍵 → **複製頻道 ID**（一樣要開發者模式）。

**Bot 在那個頻道必須有這四個權限**，缺一個啟動時就會明確告訴你缺哪個：

| 權限 | 為什麼要 |
|---|---|
| View Channel | 不然連定址都做不到 |
| Send Messages | 每一塊檔案是一則訊息 |
| Attach Files | 塊本身是附件 |
| Read Message History | 讀回來時要重新取得附件網址 |

### 2.3 把 Bot 拉進伺服器

左邊選單 **OAuth2** → **URL Generator**：
- SCOPES 勾 **bot**
- BOT PERMISSIONS 勾上面那四個

複製產生的網址，貼到瀏覽器，選你的伺服器 → 授權。

### 2.4 想一組密碼

**至少 12 個位元組**，程式會強制檢查。產生一組：

```bash
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

> 🔐 **這組密碼不只是登入用的，它包著所有檔案的加密金鑰。**
> **弄丟它 = 檔案永遠打不開**，不是「重設一下就好」。現在就存進密碼管理器。

---

## 3. 路線 A：獨立單機版（不需要 Docker）

> **想雙擊打開、不碰終端機？** 這條路現在也有桌面外殼可以用：打開
> `DiscordDrive-0.1.0-portable.exe` 選「在這台電腦上執行」，第一次會指引你去哪填設定、
> 之後每次開啟跳密碼輸入視窗即可，效果跟下面的終端機版完全一樣，只是密碼用視窗打字
> 代替終端機打字。以下步驟是終端機版，想知道兩者差在哪見 `BUILD.md`。

### 3.1 拿到執行檔

已經建好的在：

```
dist-standalone/discord-drive.exe
```

要自己重建的話（需要 Python 3.12 與已建好的前端）：

```bash
cd client/app && npm install && npm run build && cd ../..
./venv/Scripts/python.exe -m pip install -r requirements-dev.txt
./venv/Scripts/python.exe -m PyInstaller discord-drive.spec --noconfirm --distpath dist-standalone --workpath build-standalone
```

> PyInstaller 跟 electron-builder 一樣**只能打自己平台的包**。要 Linux 版就得在 Linux 上建。

### 3.2 第一次啟動：它會幫你開設定檔

```bash
./dist-standalone/discord-drive.exe
```

第一次跑會寫一份設定檔然後**故意停下來**，並印出路徑：

```
Wrote a settings file to:
  C:\Users\你\AppData\Roaming\Discord Drive\drive.env

Fill in the REQUIRED values and start the drive again.
```

資料目錄依平台而定：

| 平台 | 位置 |
|---|---|
| Windows | `%APPDATA%\Discord Drive\` |
| macOS | `~/Library/Application Support/Discord Drive/` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/discord-drive/` |

用 `DISCORD_DRIVE_HOME` 可以指定別的目錄——**同一台機器要跑兩個各自獨立的 drive 就用它。**

### 3.3 填設定檔

打開那個 `drive.env`，把第 2 節拿到的東西填進去：

```ini
DISCORD_BOT_TOKEN=你剛剛複製的那一長串
DISCORD_USER_ID=你的使用者ID          # 走 DM 就填這個
# DISCORD_CHANNEL_ID=頻道ID          # 走頻道就改填這個
SFTP_USER=你想用的帳號名
```

**注意 `drive.env` 裡沒有 `SFTP_PASSWORD=` 這一行，那是故意的**——見下一節。

### 3.4 再跑一次

```bash
./dist-standalone/discord-drive.exe
```

會問你密碼：

```
Drive password: ▂
```

打第 2.4 節那組。看到這幾行就是成功了：

```
INFO  src.db: Metadata store: SQLite at ...\drive.sqlite3
INFO  src.discord_api: Discord bot authenticated as ...
INFO  src.main: SFTP server listening on port 2222
INFO  src.main: Web API listening on 127.0.0.1:8080
```

打開 <http://127.0.0.1:8080>，用 `SFTP_USER` 跟剛剛那組密碼登入。

### 3.5 為什麼密碼不寫在設定檔裡

因為 **`drive.env` 跟 `drive.sqlite3` 在同一個目錄**。密碼寫進去，
等於把鎖跟鑰匙放在同一個抽屜——**複製那個資料夾就等於複製整個 drive**。

所以預設是啟動時在終端機問，密碼完全不落地。

要無人值守自動啟動的話，兩種方式都還能用：

```bash
# 環境變數
SFTP_PASSWORD='你的密碼' ./dist-standalone/discord-drive.exe

# 或指向一個你自己設好權限的檔案
SFTP_PASSWORD_FILE=/path/to/secret ./dist-standalone/discord-drive.exe
```

---

## 4. 路線 B：標準版（Docker + 桌面 App）

### 4.1 後端

```bash
cp .env.example .env
```

打開 `.env` 填標成 REQUIRED 的：`DISCORD_BOT_TOKEN`、`DISCORD_USER_ID` 或
`DISCORD_CHANNEL_ID`、`SFTP_USER`、`MONGO_ROOT_PASSWORD`。

密碼走 docker secret，不放 `.env`：

```bash
mkdir -p secrets
python -c "import secrets; print(secrets.token_urlsafe(24))" > secrets/sftp_password
```

然後一行起完整個堆疊（**它會等到 drive 真的打得開才回來**）：

```powershell
.\scripts\start.ps1
```

手動版：

```bash
cd client/app && npm install && npm run build && cd ../..
docker compose up -d --build
```

確認活著：

```bash
curl http://127.0.0.1:8080/api/health
```

看到 `{"ok": true}` 就成了。

### 4.2 桌面 App

已經建好的在：

```
dist-desktop/DiscordDrive-0.1.0-portable.exe   # 免安裝，複製過去雙擊就開
dist-desktop/DiscordDrive-0.1.0-setup.exe      # 安裝版，會建捷徑、可解除安裝
```

自己重建：

```bash
cd client/shell && npm install && npm run dist
```

### 4.3 這個 exe 裡面**沒有**你的檔案，也沒有後端

它是一個視窗，加上第一次開啟時問「伺服器在哪」的設定畫面。
檔案管理介面本身是後端吐出來的。

```
┌─ 任何一台 Windows ──────┐         ┌─ 跑後端的那台 ─────────┐
│ DiscordDrive.exe        │         │ docker compose up -d   │
│  · 視窗                 │──HTTP──▶│  · Web 8080            │
│  · 首次設定：伺服器位址  │         │  · SFTP 2222           │
└─────────────────────────┘         │  · MongoDB             │
                                    └───────────┬────────────┘
                                     加密後的塊  ▼
                                              Discord
```

第一次開會問伺服器位址。**預設的 `http://127.0.0.1:8080` 只有在後端跑在同一台
機器上時才對。** 從別台連要先讓後端連得到，三條路與各自代價：

| 做法 | 代價 |
|---|---|
| **1. 私人網路**（Tailscale / WireGuard） | 兩邊各裝一個軟體。**唯一不新增出錯方式的選項** |
| 2. 反向代理配真憑證（Caddy 兩行） | 要一個網域、一張要續期的憑證 |
| 3. LAN 明文 | 要同時改 `WEB_BIND=0.0.0.0` 與 `WEB_COOKIE_SECURE=0`。**不建議** |

之後要換伺服器：功能表 → 切換伺服器，或 `Ctrl+Shift+S`。

---

## 5. 開始使用

### 5.1 網頁介面

<http://127.0.0.1:8080>，用你的帳號密碼登入。上傳、下載、改名、刪除、垃圾桶都在裡面。

**登入 = 把主金鑰解開放進記憶體**，所以 session 同時有閒置逾時（預設 10 分鐘）
和絕對上限（預設 2 小時）。瀏覽器可以把它們調短，**不能調長**。

同一個帳號可以同時從好幾個地方登入，狀態列會顯示幾個，也有一個按鈕可以
「結束其他 session 但不結束自己這個」。

### 5.2 SFTP

```bash
sftp -P 2222 你的帳號@localhost
```

一般的檔案系統操作都可以：`ls` / `cd` / `get` / `put` / `rm` / `mkdir` /
`rename` / `chmod`，也支援隨機讀寫與 `truncate`。

WinSCP、FileZilla、Cyberduck 這類客戶端也可以，主機填 `localhost`、埠 `2222`。

**不支援符號連結**（`symlink` / `readlink` / `link` 會回 unsupported）。

### 5.3 垃圾桶

刪除是丟垃圾桶，預設 **30 天**後才真的清掉（`TRASH_RETENTION_DAYS`）。
「至少這麼久」——清理是背景掃描，不是精準到秒的定時炸彈。

---

## 6. 備份與救援 ⚠️ 這節最重要

### 6.1 你必須備份兩樣東西，而且要分開放

| 東西 | 弄丟的後果 |
|---|---|
| **密碼** | **檔案永遠打不開。** 它包著主金鑰，沒有救援路徑、沒有後門 |
| **metadata**（單機版 `drive.sqlite3` / 標準版 MongoDB） | **不知道哪些塊組成哪個檔案。** Discord 上的塊還在，但拼不回來 |

**兩者互相不能替代**，缺任何一個都等於檔案沒了。

分開放的理由：兩個放在一起，一次意外就同時失去。

### 6.2 單機版怎麼備份

關掉 drive，然後複製整個資料目錄裡的：

```
drive.sqlite3
drive.sqlite3-wal      ← 如果存在，一定要一起
drive.sqlite3-shm      ← 同上
```

> **`-wal` 檔不能漏。** SQLite 用 WAL 模式，最近的寫入可能還在那裡面，
> 只複製主檔會拿到一份舊的。

### 6.3 標準版怎麼備份

```bash
docker compose exec mongodb mongodump --archive --gzip \
  -u "$MONGO_ROOT_USERNAME" -p "$MONGO_ROOT_PASSWORD" --authenticationDatabase admin \
  > backup-$(date +%F).gz
```

> ⚠️ **`users` 跟 `keystore` 必須一起還原。** 帳號那一列是「哪一把包好的金鑰屬於
> 這個部署」的唯一線索。只還原其中一個，會留下一把沒有東西指向它的金鑰——
> 伺服器會**拒絕啟動**，而不是若無其事地在讀不懂的資料上蓋一把新金鑰。
> （這個守衛是刻意加的，因為沒有它的話症狀是「不報錯，只是從此解不開」。）

### 6.4 換密碼

設 `SFTP_PASSWORD_OLD` 為舊的、`SFTP_PASSWORD` 為新的，啟動一次，然後把
`SFTP_PASSWORD_OLD` 拿掉。**只會重寫那 32 bytes，不會重傳任何檔案。**

---

## 7. 疑難排解

| 症狀 | 原因 | 怎麼修 |
|---|---|---|
| `DISCORD_BOT_TOKEN was rejected by Discord (401)` | token 打錯或被重設過 | 回 Developer Portal → Bot → Reset Token 拿新的 |
| `cannot open a DM with DISCORD_USER_ID=... (403)` | bot 跟你沒有共同伺服器，或你關閉了成員私訊 | 把 bot 拉進一個你也在的伺服器；檢查隱私設定 |
| `Discord rejected DISCORD_USER_ID=... as malformed (400)` | 填的是使用者**名稱**不是數字 ID | 開發者模式 → 右鍵 → 複製使用者 ID |
| `cannot see DISCORD_CHANNEL_ID=... (403/404)` | ID 錯，或 bot 沒被加進那個伺服器 | Discord 對「看不到」跟「不存在」回一樣的碼，兩個都要檢查 |
| `bot is missing permissions on channel ...` | 四個權限缺一 | 訊息會直接列出缺哪個 |
| 網頁顯示「前端還沒有建置」 | `client/app/dist` 是空的 | `cd client/app && npm run build`，標準版再 `docker compose restart` |
| 從別台連得上但登入後每個動作都 401 | 明文 HTTP 上 `WEB_COOKIE_SECURE=1`，瀏覽器不存 cookie | 走 Tailscale 或反代；真的要明文就設 0 |
| SmartScreen 擋住 | 沒有程式碼簽章憑證 | 「其他資訊 → 仍要執行」。這不是打包壞了 |
| 上傳大檔到一半停住 | Discord 速率限制 | 伺服器會自己退避重試，log 會看到 429 |
| 單機版每次啟動都問密碼 | 這是預設行為 | 見 3.5，用 `SFTP_PASSWORD_FILE` |

看 log：

```bash
docker compose logs --tail 40 sftp-discord-server    # 標準版
```

單機版的 log 直接印在終端機上。

---

## 8. 已知限制

- **只能跑一份。** 不要 `--scale`、不要讓第二份指向同一個資料庫、部署時不要新舊重疊。
  開啟中的檔案 handle 透過一個**行程內**的字典協調，第二份行程對第一份改過的檔案
  會繼續給舊的塊配置——**不報錯、不寫 log，就是舊的位元組。**
- **只有一個帳號。** 開第二個帳號卡在「還沒有密碼救援路徑」。
- **不支援符號連結。**
- **真正同時寫入是後寫的贏。** 跨連線保證的是「每次操作前看得到別人 commit 完的狀態」，
  不是寫入互斥。
- **單機版：純 SFTP 用法不會清游離節點。** 被中斷的覆寫留下的暫存節點要等到
  有人開一次網頁 UI 才會被回收（背景任務刻意不長期持有主金鑰）。
- **能寫資料庫的人可以把檔案還原成舊版本而不被察覺。** 這是唯一一種不會被抓到的竄改，
  擋它需要一個攻擊者碰不到的單調計數器，三種做法都評估過、都不值得。
  **其他所有竄改——改名、搬移、對調兩個檔案、刪掉一個——都會在下次讀取或列目錄時被抓到。**

---

## 相關文件

| 檔案 | 內容 |
|---|---|
| [`README.md`](README.md) | 這個服務是什麼、架構、已知限制 |
| [`BUILD.md`](BUILD.md) | 建置與打包的細節 |
| [`.env.example`](.env.example) | 每一個設定與填錯的代價 |
| [`design-standalone.md`](design-standalone.md) | 單機版怎麼換掉 MongoDB 而不動 `vfs.py` |
| [`ROADMAP.md`](ROADMAP.md) | 已拍板的決策與待辦 |
| [`SOP.md`](SOP.md) | 重複踩到的坑與檢查順序 |
