// Discord Drive — the desktop shell.
//
// What this is, and what it deliberately is not.
//
// It is a window with a floor on its size, a first-run screen that asks where
// the server is, and a navigation lock. It contains no copy of the file
// manager: the SPA is served by the same aiohttp process as the API, and this
// window loads it from there.
//
// That is not a packaging convenience, it is what keeps the session design
// intact. Authentication is a `dd_session` cookie marked HttpOnly and
// SameSite=Strict. A bundled page loaded from file:// fetching a remote server
// is a cross-origin request, and a SameSite=Strict cookie is not sent on one --
// so shipping the SPA inside the .exe would mean replacing the cookie with a
// token in an Authorization header, and with it the reason a script that gets
// into the page cannot read the credential. The setup screen below is the only
// page this app carries, and it talks to nothing but the main process.

const { app, BrowserWindow, Menu, ipcMain, net, session, shell } = require("electron");
const fs = require("node:fs");
const path = require("node:path");

const { normaliseServerUrl } = require("./server-url.js");
const { LocalDrive, backendPath } = require("./backend.js");

const CONFIG_PATH = path.join(app.getPath("userData"), "config.json");

const MIN_WIDTH = 1024;
const MIN_HEIGHT = 640;
const PROBE_TIMEOUT_MS = 6000;

let mainWindow = null;
let setupWindow = null;

// The standalone backend, when this app is running the no-Docker path rather
// than connecting to a server elsewhere. One instance for the app's whole
// lifetime -- `dd:localStatus` reuses its live child across calls rather than
// spawning a fresh one for every check; see backend.js for why.
const localDrive = new LocalDrive();

// True once a local connection is actually serving the main window, which is
// the one piece of state that decides whether quitting has a child process to
// wait on. Set on success in `dd:localStart`, cleared once it is stopped.
let localModeActive = false;

async function stopLocalBackendIfRunning() {
  if (!localModeActive && !localDrive.running) return;
  localModeActive = false;
  await localDrive.stop();
}

/* --------------------------------------------------------------- settings */

function readConfig() {
  try {
    const parsed = JSON.parse(fs.readFileSync(CONFIG_PATH, "utf8"));
    return typeof parsed === "object" && parsed ? parsed : {};
  } catch {
    // Missing or corrupt both mean "ask again". There is nothing in here worth
    // recovering -- it is one URL -- and a half-parsed config that sent the
    // window at a wrong origin would be worse than the setup screen.
    return {};
  }
}

function writeConfig(next) {
  fs.mkdirSync(path.dirname(CONFIG_PATH), { recursive: true });
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(next, null, 2), "utf8");
}

/* ----------------------------------------------------------------- probing */

/**
 * Ask a candidate server whether it is one, before anything is saved.
 *
 * `/api/health` needs no session, so this distinguishes "nothing is listening"
 * from "something is listening but it is not this" -- which is the difference
 * between "start the container" and "you typed the wrong port", and the two
 * have completely different fixes.
 */
function probe(origin) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (result) => {
      if (!settled) {
        settled = true;
        resolve(result);
      }
    };

    let request;
    try {
      request = net.request({ method: "GET", url: `${origin}/api/health` });
    } catch (error) {
      done({ ok: false, reason: "url", detail: String(error.message || error) });
      return;
    }

    const timer = setTimeout(() => {
      try {
        request.abort();
      } catch {
        /* already gone */
      }
      done({ ok: false, reason: "timeout" });
    }, PROBE_TIMEOUT_MS);

    request.on("response", (response) => {
      let body = "";
      response.on("data", (chunk) => {
        body += chunk.toString("utf8");
      });
      response.on("end", () => {
        clearTimeout(timer);
        try {
          const parsed = JSON.parse(body);
          if (response.statusCode === 200 && parsed.ok === true) {
            done({ ok: true });
            return;
          }
        } catch {
          /* falls through to "not this server" */
        }
        done({ ok: false, reason: "not-discord-drive", status: response.statusCode });
      });
    });

    request.on("error", (error) => {
      clearTimeout(timer);
      done({ ok: false, reason: "unreachable", detail: String(error.message || error) });
    });

    request.end();
  });
}

/* ----------------------------------------------------------------- windows */

