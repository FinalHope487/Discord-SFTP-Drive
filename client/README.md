# client/ — 前端與桌面外殼

兩個各自獨立的 npm 套件。打包步驟在 [`../BUILD.md`](../BUILD.md)，這裡只講結構。

```
client/
├── app/        檔案管理介面（Vite + React）。由後端當靜態資源吐出來
└── shell/      桌面外殼（Electron）。視窗、尺寸下限、首次設定伺服器位址
```

## 為什麼是兩個

外殼**不含**檔案管理介面。認證是 `dd_session` cookie，帶 `HttpOnly` 與
`SameSite=Strict`；從 `file://` 載入的頁面去 fetch 遠端伺服器是跨來源請求，
`SameSite=Strict` 的 cookie 不會被送出。把 SPA 包進 exe 就得改用 Authorization
header，等於放棄「頁面裡的腳本讀不到憑證」這個保證。

所以外殼只帶一頁設定畫面，填完位址之後 `loadURL(伺服器)`，之後全部同源，
cookie 照 2026-08-02 拍板的設計運作。

**代價**：exe 一定要有一台跑得起來的後端才有用。**好處**：改前端不用重打包 exe，
使用者下次開啟就是新版。

---

## app/ — 檔案管理介面

```
src/
├── api.js       唯一知道 wire format 的模組。ApiError 帶 401 / 409 / integrity 的判別
├── App.jsx      狀態機與版面：導覽歷史、選取、上傳佇列、對話框路由
├── Login.jsx    登入。session 長度的選項來自伺服器回報的上限
├── Panes.jsx    側欄、清單／格狀、詳細資訊、垃圾桶、搜尋、傳輸匣、狀態列
├── Dialogs.jsx  新增／改名／確認／還原衝突／連線／到期／完整性橫幅
├── format.js    大小、時間、路徑、種類
├── i18n.js      中英兩份字典
├── icons.jsx    內嵌 SVG，沒有圖示字型也沒有 CDN
└── styles.css   Nocturne 的 token，字型改用系統字（不 @import Google Fonts）
```

指令：`npm run dev`（5173，把 `/api` 代理到 8080）、`npm run build`（產出 `dist/`）。

### 幾個不明顯的地方

- **`dist/` 是掛進容器的，不是烤進 image 的**（`docker-compose.yml` 的
  `./client/app/dist:/app/web:ro`）。改前端不必重建 image，也就不必掉光所有 session。
- **倒數計時來自伺服器**。`GET /api/session` 每 10 秒問一次，兩次之間用 `Date.now()`
  的真實差值內插。原型是每 700ms 的 `setInterval` 扣一秒，時鐘快 43%，而且快的方向
  正好是「以為還有時間」。
- **清單裡每一列的盾牌是空心的，不是打勾**。列目錄只驗子項集合（誰在裡面），
  不驗每個子項自己的標籤——所以清單上的大小是未經驗證的，開啟或下載才會真的檢查。
  畫一個綠勾等於介面替伺服器說了它沒說過的話。
- **完整性失敗不能用 × 關掉**，要按「我知道了，記錄下來」，事件會留在狀態列的計數裡。
  這種事件代表有人在沒有金鑰的情況下改了資料庫或 Discord 那一側，一按就消失等於沒發生過。
- **上傳進度是瀏覽器送出的位元組數**，不是伺服器切到第幾塊——分塊在伺服器那側做，
  瀏覽器沒有誠實的方式知道那個數字。
- **下載走 `<a download>` 而不是 fetch 成 blob**。2 GiB 的檔案讀進記憶體再交給
  `createObjectURL` 就是 2 GiB 的 renderer heap。代價是傳輸匣不顯示下載進度，所以它也不假裝有。
- **沒有 CDN**。沒有 React CDN、沒有 webfont、沒有圖示字型。`index.html` 的 CSP 把這件事寫死，
  以後誰加了外部來源會直接壞掉，而不是「剛好在線上的人看起來正常」。

---

## shell/ — 桌面外殼

```
main.js              視窗、首次設定流程、導覽鎖定、權限一律拒絕
server-url.js        使用者輸入 → origin。唯一有安全後果的邏輯，所以獨立成模組
server-url.test.js   node --test
setup.html           首次設定畫面（唯一隨 app 出貨的頁面）
setup-preload.js     三個 IPC 函式的橋，只掛在設定視窗上
make-icon.py         產生 icon.png，只用標準函式庫
```

指令：`npm start`（不打包直接跑）、`npm run dist`（產出 `../../dist-desktop/`）、
`node --test`。

### 幾個不明顯的地方

- **兩個視窗，不是一個。** 設定視窗有 preload，主視窗**完全沒有 preload**。
  同一個視窗先載設定頁再載遠端頁的話，遠端頁也會拿到那個 bridge。
- **存檔之前先 probe。** 「連線並記住」會先打 `/api/health`，所以
  「沒有東西在聽」與「有東西在聽但不是這個服務」分開回報。前者去起容器，後者是埠號打錯。
- **`server-url.js` 拒絕非 http(s) 的 scheme，而不是改寫它。**
  第一版把 `http://` 硬黏在前面，`file:///etc/passwd` 就變成一個 host 叫 `file` 的 origin。
  測試裡有這一條。判斷 scheme 看的是 `://` 不是冒號——`localhost:8080` 有冒號但不是 scheme。
- **`minWidth` / `minHeight` 是 OS 層級的**，拖到下限游標就停住。頁面裡那層 1024×640 的遮罩
  仍然留著：`minWidth` 管的是邏輯像素，系統縮放 125%／150% 能讓 CSS 像素低於下限，
  而視窗在 OS 眼中仍然合法。
- **0×0 不是「視窗太小」**，是「還沒量到」。用 `ResizeObserver` 補 `resize` 事件不會發生的那一次。

---

## 設計原型在哪

`design/v1-file-manager-prototype.dc.html`（連同 `support.js` 與 `_ds/`）是這一版介面的
設計原型，17 個狀態、資料全是寫死的 mock、執行時從 unpkg 抓 React 與 Babel。
**它不是 app，離線打開是一片空白。** 留著只當視覺與文案的參考。
