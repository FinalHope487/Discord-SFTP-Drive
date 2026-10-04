# 2026-08-07 · 獨立單機版：packaged app 的密碼走 (a)——外殼跳密碼視窗、用 stdin 餵給後端子行程

**決策**：獨立單機版：packaged app 的密碼走 (a)——外殼跳密碼視窗、用 stdin 餵給後端子行程

**依據**：
（2026-08-07）。另外兩個選項——寫進 `drive.env`（鎖跟鑰匙放一起）、OS 憑證保管庫
（新相依套件）——都否決。**同一條 stdin 管線也拿來當關機訊號**：外殼想關閉時直接
`child.stdin.end()`，不需要另外設計一套協定。理由是 Windows 上量到的事實，不是猜的——
`child.kill()` 在 Windows 一律是強制 TerminateProcess，不管傳哪個訊號名稱都一樣；
`taskkill` 不加 `/f` 對一個沒有視窗的 console 行程會直接拒絕（「這個處理程序只能強制終止」）。
關閉 stdin 兩邊都測過會動：`main.py` 的 `_wait_for_shutdown` 現在接受 `extra_stop`，
跟原本的訊號等待賽跑；`standalone.py` 在被外殼餵密碼的模式下（`DISCORD_DRIVE_STDIN_LIFECYCLE=1`）
把「stdin 讀到 EOF」接上那個 `extra_stop`。

**反悔成本**：搬入時原文未單獨記錄（2026-10-04 自 `ROADMAP.md`〈已拍板的長期決策〉搬入）。
