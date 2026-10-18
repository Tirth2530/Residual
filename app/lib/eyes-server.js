// Serves the eye tracker's files (ui/eyes: the bundle, MediaPipe's WebAssembly runtime, the face model) on
// 127.0.0.1 only. MediaPipe fetches its runtime and model, and fetch doesn't work from file:// pages.
// Only files inside that folder, read-only; nothing from outside this computer can reach it.
const http = require("http");
const fs = require("fs");
const path = require("path");

const TYPES = { ".js": "text/javascript", ".mjs": "text/javascript", ".wasm": "application/wasm", ".task": "application/octet-stream",
                ".json": "application/json", ".data": "application/octet-stream" };

function startEyesServer(root) {
  const base = path.resolve(root);
  const server = http.createServer((req, res) => {
    const url = decodeURIComponent((req.url || "/").split("?")[0]);
    const file = path.resolve(base, "." + url);
    res.setHeader("Access-Control-Allow-Origin", "*");          // the Home page is file://
    res.setHeader("Cross-Origin-Resource-Policy", "cross-origin");
    if (req.method !== "GET" || (file !== base && !file.startsWith(base + path.sep))) { res.statusCode = 403; return res.end(); }
    fs.stat(file, (err, st) => {
      if (err || !st.isFile()) { res.statusCode = 404; return res.end(); }
      res.setHeader("Content-Type", TYPES[path.extname(file)] || "application/octet-stream");
      res.setHeader("Content-Length", st.size);
      fs.createReadStream(file).pipe(res);
    });
  });
  return new Promise((resolve, reject) => {
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => resolve({ server, base: `http://127.0.0.1:${server.address().port}/` }));
  });
}

module.exports = { startEyesServer };
