// Talks to Residual Relay (../agent) on this computer. Residual Home sends structured intents,
// never prompt strings, and follows the task until it finishes. The agent is the same one ASI
// users talk to on Agentverse: one orchestrator, one Photon action layer.

const { EventEmitter } = require("events");
const fs = require("fs");

class AgentClient extends EventEmitter {
  constructor(cfg = {}, { fetchFn = globalThis.fetch } = {}) {
    super();
    this.url = (cfg.url || "http://127.0.0.1:8100").replace(/\/$/, "");
    this.tokenFile = cfg.tokenFile || null;          // the Relay writes it (agent/state/local_token); only this computer's files hold it
    this.fetch = fetchFn;
    this.health = { ok: false };
    this.watching = new Map();       // task id -> last status
    this._poll = null;
  }
  // The shared secret with the Relay's local API: RESIDUAL_LOCAL_TOKEN, else the file the Relay made. Read each time, so
  // starting the Relay after the app just works.
  _token() {
    const env = (process.env.RESIDUAL_LOCAL_TOKEN || "").trim();
    if (env) return env;
    if (!this.tokenFile) return null;
    try { return fs.readFileSync(this.tokenFile, "utf8").trim() || null; } catch (e) { return null; }
  }
  async _req(path, opts = {}) {
    const tok = this._token();
    const r = await Promise.race([
      this.fetch(this.url + path, { ...opts, headers: { "Content-Type": "application/json", ...(tok ? { "X-Residual-Token": tok } : {}), ...(opts.headers || {}) } }),
      new Promise((_, rej) => setTimeout(() => rej(new Error("agent timed out")), opts.timeout || 4000))
    ]);
    return r.json();
  }
  async check() {
    try {
      const h = await this._req("/health");
      // a bare {ok:true} is what the Relay says to a caller whose token it doesn't accept
      const auth = !!(h && (h.contacts || h.photon));
      this.health = { ...h, ok: true, auth, ...(auth ? {} : { warning: "The Relay is running but didn't accept Residual's token. Restart both, or check agent/state/local_token." }) };
    }
    catch (e) { this.health = { ok: false, error: "Residual Relay isn't running (cd agent && python residual_agent.py)" }; }
    this.emit("health", this.health);
    return this.health;
  }
  // Returns the task summary, or {status: "unavailable"} so the caller can fall back locally.
  async intent(intent) {
    const t0 = Date.now();
    try {
      const res = await this._req("/intent", { method: "POST", body: JSON.stringify(intent), timeout: 6000 });
      this.emit("call", { intent, res, ms: Date.now() - t0 });
      if (res.task_id) this.follow(res.task_id);
      return res;
    } catch (e) {
      const res = { status: "unavailable", error: String(e.message || e) };
      this.emit("call", { intent, res, ms: Date.now() - t0 });
      return res;
    }
  }
  async cancel(taskId, confirmed) {
    try { return await this._req(`/tasks/${taskId}/cancel`, { method: "POST", body: JSON.stringify({ confirmed: !!confirmed }) }); }
    catch (e) { return { status: "unavailable", error: String(e.message || e) }; }
  }
  async simulateReply(contact, text) {
    try { return await this._req("/simulate/reply", { method: "POST", body: JSON.stringify({ contact, text }) }); }
    catch (e) { return { error: String(e.message || e) }; }
  }
  async tasks() { try { return await this._req("/tasks"); } catch (e) { return []; } }
  // ---- sandbox ordering (see agent/commerce.py)
  async order(which = "usual") {
    try { return await this._req("/orders", { method: "POST", body: JSON.stringify({ which }) }); }
    catch (e) { return { status: "rejected", error: "Residual Relay isn't running" }; }
  }
  async confirmOrder(id, confirmed) {
    try { return await this._req(`/orders/${id}/confirm`, { method: "POST", body: JSON.stringify({ confirmed: !!confirmed }) }); }
    catch (e) { return { status: "unavailable", error: String(e.message || e) }; }
  }
  async orders() { try { return await this._req("/orders"); } catch (e) { return []; } }
  // ---- requests from strangers on ASI:One: nothing is sent until the person approves them on this screen
  async asiPending() { try { const l = await this._req("/asi/pending"); return Array.isArray(l) ? l : []; } catch (e) { return []; } }
  async asiDecide(id, approve) {
    try { return await this._req(`/asi/pending/${encodeURIComponent(id)}/decide`, { method: "POST", body: JSON.stringify({ approve: !!approve }) }); }
    catch (e) { return { status: "unavailable", error: String(e.message || e) }; }
  }
  watchAsi(ms = 1500) {
    if (this._asi) return;
    let last = "";
    this._asi = setInterval(async () => {
      const list = await this.asiPending();
      const sig = list.map(p => p.id).join("|");
      if (sig !== last) { last = sig; this.emit("asi", list); }
    }, ms);
  }
  stopAsi() { if (this._asi) { clearInterval(this._asi); this._asi = null; } }
  // ---- the agent team: the care circle (one agent per family member) and the Hands (errands in a real browser)
  async circle() { try { const c = await this._req("/circle"); return c && Array.isArray(c.members) ? c : { members: [], mode: "off" }; } catch (e) { return { members: [], mode: "off" }; } }
  async circleSay(member, text) {
    try { return await this._req("/circle/say", { method: "POST", body: JSON.stringify({ member, text }) }); }
    catch (e) { return { error: String(e.message || e) }; }
  }
  async errands() { try { const l = await this._req("/errands"); return Array.isArray(l) ? l : []; } catch (e) { return []; } }
  async errandNew(goal, url) {
    try { return await this._req("/errands", { method: "POST", body: JSON.stringify({ goal, url }), timeout: 8000 }); }
    catch (e) { return { status: "unavailable", error: String(e.message || e) }; }
  }
  async errandConsent(id, approve) {
    try { return await this._req(`/errands/${encodeURIComponent(id)}/consent`, { method: "POST", body: JSON.stringify({ approve: !!approve }) }); }
    catch (e) { return { status: "unavailable", error: String(e.message || e) }; }
  }
  async errandCancel(id) {
    try { return await this._req(`/errands/${encodeURIComponent(id)}/cancel`, { method: "POST", body: "{}" }); }
    catch (e) { return { status: "unavailable", error: String(e.message || e) }; }
  }
  watchErrands(ms = 1500) {
    if (this._errands) return;
    let last = "";
    this._errands = setInterval(async () => {
      const list = await this.errands();
      const sig = list.map(e => e.id + e.status + (e.helper || "")).join("|");
      if (sig !== last) { last = sig; if (list.length) this.emit("errands", list); }
    }, ms);
  }
  stopErrands() { if (this._errands) { clearInterval(this._errands); this._errands = null; } }
  async audit() { try { return await this._req("/audit"); } catch (e) { return []; } }
  watchOrders(ms = 1500) {
    if (this._orders) return;
    let last = "";
    this._orders = setInterval(async () => {
      const list = await this.orders();
      const sig = (list || []).map(o => o.id + o.status).join("|");
      if (sig !== last) { last = sig; this.emit("orders", list); }
    }, ms);
  }
  follow(id) {
    this.watching.set(id, null);
    if (!this._poll) this._poll = setInterval(() => this._tick(), 700);
  }
  async _tick() {
    for (const [id, last] of this.watching) {
      let t;
      try { t = await this._req(`/tasks/${id}`); } catch (e) { continue; }
      const sig = `${t.status}|${(t.events || []).length}`;
      if (sig !== last) { this.watching.set(id, sig); this.emit("task", t); }
      if (["acknowledged", "declined_all", "exhausted", "sent", "failed", "cancelled"].includes(t.status)) this.watching.delete(id);
    }
    if (!this.watching.size) { clearInterval(this._poll); this._poll = null; }
  }
}

module.exports = { AgentClient };
