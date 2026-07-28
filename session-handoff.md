# Session Handoff

依 `.claude/templates/session-handoff.md` 產出。
涵蓋範圍：測試進版控（pytest 化）＋ 設定 fail-fast ＋ docker-compose 補齊。

> **下一個 session 請先看最下面的「開工前：我需要你提供的資料」。**
> 目前所有進度都卡在同一件事上——沒有真實憑證，就無法再往前驗證任何東西。

---

## 目前狀態

模組：`tests/`（新增）、`src/config.py`、`src/main.py`、`pytest.ini`、
`requirements-dev.txt`、`docker-compose.yml`。

上一輪的結論是「四項核心修復已驗證，但測試在 scratchpad，下一輪等於沒有回歸保護」。
**這個缺口已補上**：測試進了 repo，改成 pytest，並在此基礎上做了 ROADMAP 唯一的 `[now]`
——設定 fail-fast，最後補齊 docker-compose。

`src/vfs.py`、`src/sftp.py`、`src/crypto.py`、`src/db.py`、`src/discord_api.py` 一行未改，
本輪只是把它們納入回歸保護。

### 驗證方式與結果

```bash
pip install -r requirements-dev.txt
pytest
```

**77 項全數通過**（78 秒），`pyflakes src tests` 乾淨。

| 檔案 | 涵蓋 | 項數 |
|---|---|---|
| `tests/test_crypto.py` | CTR round-trip、密文長度、7 個 offset 的獨立解密、nonce 不重複 | 11 |
| `tests/test_sftp_e2e.py` | realpath/listdir/stat、多 chunk 上傳下載、5 個 offset 隨機讀取、密文確認、錯誤碼、truncate 釋放舊 chunk、刪除無孤兒 | 24 |
| `tests/test_put_get.py` | `sftp.put()` / `sftp.get()`（asyncssh 並行 copier 路徑，512KB） | 2 |
| `tests/test_rename.py` | `.filepart` 流程、同目錄改名、跨目錄搬移、目錄改名含子節點、v3 不覆寫、posix_rename 覆寫、自身子樹防護 | 15 |
| `tests/test_discord_retry.py` | 429 重試實際重送完整 body、重試次數、耗盡預算的例外型別 | 6 |
| `tests/test_config.py` | 必填項、空字串視同未設、金鑰長度以 bytes 計、DM/channel 二擇一、port 格式、一次回報所有問題 | 19 |

上一輪 42 項 → 本輪 77 項。數字變多主要是把原本一支腳本裡的連續斷言拆成互相獨立的
test（每個 test 自己建 server、自己建資料），不是新增了等量的覆蓋面。**拆開是為了讓失敗
可定位**：原本任何一步壞掉，後面的斷言會連帶崩掉或被跳過。

fail-fast 另外用真實啟動路徑驗過（不是只有單元測試）：空環境啟動 → exit 1 並一次列出
5 個問題；短金鑰＋壞 port → exit 1 並列出 2 個問題。

docker-compose 是**真的 build 起來跑過**，不是只看檔案：

- `docker compose up -d --build` → mongo healthy → app 連上帶認證的 MongoDB → 產生 host key
  → listen 2222。
- 從**宿主機**用真的 asyncssh client 連進容器：`realpath('.')` 回 `/`、`listdir('/')`
  回 `['.', '..']`、密碼錯誤被拒。
- `down` 再 `up`（保留 volume）→ host key 指紋不變（`SHA256:steMz5P7...`），且 log 沒有再出現
  "Generating new host key"。volume 有效。
- 27017 從宿主機連不到（connection refused）。
- 缺 `AES_SECRET_KEY` → `docker compose up` 直接拒絕，不建任何東西。
- `AES_SECRET_KEY=short`（compose 攔不到的格式錯誤）→ 容器 exit 1、log 有可讀訊息、
  **重試 5 次後停住**（`RestartCount=5, Running=false`），不是無限迴圈。

驗完已 `down -v` 清掉 volume（裡面的 MongoDB root 帳號是用測試密碼建的，留著會害你之後
用真密碼連不上——就是下面「已知的坑」講的那個問題）。宿主機殘留一個 mongo 自己宣告的
匿名 volume（`/data/configdb`），空的，`docker volume prune -f` 可清。

