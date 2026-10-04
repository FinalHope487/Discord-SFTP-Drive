# 2026-07-31 · KDF 用 Argon2id，套件用 `argon2-cffi`

**決策**：KDF 用 Argon2id，套件用 `argon2-cffi`

**依據**：
（2026-07-31，取代 PBKDF2-HMAC-SHA256）。
新包裝一律 Argon2id（64 MiB / t=3 / p=1）；**既有 PBKDF2 記錄照樣打得開**，因為每份記錄
自帶 `kdf` 欄位，一行 migration 都沒有。不選 `cryptography` 44 內建的 Argon2id 是因為
要把 cryptography 跨兩個 major 升上去，而 asyncssh 整個傳輸層坐在它上面。
實測 125ms，比 PBKDF2 600k 的 214ms 還快。

**反悔成本**：搬入時原文未單獨記錄（2026-10-04 自 `ROADMAP.md`〈已拍板的長期決策〉搬入）。
