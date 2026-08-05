// Discord Drive — Electron shell.
//
// This is the whole answer to "can the window be hard-limited in size": minWidth
// and minHeight below are enforced by the OS window manager, so the resize
// cursor simply stops at 1024 x 640. It is not a resize-then-snap-back — the
// drag never gets past the limit.
//
// The in-page 1024x640 warning stays anyway. minWidth is measured in logical
// pixels, so display scaling at 125%/150% and the app's own zoom can still put
// CSS pixels below the floor while the window is legally large enough.

const { app, BrowserWindow, shell, session } = require("electron");
const path = require("node:path");

// Where the aiohttp server publishes the SPA. docker-compose.yml binds
// 127.0.0.1:8080:8080 — that publish, not WEB_HOST, is the network boundary.
const SERVER_URL = process.env.DD_SERVER_URL || "http://127.0.0.1:8080";

// Set DD_LOCAL=1 to load the bundled prototype instead of the live server.
const LOCAL_BUNDLE = path.join(__dirname, "app", "index.html");

const MIN_WIDTH = 1024;
const MIN_HEIGHT = 640;

function createWindow() {
  const win = new BrowserWindow({
    width: 1360,
    height: 860,
    minWidth: MIN_WIDTH,
    minHeight: MIN_HEIGHT,
    backgroundColor: "#161826",
    titleBarStyle: process.platform === "darwin" ? "hiddenInset" : "default",
    // The design draws its own traffic lights and title bar, so on macOS we
    // hide the native one and let the page own that strip.
    trafficLightPosition: { x: 12, y: 13 },
    show: false,
    webPreferences: {
      // The renderer is a plain SPA talking to localhost over fetch. It needs
      // no Node access, and the master key never reaches it — the browser only
      // ever holds an opaque session id.
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      spellcheck: false,
    },
  });

  win.once("ready-to-show", () => win.show());

  // External links go to the real browser, never inside the app window.
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  // Keep the renderer pinned to its origin. A session cookie that authorises
  // reads of every stored file has no business being sent anywhere else.
  win.webContents.on("will-navigate", (event, url) => {
    const allowed = process.env.DD_LOCAL === "1"
      ? url.startsWith("file://")
      : url.startsWith(SERVER_URL);
    if (!allowed) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });

  if (process.env.DD_LOCAL === "1") {
    win.loadFile(LOCAL_BUNDLE);
  } else {
    win.loadURL(SERVER_URL);
    win.webContents.on("did-fail-load", (_e, code, desc) => {
      // Almost always "the container isn't up yet". Say so instead of showing
      // Chromium's error page.
      win.loadURL(
        "data:text/html;charset=utf-8," +
          encodeURIComponent(`<!doctype html><html><body style="margin:0;height:100vh;display:grid;place-items:center;background:#161826;color:#e9e9ed;font:14px/1.6 system-ui,-apple-system,'Noto Sans TC',sans-serif">
<div style="max-width:44ch;padding:24px">
<div style="font-size:17px;font-weight:500;margin-bottom:8px">連不上 ${SERVER_URL}</div>
<div style="color:#9a9aa8">後端還沒起來。在 repo 目錄跑 <code style="font-family:ui-monospace,monospace;color:#9184d9">docker compose up -d</code>，然後重開這個視窗。</div>
<div style="margin-top:14px;font-family:ui-monospace,monospace;font-size:12px;color:#6b6b7a">${desc} (${code})</div>
</div></body></html>`)
      );
    });
  }

  return win;
}

app.whenReady().then(() => {
  // Deny every permission the app has no use for. It uploads files the user
  // picks; it does not need a camera, a microphone, or your location.
  session.defaultSession.setPermissionRequestHandler((_wc, _perm, cb) => cb(false));

  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
