// Scrolling a real site the way a trackpad does.
//
// window.scrollBy only moves the window. Many sites scroll inside a container instead (YouTube Shorts, Instagram Reels and
// feeds), and some use smooth scrolling, so the position hadn't changed yet when it was checked. Both ended in "That's the
// end of the page" with nothing moved. A real wheel event over the middle of what is visible scrolls whatever is under that
// point, container or window, and triggers scroll-snap the way a person's hand would. Success is judged by whether anything
// under that point moved (the page helper's probe()), looked at twice, so smooth scrolling and snap points have time to settle.
//
// Chromium's wheel convention: a NEGATIVE deltaY scrolls DOWN.

const real = ms => new Promise(r => setTimeout(r, ms));

const WORDS = {
  down: { done: "Scrolled down", end: "That's the end of the page" },
  up: { done: "Scrolled up", end: "Already at the top" },
};

/**
 * wc:       the page's webContents (getZoomFactor, sendInputEvent)
 * pageCall: (method, ...args) -> the page helper's answer (probe, clearMarks)
 * inject:   puts the page helper into the page if it isn't there
 * fallback: () -> {cx, cy, vh} (CSS px) when the helper still isn't answering: the middle of the visible stage
 */
async function scrollPage({ wc, pageCall, dir = "down", inject, fallback, sleep = real, settleMs = 450 }) {
  const w = WORDS[dir === "up" ? "up" : "down"];
  const z = (wc.getZoomFactor && wc.getZoomFactor()) || 1;
  let p0 = await pageCall("probe");
  if (!p0 && inject) { try { await inject(); } catch (e) { /* page may be mid-navigation */ } p0 = await pageCall("probe"); }
  const spot = p0 || (fallback && fallback());
  if (!spot) return { done: "Couldn't scroll this page" };
  const amount = Math.round(Math.max(200, spot.vh * 0.8) * z);
  try {
    wc.sendInputEvent({ type: "mouseWheel", x: Math.round(spot.cx * z), y: Math.round(spot.cy * z), deltaX: 0,
                        deltaY: (dir === "up" ? 1 : -1) * amount, canScroll: true });
  } catch (e) { return { done: "Couldn't scroll this page" }; }
  if (!p0) { await pageCall("clearMarks"); return { done: w.done, scrolled: true }; }   // can't tell if it moved: don't claim "the end"
  await sleep(settleMs);
  let p1 = await pageCall("probe");
  if (p1 && p1.sig === p0.sig) { await sleep(settleMs + 50); p1 = await pageCall("probe"); }   // smooth scrolling, snap points
  await pageCall("clearMarks");
  return p1 && p1.sig !== p0.sig ? { done: w.done, scrolled: true } : { done: w.end, end: true };
}

module.exports = { scrollPage };
