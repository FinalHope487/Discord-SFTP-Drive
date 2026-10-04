# 2026-08-03 · AES-CBC 的 IV 存哪

**決策**：
**前提已作廢**。演算法是 AES-256-CTR，沒有 file-level IV，
改為每個 chunk 各帶一個 16 bytes nonce 存在該 chunk 的 metadata。理由是 SFTP 是 offset-based，
CBC 必須從頭依序解，per-chunk nonce 才讓隨機讀取成立；附帶好處是密文長度等於明文長度，
不需要 padding。完整性另疊兩層 HMAC-SHA256（`chunk_tag` / `node_tag`），
涵蓋範圍與明確沒涵蓋的部分見 `2026-07-31-integrity-tag-scope.md`。

**依據**：開案問題的最終結論，原拍板日未記錄；2026-08-03 併入 `ROADMAP.md`〈開案問題與最終結論〉，檔名用該日期。

**反悔成本**：搬入時原文未單獨記錄（2026-10-04 自 `ROADMAP.md` 搬入）。
