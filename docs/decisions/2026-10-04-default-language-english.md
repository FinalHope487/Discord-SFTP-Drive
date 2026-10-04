# 2026-10-04 · 檔案管理員與桌面外殼的預設語言改成英文

**決策**：沒有存過語言偏好時，SPA（`localStorage` 的 `dd.lang`）與外殼（`config.json` 的 `lang`）都顯示英文；`<html lang>` 初始值是 `en`。已存的偏好照舊生效，切換按鈕不變。兩邊的偏好仍然各自獨立（`2026-08-11-shell-and-spa-language-stay-separate.md` 不變）。

**依據**：user 於 2026-10-04 要求「支援全英文 UI」，在「補齊英文模式」「預設改英文」「跟系統語系」之中選了預設改英文。推翻 `2026-08-11-shell-default-language-zh.md`。

**反悔成本**：改回三個常數（`client/app/src/App.jsx` 的 fallback、`client/shell/language.js` 的 `DEFAULT_LANGUAGE`、兩頁 `let lang`）與三個 `<html lang>`，加上 `tests/test_ui_language.py`、`tests/test_ui_login.py`、`client/shell/language.test.js` 與 `tests/test_ui_shell.py` 兩條預設語言斷言。已存偏好的使用者不受影響。

**釘住**：`test_a_first_visit_is_in_english`、`test_a_config_with_no_language_still_opens`、`language.test.js` 的 `DEFAULT_LANGUAGE`
