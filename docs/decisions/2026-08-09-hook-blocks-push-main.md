# 2026-08-09 · `push main` 由 PreToolUse hook 實際攔阻，不再只是 `CLAUDE.md` 的行為引導

**決策**：`push main` 由 PreToolUse hook 實際攔阻，不再只是 `CLAUDE.md` 的行為引導

**依據**：
（2026-08-09）。
`.claude/hooks/block-push-main.py` 檢查兩條路徑：refspec 指名的目標，以及沒有 refspec 時的
當前分支。`.claude/settings.json` 的 `Bash(git push:*)` allow **保留**——功能分支不該每次
跳確認，該擋的只有 main。`deny` 字串比對做不到這件事，涵蓋不了 `git push -u origin HEAD`
與在 main 上不帶參數的 `git push`。

**反悔成本**：搬入時原文未單獨記錄（2026-10-04 自 `ROADMAP.md`〈已拍板的長期決策〉搬入）。
