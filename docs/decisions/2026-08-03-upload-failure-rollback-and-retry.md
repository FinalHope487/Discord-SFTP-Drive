# 2026-08-03 · 上傳失敗要不要 rollback

**決策**：
要。`DiscordFile._rollback()`（`src/vfs.py`），
範圍與代價見 `2026-08-01-rollback-only-handle-created-files.md`，覆蓋見 `tests/test_write_failures.py`。
重試涵蓋 429 / 500 / 502 / 503 / 504 與傳輸層例外，指數退避加抖動、最多 5 次；4xx 不重試。
每次 attempt 重建 request body（`aiohttp.FormData` 是一次性的）。
主動節流在 `src/ratelimit.py`，讀 `X-RateLimit-*` 在被告知之前就先等。
孤兒附件的原則是**一律先寫 metadata 才刪舊附件**——反過來會把一次失敗的更新變成真的資料遺失。

**依據**：開案問題的最終結論，原拍板日未記錄；2026-08-03 併入 `ROADMAP.md`〈開案問題與最終結論〉，檔名用該日期。

**反悔成本**：搬入時原文未單獨記錄（2026-10-04 自 `ROADMAP.md` 搬入）。
