# SOP

重複問題的處理路徑記錄。

## 觸發條件

同一類問題第二次出現，立刻補一條。第一次出現不寫（可能是偶發）。
例外：由環境／工具本身決定、必然重現的問題，第一次就寫。

## 格式

`[症狀] → [優先檢查順序] → [根因類型]`

若這條教訓是「以後行為要改」而不只是「怎麼查」，加一行
`→ 已升格為 CLAUDE.md 的〈哪一節〉`，並實際寫進 `CLAUDE.md`。

範例：
`啟動時連不上 Discord → 1. 檢查 .env token 2. 檢查網路/proxy 3. 檢查 intents 設定 → 設定值缺漏`

---

## 條目

<!-- 新條目往下加，不刪舊條目 -->

`python 指令 exit 49 且完全沒有輸出（pip / venv / 跑測試都一樣）→ 1. which python：若指向 AppData\Local\Microsoft\WindowsApps\python，那是 Microsoft Store 的轉址 stub，不是直譯器，永遠 exit 49 2. 改用絕對路徑 C:\Users\sword\anaconda3\python.exe（3.12.7，本專案 venv 的來源）3. venv 位於 D:\my-projects\Discord-Drive\venv（已在 .gitignore 內）；一律用 ./venv/Scripts/python.exe 跑測試與 lint，不要用裸 python → 環境 PATH 解析到假的直譯器`

`pip install -r requirements.txt 失敗 → 1. 檢查檔案是否含非 ASCII 字元（pip 以 locale 編碼讀取，中文 Windows 的 cp950 會在解析任何套件前就 UnicodeDecodeError）2. 檢查直接依賴是否宣告了寬鬆的轉移依賴範圍、而 pip 解析到範圍內不相容的最新版（pip check 抓不到，因為壞組合仍符合宣告的 metadata；改用「裝完實際 import 一次」驗證）3. 檢查 Python 版本與 wheel 供應 → 依賴宣告不完整`

`docker compose exec 帶絕對路徑參數，容器內回報找不到檔案、且錯誤訊息裡出現 C:/Program Files/Git/... → 1. 認出症狀：Git Bash (MSYS2) 會把看起來像 Unix 路徑的參數改寫成 Windows 路徑，錯誤訊息中的 C:/Program Files/Git 前綴就是證據，容器本身沒問題 2. 在該次指令前加 MSYS_NO_PATHCONV=1 3. 或改用 sh -c '...' 把路徑包進單引號字串裡（此法連 docker run -v 的 volume 掛載也適用）→ 宿主 shell 改寫參數，非容器或應用程式問題`

`測試全綠但服務一碰到真的 MongoDB 就起不來（`CannotCreateIndex` / `Expression not supported in partial index`）→ 1. 認出這一類：`tests/fakes.py` 的 `FakeDB` 只記下索引參數，不驗證規格也不強制唯一性，所以任何「MongoDB 會拒絕的索引」在單元測試裡都是綠的 2. 索引規格的正確性只有真的 MongoDB 能判斷——起 `docker compose up -d` 看啟動 log，或用一次性 scratch collection 直接試建（本專案就是這樣分辨 `$exists: false` 與 `{field: None}` 的）3. partialFilterExpression 只接受：等值、`$exists: true`、範圍運算子、`$type`、`$and`/`$or`/`$in`。`$exists: false` **不在裡面**（內部是 `$not`）；要表達「欄位不存在」就用對 null 的等值比對，它同時匹配 null 與缺欄位 → 假件不模擬的那一層，不是程式邏輯錯`
（2026-08-05：垃圾桶的部分唯一索引從 2026-08-03 寫錯到今天沒被發現，因為那三天沒有在真環境重啟過。**推論：凡是只有真依賴會驗證的東西——索引規格、rate limit、附件 URL 過期——綠色的單元測試不構成證據。** 上線前一定要在真的 stack 起一次。）

`asyncssh 的 listen 或 connect 每次要 0.8～1 秒，但 CPU 沒在忙、金鑰長度也無關 → 1. 先 profile 而不是猜（cProfile 排 cumulative，一眼就會看到 socket.getfqdn → _socket.gethostbyaddr）2. 那是 asyncssh 在算 GSSAPI 預設主機名，跟 SSH 本身無關 3. 兩端都傳 gss_host=None：這個專案只做密碼認證，順帶關掉本來就不該提供的 GSSAPI 路徑 → 宿主反向 DNS 慢，非應用程式效能問題`
（2026-07-31：修完 302 項從 208 秒降到 23 秒。教訓是「先量再修」——原本 ROADMAP 上的計畫是重構 fixture，完全打錯地方。）

