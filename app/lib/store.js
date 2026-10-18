// Small local memory: what was watched, searched and who was messaged, so Home can
// offer "continue" cards and search can suggest instead of asking someone to type.
// One JSON file in the app's data folder. Nothing leaves the laptop.

const fs = require("fs");
const path = require("path");

const EMPTY = { watched: [], searches: {}, chats: [], lastUrl: {}, typed: [], usage: [], care: [], briefed: 0, voiceId: "" };

class Store {
  constructor(dir) {
    this.file = path.join(dir, "residual-home.json");
    try { this.d = { ...EMPTY, ...JSON.parse(fs.readFileSync(this.file, "utf8")) }; }
    catch (e) { this.d = JSON.parse(JSON.stringify(EMPTY)); }
    this._t = null;
  }
  save() {
    clearTimeout(this._t);
    this._t = setTimeout(() => {
      try { fs.mkdirSync(path.dirname(this.file), { recursive: true }); fs.writeFileSync(this.file, JSON.stringify(this.d, null, 1)); } catch (e) {}
    }, 300);
  }
  watched(app, title, url) {
    if (!title || !url) return;
    this.d.watched = [{ app, title, url, t: Date.now() }, ...this.d.watched.filter(w => w.url !== url)].slice(0, 40);
    this.save();
  }
  searched(app, q) {
    const list = this.d.searches[app] || [];
    this.d.searches[app] = [q, ...list.filter(x => x.toLowerCase() !== q.toLowerCase())].slice(0, 20);
    this.save();
  }
  chatted(name) {
    if (!name) return;
    this.d.chats = [{ name, t: Date.now() }, ...this.d.chats.filter(c => c.name !== name)].slice(0, 20);
    this.save();
  }
  typed(text) {
    if (!text) return;
    this.d.typed = [text, ...this.d.typed.filter(x => x !== text)].slice(0, 50);
    this.save();
  }
  // what was chosen and when: feeds the "Suggested for now" model
  used(id) { if (!id) return; this.d.usage.push({ id, t: Date.now() }); this.d.usage = this.d.usage.slice(-3000); this.save(); }
  // care log: control changes, reliability samples, fatigue, help calls. For the care report.
  care(kind, data) { this.d.care.push({ t: Date.now(), kind, ...data }); this.d.care = this.d.care.slice(-8000); this.save(); }
  careReport(days = 14) {
    const since = Date.now() - days * 864e5, out = {};
    for (const e of this.d.care) {
      if (e.t < since) continue;
      const day = new Date(e.t).toISOString().slice(0, 10);
      const d = out[day] || (out[day] = { day, migrations: [], rel: {}, tired: 0, help: 0, minutes: 0, selections: 0 });
      if (e.kind === "migrate") d.migrations.push({ t: e.t, from: e.from, to: e.to, why: e.why });
      else if (e.kind === "rel") for (const [c, v] of Object.entries(e.rel || {})) (d.rel[c] = d.rel[c] || []).push(v);
      else if (e.kind === "tired") d.tired++;
      else if (e.kind === "help") d.help++;
      else if (e.kind === "session") { d.minutes += e.minutes || 0; d.selections += e.selections || 0; }
    }
    return Object.values(out).sort((a, b) => a.day.localeCompare(b.day)).map(d => ({
      ...d, rel: Object.fromEntries(Object.entries(d.rel).map(([c, v]) => [c, Math.round(v.reduce((a, b) => a + b, 0) / v.length)]))
    }));
  }
  lastUrl(app, url) { this.d.lastUrl[app] = url; this.save(); }
  history(app) {
    const s = this.d.searches[app] || [];
    const w = this.d.watched.filter(x => x.app === app).map(x => x.title);
    return [...new Set([...s, ...w])];
  }
}

module.exports = { Store };
