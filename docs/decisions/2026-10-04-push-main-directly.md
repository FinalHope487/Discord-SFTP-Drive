# 2026-10-04 · 直接推 `main`，拿掉擋推 `main` 的 hook

**決策**：每輪 commit 到 `main` 並 `git push origin main`，不開分支、不開 PR。`.claude/hooks/block-push-main.py`、它在 `.claude/settings.json` 的 `PreToolUse` 註冊、`tests/test_push_guard.py`（18 項）一併移除。

**依據**：2026-10-04 換上新版 collab-kit 規則檔，其〈一輪結束前〉第 4 條要求直接推 `main`，與 hook 衝突；user 在 `QUESTIONS.md` Q1 選「拿掉 hook、推 main」。推翻 `2026-08-09-hook-blocks-push-main.md`。

**反悔成本**：`git revert` 移除 hook 的那個 commit 即恢復 hook、註冊與 18 項測試，並把規則檔第 4 條改回開分支＋PR。已直接推上 `main` 的 commit 不受影響。
