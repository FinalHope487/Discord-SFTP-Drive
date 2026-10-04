# 2026-08-03 · SFTP 的 `rm` 進垃圾桶，`rmdir` 仍要求空目錄

**決策**：SFTP 的 `rm` 進垃圾桶，`rmdir` 仍要求空目錄

**依據**：
（2026-08-03）。ENOTEMPTY 是 POSIX 契約，
有垃圾桶不是默默吞掉整棵樹的理由。非空目錄整棵進垃圾桶的能力放在 `vfs.trash()`，
只從 web UI 走（`DELETE /api/dir?recursive=true`）。

**反悔成本**：搬入時原文未單獨記錄（2026-10-04 自 `ROADMAP.md`〈已拍板的長期決策〉搬入）。
