// Test my controls: scoring. Pure, so the numbers in a write-up come from tested code.
//
// trials:   [{ want: "doubleBlink", got: "doubleBlink" | "brow" | null, ms }]   one per prompt
// natural:  [{ input: "brow", at }]   every gesture detected during the natural-behaviour period
// naturalMs how long that period was
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.Evaluate = api;
})(typeof self !== "undefined" ? self : this, function () {
  const median = xs => { if (!xs.length) return null; const s = [...xs].sort((a, b) => a - b), m = s.length >> 1; return s.length % 2 ? s[m] : Math.round((s[m - 1] + s[m]) / 2); };

  // How many prompts per gesture (the brief: 20 / 20 / 10, fallbacks 10)
  const PLAN = { doubleBlink: 20, brow: 20, winkLeft: 20, winkRight: 20, longBlink: 10, lookLeft: 10, lookRight: 10 };
  const triple = p => p.some((g, i) => i >= 2 && g === p[i - 1] && g === p[i - 2]);
  function plan(gestures, counts = PLAN, rnd = Math.random) {
    let p = draft(gestures, counts, rnd);
    for (let k = 0; k < 200 && triple(p); k++) p = draft(gestures, counts, rnd);
    return p;
  }
  function draft(gestures, counts, rnd) {
    // random order, but never the same gesture three times in a row (people anticipate): pick each next
    // prompt at random, weighted by how many of each are left, skipping one that would make a third in a row
    const left = {};
    for (const g of gestures) left[g] = counts[g] || 10;
    const out = [];
    for (;;) {
      const banned = out.length >= 2 && out[out.length - 1] === out[out.length - 2] ? out[out.length - 1] : null;
      let pool = gestures.filter(g => left[g] > 0 && g !== banned);
      if (!pool.length) pool = gestures.filter(g => left[g] > 0);
      if (!pool.length) return out;
      const total = pool.reduce((a, g) => a + left[g], 0);
      let r = rnd() * total, pick = pool[pool.length - 1];
      for (const g of pool) { r -= left[g]; if (r < 0) { pick = g; break; } }
      out.push(pick); left[pick]--;
    }
  }

  function score(trials, natural, naturalMs) {
    const gestures = [...new Set(trials.map(t => t.want))];
    const per = {};
    for (const g of gestures) {
      const ts = trials.filter(t => t.want === g);
      const hit = ts.filter(t => t.got === g);
      per[g] = {
        prompted: ts.length,
        correct: hit.length,
        missed: ts.filter(t => !t.got).length,
        wrong: ts.filter(t => t.got && t.got !== g).length,
        accuracy: ts.length ? +(hit.length / ts.length).toFixed(3) : null,
        medianLatencyMs: median(hit.map(t => t.ms)),
        falseActivations: natural.filter(n => n.input === g).length,
      };
    }
    // a gesture that was never prompted can still fire by accident
    for (const n of natural) if (!per[n.input]) per[n.input] = { prompted: 0, correct: 0, missed: 0, wrong: 0, accuracy: null, medianLatencyMs: null, falseActivations: natural.filter(x => x.input === n.input).length };
    const minutes = naturalMs / 60000;
    return {
      perGesture: per,
      prompted: trials.length,
      correct: trials.filter(t => t.got === t.want).length,
      naturalSeconds: Math.round(naturalMs / 1000),
      falseCommands: natural.length,
      falsePerMinute: minutes > 0 ? +(natural.length / minutes).toFixed(2) : null,
    };
  }
  return { plan, score, PLAN, median };
});
