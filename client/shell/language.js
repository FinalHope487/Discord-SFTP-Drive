// What a stored language preference is allowed to be, and the menu's words.
//
// Split out of main.js for the reason server-url.js is: this is the half of
// the feature that is a function of its arguments, so `node --test` can check
// it. Everything else about the switch -- the config file, the IPC handlers,
// the two pages -- can only be checked by launching a window, which is what
// tests/test_ui_shell.py does.
//
// The page copy is not here. Both pages carry their own strings inline
// because their Content-Security-Policy allows `script-src 'unsafe-inline'`
// and nothing else: a `<script src="...">` in either of them is blocked. The
// duplication that would normally argue against this does not exist -- the
// two pages share no sentence.

const LANGUAGES = ["zh", "en"];
const DEFAULT_LANGUAGE = "zh";

/**
 * The language a value means, or the fallback.
 *
 * Deliberately total. It reads config.json, which is a file on disk that a
 * user can edit and that an older version of this app wrote without a `lang`
 * key at all, so "not a language" is a normal input and not an error worth
 * propagating -- readConfig() already treats a corrupt config as "ask again".
 */
function normaliseLanguage(value, fallback = DEFAULT_LANGUAGE) {
  const text = typeof value === "string" ? value.trim().toLowerCase() : "";
  if (LANGUAGES.includes(text)) return text;
  return LANGUAGES.includes(fallback) ? fallback : DEFAULT_LANGUAGE;
}

/** The other one. Two languages, so the switch is a toggle rather than a list. */
function otherLanguage(value) {
  return normaliseLanguage(value) === "zh" ? "en" : "zh";
}

// The application menu, which is the shell's own chrome rather than either
// page's. It follows the preference the setup screens write, and is rebuilt
// when they write it.
const MENU = {
  zh: {
    app: "Discord Drive",
    changeServer: "切換伺服器…",
    reload: "重新載入",
    forceReload: "強制重新載入",
    devTools: "開發人員工具",
    resetZoom: "原始大小",
    zoomIn: "放大",
    zoomOut: "縮小",
    quit: "結束",
    edit: "編輯",
    cut: "剪下",
    copy: "複製",
    paste: "貼上",
    selectAll: "全選",
  },
  en: {
    app: "Discord Drive",
    changeServer: "Change server…",
    reload: "Reload",
    forceReload: "Force reload",
    devTools: "Developer tools",
    resetZoom: "Actual size",
    zoomIn: "Zoom in",
    zoomOut: "Zoom out",
    quit: "Quit",
    edit: "Edit",
    cut: "Cut",
    copy: "Copy",
    paste: "Paste",
    selectAll: "Select all",
  },
};

function menuStrings(lang) {
  return MENU[normaliseLanguage(lang)];
}

module.exports = {
  LANGUAGES,
  DEFAULT_LANGUAGE,
  normaliseLanguage,
  otherLanguage,
  menuStrings,
};
