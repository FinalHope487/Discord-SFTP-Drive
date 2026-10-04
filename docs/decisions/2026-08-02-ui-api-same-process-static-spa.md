# 2026-08-02 · Client UI 的 API 與 SFTP server 同 process，前端是純靜態 SPA

**決策**：Client UI 的 API 與 SFTP server 同 process，前端是純靜態 SPA

**依據**：
（2026-08-02）。
**理由是「第二個副本」那條**：獨立 process 連同一個 MongoDB，`_node_versions` 這個 process 內
字典就會對 UI 說謊——查不到會被當成「沒人改過」，UI 拿著過期的 chunk layout 讀檔，
**沒有錯誤、沒有 log，只是舊位元組**。`aiohttp` 本來就是相依。**代價**：UI 出問題會拖到 SFTP，
兩者不能分開重啟——但這個服務本來就只能跑一個副本。**若日後做了 node 層級樂觀鎖可重新評估。**

**反悔成本**：搬入時原文未單獨記錄（2026-10-04 自 `ROADMAP.md`〈已拍板的長期決策〉搬入）。
