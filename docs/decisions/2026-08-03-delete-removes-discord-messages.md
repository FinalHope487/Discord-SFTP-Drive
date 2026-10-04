# 2026-08-03 · 檔案刪除：只刪 metadata 還是也刪 Discord 訊息

**決策**：
兩者都做。涵蓋三條會產生孤兒附件的
路徑：刪除、truncate 覆寫、`posix_rename` 覆寫既有目標，各有測試盯著「操作後 Discord 端
不得殘留附件」（`tests/test_sftp_e2e.py`、`tests/test_rename.py`）。
2026-08-03 起 SFTP 的 `rm` 改為進垃圾桶，實際刪除由 sweeper 執行。

**依據**：開案問題的最終結論，原拍板日未記錄；2026-08-03 併入 `ROADMAP.md`〈開案問題與最終結論〉，檔名用該日期。

**反悔成本**：搬入時原文未單獨記錄（2026-10-04 自 `ROADMAP.md` 搬入）。
