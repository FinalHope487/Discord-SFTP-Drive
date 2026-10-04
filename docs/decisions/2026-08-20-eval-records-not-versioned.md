# 2026-08-20 · 評測記錄不進版控

**決策**：agy 評測的記錄檔不進版控，只有由評測轉成的規則改動進 PR。

**依據**：第九輪 `QUESTIONS.md` 由 user 答覆。`.gitignore` 已擋住 `AGY_GEMINI_EVAL.md`、`QUESTIONS.md`、`session-handoff.md`；
這些檔的收件人是下一輪的自己，對外沒有意義（同 `2026-08-10-readme-only-public-doc.md` 把內部工作文件移出版控的理由）。

**反悔成本**：從 `.gitignore` 拿掉該行再 `git add`；本機檔案仍在，沒有資料遺失。