**仍未以真實 Discord bot token 實測。** 這一點與上一輪相同，沒有進展——
上面所有 Discord 相關的行為仍然只有 fake 涵蓋。

---

## 已完成

### 一、測試進版控並改用 pytest

新增 `tests/`：

- `tests/conftest.py`：共用 fixture。`sftp` fixture 每個 test 起一台真的 asyncssh
  server 走 loopback，`fake_db` / `fake_discord` 每個 test 給全新的空實例，所以 test 之間
  不會經由 metadata store 互相污染。host key 是 session-scoped（產生一次約一秒，沒有
  test 依賴 per-test 的主機身分）。
- `tests/fakes.py`：`FakeDB` / `FakeDiscord`，原本四支腳本各自複製一份，現在單一來源。
- `pytest.ini`：`asyncio_mode = auto`（全部都是 async，逐一標記只是雜訊）、
  `pythonpath = .`（免除原本寫死的 `sys.path.insert(0, r"D:\my-projects\Discord-Drive")`
  ——那行讓測試只能在這台機器上跑）。
- `requirements-dev.txt`：`pytest==8.3.3`、`pytest-asyncio==0.24.0`、`pyflakes==3.4.0`，
  `-r requirements.txt`。已在乾淨 venv 實際安裝並 import 驗證過。

斷言邏輯逐項對照原腳本搬過來，沒有放寬。原本的 `check(label, cond)` 只印字串，
失敗時看不到實際值；改成 assert 之後 pytest 直接給出差異。

### 二、設定 fail-fast（ROADMAP 的 `[now]`）

`src/config.py`：

- 移除所有 secret 的預設值。原本 `AES_SECRET_KEY` 未設會靜默改用寫死的公開常數
  `0123456789abcdef...`，`SFTP_USER`/`SFTP_PASSWORD` 未設是公開已知的 `testuser`/`testpass`
  ——三者都是「跑得起來但等於沒防護」，比崩潰危險。
- 新增 `check(env)`（回傳問題列表，對傳入的 mapping 是純函式，所以測試不必重載模組或
  改動真實環境）與 `validate(env)`（有問題就丟 `ConfigError`）。
- 驗的內容：`DISCORD_BOT_TOKEN` / `SFTP_USER` / `SFTP_PASSWORD` / `AES_SECRET_KEY` 必填
  （空字串視同未設，`FOO=` 是打錯不是刻意留空）；AES 金鑰**至少 32 bytes 且以 bytes 計**
  ，不足直接拒絕而不是補 `\0`；`DISCORD_USER_ID` 與 `DISCORD_CHANNEL_ID` 至少要有一個；
  `SFTP_PORT` 可解析且在範圍內。
- **一次回報所有問題**，不是遇到第一個就停——一次重啟只解決一個設定錯誤會很難用。
- `SFTP_PORT` 不再於 import 時 `int()`。原本打錯會得到一個裸 traceback，而且是在其他問題
  能被回報之前就炸掉。

`src/main.py`：`validate()` 在 `__main__` 最前面呼叫，任何 socket 或連線之前；
失敗印訊息並 `sys.exit(1)`。

### 三、docker-compose 補齊

`docker-compose.yml` 重寫。原本的三個缺漏都補了，另外處理了 fail-fast 帶來的新問題：

- **`DISCORD_USER_ID` 沒傳** → 補上。原本只傳 `DISCORD_CHANNEL_ID`，DM 模式在 Docker 下
  必定失效。兩者都以可空的形式傳進去，由 app 驗「至少要有一個」——用哪一個是選擇，不是遺漏。
- **host key 沒有 volume** → 新增 `host_key_data` volume 掛在 `/app/keys`，
  `SFTP_HOST_KEY_PATH=/app/keys/host_key`。原本每次 `up --build` 都換金鑰，客戶端會跳
  host key mismatch——而那個警告跟真的中間人攻擊長得一模一樣，等於把使用者訓練成無視它。
- **MongoDB 無認證且對宿主開放 27017** → 加上 root 帳密
  （`MONGO_ROOT_USERNAME` / `MONGO_ROOT_PASSWORD`），`MONGO_URI` 由 compose 組出來帶
  `authSource=admin`；**整段 `ports:` 拿掉**。那個 port 對外開的是一個無密碼、裝著每個檔案
  metadata 與 chunk 位置的資料庫。只有 app 需要連，而它走 compose 內網。
