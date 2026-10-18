// Lifeline: help that doesn't stop until a human answers.
//
// 1. Alert the first contact's phone (ntfy push, urgent, with an "I'm on my way" button).
// 2. Every `escalateSec` with no answer, alert the next contact too, and re-alert everyone so far.
// 3. Stops only when someone taps "I'm on my way" on their phone, or the person cancels with a
//    deliberate double confirm in the app.
//
// Runs in Electron's main process, not the page UI, so a frozen screen doesn't stop it, and the
// app refuses to quit while a call for help is unanswered (see main.js).
//
// Acknowledgements carry a one-time incident code that only the alert's own button knows, so a
// stray or malicious post to the ack topic ("Maya: on my way") can't silence a real emergency.
// With no phones configured it runs in simulation mode (logged, visible on screen), so it can be demoed.

const { EventEmitter } = require("events");
const crypto = require("crypto");

class Lifeline extends EventEmitter {
  constructor(cfg = {}, { fetchFn = globalThis.fetch, now = () => Date.now(), timers = globalThis } = {}) {
    super();
    this.cfg = { person: "Dev", contacts: [], ackTopic: "", escalateSec: 60, pollSec: 3, server: "https://ntfy.sh", ...cfg };
    this.fetch = fetchFn; this.now = now; this.timers = timers;
    this.simulated = !this.cfg.contacts.some(c => c.topic);
    this.ackTopic = this.cfg.ackTopic || (this.cfg.contacts.find(c => c.topic)?.topic || "residual") + "-ack";
    this.reset();
  }

  reset() {
    this.incident = crypto.randomUUID();   // unguessable: it is what makes an acknowledgement genuine
    this.state = { active: false, reason: "", startedAt: 0, reached: [], nextAt: 0, ackBy: null, ackAt: 0, log: [], simulated: this.simulated };
    this._clear();
  }
  get busy() { return this.state.active && !this.state.ackBy; }
  _clear() {
    if (this._esc) this.timers.clearInterval(this._esc);
    if (this._poll) this.timers.clearInterval(this._poll);
    this._esc = this._poll = null;
  }
  _log(text) { this.state.log.unshift({ t: this.now(), text }); this.state.log = this.state.log.slice(0, 30); this._emit(); }
  _emit() { this.emit("state", this.snapshot()); }
  snapshot() {
    const s = this.state, contacts = this.cfg.contacts;
    const nextContact = contacts[s.reached.length] || null;
    return { ...s, log: s.log.slice(0, 8), contacts: contacts.map(c => c.name),
             nextContact: nextContact && nextContact.name,
             secondsToNext: s.active && !s.ackBy ? Math.max(0, Math.round((s.nextAt - this.now()) / 1000)) : null };
  }

  async start(reason = "needs help") {
    if (this.state.active && !this.state.ackBy) { this._log("Already calling for help"); return this.snapshot(); }
    this.reset();
    Object.assign(this.state, { active: true, reason, startedAt: this.now() });
    this._since = Math.floor(this.now() / 1000) - 1;
    this._log(`${this.cfg.person} asked for help: ${reason}`);
    await this._escalate();
    this._esc = this.timers.setInterval(() => this._tick(), 1000);
    if (!this.simulated) this._poll = this.timers.setInterval(() => this._checkAck(), this.cfg.pollSec * 1000);
    return this.snapshot();
  }

  async _tick() {
    const s = this.state;
    if (!s.active || s.ackBy) return;
    if (this.now() >= s.nextAt) await this._escalate();
    else this._emit(); // countdown
  }

  // Reach the next contact (or everyone again when all have been tried), then wait.
  async _escalate() {
    const s = this.state, contacts = this.cfg.contacts;
    if (!contacts.length) { this._log("No contacts set up in config.json"); s.nextAt = this.now() + this.cfg.escalateSec * 1000; return; }
    const i = s.reached.length < contacts.length ? s.reached.length : null;
    const targets = i === null ? contacts : [contacts[i]];
    if (i !== null) s.reached.push(contacts[i].name);
    const round = i === null ? " (again)" : "";
    for (const c of targets) await this._send(c, round);
    if (i !== null && i > 0) for (const c of contacts.slice(0, i)) await this._send(c, " (still no answer)");
    s.nextAt = this.now() + this.cfg.escalateSec * 1000;
    this._emit();
  }

  async _send(contact, note) {
    const msg = `${this.cfg.person} ${this.state.reason}${note}. Tap "I'm on my way" if you're going.`;
    if (this.simulated || !contact.topic) { this._log(`Simulated alert to ${contact.name}`); return; }
    try {
      await this.fetch(this.cfg.server, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          topic: contact.topic, title: `${this.cfg.person} needs help`, message: msg, priority: 5,
          tags: ["rotating_light"],
          actions: [{ action: "http", label: "I'm on my way", url: `${this.cfg.server}/${this.ackTopic}`,
                      method: "POST", body: `ack ${this.incident} ${contact.name}`, clear: true }]
        })
      });
      this._log(`Alerted ${contact.name}`);
    } catch (e) {
      this._log(`Couldn't reach ${contact.name} (no internet?). Retrying.`);
    }
  }

  async _checkAck() {
    if (!this.state.active || this.state.ackBy) return;
    try {
      const r = await this.fetch(`${this.cfg.server}/${this.ackTopic}/json?poll=1&since=${this._since}`);
      const text = await r.text();
      for (const line of text.split("\n")) {
        if (!line.trim()) continue;
        const m = JSON.parse(line);
        if (m.event !== "message") continue;
        const parts = String(m.message || "").trim().split(/\s+/);
        if (parts[0] === "ack" && parts[1] === this.incident) { this.acknowledge(parts.slice(2).join(" ") || "Someone"); break; }
        if (!this._warned) { this._warned = true; this._log("Ignored an acknowledgement that doesn't match this alert"); }
      }
    } catch (e) { /* keep polling */ }
  }

  acknowledge(name) {
    if (!this.state.active || this.state.ackBy) return;
    this.state.ackBy = name; this.state.ackAt = this.now();
    if (this._esc) this.timers.clearInterval(this._esc);
    if (this._poll) this.timers.clearInterval(this._poll);
    this._esc = this._poll = null;
    this._log(`${name} is on the way`);
  }

  cancel(by = "the person") {
    if (!this.state.active) return this.snapshot();
    this._clear();
    this.state.active = false;
    this._log(`Cancelled by ${by}`);
    if (!this.simulated) {
      for (const c of this.cfg.contacts.filter(c => this.state.reached.includes(c.name) && c.topic)) {
        this.fetch(this.cfg.server, { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ topic: c.topic, title: `${this.cfg.person} is okay`, message: "False alarm, all good now.", priority: 3 }) }).catch(() => {});
      }
    }
    return this.snapshot();
  }
}

module.exports = { Lifeline };
