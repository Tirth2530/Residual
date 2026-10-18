const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("dock", {
  onState: fn => ipcRenderer.on("state", (_e, d) => fn(d)),
  pick: side => ipcRenderer.send("dock-pick", side)
});
