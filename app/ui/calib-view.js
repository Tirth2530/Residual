// What the calibration result sheet says, as a pure function so it can be tested: it never says
// "all set" unless every movement tried was learned (VALID), and offers Retry for each one that wasn't.
(function (root) {
  const NAME = { hand: "Hand pinch", brow: "Eyebrow raise", blink: "Long blink" };
  const SHORT = { hand: "hand pinch", brow: "eyebrow", blink: "blink" };
  const CH = ["hand", "brow", "blink"];
  const usable = r => r && (r.status === "VALID" || r.status === "WEAK");
  function calibVerdict(results = {}, gaze = null) {
    const tried = CH.filter(c => results[c]);
    const bad = tried.filter(c => !usable(results[c]));
    const weak = tried.filter(c => results[c].status === "WEAK");
    const gazeBad = !!(gaze && gaze.calibrated === false);
    const allOk = tried.length > 0 && !bad.length && !weak.length && !gazeBad;
    const rows = tried.map(c => {
      const r = results[c];
      const mark = r.status === "VALID" ? "✓ learned" : r.status === "WEAK" ? "✓ learned, but faint"
        : r.status === "NOT_VISIBLE" ? "✕ couldn't see it" : r.skipped ? "✕ skipped" : "✕ not clear enough";
      return { channel: c, name: NAME[c], ok: usable(r), mark, reason: r.status !== "VALID" ? r.reason || "" : "" };
    });
    if (gaze) rows.push({ channel: "gaze", name: "Looking left and right", ok: !!gaze.calibrated,
                          mark: gaze.calibrated ? "✓ learned" : "✕ not clear enough", reason: gaze.calibrated ? "" : gaze.reason || "" });
    const choices = [...bad, ...weak].map(c => ({ label: `Retry ${SHORT[c]}`, retry: c }));
    if (gazeBad) choices.push({ label: "Retry looking left and right", retry: "gaze" });
    if (choices.length) choices.push({ label: bad.length || gazeBad ? "Skip this movement" : "Keep it", close: true });
    else choices.push({ label: "Okay", close: true });
    const names = tried.filter(c => usable(results[c])).map(c => NAME[c].toLowerCase());
    const title = allOk ? "You're all set." : names.length ? `I learned ${names.join(" and ")}.` : "I couldn't learn a movement yet.";
    const sub = allOk ? "" : "Movements that weren't clear won't be used for anything until they're learned." + (names.length ? "" : " Keys still work.");
    return { allOk, title, sub, rows, choices };
  }
  const api = { calibVerdict };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else Object.assign(root, api);
})(typeof window !== "undefined" ? window : globalThis);
