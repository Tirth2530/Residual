// How readable is a control signal right now? Interaction reliability, not a medical measure.
//
// Every frame, the signal (eyebrow raise, or a wink) is compared with this person's own thresholds
// from Setup. An "attempt" is any rise above the person's "starting to move" level (off) held at least
// minMs. For each attempt we keep:
//   peak     the highest value
//   margin   (peak - command threshold) / (learned gesture level - command threshold)
//            1.0 = as strong as in Setup, 0 = only just reached the threshold, below 0 = didn't reach it
//   full     did it reach the command threshold (so the switch could fire)
//   durMs    how long it lasted
// From recent attempts it reports the evidence in plain words, and a verdict:
//   "weak"   most recent attempts missed or only just made it, or retries after near misses keep happening
//   "clear"  the last few attempts were full and comfortably above threshold
//   "ok"     neither
// It never changes anything itself: the caller asks the person.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.Quality = api;
})(typeof self !== "undefined" ? self : this, function () {
  const median = xs => { if (!xs.length) return 0; const s = [...xs].sort((a, b) => a - b), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };

  const DEFAULTS = {
    minMs: 200, maxMs: 2500,     // shorter is a flicker, longer is not a deliberate raise
    keepMs: 180000, keep: 12,    // attempts remembered
    weakOf: 5, weakCount: 4,     // weak if 4 of the last 5 attempts missed or only just made it
    nearMargin: 0.15,            // "only just made it"
    weakMedian: 0.1,
    retryMs: 4000,               // a miss followed this soon by another attempt = the person was trying
    retriesWeak: 3,              // this many inferred misses in the last 2 minutes = weak
    clearOf: 3, clearMargin: 0.35,
    faceWindowMs: 60000,
  };

  class SignalQuality {
    constructor(name, o = {}) {
      this.name = name;
      this.o = { ...DEFAULTS, ...o };
      this.attempts = [];
      this.cur = null;
      this.faceLog = [];   // [t, faceFound]
      this.lastT = 0;
    }
    reset() { this.attempts = []; this.cur = null; }
    // value: the signal now; th: { on, off } command / release thresholds; learned: the level Setup measured
    update(t, face, value, th, learned) {
      this.lastT = t;
      this.faceLog.push([t, !!face]);
      while (this.faceLog.length && t - this.faceLog[0][0] > this.o.faceWindowMs) this.faceLog.shift();
      if (!face || value == null) { this.cur = null; return null; }
      if (value > th.off) {
        if (!this.cur) this.cur = { t0: t, peak: value };
        this.cur.peak = Math.max(this.cur.peak, value);
        return null;
      }
      if (!this.cur) return null;
      const a = this.cur; this.cur = null;
      const durMs = t - a.t0;
      if (durMs < this.o.minMs || durMs > this.o.maxMs) return null;
      const margin = (a.peak - th.on) / Math.max(1e-3, learned - th.on);
      const prev = this.attempts[this.attempts.length - 1];
      const att = { t, peak: +a.peak.toFixed(3), margin: +margin.toFixed(2), full: a.peak >= th.on, durMs: Math.round(durMs),
                    retry: !!(prev && !prev.full && t - durMs - prev.t <= this.o.retryMs) };
      this.attempts.push(att);
      this.attempts = this.attempts.filter(x => t - x.t <= this.o.keepMs).slice(-this.o.keep);
      return att;
    }
    stats() {
      const A = this.attempts, o = this.o;
      const lastN = A.slice(-o.weakOf);
      const recent = A.filter(x => this.lastT - x.t <= 120000);
      const durs = A.filter(x => x.full).map(x => x.durMs);
      const mean = durs.length ? durs.reduce((a, b) => a + b, 0) / durs.length : 0;
      const sd = durs.length > 1 ? Math.sqrt(durs.reduce((a, b) => a + (b - mean) ** 2, 0) / (durs.length - 1)) : 0;
      const faceRate = this.faceLog.length ? this.faceLog.filter(x => x[1]).length / this.faceLog.length : 1;
      return {
        attempts: A.length,
        lastN: lastN.length,
        missed: lastN.filter(x => !x.full).length,
        nearMiss: lastN.filter(x => x.full && x.margin < o.nearMargin).length,
        medianMargin: +median(lastN.map(x => x.margin)).toFixed(2),
        inferredMisses: recent.filter(x => x.retry).length,
        timingCv: mean ? +(sd / mean).toFixed(2) : null,
        faceRate: +faceRate.toFixed(2),
      };
    }
    // { verdict, reasons[] }: the evidence, in words a person (or a judge in the Lab) can read
    assess() {
      const o = this.o, s = this.stats(), A = this.attempts, reasons = [];
      if (s.faceRate < 0.7) return { verdict: "ok", reasons: [`Face seen only ${Math.round(s.faceRate * 100)}% of the last minute, so I'm not judging the ${this.name} signal`], stats: s };
      if (s.lastN >= o.weakOf) {
        const weak = s.missed + s.nearMiss;
        if (weak >= o.weakCount) reasons.push(`${weak} of the last ${s.lastN} ${this.name} attempts ${s.missed ? `missed the threshold (${s.missed})` : ""}${s.missed && s.nearMiss ? " or " : ""}${s.nearMiss ? `only just cleared it (${s.nearMiss})` : ""}`);
        if (s.medianMargin < o.weakMedian) reasons.push(`typical strength is ${Math.round(s.medianMargin * 100)}% of what Setup learned above the threshold`);
      }
      if (s.inferredMisses >= o.retriesWeak) reasons.push(`${s.inferredMisses} times in 2 minutes a ${this.name} attempt fell short and was tried again`);
      if (reasons.length) return { verdict: "weak", reasons, stats: s };
      const lastC = A.slice(-o.clearOf);
      if (lastC.length === o.clearOf && lastC.every(x => x.full && x.margin >= o.clearMargin))
        return { verdict: "clear", reasons: [`the last ${o.clearOf} ${this.name} attempts were all at least ${Math.round(o.clearMargin * 100)}% of the learned strength`], stats: s };
      return { verdict: "ok", reasons: [], stats: s };
    }
  }

  // What to ask, given the control mode and the verdict. Pure, so the "never switch without asking" rule is tested.
  function suggestion(mode, verdict, declinedUntil, now, name = "eyebrow") {
    if (declinedUntil && now < declinedUntil) return null;
    if (mode === "two" && verdict === "weak") return { to: "one", text: `Your ${name} signal is getting harder to read. Switch to one-signal mode?` };
    if (mode === "one" && verdict === "clear") return { to: "two", text: `Your ${name} signal looks reliable again. Restore two-signal control?` };
    return null;
  }

  return { SignalQuality, suggestion, median, DEFAULTS };
});
