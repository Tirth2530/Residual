// Run: node --test tests/
const test = require("node:test");
const assert = require("node:assert");
const { AI, ruleReplies, parseJSON } = require("../lib/ai");
const { Lifeline } = require("../lib/lifeline");

test("rule replies answer the last incoming message", () => {
  assert.deepStrictEqual(ruleReplies([{ me: false, text: "How are you feeling?" }])[0], "I'm doing okay today");
  assert.deepStrictEqual(ruleReplies([{ me: false, text: "Did you eat?" }]).slice(0, 2), ["Yes, I ate", "Not yet"]);
  assert.strictEqual(ruleReplies([]).length, 4);
});

test("parseJSON survives chatty model output", () => {
  assert.deepStrictEqual(parseJSON('Sure! {"replies":["a","b"]} hope this helps'), { replies: ["a", "b"] });
  assert.strictEqual(parseJSON("nope"), null);
});

test("AI falls back to rules when the model is down", async () => {
  const ai = new AI({ provider: "ollama" });
  ai.status = { ok: false };
  const r = await ai.replies({ contact: "Mom", messages: [{ me: false, text: "Did you sleep well?" }], person: "Dev" });
  assert.strictEqual(r.source, "rules");
  assert.strictEqual(r.list.length, 4);
  const s = await ai.searchIdeas({ app: "youtube", history: ["lakers"], hour: 22 });
  assert.strictEqual(s.list[0], "lakers");
  assert.strictEqual(s.list.length, 6);
});

test("AI uses model output when it is good", async () => {
  const ai = new AI({ provider: "ollama" });
  ai.status = { ok: true }; ai.model = "x";
  ai.json = async () => ({ replies: ["Yes I slept fine", "Slept okay, you?", "Not great", "Call me later?"] });
  const r = await ai.replies({ contact: "Mom", messages: [{ me: false, text: "Sleep well?" }], person: "Dev" });
  assert.strictEqual(r.source, "ai");
  assert.strictEqual(r.list[0], "Yes I slept fine");
});

function fakeClock() {
  let t = 1_000_000, id = 0; const iv = new Map();
  return {
    now: () => t,
    timers: { setInterval: (fn, ms) => { iv.set(++id, { fn, ms, next: t + ms }); return id; }, clearInterval: i => iv.delete(i) },
    async advance(ms) {
      const end = t + ms;
      while (true) {
        let nx = null; for (const [, v] of iv) if (v.next <= end && (!nx || v.next < nx.next)) nx = v;
        if (!nx) break;
        t = nx.next; nx.next += nx.ms; await nx.fn();
      }
      t = end;
    }
  };
}

test("Lifeline escalates contact by contact until someone answers", async () => {
  const sent = []; const clock = fakeClock(); let ack = false;
  const fetchFn = async (url, opts) => {
    if (opts && opts.method === "POST") { sent.push(JSON.parse(opts.body).topic); return { text: async () => "" }; }
    if (!ack) return { text: async () => "" };
    // a forged ack first (no incident code), then the real one from Raj's button
    return { text: async () => JSON.stringify({ event: "message", message: "Maya on my way" }) + "\n" +
                               JSON.stringify({ event: "message", message: `ack ${L.incident} Raj` }) + "\n" };
  };
  const L = new Lifeline({ person: "Dev", contacts: [{ name: "Maya", topic: "m" }, { name: "Raj", topic: "r" }], escalateSec: 60 },
                         { fetchFn, now: clock.now, timers: clock.timers });
  await L.start("needs help");
  assert.deepStrictEqual(sent, ["m"]);
  await clock.advance(61_000);
  assert.deepStrictEqual(sent, ["m", "r", "m"]);          // Raj now, Maya again
  ack = true;
  await clock.advance(4_000);
  assert.strictEqual(L.snapshot().ackBy, "Raj");
  const before = sent.length;
  await clock.advance(120_000);
  assert.strictEqual(sent.length, before);                // stops once answered
});

test("Lifeline works in simulation mode and can be cancelled", async () => {
  const clock = fakeClock();
  const L = new Lifeline({ contacts: [{ name: "Maya" }] }, { fetchFn: async () => ({}), now: clock.now, timers: clock.timers });
  const s = await L.start("is in pain");
  assert.strictEqual(s.simulated, true);
  assert.ok(s.log.some(l => /Simulated alert to Maya/.test(l.text)));
  L.cancel();
  assert.strictEqual(L.snapshot().active, false);
});
