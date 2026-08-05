// The bridge for the setup page, and nothing else.
//
// Three functions, each one an ipcRenderer.invoke with no arguments the page
// can use to reach anything else. There is no `require`, no filesystem, and no
// generic "send" -- a preload that exposed one would hand the renderer the
// whole main process through a single string argument.
//
// This preload is attached to the setup window only. The window that loads the
// remote SPA is created with no preload at all, so none of this exists there.

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("dd", {
  current: () => ipcRenderer.invoke("dd:current"),
  probe: (url) => ipcRenderer.invoke("dd:probe", String(url)),
  connect: (url) => ipcRenderer.invoke("dd:connect", String(url)),
  onProblem: (handler) => {
    ipcRenderer.on("dd:problem", (_event, problem) => handler(problem));
  },
});
