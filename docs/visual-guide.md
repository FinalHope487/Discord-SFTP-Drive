# 圖片操作說明：怎麼放、放哪、怎麼不讓它過期

研究日期 2026-10-04。同日落地：README〈Using it〉的四張 PNG 在 `docs/images/`。

## 重新產生截圖

介面改了就跑一次，再看 `git diff --stat docs/images` 與變動的圖，確認它還在教旁邊那一步：

```bash
cd client/app && npm run build
./venv/Scripts/python.exe -m pytest tests/capture_readme_images.py
```

腳本檔名不是 `test_*.py`，預設的 `pytest` 不會跑它。圖片連結指向不存在的檔案時，`tests/test_doc_references.py` 會紅。

## 查到的事實

| 事實 | 來源 |
|---|---|
| GitHub Markdown 能用的圖片格式：PNG、GIF、JPEG、SVG；影片：MP4、MOV、WebM（建議 H.264） | [GitHub Docs：Attaching files](https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/attaching-files) |
| 上限：圖片與 GIF 一律 10 MB；影片在免費方案 10 MB、付費方案 100 MB | 同上 |
| 在編輯器拖曳上傳的檔案會換成一個匿名 URL，**存在 GitHub，不在 repo 裡** | 同上 |
| README 裡的 GIF 會自動播放、循環，不用按播放 | [rapidevelopers](https://www.rapidevelopers.com/md/github-for-non-tech/how-to-embed-videos-in-github-readme) |
| 不能嵌 YouTube／Vimeo 播放器；替代做法是放一張縮圖，點了連到影片 | 同上 |
| `<picture>` 加 `prefers-color-scheme` 可以依淺色／深色主題換圖；`<img>` 是退路，`alt` 寫在它身上 | [GitHub Blog](https://github.blog/developer-skills/github/how-to-make-your-images-in-markdown-on-github-adjust-for-dark-mode-and-light-mode/) |
| Playwright 可以截圖，但判斷不了「這張還該不該留在文件裡」；最該自動化的是「狀態可重現、過期代價高」的那幾張；用 locator 只截一個面板，比截整個視窗穩定 | [dev.to：Playwright screenshots for documentation](https://dev.to/jakexkim/playwright-screenshots-for-documentation-what-to-automate-and-what-to-review-3h44) |

## 格式怎麼選

| 要教的東西 | 用什麼 | 理由 |
|---|---|---|
| 一個畫面上要點哪裡（設定頁、登入頁） | PNG 截圖，必要時框出重點 | 最小、最清楚，看得到字 |
| 一段 3～10 秒的連續動作（拖曳上傳、還原垃圾桶） | GIF | 在 README 會自動播放；超過 10 秒就會太大或太糊 |
| 從安裝到第一次上傳的完整流程 | MP4 拖曳上傳，或縮圖連到影片 | GIF 塞不下；免費方案的 MP4 上限是 10 MB |

## 放在哪

| 位置 | 好處 | 壞處 |
|---|---|---|
| repo 內 `docs/images/`，README 用相對路徑 | 跟著版本走；`git clone` 下來就有；可以用腳本重新產生 | 每換一版圖，歷史就多一份二進位檔 |
| 拖曳上傳（GitHub 匿名 URL） | 不佔 repo | 不在版控、沒辦法用腳本重產；改圖得手動重傳 |
| GitHub Wiki | 和程式碼分開 | 這個 repo 的公開文件規則是「每個事實只有一個家，而且都要能從 `README.md` 連到」（`docs/decisions/2026-08-11-one-home-per-fact.md`），Wiki 等於另開一個家 |

## 本專案的建議

1. **放 README 的〈Using it〉**，三到五張：登入、檔案清單、上傳、垃圾桶還原、桌面外殼的設定頁。〈Step 1 — the Discord side〉不放截圖：那是 Discord 的介面，改了我們也不會知道，連到 Discord 官方文件比較不會過期
2. **圖片進 repo 的 `docs/images/`**，用 PNG 和短 GIF；MP4 只在真的需要完整流程時才拖曳上傳
3. **截圖用腳本產生，不手動截**：沿用 `tests/test_ui_login.py` 那一套（真瀏覽器 → 真 aiohttp → `fake_discord`），寫一支 `scripts/` 底下的截圖腳本，資料一律是假的。UI 改了就重跑一次，用 `git diff --stat docs/images` 看哪幾張變了，再人工看一眼
4. **淺色／深色各一張**，用 `<picture>` 包起來；`alt` 寫出這張圖在教哪一步，不要只寫「截圖」
5. **圖片裡不能出現真資料**：真檔名、伺服器位址、bot token、頻道 ID。用假資料產生的截圖本來就不會有

## 要先知道的限制

- `tests/test_doc_references.py` 已擴大到嵌入的圖片（Markdown 圖片語法與 HTML 的 src 屬性）
- `client/shell/local.html` 的首次執行畫面在打包後的 app 裡是離線頁，連不到 README 的圖片；要在 app 裡給圖，就得把圖打包進 `client/shell`，並加進 `package.json` 的 `files` 清單
- SPA 只有深色主題（`client/app/src/styles.css` 的 `color-scheme: dark`），所以只產生一套圖，不用 `<picture>`
