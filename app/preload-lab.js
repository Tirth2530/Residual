const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("lab", {
  backlog: () => ipcRenderer.invoke("lab-backlog"),
  evalResults: () => ipcRenderer.invoke("lab-eval"),
  care: () => ipcRenderer.invoke("lab-care"),
  preflight: () => ipcRenderer.invoke("preflight-run"),
  history: () => ipcRenderer.invoke("lab-history"),
  toHome: a => ipcRenderer.invoke("lab-cmd", a),          // presenter controls: e.g. {simAck:true}
  onTelemetry: fn => ipcRenderer.on("telemetry", (_e, d) => fn(d))
});