`跑會改寫原始碼的腳本（突變測試之類）被中斷後，測試結果或行為變得莫名其妙 → 1. 先 git diff / grep 確認 src/ 沒有殘留被改壞的程式碼，不要先去 debug 測試 2. 這類腳本一律把備份寫到磁碟而不是記憶體，程序被 kill 時 finally 不保證會跑 3. 絕對不要讓腳本開機時「自動還原備份」——它分不出「上次殘留的突變」與「這期間寫的新修正」，會把後者蓋掉；正確做法是偵測到殘留就停下來報告 → 工具本身的副作用，非受測程式問題`
（2026-07-31 同一輪連續踩兩次；第二次是自動還原把剛寫好的修正回退了，看起來像「修正沒生效」，昂貴得多。）

`寫在 scratchpad 的驗收腳本一跑就 KeyError: 'SFTP_USER'（或其他 .env 變數），但 .env 明明有那一行 → 1. 不要去翻 .env 或懷疑 docker 環境變數，問題在 load_dotenv() 2. 無參數的 load_dotenv() 走 find_dotenv()，那是從「呼叫端檔案所在目錄」往上找，不是從 cwd——腳本在 scratchpad，往上找不到專案的 .env 3. 一律寫成 load_dotenv(r"D:\my-projects\Discord-Drive\.env") → 函式的搜尋起點與直覺不符，非設定檔缺漏`
（本專案的慣例是把驗收腳本寫在 scratchpad，所以每一支新腳本都會踩到。憑證留在 .env 讓腳本自己讀、不用命令列參數傳，是為了不讓密碼出現在指令紀錄裡。）

`用字串拼出來的腳本把資料寫進 DB，之後讀取報 TypeError（例如 string indices must be integers）→ 1. 先確認寫進去的欄位型別，不要先去 debug 讀取端：多半是 JSON 被編碼了兩次，存進去的是字串而不是 list/dict 2. 根因通常是 json.dumps 包在 f-string 裡再包 repr，層數自己算錯 3. 正解是不要把資料內插進程式碼——改用 base64 傳值，並在寫入後讀回來斷言型別 → 產生程式碼的引號層級錯誤，非資料庫或應用程式問題`
（2026-07-31 同一輪踩兩次，第二次把正在驗收的檔案 metadata 寫壞、必須刪掉重來。教訓是「這種 helper 一定要自我驗證」。）

`新加的安全檢查/行為在單元測試全綠，但實地用真實客戶端一測就發現根本沒生效 → 1. 先看 src/sftp.py 那一層是不是繞過了你加檢查的那個函式（scandir 呼叫 children() 而不是 list_dir、方法簽章對不上導致 asyncssh 根本沒呼叫到、cleanup 不在 wait_closed() 的等待範圍內）2. 判準：你的測試是直接呼叫 VFS，還是走完整協定？直接呼叫 VFS 的測試對「協定層沒接上」完全免疫 3. 每一個安全性質至少要有一條走真實 asyncssh 連線、而且是「竄改之後從協定那一端斷言被拒」的測試——happy path 不算數 → 協定層與 VFS 層有兩條路徑，檢查只加在其中一條`
（2026-08-01 寫入，第三次出現：SFTPServer 方法簽章對不上、SIGTERM 的 flush 不在 wait_closed() 等待範圍內、scandir 繞過 list_dir。共同點都是「VFS 那半邊是對的」。tests/test_sftp_e2e.py 存在的理由就是這件事。）

`改動牽涉到資料庫「約束」（唯一索引、外鍵之類）時，測試全綠但真實 MongoDB 會炸 → 1. 先問「這個假件有沒有假裝實作它其實不強制的東西」：tests/fakes.py 的 create_index 明講了『唯一性是 MongoDB 的職責，這裡只記錄不強制』，所以任何依賴唯一索引的設計在假件上必然全綠 2. 判準是「我的改動有沒有讓兩份文件共用同一組索引鍵」——垃圾桶讓被刪節點保留原本的 (parent_id, filename)，於是『刪掉再建同名檔案』在真 DB 上是 duplicate key，在假件上什麼事都沒有 3. 補法是斷言「我們有沒有去要求那個約束」（索引選項、partialFilterExpression 的內容），而不是斷言約束生效 → 假件刻意不模擬的那一半，非應用程式邏輯問題`
（2026-08-03 寫入。「單元測試全綠、實地才壞」的第二個軸線：上一條是協定層繞過 VFS 層，這條是持久層假件與真件行為不同。共同點是**測試證明的東西比它看起來證明的少**，而發現方式不是更用力寫測試，是去讀那個替身自己宣告了不做什麼。順帶：改索引選項時，舊的「重新 create 一次以取得名稱」復原路徑會失效，因為那次 create 也會撞 conflict；改用 index_information() 依 key spec 反查名稱。）

