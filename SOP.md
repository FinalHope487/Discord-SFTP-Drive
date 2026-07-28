# SOP

重複問題的處理路徑記錄。

## 觸發條件

同一類問題第二次出現，立刻補一條。第一次出現不寫（可能是偶發）。

## 格式

`[症狀] → [優先檢查順序] → [根因類型]`

範例：
`啟動時連不上 Discord → 1. 檢查 .env token 2. 檢查網路/proxy 3. 檢查 intents 設定 → 設定值缺漏`

---

## 條目

<!-- 新條目往下加，不刪舊條目 -->

`python 指令 exit 49 且完全沒有輸出（pip / venv / 跑測試都一樣）→ 1. 先 which python：若指向 AppData\Local\Microsoft\WindowsApps\python，那是 Microsoft Store 的轉址 stub，不是直譯器，永遠 exit 49 2. 改用絕對路徑 C:\Users\sword\anaconda3\python.exe（3.12.7，本專案 venv 的來源）3. venv 現已存在於 D:\my-projects\Discord-Drive\venv（2026-07-29 本輪建立，已在 .gitignore 內）；一律用 ./venv/Scripts/python.exe 跑測試與 lint，不要用裸 python → 環境 PATH 解析到假的直譯器`
（註：此條在只出現一次時就寫入，破例理由是它由 PATH 順序決定、必然重現，不是偶發。2026-07-29 第二次遇到，已更新第 3 步：venv 不再需要每次重建。）

`pip install -r requirements.txt 失敗 → 1. 檢查檔案是否含非 ASCII 字元（pip 以 locale 編碼讀取，中文 Windows 的 cp950 會在解析任何套件前就 UnicodeDecodeError） 2. 檢查直接依賴是否宣告了寬鬆的轉移依賴範圍、而 pip 解析到範圍內不相容的最新版（pip check 抓不到，因為壞組合仍符合宣告的 metadata；改用「裝完實際 import 一次」驗證） 3. 檢查 Python 版本與 wheel 供應 → 依賴宣告不完整`

`docker compose exec 帶絕對路徑參數，容器內回報找不到檔案、且錯誤訊息裡出現 C:/Program Files/Git/... → 1. 認出症狀：Git Bash (MSYS2) 會把看起來像 Unix 路徑的參數改寫成 Windows 路徑，錯誤訊息中的 C:/Program Files/Git 前綴就是被改寫的證據，容器本身沒問題 2. 在該次指令前加 MSYS_NO_PATHCONV=1 3. 或改用 sh -c '...' 把路徑包進單引號字串裡（此法連 docker run -v 的 volume 掛載也適用） → 宿主 shell 改寫參數，非容器或應用程式問題`
（註：此條同樣在只出現一次時就寫入，理由與上面那條相同——由 shell 種類決定、必然重現。本輪查 /app/keys 權限時第一次撞到。）
