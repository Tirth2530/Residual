// Run: npm test
// Scrolling a real site the way a trackpad does: a real wheel event at the centre of what's visible, judged by whether
// anything under that point moved. (The old window.scrollBy did nothing on pages that scroll inside a container, like
// YouTube Shorts and Instagram Reels, and called smooth-scrolling pages "the end of the page".)
const test = require("node:test");
const assert = require("node:assert");
const { scrollPage } = require("../lib/scroll");

const noWait = () => Promise.resolve();

function rig({ sigs, zoom = 1, probeFails = false, vh = 600 }) {
  const wheels = [], calls = [];
  let i = 0;
  const wc = { getZoomFactor: () => zoom, sendInputEvent: e => wheels.push(e) };
  const pageCall = async (m, ...a) => {
    calls.push(m);
    if (m === "probe") return probeFails ? null : { cx: 500, cy: 300, vh, sig: sigs[Math.min(i++, sigs.length - 1)] };
    return true;
  };
  return { wc, pageCall, wheels, calls, args: { wc, pageCall, sleep: noWait, inject: async () => { calls.push("inject"); } } };
}

test("scrolling down sends one real wheel event (negative deltaY is down) at the centre of the visible page", async () => {
  const r = rig({ sigs: ["0,0", "0,480"] });
  const res = await scrollPage({ ...r.args, dir: "down" });
  assert.deepStrictEqual(res, { done: "Scrolled down", scrolled: true });
  assert.strictEqual(r.wheels.length, 1);
  const w = r.wheels[0];
  assert.strictEqual(w.type, "mouseWheel");
  assert.ok(w.deltaY < 0 && w.deltaX === 0 && w.canScroll === true);
  assert.deepStrictEqual([w.x, w.y], [500, 300]);
  assert.strictEqual(Math.abs(w.deltaY), 480);                       // 0.8 of the 600 px that is visible above the dock
});

test("scrolling up is a positive wheel delta", async () => {
  const r = rig({ sigs: ["0,480", "0,0"] });
  const res = await scrollPage({ ...r.args, dir: "up" });
  assert.deepStrictEqual(res, { done: "Scrolled up", scrolled: true });
  assert.ok(r.wheels[0].deltaY > 0);
});

test("a page that scrolls inside a container counts as scrolled when the container moved, not the window", async () => {
  const r = rig({ sigs: ["0,0|feed:0,0", "0,0|feed:801,0"] });        // the window never moves: the feed does (Shorts, Reels)
  assert.deepStrictEqual(await scrollPage({ ...r.args, dir: "down" }), { done: "Scrolled down", scrolled: true });
});

test("nothing moved after waiting: it really is the end, and the words say so", async () => {
  const down = await scrollPage({ ...rig({ sigs: ["0,900"] }).args, dir: "down" });
  assert.deepStrictEqual(down, { done: "That's the end of the page", end: true });
  const up = await scrollPage({ ...rig({ sigs: ["0,0"] }).args, dir: "up" });
  assert.deepStrictEqual(up, { done: "Already at the top", end: true });
});

test("smooth scrolling and snap points that settle late still count (it looks again before saying 'end')", async () => {
  const r = rig({ sigs: ["0,0", "0,0", "0,520"] });                   // first look: not yet; second look: moved
  const res = await scrollPage({ ...r.args, dir: "down" });
  assert.strictEqual(res.scrolled, true);
  assert.strictEqual(r.calls.filter(c => c === "probe").length, 3);
});

test("the wheel position and size follow the page's zoom (Residual's 'larger text' zooms the site)", async () => {
  const r = rig({ sigs: ["0,0", "0,1"], zoom: 1.5 });
  await scrollPage({ ...r.args, dir: "down" });
  assert.deepStrictEqual([r.wheels[0].x, r.wheels[0].y], [750, 450]);   // CSS px * zoom = view px
  assert.strictEqual(Math.abs(r.wheels[0].deltaY), 720);                // 480 * 1.5
});

test("a tiny viewport still scrolls a useful amount", async () => {
  const r = rig({ sigs: ["0,0", "0,1"], vh: 100 });
  await scrollPage({ ...r.args, dir: "down" });
  assert.ok(Math.abs(r.wheels[0].deltaY) >= 200);
});

test("the covered-button marks are cleared after a scroll, so stale choices aren't offered", async () => {
  const r = rig({ sigs: ["0,0", "0,480"] });
  await scrollPage({ ...r.args, dir: "down" });
  assert.ok(r.calls.includes("clearMarks"));
});

test("a page without Residual's helper gets it injected, and if it still won't answer a wheel is sent at the middle anyway", async () => {
  const r = rig({ sigs: [], probeFails: true });
  const res = await scrollPage({ ...r.args, dir: "down", fallback: () => ({ cx: 400, cy: 250, vh: 500 }) });
  assert.ok(r.calls.includes("inject"));
  assert.strictEqual(r.wheels.length, 1);
  assert.deepStrictEqual([r.wheels[0].x, r.wheels[0].y], [400, 250]);
  assert.ok(res.done && !res.end);                                      // it can't know if it moved, so it doesn't claim "the end"
});

test("a failing wheel is reported as a plain failure, not a crash", async () => {
  const r = rig({ sigs: ["0,0", "0,0"] });
  r.wc.sendInputEvent = () => { throw new Error("destroyed"); };
  const res = await scrollPage({ ...r.args, dir: "down" });
  assert.match(res.done, /couldn't scroll/i);
});