// setup.html (connect to a server) and local.html (run on this device) are
// both plain pages inside this one window, reached from each other by an
// ordinary link -- Electron re-injects the preload on every navigation
// within a window, so `window.dd` and `window.ddLocal` both stay available
// across that. `page` picks which one to open with; a caller that wants a
// specific screen on a window already open still gets it, since switching
// modes while setup is already on screen has to work too.
function createSetupWindow(problem, page = "setup.html") {
  if (setupWindow && !setupWindow.isDestroyed()) {
    // `loadFile` here is programmatic (this function was called from code --
    // the menu, or did-fail-load's fallback -- not from a link the page's own
    // script handled), and Electron's `will-navigate` only fires for
    // navigations the page itself initiates. So the same leaving-local.html
    // cleanup the listener below covers for a clicked link has to be done by
    // hand on this path too.
    const current = setupWindow.webContents.getURL();
    if (current.endsWith("local.html") && page !== "local.html") {
      stopLocalBackendIfRunning();
    }
    setupWindow.loadFile(path.join(__dirname, page));
    setupWindow.focus();
    if (problem) setupWindow.webContents.send("dd:problem", problem);
    return setupWindow;
  }

  setupWindow = new BrowserWindow({
    width: 620,
    height: 640,
    resizable: true,
    backgroundColor: "#161826",
    title: "Discord Drive",
    autoHideMenuBar: true,
    show: false,
    webPreferences: {
      // The privileged bridge lives on this window only, and this window only
      // ever loads a file:// page that ships inside the app. The main window
      // -- the one that loads a remote origin -- is created with no preload at
      // all, so the remote page has nothing to reach for.
      preload: path.join(__dirname, "setup-preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  setupWindow.loadFile(path.join(__dirname, page));
  setupWindow.once("ready-to-show", () => {
    setupWindow.show();
    if (problem) setupWindow.webContents.send("dd:problem", problem);
  });

  // Leaving local.html for anywhere else means whatever `status()` spawned
  // there -- a child sitting in "awaiting-password", say -- is no longer
  // wanted. Not awaited: there is nothing in flight for a child that never
  // got a password to protect, so nothing is gained by making the click wait
  // on it.
  setupWindow.webContents.on("will-navigate", (_event, url) => {
    const current = setupWindow.webContents.getURL();
    if (current.endsWith("local.html") && !url.endsWith("local.html")) {
      stopLocalBackendIfRunning();
    }
  });

  setupWindow.on("closed", () => {
    setupWindow = null;
    stopLocalBackendIfRunning();
    // Closing setup with nothing configured and no main window open means the
    // app has nothing to show. Quitting beats leaving a process with no window
    // running in the background.
    if (!mainWindow || mainWindow.isDestroyed()) app.quit();
  });

  return setupWindow;
}

function createMainWindow(origin) {
  mainWindow = new BrowserWindow({
    width: 1360,
    height: 860,
    // Enforced by the window manager, so the drag stops at the floor rather
    // than snapping back from beyond it. The in-page curtain stays anyway:
    // these are logical pixels, and display scaling at 125%/150% can put CSS
    // pixels under the floor while the window is legally large enough.
    minWidth: MIN_WIDTH,
    minHeight: MIN_HEIGHT,
    backgroundColor: "#161826",
    title: "Discord Drive",
    autoHideMenuBar: true,
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
    },
  });

  mainWindow.loadURL(origin);
  mainWindow.once("ready-to-show", () => mainWindow.show());

  // Links to anywhere else open in the real browser. A session cookie that
  // authorises reads of every stored file has no business being carried into
  // a window this app is driving.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  mainWindow.webContents.on("will-navigate", (event, url) => {
    let target;
    try {
      target = new URL(url).origin;
    } catch {
      target = null;
    }
    if (target !== origin) {
      event.preventDefault();
      if (target) shell.openExternal(url);
    }
  });

  mainWindow.webContents.on("did-fail-load", (_event, code, description, failedUrl, isMainFrame) => {
    // Sub-resource failures are the page's problem, not the shell's; only a
    // main-frame failure means "this address did not work".
    if (!isMainFrame) return;
    if (code === -3) return; // ERR_ABORTED — a navigation we cancelled ourselves
    const window = mainWindow;
    mainWindow = null;
    if (localModeActive) {
      // The local backend answering /api/health once is not a promise it
      // still is -- it can still exit later (a crash, or the drive's own
      // idle/absolute session ceiling). Reopening on setup.html would ask
      // for a server address that was never the point; local.html is where
      // this actually needs to go, and its own status check will find
      // whatever state the backend is actually in.
      localModeActive = false;
      stopLocalBackendIfRunning();
      createSetupWindow(null, "local.html");
    } else {
      createSetupWindow({ code, description, url: failedUrl || origin });
    }
    if (window && !window.isDestroyed()) window.destroy();
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  return mainWindow;
}

function openDrive(origin, { local }) {
  const config = { ...readConfig() };
  if (local) {
    // No serverUrl to remember: the origin is re-derived from the settings
    // file on every launch (readWebPort in backend.js), and the password is
    // never remembered at all, so there is nothing stable about this run's
    // origin worth writing down beyond "this is the mode to come back to".
    config.mode = "local";
    delete config.serverUrl;
  } else {
    config.mode = "remote";
    config.serverUrl = origin;
  }
  writeConfig(config);

  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.destroy();
    mainWindow = null;
  }
  createMainWindow(origin);
  if (setupWindow && !setupWindow.isDestroyed()) {
    const window = setupWindow;
    setupWindow = null;
    window.destroy();
  }
}

function connectTo(origin) {
  openDrive(origin, { local: false });
}

/* -------------------------------------------------------------------- menu */

function buildMenu() {
  Menu.setApplicationMenu(
    Menu.buildFromTemplate([
      {
        label: "Discord Drive",
        submenu: [
          {
            label: "切換伺服器…  /  Change server…",
            accelerator: "CmdOrCtrl+Shift+S",
            click: () => createSetupWindow(null),
          },
          { type: "separator" },
          { role: "reload" },
          { role: "forceReload" },
          { role: "toggleDevTools" },
          { type: "separator" },
          { role: "resetZoom" },
          { role: "zoomIn" },
          { role: "zoomOut" },
          { type: "separator" },
          { role: "quit" },
        ],
      },
      {
        label: "編輯 / Edit",
        submenu: [
          { role: "cut" },
          { role: "copy" },
          { role: "paste" },
          { role: "selectAll" },
        ],
      },
    ]),
  );
}

/* -------------------------------------------------------------------- boot */

// One instance. Two windows against one server is fine -- the account allows
// several connections -- but two copies of this app fighting over one config
// file is not something anybody asked for.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    const window = mainWindow || setupWindow;
    if (window && !window.isDestroyed()) {
      if (window.isMinimized()) window.restore();
      window.focus();
    }
  });

  app.whenReady().then(() => {
    // Deny every permission the app has no use for. It uploads files the user
    // picks; it needs no camera, no microphone, and no location.
    session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) =>
      callback(false),
    );

    ipcMain.handle("dd:current", () => readConfig().serverUrl || "");
    ipcMain.handle("dd:probe", async (_event, input) => {
      let origin;
      try {
        origin = normaliseServerUrl(input);
      } catch (error) {
        return { ok: false, reason: error.message === "scheme" ? "scheme" : "url" };
      }
      return { ...(await probe(origin)), origin };
    });
    ipcMain.handle("dd:connect", async (_event, input) => {
      let origin;
      try {
        origin = normaliseServerUrl(input);
      } catch (error) {
        return { ok: false, reason: error.message === "scheme" ? "scheme" : "url" };
      }
      connectTo(origin);
      return { ok: true, origin };
    });

    // The local (no-Docker) path. Data lives in this app's own userData
    // directory -- the same folder this file's own config.json is in -- so
    // one app data location holds both halves rather than inventing a second
    // one next to it.
    const localDataHome = app.getPath("userData");

    ipcMain.handle("dd:localStatus", async (_event, force) => {
      const result = await localDrive.status(backendPath(app.isPackaged, process.resourcesPath), localDataHome, { force });
      return result;
    });
    ipcMain.handle("dd:localStart", async (_event, password) => {
      const result = await localDrive.start(String(password));
      if (result.ok) {
        localModeActive = true;
        openDrive(result.origin, { local: true });
      }
      return { ok: result.ok, reason: result.reason, output: result.output };
    });
    ipcMain.handle("dd:localOpenDataFolder", () => {
      fs.mkdirSync(localDataHome, { recursive: true });
      shell.openPath(localDataHome);
    });

    buildMenu();

    const saved = readConfig();
    if (saved.mode === "local") createSetupWindow(null, "local.html");
    else if (saved.serverUrl) createMainWindow(saved.serverUrl);
    else createSetupWindow(null);

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        const current = readConfig();
        if (current.mode === "local") createSetupWindow(null, "local.html");
        else if (current.serverUrl) createMainWindow(current.serverUrl);
        else createSetupWindow(null);
      }
    });
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });

  // Async cleanup on the way out: intercept the first quit request, stop the
  // local backend if one is running (which is what makes it exit through its
  // own drain rather than being killed alongside this process), then quit for
  // real. `quitting` is what tells the second, self-triggered request apart
  // from the first so this does not loop.
  let quitting = false;
  app.on("before-quit", (event) => {
    if (quitting) return;
    if (!localModeActive && !localDrive.running) return;
    quitting = true;
    event.preventDefault();
    stopLocalBackendIfRunning().finally(() => app.quit());
  });
}
