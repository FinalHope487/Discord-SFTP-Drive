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

`python 指令 exit 49 且完全沒有輸出（pip / venv / 跑測試都一樣）→ 1. 先 which python：若指向 AppData\Local\Microsoft\WindowsApps\python，那是 Microsoft Store 的轉址 stub，不是直譯器，永遠 exit 49 2. 改用絕對路徑 C:\Users\sword\anaconda3\python.exe（3.12.7，本專案 venv 的來源）3. 專案沒有進版控的 venv，驗證前要先自己建一個並裝 requirements-dev.txt → 環境 PATH 解析到假的直譯器`
（註：此條在只出現一次時就寫入，破例理由是它由 PATH 順序決定、必然重現，不是偶發。）

`pip install -r requirements.txt 失敗 → 1. 檢查檔案是否含非 ASCII 字元（pip 以 locale 編碼讀取，中文 Windows 的 cp950 會在解析任何套件前就 UnicodeDecodeError） 2. 檢查直接依賴是否宣告了寬鬆的轉移依賴範圍、而 pip 解析到範圍內不相容的最新版（pip check 抓不到，因為壞組合仍符合宣告的 metadata；改用「裝完實際 import 一次」驗證） 3. 檢查 Python 版本與 wheel 供應 → 依賴宣告不完整`
