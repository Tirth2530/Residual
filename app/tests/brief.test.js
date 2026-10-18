// Run: npm test
const test = require("node:test");
const assert = require("node:assert");
const { SignalQuality, suggestion } = require("../ui/quality");
const Evaluate = require("../ui/evaluate");

// a person's eyebrow: rest 0.05, learned raise 0.6 -> on 0.35, off 0.215
const TH = { on: 0.35, off: 0.215 }, LEARNED = 0.6;
function raise(q, t0, peak, ms = 500) {
  let t = t0;
  for (; t < t0 + ms; t += 33) q.update(t, true, peak, TH, LEARNED);
  for (let k = 0; k < 10; k++, t += 33) q.update(t, true, 0.05, TH, LEARNED);
  return t + 1500;
}

test("strong raises are clear, never weak", () => {
  const q = new SignalQuality("eyebrow");
  let t = 0;
  for (let i = 0; i < 6; i++) t = raise(q, t, 0.58);
  assert.strictEqual(q.assess().verdict, "clear");
});

test("raises that only just clear the threshold are weak, with the reason in words", () => {
  const q = new SignalQuality("eyebrow");
  let t = 0;
  for (let i = 0; i < 5; i++) t = raise(q, t, 0.36);
  const a = q.assess();
  assert.strictEqual(a.verdict, "weak");
  assert.match(a.reasons.join(" "), /only just cleared it \(5\)/);
});

test("near misses that are tried again count as inferred misses", () => {
  const q = new SignalQuality("eyebrow");
  let t = 0;
  for (let i = 0; i < 3; i++) { t = raise(q, t, 0.28) - 1400; t = raise(q, t, 0.5); }
  const s = q.stats();
  assert.strictEqual(s.inferredMisses, 3);
  assert.strictEqual(q.assess().verdict, "weak");
});

test("a lost face is not judged (no false weakness from looking away)", () => {
  const q = new SignalQuality("eyebrow");
  let t = 0;
  for (let i = 0; i < 5; i++) t = raise(q, t, 0.3);
  for (let k = 0; k < 400; k++, t += 33) q.update(t, false, null, TH, LEARNED);
  assert.strictEqual(q.assess().verdict, "ok");
});

test("flickers and long holds are not attempts", () => {
  const q = new SignalQuality("eyebrow");
  raise(q, 0, 0.5, 100);
  raise(q, 5000, 0.5, 4000);
  assert.strictEqual(q.attempts.length, 0);
});

test("adaptation only ever suggests; declining silences it for a while", () => {
  assert.deepStrictEqual(suggestion("two", "weak", 0, 1000).to, "one");
  assert.match(suggestion("two", "weak", 0, 1000).text, /^Your eyebrow signal is getting harder to read\. Switch to one-signal mode\?$/);
  assert.strictEqual(suggestion("two", "weak", 5000, 1000), null);
  assert.strictEqual(suggestion("two", "clear", 0, 1000), null);
  assert.strictEqual(suggestion("one", "weak", 0, 1000), null);
  assert.match(suggestion("one", "clear", 0, 1000).text, /looks reliable again\. Restore two-signal control\?$/);
  assert.match(suggestion("two", "weak", 0, 1, "wink").text, /^Your wink signal/);
});

test("weak signal recovery: weak, then clear again", () => {
  const q = new SignalQuality("eyebrow");
  let t = 0;
  for (let i = 0; i < 5; i++) t = raise(q, t, 0.33);
  assert.strictEqual(q.assess().verdict, "weak");
  for (let i = 0; i < 12; i++) t = raise(q, t, 0.57);
  assert.strictEqual(q.assess().verdict, "clear");
});

test("Test my controls plan: 20 / 20 / 10, never three in a row", () => {
  const p = Evaluate.plan(["doubleBlink", "brow", "longBlink"]);
  assert.strictEqual(p.length, 50);
  assert.strictEqual(p.filter(x => x === "brow").length, 20);
  assert.strictEqual(p.filter(x => x === "longBlink").length, 10);
  assert.ok(!p.some((g, i) => i >= 2 && g === p[i - 1] && g === p[i - 2]));
});

test("Test my controls scoring: correct, missed, wrong, latency, false per minute", () => {
  const trials = [
    { want: "brow", got: "brow", ms: 400 }, { want: "brow", got: "brow", ms: 600 }, { want: "brow", got: null, ms: null },
    { want: "doubleBlink", got: "brow", ms: 300 }, { want: "doubleBlink", got: "doubleBlink", ms: 800 },
  ];
  const r = Evaluate.score(trials, [{ input: "brow", at: 1000 }, { input: "longBlink", at: 5000 }], 120000);
  assert.deepStrictEqual(r.perGesture.brow, { prompted: 3, correct: 2, missed: 1, wrong: 0, accuracy: 0.667, medianLatencyMs: 500, falseActivations: 1 });
  assert.strictEqual(r.perGesture.doubleBlink.wrong, 1);
  assert.strictEqual(r.perGesture.longBlink.falseActivations, 1);
  assert.strictEqual(r.falsePerMinute, 1);
});

