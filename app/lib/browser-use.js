// AI web mode: Browser Use as the general fallback for websites Residual has no built-in actions for.
//
// Known apps (YouTube, Instagram, WhatsApp, Netflix) use fast local actions and never touch this.
// For any other site:
//   1. one Browser Use session for the visit (its live view is shown in Residual's middle panel)
//   2. ONE task reads the page and returns a short list of meaningful actions (cached per page)
//   3. Residual offers those actions as two-at-a-time choices (no agent call per blink or eyebrow)
//   4. the chosen action is ONE task, which also returns the next page's actions (so the page is read
//      again only when it actually changed)
// A task that fails or times out leaves the person on the last safe screen with a plain message.
// The API key comes only from the environment or app/.env (git-ignored), never from code.

const { EventEmitter } = require("events");
const fs = require("fs");
const path = require("path");

const BASE = "https://api.browser-use.com/api/v2";

const ACTIONS_SCHEMA = JSON.stringify({
  type: "object",
  properties: {
    page: { type: "string", description: "What this page is, in a few words" },
    url: { type: "string", description: "The URL of the page you are on now" },
    actions: {
      type: "array", maxItems: 8,
      items: { type: "object", properties: {
        label: { type: "string", description: "What the person would choose, 2 to 5 words, e.g. 'Read the top story'" },
        instruction: { type: "string", description: "Exact instruction for a browser agent to do this on this page" }
      }, required: ["label", "instruction"] }
    }
  },
  required: ["actions"]
});

const RULES = "The person using this has severe motor impairment and chooses with only two signals, so keep choices few and clear. " +
  "You may read, navigate, scroll, open links and search. Never buy, pay, check out, post publicly, send messages, delete, unsubscribe, " +
  "sign in, sign up, change account or security settings, or accept terms; never type passwords or personal details. " +
  "Skip actions that need typing free text. Skip cookie banners by closing them if they block the page.";
// Defence in depth: whatever the model returns, consequential actions are never offered as choices here.
// They need Residual's separate confirmed workflow (and money only ever goes through the spending policy).
const CONSEQUENTIAL = /\b(buy|purchase|pay|payment|checkout|check out|place (an )?order|order now|add to (cart|bag|basket)|post|publish|tweet|share publicly|send|delete|remove|unsubscribe|subscribe|sign ?in|log ?in|sign ?up|register|create (an )?account|password|security|2fa|two-factor|accept (all|terms)|donate|transfer)\b/i;
const safeActions = list => (list || []).filter(a => a && a.label && a.instruction && !CONSEQUENTIAL.test(a.label + " " + a.instruction));

function readKey(appDir) {
  if (process.env.BROWSER_USE_API_KEY) return process.env.BROWSER_USE_API_KEY.trim();
  try {
    const env = fs.readFileSync(path.join(appDir, ".env"), "utf8");
    const m = env.match(/^\s*BROWSER_USE_API_KEY\s*=\s*"?([^"\n]+)"?/m);
    return m ? m[1].trim() : null;
  } catch (e) { return null; }
}

class BrowserUse extends EventEmitter {
  constructor({ apiKey, fetchFn = globalThis.fetch, base = BASE, pollMs = 1500, readTimeoutMs = 90000, doTimeoutMs = 150000 } = {}) {
    super();
    this.key = apiKey || null;
    this.fetch = fetchFn; this.base = base; this.pollMs = pollMs;
    this.readTimeoutMs = readTimeoutMs; this.doTimeoutMs = doTimeoutMs;
    this.cache = new Map();          // url -> actions
    this.state = { status: "idle", url: null, sessionId: null, liveUrl: null, actions: [], page: "", error: null, busySince: null, task: null };
  }
  get ready() { return !!this.key; }
  set(s) { Object.assign(this.state, s); this.emit("state", { ...this.state }); }

