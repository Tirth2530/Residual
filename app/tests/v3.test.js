// Run: npm test
const os = require("os"), fs = require("fs"), path = require("path");
const test = require("node:test");
const assert = require("node:assert");
const { SpeechManager, SingleFlight, once } = require("../ui/speech");
const { calibVerdict } = require("../ui/calib-view");
const { AgentClient } = require("../lib/agent-client");
const { Lifeline } = require("../lib/lifeline");

// a fake voice: each utterance finishes when the test says so
function fakeVoice() {
  const v = { said: [], cancels: 0, pending: [] };
  v.speak = text => new Promise(res => { v.said.push(text); v.pending.push(res); });
  v.cancel = () => { v.cancels++; const p = v.pending.splice(0); p.forEach(r => r()); };
  v.finish = async () => { const r = v.pending.shift(); r && r(); await new Promise(r => setTimeout(r, 0)); };
  return v;
}
const tick = () => new Promise(r => setTimeout(r, 0));

test("the welcome speaks once per launch, however often Home asks", () => {
  const w = once();
  assert.deepStrictEqual([w(), w(), w()], [true, false, false]);
});

test("the same line is never stacked up by repeated Selects", async () => {
  const v = fakeVoice(), m = new SpeechManager(v);
  m.say("Good morning everyone!", { priority: 2 });
  m.say("Good morning everyone!", { priority: 2 });
  m.say("Good morning everyone!", { priority: 2 });
  await v.finish(); await tick();
  assert.deepStrictEqual(v.said, ["Good morning everyone!"]);
});

test("an emergency interrupts news; ordinary speech queues", async () => {
  const v = fakeVoice(), m = new SpeechManager(v);
  const news = m.say("Here is the news", { priority: 3 });
  m.say("I need water", { priority: 2 });         // waits: doesn't cut the news off
  await tick();
  assert.deepStrictEqual(v.said, ["Here is the news"]);
  m.say("Dev needs help now", { priority: 0 });
  assert.strictEqual(await news, "interrupted");
  await tick();
  assert.strictEqual(v.said[1], "Dev needs help now");
  await v.finish(); await tick();
  assert.strictEqual(v.said[2], "I need water");   // the person's words still get said
});

test("ambient speech is dropped if anything else is speaking; navigation doesn't stop an alarm", async () => {
  const v = fakeVoice(), m = new SpeechManager(v);
  m.say("Calling Maya", { priority: 0 });
  assert.strictEqual(await m.say("Good morning, Dev.", { priority: 4 }), "dropped");
  m.stop(3);
  assert.ok(m.speaking);
  assert.strictEqual(v.cancels, 0);
});

test("briefing and routines can't start twice concurrently", async () => {
  const f = new SingleFlight();
  let runs = 0, release;
  const job = () => { runs++; return new Promise(r => { release = r; }); };
  const a = f.run("briefing", job);
  assert.strictEqual(f.run("briefing", job), null);
  assert.strictEqual(f.run("briefing", job), null);
  assert.ok(f.run("routine", async () => {}));     // other keys are independent
  await tick(); release(); await a;
  assert.strictEqual(runs, 1);
  const b = f.run("briefing", job); await tick(); release(); await b;
  assert.strictEqual(runs, 2);                     // after it finished, it can run again
});

test("failed eyebrow calibration shows Retry, never 'all set'", () => {
  const v = calibVerdict({ hand: { status: "VALID", count: 3 }, brow: { status: "FAILED", count: 0, reason: "no clear raise" },
                           blink: { status: "VALID", count: 3 } });
  assert.strictEqual(v.allOk, false);
  assert.doesNotMatch(v.title, /all set/i);
  assert.match(v.title, /hand pinch and long blink/);
  assert.deepStrictEqual(v.choices.map(c => c.label), ["Retry eyebrow", "Skip this movement"]);
  assert.strictEqual(v.rows.find(r => r.channel === "brow").mark, "✕ not clear enough");
  assert.strictEqual(v.rows.find(r => r.channel === "hand").mark, "✓ learned");
});

test("'all set' only when every movement is VALID (and gaze, if tried)", () => {
  const ok = { hand: { status: "VALID" }, brow: { status: "VALID" }, blink: { status: "VALID" } };
  assert.strictEqual(calibVerdict(ok).title, "You're all set.");
  assert.strictEqual(calibVerdict(ok, { calibrated: false, reason: "eyes not seen" }).allOk, false);
  assert.strictEqual(calibVerdict({ ...ok, blink: { status: "WEAK" } }).allOk, false);
  assert.strictEqual(calibVerdict({ hand: { status: "NOT_VISIBLE" }, brow: { status: "FAILED" }, blink: { status: "FAILED" } }).title,
                     "I couldn't learn a movement yet.");
});

test("agent client: unavailable agent is reported, not treated as success", async () => {
  const c = new AgentClient({ url: "http://x" }, { fetchFn: () => Promise.reject(new Error("ECONNREFUSED")) });
  const r = await c.intent({ intent: "request_caregiver_help", need: "repositioning" });
  assert.strictEqual(r.status, "unavailable");
  assert.strictEqual((await c.check()).ok, false);
});

