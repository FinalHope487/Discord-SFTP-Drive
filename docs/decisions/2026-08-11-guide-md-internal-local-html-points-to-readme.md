# 2026-08-11 · `GUIDE.md` 不進「Where things are」表格，`local.html` 的中文首次執行畫面改指`README.md`

**決策**：`GUIDE.md` 不進「Where things are」表格，`local.html` 的中文首次執行畫面改指`README.md`

**依據**：
（2026-08-11，`QUESTIONS.md` 第 3 題）。**選項 A 的前提是錯的，
實際落地的是選項 C。** `QUESTIONS.md` 原本寫「README 表格補一列標明 Chinese，
`GUIDE.md` 刪掉與 README 重複的部分」，動手前才發現 `GUIDE.md` **在 `.gitignore:42`**
——跟 `BUILD.md` 同類的「內部工作文件」，`git clone` 拿不到、`client/shell` 的
`package.json` 的 `files` 清單也沒有它。**`local.html` 的中文畫面在這之前就已經
指著一個打包後的 app 永遠找不到的檔案**，不是這一輪造成的，是這一輪查前提時發現的。
A 選項若照字面做，會是把一個不存在於任何使用者機器上的檔案，**寫進公開文件的表格裡**
——那正是 `tests/test_doc_references.py` 要抓的那類錯誤，只是它沒掃到 `README.md`
以外指向 `GUIDE.md` 的產品文案。
**改成 C**：`local.html` 中英文兩個版本現在都指向 `README.md` 的
「Step 1 — the Discord side」，中文畫面誠實承認那是英文文件。`GUIDE.md` 本身
（未進版控）順手縮成一份「準備 Discord」的本機參考，但它不再被任何被追蹤的檔案引用，
純粹是留給操作這份 repo 的人自己看。

**反悔成本**：搬入時原文未單獨記錄（2026-10-04 自 `ROADMAP.md`〈已拍板的長期決策〉搬入）。
