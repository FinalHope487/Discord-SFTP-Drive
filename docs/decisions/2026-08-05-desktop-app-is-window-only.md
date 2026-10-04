# 2026-08-05 · 桌面 app 只是視窗，SPA 由後端吐出來，不包進 exe

**決策**：桌面 app 只是視窗，SPA 由後端吐出來，不包進 exe

**依據**：
（2026-08-05）。
**理由是 cookie**：認證是 `dd_session`，帶 `HttpOnly` 與 `SameSite=Strict`。從 `file://`
載入的頁面去 fetch 遠端伺服器是跨來源請求，`SameSite=Strict` 的 cookie 不會被送出——
要能用就得改成 Authorization header，等於推翻 2026-08-02 那條「瀏覽器只拿到不透明 id」
的設計。exe 只帶一頁「填伺服器位址」的設定畫面，填完 `loadURL(伺服器)`，之後全部同源。
**代價**：exe 一定要有一台跑得起來的後端。**好處**：改前端不必重打包，也不必重建 image
（`dist/` 是掛進容器的），因此不會掉光所有 session。

**反悔成本**：搬入時原文未單獨記錄（2026-10-04 自 `ROADMAP.md`〈已拍板的長期決策〉搬入）。