- **`restart: always` → `on-failure:5`**。fail-fast 之後，設定錯誤會讓容器 exit 1；
  配上 `always` 就變成看不見的無限重啟迴圈。改成有上限，錯誤訊息會留在 `logs` 最後一行。
- **compose 層先擋一次**：必填項用 `${VAR:?訊息}`，缺了就在 `docker compose up` 當場報錯，
  連容器都不會建。app 的 `validate()` 仍是第二道（compose 攔不到格式錯誤）。
- **mongo 加 healthcheck、app 改 `depends_on: condition: service_healthy`**。否則 app 可能
  贏了開機競賽、Mongo ping 失敗，把重試次數浪費在一個只是還沒開完機的資料庫上。
- 移除已廢棄的 `version: '3.8'`。

### 四、文件回填

- `.env.example`：分成 REQUIRED / OPTIONAL 兩段，附金鑰產生指令；新增
  `MONGO_ROOT_USERNAME` / `MONGO_ROOT_PASSWORD`（compose 專用）並註明
  `MONGO_URI` 在 compose 下是組出來的、不必自己設；註明「弄丟金鑰＝弄丟所有已存檔案」。
- `missing_info.md`：每節補「現況」段落記錄最後實際做成什麼。**第 3 節（AES-CBC 的
  file-level IV）標為前提作廢**——上一輪已換成 per-chunk nonce，該段描述的東西不存在。
  這是上一輪交接明確點名要處理的。
- **`.gitignore`（新建）**：這個 repo 至今不是 git repo，本輪第一次補上。含
  `.env`（留 `.env.example` 例外）、`host_key` / `keys/`、`__pycache__/`、`venv/`、
  `.pytest_cache/`。**在你 `git init` 之前就先存在**，所以第一次 commit 就不會把
  `.env` 或 host key 帶進去——不必等下一輪再補。
- `ROADMAP.md`：`[now]` 的 fail-fast、`[next]` 的「測試進 repo」與「compose 補齊」移除
  （皆已完成）；新增測試速度、容器 root 執行、log 是否洩密三項。
- `SOP.md`：新增一條「`python` 指令 exit 49 且無輸出」。破例在只出現一次時就寫入，
  理由寫在條目裡——它由 PATH 順序決定，必然重現，不是偶發。

---

## 未完成待辦

完整清單見 `ROADMAP.md`。新的 `[now]` 只有一項：

1. **設定值的可達性檢查**。目前的 fail-fast 只驗「有沒有填、格式對不對」，沒驗「填的
   東西能不能用」。bot token 是否有效、`DISCORD_USER_ID` 能否開成 DM、
   `DISCORD_CHANNEL_ID` 該頻道 bot 有沒有上傳附件權限——這些現在全都要等第一次上傳
   才會炸。**這是你本輪明確指示的：等確定能跑起來之後再處理**，因為在接上真實憑證之前，
   沒有辦法驗證這段檢查程式碼本身是對的。

已知的坑：

- **MongoDB 密碼只在 volume 初始化那一次生效**。`MONGO_INITDB_ROOT_PASSWORD` 只有在
  `mongo_data` 是空的時候才會建帳號。之後改 `.env` 裡的密碼不會有任何作用，只會變成
  app 連不上而你找不到原因。要真的換密碼：進 mongosh 改，或 `down -v` 砍掉 volume
  （**連同所有檔案 metadata 一起消失**）。
- **寫入只支援循序 offset**（未變）。就地修改既有檔案回 `FX_OP_UNSUPPORTED`。
- **AES-CTR 無完整性驗證**（未變）。密文遭竄改不會被偵測。
- **測試 78 秒**。每個 test 重開一次 server，e2e 那組又各自重寫 300KB。目前可忍受，
  但進 CI 前值得改成 module-scoped server。
- **`setstat` 的 size 變更會被拒絕**（未變）。

---

## 本輪不可碰的範圍

- **`src/vfs.py` / `src/sftp.py` / `src/crypto.py` / `src/db.py` / `src/discord_api.py`**
  ——一行未改。本輪的目的是替它們建立回歸保護，而不是同時改動它們；同一輪裡既改實作又
  建測試，測試就只是在複述當下的行為，證明不了任何事。
