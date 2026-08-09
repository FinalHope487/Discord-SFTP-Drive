# 待決問題

**回答方式：直接寫在每題下面，或說「全部照推薦，除了 3 和 7」。**
答完後清空本檔；有長期效力的決策搬進 `ROADMAP.md`「已拍板的長期決策」或 `SOP.md`。

---

## 待拍板

## Q1 · commit 的作者 email 要用哪個

**背景**：collab-kit 的「Git Commit 規範」指定作者 `AllenOuO`、email
`swordmaster123.123@gmail.com`。本輪四個 commit 照這條寫了，`git push` 被 GitHub 拒絕：
`push declined due to email privacy restrictions`——這個帳號開了「不允許命令列 push 暴露我的
email」。倉庫既有的 commit 全部用 `130008444+FinalHope487@users.noreply.github.com`。
四個 commit 還在本機，沒有推上去。

**選項**

- **A（推薦）**：規則裡的 email 改成 `130008444+FinalHope487@users.noreply.github.com`，
  作者名維持 `AllenOuO`。我把本輪四個 commit 的 email 改掉再推。
  → 與倉庫既有 commit 一致，不必動 GitHub 設定，真實 email 也不會出現在公開歷史。
- **B**：去 GitHub 關掉 email privacy 保護，然後照原規則推。
  → 規則不用改，但 `swordmaster123.123@gmail.com` 會出現在每一個 commit 的公開歷史裡。
  這是你的帳號設定，我不會去動。
- **C**：作者維持你自己的 `Lin <130008444+...>`，拿掉 collab-kit 那條規則。
  → 歷史最一致，但就沒有「哪些 commit 是 agent 寫的」這個區分了。

**代價**：四個 commit 還沒推出去，A 只要 `git rebase --exec` 或 `filter-branch` 改 email，
代價幾分鐘且不影響任何人。等愈久、上面疊愈多 commit，改起來愈麻煩。

**擋住了**：本輪收尾的最後一步（push 當前分支）。其餘收尾全部完成。

**等答案期間我做了**：四個 commit 已經分段做好且訊息寫完，只差 email 一欄；
`ROADMAP.md` / `SOP.md` / `QUESTIONS.md` 都已更新並各自進了 commit。

<!--
## Q2 · <一句話標題>

**背景**：<相關檔案與現況，只寫做決定需要的>
**選項**
- **A（推薦）**：<做法> → <會怎樣>
- **B**：<做法> → <會怎樣>
**代價**：照 A 做最壞是 <>；事後改成 B 要動 <>
**擋住了**：<沒答就停住的工作；沒擋住寫「已繞過」>
**等答案期間我做了**：<>
-->

## 待你執行／待你批准的動作

<!-- - [ ] <動作>｜為什麼｜已備好的東西（路徑）｜不做的後果 -->

## 卡住（修 3 輪仍紅）

<!-- - <症狀>｜試過什麼｜每次的錯誤｜我認為根因在哪 -->
