# Session Handoff

跨多輪任務結束前，依此模板產出交接摘要。

> 2026-08-07 · 獨立單機版的後端落地（分支 `feat/standalone-app`）

---

## 目前狀態

**後端整條路都通了，而且驗證過。** `dist-standalone/discord-drive.exe`（17 MB）
建得出來、跑得起來、會建自己的 SQLite 資料庫與六個索引，**不需要 Docker、不需要 MongoDB**。
啟動序列走到 Discord 認證才停，因為我手上只有假 token——那一步只有你能做。

- **702 項測試全過**（+38），pyflakes 乾淨。
- **整套測試在真 SQLite 上再跑一次：699 過、3 skip**（skip 的是 MongoDB 專屬的索引遷移路徑）。
- `vfs.py`、`crypto.py`、`keystore.py`、`sftp.py`、`web.py` **一行都沒改**。
- **在 `feat/standalone-app` 分支上，尚未 commit。**

## 已完成

- **`src/sqlitedb.py`**（新）——長得跟 Motor 一樣的 SQLite 後端。文件整份存 JSON，
  索引欄位用 generated column 拉出來（不是手寫同步，那會寫錯而且錯得無聲）。
- **`src/standalone.py`**（新）——免 Docker 的進入點：決定資料目錄、填單機版設定、
  解析密碼，然後交給沒改過的 `main.start_server()`。
- **`src/db.py`**——`DB_BACKEND` 分派。索引宣告仍只有這一份，兩個後端共用。
- **`src/config.py`**——`DB_BACKEND` / `SQLITE_PATH`，含驗證；compose 與 `.env.example` 同步。
- **`discord-drive.spec`**（新）+ `requirements-dev.txt` 加 `pyinstaller==6.21.0`（**新相依，僅建置用**）。
- **`tests/`**——`--db=sqlite` 選項、`sqlite_support.py`（測試用寫回代理）、
  `test_sqlite_backend.py`（20 項）、`test_standalone.py`（16 項）。
- 文件：`design-standalone.md`（新）、`README.md`、`BUILD.md`、`ROADMAP.md`、`SOP.md`。

**這一輪抓到的兩個 bug，都只有「拿真 SQLite 跑整套測試」才會現形：**

1. **SQLite 索引名是 per-database 不是 per-collection。** `nodes`/`keystore`/`users`
   各自要的 `id_1` 互相覆蓋，**啟動後只有最後一個存在**，另外兩個唯一索引無聲消失。
   在那之前 664 項測試全綠——因為沒有任何一項測試證明過重複鍵會被拒
   （`fakes.py` 自己講明不強制唯一性）。已修（SQL 層加表名前綴），教訓進 `SOP.md`。
2. **`test_swapping_two_filenames_is_caught_on_both` 描述的竄改，真資料庫會拒絕。**
   直接對調兩個檔名中間會有一瞬間兩個活節點同名。**那條測試從來沒有在真環境重現的可能。**
   已改走暫用名，斷言與結果完全不變。

## 未完成待辦

- **`[now]` packaged app 的密碼從哪來——我停在這裡，因為它動到金鑰處理。**
  現在的 exe 是 console 程式，沒有 `SFTP_PASSWORD` 就在終端機問，密碼不落地。
  **Electron 外殼沒有終端機**，三個選項與代價寫在 `ROADMAP.md` 最上面那條。
  **這是唯一擋住「雙擊打開」的東西。**
- **`[next]` Electron 外殼啟動後端**——卡在上面那條，餵密碼的方式決定子行程怎麼起。
- **用真的 bot token 實跑一輪單機版**：上傳、讀回、覆寫、重啟後仍讀得到。
  **只有你能做**，我手上沒有憑證。
- **Linux 版沒建過。** PyInstaller 跟 electron-builder 一樣只能打自己平台的包。
- `[later]` `children()` 在兩個後端都是全表掃描（**不是這輪引入的**，兩邊一致）。
  修法要加 `parent_id` 索引，屬於改 schema，沒動。

## 本輪不可碰的範圍

- **`vfs.py` / `crypto.py` / `keystore.py` / `users.py` / `sftp.py` / `web.py`**：一行都沒改。
  整個方案的重點就是讓加密、tag 完整性、認證流程全部落在不用動的那一邊。
- **schema / tag 版本**：`TAG_VERSION` 仍是 3，沒有任何 migration。
- **既有 MongoDB 部署**：`DB_BACKEND` 預設仍是 `mongo`，compose 裡還把它釘死成 `mongo`
  （否則 `.env` 一改就會產生「起了一個沒人連的資料庫」）。既有 `.env` 行為完全不變。
- **兩種後端之間沒有遷移工具**，這是拍板時就講明的。
- **`BLUEPRINT.md`**：沒讀。

## 下一步建議任務

1. **先回答密碼那一題**（`ROADMAP.md` 的 `[now]`）。它擋住外殼整合，
   而且是三個選項裡唯一你不能事後便宜改掉的——(b) 一旦寫進設定檔，
   之後要收回來意味著所有既有安裝都要處理。
2. **再用真 token 實跑一輪單機版**。排第二是因為它會驗到我驗不到的那一半，
   而且不需要等第 1 題。
3. 決定要不要 commit、以及怎麼拆（六件事彼此獨立：SQLite 後端／設定分派／
   測試基礎建設／進入點／打包／文件）。
