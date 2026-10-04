# 2026-08-03 · Host key 從哪來

**決策**：
`SFTP_HOST_KEY_PATH`（預設 `host_key`），檔案不存在才產生。
`docker-compose.yml` 有 `host_key_data:/app/keys`，容器重建不會換金鑰（否則客戶端會跳
mismatch，而那個警告與真實中間人攻擊的警告長得一模一樣）。金鑰權限強制 `0600`，
既有金鑰也會被自動修復。**例外是舊的 root-running build 留下的 volume**，
`ensure_host_key()`（`src/main.py`）會偵測並在錯誤訊息裡說明一次性遷移做法。

**依據**：開案問題的最終結論，原拍板日未記錄；2026-08-03 併入 `ROADMAP.md`〈開案問題與最終結論〉，檔名用該日期。

**反悔成本**：搬入時原文未單獨記錄（2026-10-04 自 `ROADMAP.md` 搬入）。
