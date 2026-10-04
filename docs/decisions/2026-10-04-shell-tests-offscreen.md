# 2026-10-04 · 外殼測試的視窗放在螢幕外、不搶焦點

**決策**：`tests/shell_support.py` 啟動 Electron 時設 `DD_SHELL_OFFSCREEN=1`；`client/shell/main.js` 看到它就把視窗建在 (-20000, -20000)、`skipTaskbar`、用 `showInactive()` 顯示、不呼叫 `focus()`。一般使用者執行時沒有這個變數，行為不變。

**依據**：user 要求跑測試時不要跳出實體視窗。先試的「完全不 show」被 user 選過，但 `test_a_rejected_token_puts_the_backend_output_on_the_screen` 在不顯示的視窗下穩定失敗（5/5 紅，顯示時 2/2 綠；關掉 `backgroundThrottling` 無效），所以改用 user 提的備案：螢幕外＋不切焦點。

**反悔成本**：刪 `main.js` 的 `OFFSCREEN` 常數與 `reveal()`、`shell_support.py` 的那個環境變數與兩個 Win32 輔助函式、一條測試。沒有資料受影響。

**釘住**：`test_the_test_window_stays_off_screen_and_unfocused`（只在 Windows 跑；CI 的 Linux 本來就在 xvfb 下）