  async req(method, p, body) {
    const r = await this.fetch(this.base + p, {
      method, headers: { "X-Browser-Use-API-Key": this.key, "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined
    });
    const text = await r.text();
    let data = null; try { data = text ? JSON.parse(text) : null; } catch (e) { data = { raw: text }; }
    if (!r.ok) throw new Error(`Browser Use ${method} ${p}: ${r.status} ${(data && (data.detail || data.message)) ? JSON.stringify(data.detail || data.message).slice(0, 200) : ""}`);
    return data;
  }

  // A task in this visit's session; resolves with { output, parsed } or throws (failed / timed out).
  async task(task, { startUrl, schema, maxSteps = 25, timeoutMs }) {
    const body = { task, maxSteps, flashMode: true, ...(schema ? { structuredOutput: schema } : {}),
                   ...(this.state.sessionId ? { sessionId: this.state.sessionId } : { startUrl: startUrl || this.state.url }) };
    const t0 = Date.now();
    const created = await this.req("POST", "/tasks", body);
    if (created.sessionId && !this.state.sessionId) this.oneOff = created.sessionId;   // auto session: closes after the task
    this.set({ task: created.id });
    for (;;) {
      if (Date.now() - t0 > timeoutMs) {
        this.req("PATCH", `/tasks/${created.id}`, { action: "stop" }).catch(() => {});
        throw new Error(`Browser Use took longer than ${Math.round(timeoutMs / 1000)} s`);
      }
      await new Promise(r => setTimeout(r, this.pollMs));
      let t;
      try { t = await this.req("GET", `/tasks/${created.id}`); }
      catch (e) {   // tasks are also listed on their session
        const s = await this.req("GET", `/sessions/${created.sessionId || this.state.sessionId}`);
        if (s.liveUrl && !this.state.liveUrl) this.set({ liveUrl: s.liveUrl });
        t = (s.tasks || []).find(x => x.id === created.id) || {};
      }
      if (t.status === "finished") {
        let parsed = null;
        try { parsed = typeof t.output === "string" ? JSON.parse(t.output) : t.output; } catch (e) {}
        return { output: t.output, parsed, cost: t.cost, ms: Date.now() - t0 };
      }
      if (t.status === "failed" || t.status === "stopped") throw new Error(`Browser Use task ${t.status}${t.output ? ": " + String(t.output).slice(0, 160) : ""}`);
    }
  }
  async refreshLive() {
    try {
      const s = await this.req("GET", `/sessions/${this.state.sessionId}`);
      if (s.liveUrl) this.set({ liveUrl: s.liveUrl });
    } catch (e) {}
  }

  // Open a site and read what can be done there.
  async open(url) {
    if (!this.ready) throw new Error("AI web mode needs a Browser Use key (BROWSER_USE_API_KEY in app/.env)");
    await this.close();
    this.set({ status: "reading", url, actions: [], page: "", error: null, busySince: Date.now(), liveUrl: null, sessionId: null });
    try {
      // a session that stays open for the whole visit, so actions run on the same page
      const s = await this.req("POST", "/sessions", { startUrl: url }).catch(() => null);
      if (s && s.id) { this.set({ sessionId: s.id, liveUrl: s.liveUrl || null }); if (!s.liveUrl) this.refreshLive(); }
      await this.read(url);
    } catch (e) {
      this.set({ status: "error", error: String(e.message || e), busySince: null });
    }
    return { ...this.state };
  }
  async read(url) {
    const key = url || this.state.url;
    if (this.cache.has(key)) return this.set({ status: "ready", actions: this.cache.get(key), busySince: null });
    const r = await this.task(`Go to ${key} if you are not there. Look at the page and list the most useful things a person would want to do here, most useful first. Do not click anything yet. ${RULES}`,
                              { startUrl: key, schema: ACTIONS_SCHEMA, maxSteps: 6, timeoutMs: this.readTimeoutMs });
    const actions = safeActions(r.parsed && r.parsed.actions).slice(0, 8);
    this.cache.set(key, actions);
    this.set({ status: "ready", actions, page: (r.parsed && r.parsed.page) || "", busySince: null, ...(r.parsed && r.parsed.url ? { url: r.parsed.url } : {}) });
  }

  // Do one chosen action; the same task reads the page it ends on.
  async run(index) {
    const a = this.state.actions[index];
    if (!a) return { ...this.state };
    const safe = { actions: this.state.actions, page: this.state.page };
    this.set({ status: "running", busySince: Date.now(), error: null, doing: a.label });
    try {
      const r = await this.task(`${a.instruction}. When that is done, stop and list the most useful things a person would want to do on the page you are on now, most useful first. ${RULES}`,
                                { schema: ACTIONS_SCHEMA, maxSteps: 20, timeoutMs: this.doTimeoutMs });
      const actions = safeActions(r.parsed && r.parsed.actions).slice(0, 8);
      this.set({ status: "ready", actions: actions.length ? actions : safe.actions, page: (r.parsed && r.parsed.page) || safe.page, busySince: null, doing: null, lastDone: a.label,
                 ...(r.parsed && r.parsed.url ? { url: r.parsed.url } : {}) });
      if (r.parsed && r.parsed.url) this.cache.set(r.parsed.url, this.state.actions);
    } catch (e) {
      // back to the last safe screen: the previous choices, with a plain message
      this.set({ status: "ready", ...safe, busySince: null, doing: null, error: `Couldn't ${a.label.toLowerCase()}: ${e.message || e}` });
    }
    return { ...this.state };
  }

  async close() {
    const sid = this.state.sessionId;
    this.set({ status: "idle", sessionId: null, liveUrl: null, actions: [], busySince: null, error: null });
    if (sid) await this.req("PATCH", `/sessions/${sid}`, { action: "stop" }).catch(() => {});
  }
}

module.exports = { BrowserUse, readKey, ACTIONS_SCHEMA, safeActions, CONSEQUENTIAL };
