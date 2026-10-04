# 2026-08-09 · commit 作者一律用倉庫既有的 git config，不另外標記哪些是 agent 寫的

**決策**：commit 作者一律用倉庫既有的 git config，不另外標記哪些是 agent 寫的

**依據**：
（2026-08-09）。
原本 collab-kit 規定寫死一個固定的作者 email，但那個 GitHub 帳號開了 email privacy
保護，帶該 email 的 commit 一 push 就被拒。已把該規則從 skill 移除。

**反悔成本**：搬入時原文未單獨記錄（2026-10-04 自 `ROADMAP.md`〈已拍板的長期決策〉搬入）。
