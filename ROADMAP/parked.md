# Roadmap：parked

狀態標為 `[parked]` 的待辦項目。格式、標籤與規則見 [`ROADMAP.md`](../ROADMAP.md)；狀態改了就把條目搬到對應的檔。

---

### [parked] 關掉直譯器時偶爾出現 `Task was destroyed but it is pending`
**具體細節**：2026-08-06 觀察到，指向 `websession.sweeper` 與 `web.trash_sweeper`；同一份程式碼連跑三次出現一次。測試拆 app 時 task 的取消還沒被 loop 處理完，純輸出噪音
**怎麼做**：拆 app 時 await 被取消的 task
**會改變什麼**：測試輸出少一行噪音
**做後回退代價**：`git revert`

### [parked] SFTP 用戶端正常斷線偶爾被記成 WARNING
**具體細節**：2026-07-31 第一次觀察到。`src/sftp.py` 的 `connection_lost()` 只把 `None` 與 `ConnectionResetError` 當正常斷線；`async with asyncssh.connect(...)` 正常關閉會觸發另一種例外。純 log 噪音
**怎麼做**：查出那個例外型別，加進正常斷線清單
**會改變什麼**：log 等級
**做後回退代價**：`git revert`

### [parked] chunk metadata 的 `index` 與 `offset` 互為冗餘
**具體細節**：評估結論是不動：冗餘已被 `chunk_tag` 保護，拿掉 `index` 要改 tag 涵蓋範圍，等於全檔重算。記著是為了不再想一遍
**怎麼做**：不做
**會改變什麼**：無
**做後回退代價**：若真的拿掉，要對所有既有 chunk 重算 tag，回退要再重算一次

### [parked] chunk 壓縮與去重
**具體細節**：只有想法，沒有評估
**怎麼做**：未知
**會改變什麼**：chunk 的儲存格式
**做後回退代價**：壓縮或去重過的 chunk 舊版讀不起來，回退要重寫所有受影響的 chunk
