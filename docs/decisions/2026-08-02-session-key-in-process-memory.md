# 2026-08-02 · HTTP session 把 master key 留在 process 記憶體，瀏覽器只拿到不透明 id

**決策**：HTTP session 把 master key 留在 process 記憶體，瀏覽器只拿到不透明 id

**依據**：
（2026-08-02）。
兩條被否決的路：**把金鑰加密塞進 cookie**——偷到 cookie 就是偷到金鑰本身，而且金鑰每個 request
都過一次網路；**每個 request 重新推導**——每次兩輪 Argon2、約 250ms。**重啟後所有 session 失效
是刻意的**：要讓 session 活過重啟，就得把 master key 寫在某個比密碼更弱的東西底下。

**反悔成本**：搬入時原文未單獨記錄（2026-10-04 自 `ROADMAP.md`〈已拍板的長期決策〉搬入）。
