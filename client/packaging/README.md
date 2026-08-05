# 打包成 app

兩條路都寫在這裡。**先讀「這包的是什麼」那一節**，不然打出來的東西會不是你期待的。

## 這包的是什麼

`packaging/electron/app/index.html` 是**設計原型的單檔離線版**——2.7 MB，樣式、字型、腳本全部
inline，沒有任何外部相依。它跑起來就是那 17 個狀態，資料是寫死的 mock。

它**還沒接上 `/api/*`**。所以打包出來的 app 有兩種用法，用環境變數切：

| 模式 | 載入什麼 | 用途 |
| --- | --- | --- |
| `DD_LOCAL=1` | 打包進去的 `app/index.html` | 原型審閱、離線 demo、給人看流程，不需要跑後端 |
| 預設 | `http://127.0.0.1:8080` | 真正的客戶端。前端由 aiohttp 一起吐出來 |

正式版要走預設那條：把接好 API 的 SPA 放進 `web.py` 的 static route，桌面 app 只是一層有
min-size 的視窗。**外殼不需要重新打包**——後端換前端，app 下次開就是新的。

## 路線 A · Electron（建議先用這條）

裝得起來就跑得起來，跨三個平台一樣的行為，代價是 body 大約 120 MB。

```bash
cp -r packaging/electron /tmp/dd-shell && cd /tmp/dd-shell
npm install

npm run start:local     # 直接看原型
npm start               # 接 127.0.0.1:8080 的真後端

npm run dist:mac        # → out/Discord Drive-0.1.0.dmg
npm run dist:win        # → out/Discord Drive Setup 0.1.0.exe
npm run dist:linux      # → out/Discord Drive-0.1.0.AppImage
```

`electron-builder` **只能打自己平台的包**（Windows 的 NSIS 要 Wine，macOS 的 dmg 要 macOS）。
要三個平台就開 CI matrix，或在各自機器上跑一次。

macOS 上要能雙擊打開而不是被 Gatekeeper 擋掉，得簽章加公證：`CSC_LINK` / `CSC_KEY_PASSWORD`
加上 `APPLE_ID` / `APPLE_APP_SPECIFIC_PASSWORD` / `APPLE_TEAM_ID`。自己用的話按住 Control 點
「打開」就過了，不用先辦開發者帳號。

## 路線 B · Tauri（body 小很多）

用系統的 WebView（macOS WebKit / Windows WebView2 / Linux WebKitGTK），body 大約 8 MB。
代價是三個平台的 WebView 引擎不同，得各自實測；Linux 的 WebKitGTK 落後最多。

```bash
npm create tauri-app@latest dd-shell -- --template vanilla
cp packaging/tauri/tauri.conf.json dd-shell/src-tauri/tauri.conf.json
cp -r packaging/electron/app dd-shell/src-tauri/../app
cd dd-shell && npm run tauri build
```

需要 Rust toolchain。`tauri.conf.json` 裡 `url` 指著 `http://127.0.0.1:8080`；要看離線原型就把
它拿掉，`frontendDist` 會接手。

## 視窗尺寸下限

`minWidth: 1024` / `minHeight: 640`（Electron 在 `main.js`，Tauri 在 `tauri.conf.json`）。
兩者都是 **OS 視窗管理員層級**的限制：拖到下限游標就停住，不是拖過頭再彈回來。

瀏覽器分頁做不到這件事。`window.resizeTo()` 只對腳本自己 `window.open` 出來的彈出視窗有效，
一般分頁一律被忽略；就算有效，那也是使用者拖一次程式彈一次的抖動，比擋住更糟。

**app 裡那層 1024×640 遮罩要留著。** `minWidth` 管的是邏輯像素，系統縮放 125%／150%、
外接小螢幕、以及 app 自己的 zoom 都能讓 CSS 像素低於下限，而視窗在 OS 眼中仍然合法。
原生限制擋掉大部分，遮罩收尾。

## 後端不在 app 裡

app 不會幫你啟動 MongoDB 和 aiohttp。後端仍然是 `docker compose up -d`，只綁 loopback。
把 Python 塞進 app bundle（PyInstaller + sidecar）評估過但不做：那會讓 `docker-compose.yml`
的 `127.0.0.1:8080:8080` 這條邊界失效，而它是目前唯一擋住外部連線的東西。

連不上的時候 app 會顯示一段中文說明和 `docker compose up -d`，不是 Chromium 的錯誤頁。

## 檔案

```
packaging/
├── electron/
│   ├── main.js            外殼：min-size、導覽鎖定、權限一律拒絕、連不上時的說明頁
│   ├── package.json       electron-builder 設定（dmg / nsis / AppImage / deb）
│   └── app/index.html     2.7 MB 單檔原型，離線可跑
└── tauri/
    └── tauri.conf.json    同樣的視窗設定，換成 Tauri 的寫法
```

`app/index.html` 是編譯產物。要改內容改 `Discord Drive 桌面版.dc.html`，然後重新產出這一包。