- **`Dockerfile`**——未改。容器仍以 root 執行；改成非特權使用者要連 host key volume 的
  權限一起處理，屬獨立一件事，已記進 `ROADMAP.md`。
- **認證模型**——維持單一使用者 / 帳密比對。只是把預設值拿掉，比對邏輯本身沒動。
- **加密演算法**——未改。AES-256-CTR、per-chunk nonce 維持原樣；本輪只動金鑰的「取得與
  驗證」，不動「使用」。
- **`todo.md`**——未改，兩項都還是 `[later]`。

---

## 下一步建議任務

**接真實 Discord bot token，跑一次實地驗收。**

順序理由：這是現在唯一還沒被任何測試覆蓋的區塊，而且它擋著 `[now]` 那項——可達性檢查
的程式碼在沒有真實憑證時無從驗證，寫了也只是另一段「看起來對、實際從沒跑過」的東西，
正是前幾輪反覆踩到的模式。

實地驗收時預期會第一批撞到的（先看這幾個，不必從頭 debug）：

1. Discord 附件 URL 有簽章且會過期。`get_attachment_url` 每次重新取，但長時間下載中途
   過期沒有測過。
2. 真實 rate limit 的密度。fake 只驗了「429 會正確重試」，沒驗「1GB ≈ 114 則訊息會不會
   把重試預算 5 次燒光」。
3. Discord 的附件大小上限跟 `MAX_CHUNK_SIZE = 9MB` 是否還相符（免費層目前是 10MiB，
   但這個數字 Discord 改過不只一次）。
4. MongoDB 的併發行為。fake 是單執行緒字典，沒有任何競態。

驗收通過之後再做可達性檢查（`ROADMAP.md` 的 `[now]`）。

---

## 開工前：我需要你提供的資料

驗收要真的把位元組送上 Discord，這些**只有你拿得到**，我無法自行取得。
把它們寫進專案根目錄的 `.env`（**不要貼進對話**，也不要進版控——`.gitignore` 本輪已建好，
已經擋掉 `.env`，這個 repo 現在就算 `git init` 也不會誤 commit 它）。

### 必填四項

| 變數 | 怎麼拿 |
|---|---|
| `DISCORD_BOT_TOKEN` | https://discord.com/developers/applications → 你的 App → Bot → Reset Token。**只會顯示一次**。 |
| `DISCORD_USER_ID` **或** `DISCORD_CHANNEL_ID` | 二擇一。Discord 設定 → 進階 → 開啟「開發者模式」，然後右鍵你自己的頭像／目標頻道 → 複製 ID。 |
| `AES_SECRET_KEY` | 自己產：`python -c "import secrets; print(secrets.token_hex(16))"`。**弄丟＝所有已上傳檔案永久解不開**，先備份到密碼管理器再繼續。 |
| `SFTP_USER` / `SFTP_PASSWORD` | 你自己決定。不要沿用 `testuser` / `testpass`，那是公開已知值。 |
| `MONGO_ROOT_PASSWORD` | 自己產，只有用 docker compose 時需要。 |

### 還需要你確認的三件事（不是資料，是決定）

1. **DM 還是頻道？** 用 DM（`DISCORD_USER_ID`）的話 bot 必須跟你有共同伺服器，否則開不了
   DM；用頻道的話 bot 需要該頻道的 View Channel / Send Messages / Attach Files 權限。
   哪一種比較好驗，你決定。
2. **測試檔案大小上限**。我打算用一個約 30–50MB 的檔案跑完整流程（會產生約 4–6 則
   Discord 訊息）。如果你的帳號有 Nitro 或對訊息量有顧慮，先講。
3. **測完的訊息要不要清掉**。程式在 SFTP 刪檔時會一併刪 Discord 訊息，但如果驗收中途
   失敗，可能留下孤兒附件。要我事後手動清、還是留著給你看？

### 你把 `.env` 準備好之後，我這邊會做的事

```bash
docker compose up -d --build
docker compose logs -f sftp-discord-server
```

然後從宿主機接 SFTP 客戶端，跑一次 connect → mkdir → 上傳 → 下載比對 → rename → 刪除，
確認 Discord 端沒有殘留附件。**這一輪我不會改任何 `src/` 的程式碼**，只驗證與回報；
要改什麼等驗收結果出來再排。
