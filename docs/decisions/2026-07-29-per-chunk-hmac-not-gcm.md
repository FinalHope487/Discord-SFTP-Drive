# 2026-07-29 · 完整性驗證採 per-chunk HMAC，不採 AES-GCM

**決策**：完整性驗證採 per-chunk HMAC，不採 AES-GCM

**依據**：
（2026-07-29）。加密層維持 AES-256-CTR；
HMAC-SHA256 存在 MongoDB 的 chunk metadata。理由：改動面積最小，且**竄改者即使控制 Discord
也改不到 tag**。

**反悔成本**：搬入時原文未單獨記錄（2026-10-04 自 `ROADMAP.md`〈已拍板的長期決策〉搬入）。