test("agent client sends the structured intent and follows the task", async () => {
  const calls = [];
  const task = { task_id: "t1", kind: "help", status: "waiting", events: [] };
  const fetchFn = async (url, opts) => {
    calls.push([url, opts && opts.body]);
    if (url.endsWith("/intent")) return { json: async () => task };
    return { json: async () => ({ ...task, status: "acknowledged", events: [1], outcome: { contact: "Maya", message: "On my way" } }) };
  };
  const c = new AgentClient({ url: "http://agent" }, { fetchFn });
  const seen = new Promise(res => c.on("task", t => { if (t.status === "acknowledged") res(t); }));
  await c.intent({ intent: "request_caregiver_help", need: "repositioning", source: "residual_ui" });
  const t = await seen;
  assert.strictEqual(JSON.parse(calls[0][1]).need, "repositioning");
  assert.strictEqual(t.outcome.contact, "Maya");
  assert.strictEqual(c.watching.size, 0);
});

test("Lifeline incident codes are random UUIDs", () => {
  const a = new Lifeline({ contacts: [] }), b = new Lifeline({ contacts: [] });
  assert.match(a.incident, /^[0-9a-f-]{36}$/);
  assert.notStrictEqual(a.incident, b.incident);
});

// ---- AI web mode (Browser Use fallback), against a fake API
const { BrowserUse, readKey } = require("../lib/browser-use");
function fakeBU({ readOutput, runFails = false, never = false } = {}) {
  const calls = [];
  let n = 0;
  const fetchFn = async (url, opts) => {
    const path = url.replace("https://api.browser-use.com/api/v2", ""), body = opts.body ? JSON.parse(opts.body) : null;
    calls.push([opts.method, path, body]);
    const ok = d => ({ ok: true, status: 200, text: async () => JSON.stringify(d) });
    if (opts.method === "POST" && path === "/sessions") return ok({ id: "S1", liveUrl: "https://live.example/S1" });
    if (opts.method === "POST" && path === "/tasks") return ok({ id: "T" + (++n), sessionId: "S1" });
    if (opts.method === "GET" && path.startsWith("/tasks/")) {
      if (never) return ok({ status: "started" });
      const id = path.split("/")[2];
      if (id === "T2" && runFails) return ok({ status: "failed", output: "element not found" });
      return ok({ status: "finished", output: JSON.stringify(id === "T1" ? readOutput : { page: "Article", url: "https://w/a", actions: [{ label: "Next section", instruction: "scroll" }] }) });
    }
    return ok({});
  };
  return { calls, bu: new BrowserUse({ apiKey: "bu_test", fetchFn, pollMs: 5, readTimeoutMs: 300, doTimeoutMs: 300 }) };
}
const READ = { page: "Wikipedia", actions: [{ label: "Read today's featured article", instruction: "click the featured article" }, { label: "Random article", instruction: "click Random article" }] };

test("AI web mode: one session, one read task, actions cached; the key goes in the header", async () => {
  const { bu, calls } = fakeBU({ readOutput: READ });
  const st = await bu.open("https://en.wikipedia.org/");
  assert.strictEqual(st.status, "ready");
  assert.deepStrictEqual(st.actions.map(a => a.label), ["Read today's featured article", "Random article"]);
  assert.strictEqual(st.liveUrl, "https://live.example/S1");
  const tasks = calls.filter(c => c[0] === "POST" && c[1] === "/tasks");
  assert.strictEqual(tasks.length, 1);                         // not one call per gesture
  assert.strictEqual(tasks[0][2].sessionId, "S1");
  assert.ok(JSON.parse(tasks[0][2].structuredOutput).properties.actions);
  await bu.read("https://en.wikipedia.org/");                  // cached: no new task
  assert.strictEqual(calls.filter(c => c[0] === "POST" && c[1] === "/tasks").length, 1);
});

test("AI web mode: running a choice is one task that also returns the next page's actions", async () => {
  const { bu, calls } = fakeBU({ readOutput: READ });
  await bu.open("https://en.wikipedia.org/");
  const st = await bu.run(0);
  assert.deepStrictEqual(st.actions.map(a => a.label), ["Next section"]);
  assert.strictEqual(st.url, "https://w/a");
  assert.match(calls.filter(c => c[1] === "/tasks").pop()[2].task, /^click the featured article/);
});

test("AI web mode: a failed action goes back to the last safe choices with a plain message", async () => {
  const { bu } = fakeBU({ readOutput: READ, runFails: true });
  await bu.open("https://en.wikipedia.org/");
  const st = await bu.run(1);
  assert.strictEqual(st.status, "ready");
  assert.strictEqual(st.actions.length, 2);
  assert.match(st.error, /Couldn't random article/);
});

test("AI web mode: a slow read times out instead of trapping the person", async () => {
  const { bu } = fakeBU({ readOutput: READ, never: true });
  const st = await bu.open("https://slow.example/");
  assert.strictEqual(st.status, "error");
  assert.match(st.error, /took longer/);
});

test("AI web mode: no key, no calls; the key is read from app/.env", async () => {
  const bu = new BrowserUse({ apiKey: null, fetchFn: () => { throw new Error("should not call"); } });
  await assert.rejects(() => bu.open("https://x"), /needs a Browser Use key/);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bu-"));
  fs.writeFileSync(path.join(dir, ".env"), "OTHER=1\nBROWSER_USE_API_KEY=bu_abc123\n");
  const saved = process.env.BROWSER_USE_API_KEY; delete process.env.BROWSER_USE_API_KEY;
  assert.strictEqual(readKey(dir), "bu_abc123");
  if (saved) process.env.BROWSER_USE_API_KEY = saved;
});
