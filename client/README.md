# client/ — 桌面前端（第 5 步）

## 內容

```
Discord Drive 桌面版.dc.html   原型本體，用瀏覽器直接開
support.js                     執行時檔案，必須和 .dc.html 同層
_ds/nocturne-…/                Nocturne 設計系統（styles.css + bundle），路徑不能改
handoff-frontend.md            mock → 真 API 要動哪裡
backend-todo.md                後端待辦（可直接貼給後端）
packaging/electron/            Electron 殼，minWidth/minHeight 1024×640
packaging/tauri/               Tauri 殼，同上
packaging/README.md            兩種打包方式的取捨
```

## 跑起來

直接用瀏覽器開 `Discord Drive 桌面版.dc.html`。不需要 build、不需要 server。
Phosphor 圖示從 unpkg CDN 載，離線時圖示會不見，版面不受影響。

右下角有狀態切換清單，17 個狀態逐個跳：登入、瀏覽、上傳成功／失敗+回滾、刪除、
批次刪除撞 429 中斷、垃圾桶還原衝突、session 過期、登入鎖定、排隊已滿、
完整性驗證失敗、搜尋。

## 資料是假的

`FS` / `TREE`（模組頂層常數）、`s.trash`、`this.entries()` 三處是寫死的 mock。
換成真 API 看 `handoff-frontend.md` §2。

## 視窗最小尺寸

1024×640。OS 層在 `packaging/` 兩個設定檔裡設，CSS 的警告遮罩只是 fallback
（瀏覽器裡縮小視窗會看到，打包後看不到，因為系統不讓你縮到那麼小）。