`測試單獨跑都過，一起跑就有一個之後全部卡住不動（沒有錯誤、沒有 timeout，就是停住）→ 1. 先懷疑模組層級的 asyncio 同步原語（本專案是 src/vfs.py 的 _dir_locks），不要先去看卡住的那一支測試——它多半是受害者不是兇手 2. 判準：pytest 的 event loop 是每支測試一個，而 asyncio.Lock 屬於它被 await 的那個 loop。一支測試結束時若還有 task 停在 async with 裡（被 abort 的 SSH 連線、還沒收尾的背景 task），那個 finally 永遠不會跑，鎖就以「已鎖住」的狀態留在字典裡；而它的鍵是 root 這種跨測試不變的 id，於是之後每一支都在等一個屬於已消失的 loop 的鎖 3. 定位方法是 autouse fixture 在每支測試後印出 registry 內容與持有者 task，兇手會自己現形（本專案就是這樣一次抓到的）4. 修法兩層：conftest 加 autouse fixture 在每支測試「之前」清空 registry，以及回頭修那支留下 pending task 的測試（多半是等錯了事件——共用的 Event 被前一個連線先 set 過而忘了 clear）→ 模組層級狀態跨 event loop，非受測程式邏輯錯`
（2026-08-06 寫入，第一次出現就寫：這是工具本身決定、必然重現的一類。症狀完全不指向原因——卡住的是後面的測試，錯的是前面那支——而且它與 src/db.py 開頭警告的 Motor「attached to a different loop」是同一個根因的兩種長相。順帶：這也是為什麼 _node_versions 那種純快取的模組層級 dict 無害，而帶鎖的不是。）

`PreToolUse hook 掛上去之後，連唯讀的 Bash 指令都被擋，而且錯誤訊息本身是亂碼 → 1. 先看 hook 是不是自己壞了，不要去查 matcher 或 settings.json 格式：訊息裡出現 JSONDecodeError 就是 hook 讀 stdin 的問題，不是設定問題 2. `json.load(sys.stdin)` 走的是 locale 編碼，中文 Windows 是 cp950，payload 只要帶一個非 ASCII 字元就解析失敗；一律改成 `sys.stdin.buffer.read().decode("utf-8")`，寫 stderr 同理走 `sys.stderr.buffer.write(...encode("utf-8"))`，否則拒絕訊息本身沒人看得懂 3. fail-closed 的範圍要收窄到「該擋的那件事」：matcher 是 `Bash` 的 hook 一旦無條件擋，會連讀檔跑測試一起擋死整個 session。讀不懂 payload 時，只在原始文字裡看得到目標關鍵字才擋 → 跨程序邊界的編碼假設，非 hook 判斷邏輯錯`
（2026-08-09 寫入，第一次出現就寫：工具與環境決定、必然重現。值得記的是它怎麼被發現的——單元測試餵的全是 ASCII，18 項全綠；是 hook 真的掛上去之後擋掉我自己的下一個指令才浮出來。**凡是「跑在別人的程序裡、吃別人餵的 stdin」的東西，測試餵什麼就只驗到什麼**，回歸測試因此補了非 ASCII 與壞 payload 兩組。）

`同一個名字的索引在多個 collection 上都要建，換到 SQLite 後只有最後一個真的存在（沒有錯誤、沒有 log）→ 1. 先跑 SELECT name FROM sqlite_master WHERE type='index' 對照你以為建了幾個，不要先懷疑建立索引的程式碼——它每一次都成功回傳了 2. 判準：MongoDB 的索引名是 per-collection，SQLite 的是 per-database。本專案 nodes / keystore / users 都要一個叫 id_1 的唯一索引，於是第二次建立看到名字被佔，判定「定義變了」而把前一個砍掉重建 3. 修法是在 SQL 那一層把表名前綴進索引名（nodes_id_1），並在 index_information() 回報時把前綴拿掉，讓上層仍然看到 MongoDB 的名字 4. 驗證方式不是「列出索引」而是「對每個 collection 各塞兩筆重複鍵，斷言都被拒」——只列名字的斷言正是這個 bug 騙過去的那種 → 跨資料庫的命名空間層級不同，非索引邏輯錯`
（2026-08-07 寫入。這是「假件不模擬的那一半」的第三次，但軸線換了：前兩次是假件比真件寬鬆，這次是**兩個真後端彼此語意不同**。發現方式值得記——不是靠讀程式碼，是靠一條斷言「三個 collection 的索引都在」的測試；在那之前整套 664 項測試在缺了兩個唯一索引的情況下全綠，因為沒有任何一項測試曾經證明過重複鍵會被拒。凡是「約束由資料庫強制」的東西，測試就必須斷言那個約束真的擋得住，而不是斷言我們要求過它。）
