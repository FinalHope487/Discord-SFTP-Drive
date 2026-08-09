// The bridge for the setup window, and nothing else.
//
// Every function here is an ipcRenderer.invoke with no arguments the page can
// use to reach anything else. There is no `require`, no filesystem, and no
// generic "send" -- a preload that exposed one would hand the renderer the
// whole main process through a single string argument.
//
// This preload is attached to the setup window only. The window that loads the
// remote SPA is created with no preload at all, so none of this exists there.
// setup.html (connect to a server) and local.html (run on this device) are
// both loaded into that same window at different times, which is why both
// bridges -- `dd` and `ddLocal` -- live in one file rather than two: Electron
// fixes a window's preload at creation and cannot swap it per navigation.

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("dd", {
  current: () => ipcRenderer.invoke("dd:current"),
  probe: (url) => ipcRenderer.invoke("dd:probe", String(url)),
  connect: (url) => ipcRenderer.invoke("dd:connect", String(url)),
  onProblem: (handler) => {
    ipcRenderer.on("dd:problem", (_event, problem) => handler(problem));
  },
});

contextBridge.exposeInMainWorld("ddLocal", {
  status: (force) => ipcRenderer.invoke("dd:localStatus", Boolean(force)),
  start: (password) => ipcRenderer.invoke("dd:localStart", String(password)),
  openDataFolder: () => ipcRenderer.invoke("dd:localOpenDataFolder"),
});
