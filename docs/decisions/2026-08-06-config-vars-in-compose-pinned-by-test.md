# 2026-08-06 · `config.py` 讀得到的每個變數都必須出現在 `docker-compose.yml`，用測試釘住

**決策**：`config.py` 讀得到的每個變數都必須出現在 `docker-compose.yml`，用測試釘住

**依據**：
（2026-08-06）。
`tests/test_compose_coverage.py` 用 `ast` 解析 `config.py` 找出所有 `os.getenv` / `_setting`
的變數名，比對 compose 的 `environment:` 與 `.env.example`。
**理由是這個形狀犯了兩次**（`DISCORD_MAX_CONCURRENCY`、`TRASH_*`），而且第二次發生時
第一次的五行註解就在同一個檔案裡幾行之外。補上缺的幾行不算修好，會斷言涵蓋率的東西才算。
秘密以 `NAME_FILE` 形式出現也算數。**在 image 內整份 skip**——那兩個檔案不在 image 裡，
而在 image 內跑整份 suite 是這個專案的慣例；skip 說的是「這題沒問」，pass 會是「答錯了」。

**反悔成本**：搬入時原文未單獨記錄（2026-10-04 自 `ROADMAP.md`〈已拍板的長期決策〉搬入）。
