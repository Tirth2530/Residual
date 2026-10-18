// The small pure pieces behind Home's greeting, the family faces, the signal chart and the confirmation screens.
// No DOM here, so they are tested (tests/homeview.test.js).
(function (root) {
  const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
  const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function greeting(date, name) {
    const h = (date || new Date()).getHours();
    const who = name ? `, ${name}` : "";
    if (h >= 5 && h < 12) return `Good morning${who}.`;
    if (h >= 12 && h < 17) return `Good afternoon${who}.`;
    if (h >= 17 && h < 22) return `Good evening${who}.`;
    return `It's late${who}.`;
  }

  // "Raj is 6 min away. Maya is in a meeting." from the care circle's answers.
  function circleLine(members) {
    const parts = (members || []).map(m => {
      if (!m || !m.member) return null;
      if (m.mode === "free") return m.eta_min ? `${m.member} is ${m.eta_min} min away` : `${m.member} is free`;
      return `${m.member} is ${m.label || m.mode || "away"}`;
    }).filter(Boolean);
    return parts.length ? parts.join(". ") + "." : "";
  }

  // The same person always gets the same colour: an initial on a warm or cool disc.
  const FACES = [{ bg: "#7DB8E8", ink: "#08202F" }, { bg: "#F4B63F", ink: "#2A1D05" }, { bg: "#8FC1A3", ink: "#0F2318" },
                 { bg: "#B3A8F5", ink: "#1B1640" }, { bg: "#F2A79D", ink: "#3A1410" }];
  function avatar(name) {
    const n = String(name || "").trim();
    let h = 0; for (const c of n) h = (h * 31 + c.codePointAt(0)) >>> 0;
    const f = FACES[h % FACES.length];
    return { ch: n ? n[0].toUpperCase() : "?", bg: f.bg, ink: f.ink };
  }

  // Bars for the last eight attempts of a signal. margin 0 = only just reached the command threshold, 1 = as strong as in Setup.
  const LOW = -0.5, SPAN = 1.5;
  function attemptBars(attempts, n = 8) {
    const last = (attempts || []).slice(-n);
    return { threshold: (0 - LOW) / SPAN * 100,
             bars: last.map(a => ({ h: clamp((a.margin - LOW) / SPAN * 100, 6, 100), ok: !!a.full })) };
  }
  function chartHTML(c) {
    return `<div class="ask-chart"><div class="line" style="bottom:${c.threshold.toFixed(1)}%"></div><div class="bars">` +
      c.bars.map(b => `<i class="bar ${b.ok ? "ok" : "low"}" style="height:${b.h.toFixed(1)}%"></i>`).join("") + "</div></div>";
  }

  // A confirmation screen: a short hero question, a kicker, and the words behind it. Callers can pass them; otherwise they're
  // taken from the title (its first sentence is the hero).
  function askParts(q) {
    const kicker = q.kicker || "Your decision";
    if (q.hero) return { kicker, hero: q.hero, body: q.body || "" };
    const t = String(q.title || "").trim();
    if (t.length <= 60) return { kicker, hero: t, body: "" };
    const m = t.match(/^(.+?[.?!])\s+([\s\S]*)$/);
    if (m && m[1].length <= 70) return { kicker, hero: m[1], body: m[2] };
    return { kicker, hero: t.slice(0, 66).trimEnd() + "…", body: t };
  }
  function heroSize(text) {
    const n = String(text || "").length;
    return n <= 16 ? 112 : n <= 26 ? 96 : n <= 40 ? 76 : n <= 60 ? 60 : 48;
  }

  // Text for a voice that takes at most `max` characters at a time: cut at sentence ends; a sentence that is itself too long is
  // cut at a space. Nothing is dropped.
  function chunkText(text, max = 380) {
    const t = String(text == null ? "" : text).replace(/\s+/g, " ").trim();
    if (!t) return [];
    const out = []; let cur = "";
    const push = () => { if (cur.trim()) out.push(cur.trim()); cur = ""; };
    for (const sent of t.match(/[^.!?]+[.!?]*\s*/g) || [t]) {
      if (sent.length > max) {                                   // one huge sentence: cut it at spaces
        push();
        let rest = sent.trim();
        while (rest.length > max) { let k = rest.lastIndexOf(" ", max); if (k < 1) k = max; out.push(rest.slice(0, k).trim()); rest = rest.slice(k).trim(); }
        cur = rest ? rest + " " : "";
        continue;
      }
      if ((cur + sent).trim().length > max) push();
      cur += sent;
    }
    push();
    return out;
  }

  const api = { greeting, circleLine, avatar, attemptBars, chartHTML, askParts, heroSize, chunkText, esc };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else Object.assign(root, { HomeView: api });
})(typeof window !== "undefined" ? window : globalThis);
