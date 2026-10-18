// Run: npm test
// Pausing the music from anywhere. It must be judged by what the player actually does afterwards, not by "the click went through".
const test = require("node:test");
const assert = require("node:assert");
const { setMusic, musicLabel } = require("../lib/music");

const nap = async () => {};

// A fake player page. `obeys` says which of the ways to stop it work.
function player({ playing = true, obeys = ["button"], has = true, title = "Song" } = {}) {
  const calls = [];
  const call = async (method, how) => {
    calls.push(method + (how ? ":" + how : ""));
    if (method === "musicState") return has ? { has: true, playing, title } : { has: false };
    if (method === "musicToggle") { if (obeys.includes(how || "button")) playing = !playing; return { clicked: true }; }
    return null;
  };
  return { call, calls, get playing() { return playing; } };
}

test("pause: clicks the player's own button and reports Paused", async () => {
  const p = player({ playing: true });
  const r = await setMusic({ call: p.call, want: "toggle", sleep: nap });
  assert.strictEqual(p.playing, false);
  assert.strictEqual(r.playing, false);
  assert.match(r.done, /paused/i);
  assert.deepStrictEqual(p.calls.filter(c => c.startsWith("musicToggle")), ["musicToggle:button"]);
});

test("play when paused", async () => {
  const p = player({ playing: false });
  const r = await setMusic({ call: p.call, want: "toggle", sleep: nap });
  assert.strictEqual(p.playing, true);
  assert.match(r.done, /playing/i);
});

test("if the button doesn't stop it, the media element is told directly", async () => {
  const p = player({ playing: true, obeys: ["media"] });
  const r = await setMusic({ call: p.call, want: "toggle", sleep: nap });
  assert.strictEqual(p.playing, false);
  assert.match(r.done, /paused/i);
  assert.deepStrictEqual(p.calls.filter(c => c.startsWith("musicToggle")), ["musicToggle:button", "musicToggle:media"]);
});

test("if nothing stops it, it says so and never claims Paused", async () => {
  const p = player({ playing: true, obeys: [] });
  const r = await setMusic({ call: p.call, want: "pause", sleep: nap });
  assert.strictEqual(r.failed, true);
  assert.doesNotMatch(r.done, /^Paused/);
  assert.strictEqual(r.playing, true);
});

test("asking for pause when already paused changes nothing", async () => {
  const p = player({ playing: false });
  const r = await setMusic({ call: p.call, want: "pause", sleep: nap });
  assert.strictEqual(p.playing, false);
  assert.deepStrictEqual(p.calls.filter(c => c.startsWith("musicToggle")), []);
  assert.strictEqual(r.playing, false);
});

test("no music open: plain answer, nothing clicked", async () => {
  const p = player({ has: false });
  const r = await setMusic({ call: p.call, want: "toggle", sleep: nap });
  assert.match(r.done, /no music/i);
  assert.strictEqual(r.none, true);
  assert.deepStrictEqual(p.calls, ["musicState"]);
});

test("a page that doesn't answer is 'no music', not a crash", async () => {
  const r = await setMusic({ call: async () => null, want: "toggle", sleep: nap });
  assert.strictEqual(r.none, true);
});

test("the Home label follows what is playing", () => {
  assert.strictEqual(musicLabel(null), null);
  assert.strictEqual(musicLabel({ has: false }), null);
  assert.match(musicLabel({ has: true, playing: true, title: "Autumn Leaves" }), /^Pause music/);
  assert.match(musicLabel({ has: true, playing: false, title: "Autumn Leaves" }), /^Play music/);
});
