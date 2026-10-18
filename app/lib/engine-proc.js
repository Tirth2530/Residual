// Starts and watches the Python engine, so the person never has to open a terminal.
// If an engine is already running on the port (someone started it by hand), it's used as is.
// If ours dies, it's restarted with backoff and the screen says so instead of silently freezing.

const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const { EventEmitter } = require("events");

class EngineProc extends EventEmitter {
  constructor(cfg = {}, root) {
    super();
    this.cfg = { autostart: true, source: "webcam", port: 8000, person: "default", python: "", ...cfg };
    this.dir = path.resolve(root, this.cfg.dir || "../engine");
    this.proc = null; this.tail = []; this.restarts = 0; this.stopped = false;
    this.state = { status: "idle", external: false, python: null, pid: null, error: null };
  }
  python() {
    if (this.cfg.python) return this.cfg.python.replace(/^~(?=\/|$)/, require("os").homedir());   // e.g. an existing venv
    for (const v of [".venv", ".venv311", "venv"]) {
      const p = path.join(this.dir, v, process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
      if (fs.existsSync(p)) return p;
    }
    return process.platform === "win32" ? "python" : "python3.11";
  }
  async alive() {
    try {
      const r = await Promise.race([fetch(`http://127.0.0.1:${this.cfg.port}/api/state`), new Promise((_, rej) => setTimeout(() => rej(new Error("t")), 1200))]);
      return r.ok;
    } catch (e) { return false; }
  }
  // The port answering is not the same as the camera working: ask the engine.
  async health() {
    try {
      const r = await Promise.race([fetch(`http://127.0.0.1:${this.cfg.port}/api/health`), new Promise((_, rej) => setTimeout(() => rej(new Error("t")), 1500))]);
      return await r.json();
    } catch (e) { return { ok: false, why: "engine_not_answering" }; }
  }
  watchHealth(ms = 3000) {
    clearInterval(this._hw);
    this._hw = setInterval(async () => {
      if (this.state.status !== "running") return;
      const h = await this.health();
      const why = h.ok ? null : h.why || "unhealthy";
      if (why !== (this.state.unhealthy || null)) this.set({ unhealthy: why, camera: h.camera || null });
    }, ms);
  }
  set(s) { Object.assign(this.state, s); this.emit("state", { ...this.state, tail: this.tail.slice(-8) }); }
  async start() {
    if (!this.cfg.autostart) return this.set({ status: "manual" });
    if (await this.alive()) return this.set({ status: "running", external: true });
    if (!fs.existsSync(path.join(this.dir, "residual", "server.py"))) return this.set({ status: "missing", error: `No engine at ${this.dir}` });
    const py = this.python();
    const args = ["-m", "residual.server", "--source", this.cfg.source, "--app", "--port", String(this.cfg.port), "--person", this.cfg.person];
    this.set({ status: "starting", python: py, error: null });
    try {
      this.proc = spawn(py, args, { cwd: this.dir, env: { ...process.env, PYTHONUNBUFFERED: "1" } });
    } catch (e) { return this.set({ status: "failed", error: String(e.message || e) }); }
    this.set({ pid: this.proc.pid });
    const take = d => { for (const l of String(d).split("\n")) if (l.trim()) this.tail.push(l.slice(0, 200)); this.tail = this.tail.slice(-40); };
    this.proc.stdout.on("data", take); this.proc.stderr.on("data", take);
    this.proc.on("error", e => this.set({ status: "failed", error: `Couldn't run ${py}: ${e.message}` }));
    this.proc.on("exit", code => {
      this.proc = null;
      if (this.stopped) return;
      this.restarts++;
      // A webcam that won't open (no camera, permission denied) shouldn't leave the person with
      // nothing: after two failures, run the engine on keys so the app still works, and say why.
      if (this.cfg.source === "webcam" && this.restarts >= 2) {
        this.cfg.source = "keyboard";
        this.fellBack = `The camera didn't start (${this.tail.slice(-1)[0] || "no details"}). Using keys for now.`;
      }
      const wait = Math.min(30, 2 ** Math.min(this.restarts, 5));
      this.set({ status: "restarting", error: this.fellBack || `Engine stopped (code ${code}). Restarting in ${wait}s.` });
      setTimeout(() => this.start(), wait * 1000);
    });
    // healthy once the port answers
    for (let i = 0; i < 40 && this.proc; i++) {
      await new Promise(r => setTimeout(r, 500));
      if (await this.alive()) { this.restarts = 0; return this.set({ status: "running", external: false }); }
    }
  }
  stop() { clearInterval(this._hw); this.stopped = true; if (this.proc) this.proc.kill(); }
}

module.exports = { EngineProc };
