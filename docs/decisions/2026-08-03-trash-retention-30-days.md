# 2026-08-03 · 保留期 30 天，到期由背景 sweeper destroy，可中斷

**決策**：保留期 30 天，到期由背景 sweeper destroy，可中斷

**依據**：
（2026-08-03）。
`TRASH_RETENTION_DAYS` / `TRASH_SWEEP_SECONDS` / `TRASH_SWEEP_BATCH`。
**sweeper 借用 session 的金鑰，不自己持有**——為了背景任務讓進程長期持有主金鑰是安全上的倒退。
代價：保留期的語意是「至少這麼久」，沒人登入時不會清。

**反悔成本**：搬入時原文未單獨記錄（2026-10-04 自 `ROADMAP.md`〈已拍板的長期決策〉搬入）。