test("Browser Use: consequential actions are never offered, whatever the model returns", () => {
  const { safeActions } = require("../lib/browser-use");
  const got = safeActions([
    { label: "Read the top story", instruction: "Click the first headline" },
    { label: "Buy now", instruction: "Click buy" },
    { label: "Open cart", instruction: "Add to cart and check out" },
    { label: "Sign in", instruction: "Log in with the saved account" },
    { label: "Share this", instruction: "Post it publicly to the feed" },
    { label: "Search recipes", instruction: "Use the site search for pasta" },
    { label: "Change password", instruction: "Open security settings" },
  ]);
  assert.deepStrictEqual(got.map(a => a.label), ["Read the top story", "Search recipes"]);
});

test("ElevenLabs voice is optional: no key or a failure means the system voice", async () => {
  const { ElevenTTS } = require("../lib/tts");
  assert.strictEqual(await new ElevenTTS({}).speak("hi"), null);
  let calls = 0;
  const ok = new ElevenTTS({ apiKey: "k", fetchFn: async () => { calls++; return { ok: true, arrayBuffer: async () => new Uint8Array([1, 2, 3]).buffer }; } });
  assert.deepStrictEqual([...(await ok.speak("I need water"))], [1, 2, 3]);
  await ok.speak("I need water");
  assert.strictEqual(calls, 1);   // cached
  const bad = new ElevenTTS({ apiKey: "k", fetchFn: async () => ({ ok: false, status: 401 }) });
  assert.strictEqual(await bad.speak("x"), null);
  assert.strictEqual(bad.failures, 1);
  const slow = new ElevenTTS({ apiKey: "k", timeoutMs: 50, fetchFn: (_u, o) => new Promise((_, rej) => o.signal.addEventListener("abort", () => rej(new Error("aborted")))) });
  assert.strictEqual(await slow.speak("x"), null);
});

test("preflight: PASS / WARN / FAIL, and optional services never fail the core demo", async () => {
  const { runChecks, centralFailed } = require("../lib/preflight");
  const routes = {
    "https://www.google.com/generate_204": { status: 204, ok: true },
    "https://api.browser-use.com/api/v2/tasks?pageSize=1": { status: 401, ok: false },
    "http://127.0.0.1:8100/health": { status: 200, ok: true, json: async () => ({ ok: true, agent: "Residual Relay", address: "agent1qabc", contacts: ["Maya"], photon: { mode: "simulated" }, calls: "simulated", asi_mode: "approve", asi_pending: 0 }) },
    "https://agentverse.ai/v1/almanac/agents/agent1qabc": { status: 404, ok: false },
  };
  const fetch = async url => { if (!routes[url]) throw new Error("offline"); return routes[url]; };
  const rows = await runChecks({ fetch, browserUseKey: "bu_x", renderer: { camera: "running", fps: 30, face: true, measured: true }, ai: { ok: false } });
  const lv = id => rows.find(r => r.id === id).level;
  assert.strictEqual(lv("camera"), "PASS");
  assert.strictEqual(lv("browseruse"), "FAIL");
  assert.strictEqual(lv("photon"), "WARN");          // simulated is flagged, not hidden
  assert.strictEqual(lv("asi"), "PASS");
  assert.strictEqual(lv("agentverse"), "FAIL");
  assert.strictEqual(lv("ollama"), "WARN");
  assert.strictEqual(centralFailed(rows), false);    // camera, face, controls and Relay are fine
  const down = await runChecks({ fetch: async () => { throw new Error("offline"); }, renderer: { camera: "running", face: true, measured: true } });
  assert.strictEqual(centralFailed(down), true);     // no Relay = core demo not ready
});

test("preflight: a Relay that won't take our token, and a live Photon with no signed webhook, are failures", async () => {
  const { runChecks, centralFailed } = require("../lib/preflight");
  const mk = health => async url => {
    if (url.endsWith("/health")) return { status: 200, ok: true, json: async () => health };
    if (url.startsWith("https://agentverse.ai")) return { status: 200, ok: true };
    return { status: 204, ok: true };
  };
  const base = { renderer: { camera: "running", face: true, measured: true }, ai: { ok: true, model: "m" } };
  const bare = await runChecks({ ...base, fetch: mk({ ok: true }) });
  assert.strictEqual(bare.find(r => r.id === "relay").level, "FAIL");
  assert.match(bare.find(r => r.id === "relay").detail, /token/);
  assert.strictEqual(centralFailed(bare), true);
  const live = { ok: true, agent: "R", address: "agent1q", contacts: ["Maya"], photon: { mode: "photon", platform: "imessage" }, calls: "off", asi_mode: "approve" };
  const unsigned = await runChecks({ ...base, fetch: mk({ ...live, webhook_signed: false }) });
  assert.strictEqual(unsigned.find(r => r.id === "photon").level, "FAIL");
  const signed = await runChecks({ ...base, fetch: mk({ ...live, webhook_signed: true }) });
  assert.strictEqual(signed.find(r => r.id === "photon").level, "PASS");
  // the token is sent when we have one
  let seen = null;
  await runChecks({ ...base, relayToken: "tok", fetch: async (url, o) => { if (url.endsWith("/health")) seen = o.headers; return { status: 204, ok: true, json: async () => ({}) }; } });
  assert.strictEqual(seen["X-Residual-Token"], "tok");
});
