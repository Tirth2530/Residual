// Run: npm test
// Residual's screen and the agent team: the circle, errands (the Hands), and what the screen shows about them.
const test = require("node:test");
const assert = require("node:assert");
const { AgentClient } = require("../lib/agent-client");
const { circleRows, errandNeeds, safeLiveUrl } = require("../ui/swarm-view");

function recorder(responses = {}) {
  const calls = [];
  const fetchFn = async (url, opts = {}) => {
    const p = url.replace(/^http:\/\/[^/]+/, "");
    calls.push({ path: p, method: opts.method || "GET", body: opts.body ? JSON.parse(opts.body) : null });
    const r = responses[p] ?? {};
    return { json: async () => (typeof r === "function" ? r() : r) };
  };
  return { calls, fetchFn };
}

test("the circle, errands and consent go through the Relay's local API", async () => {
  const { calls, fetchFn } = recorder({ "/circle": { members: [{ member: "Maya" }], mode: "team" }, "/errands": [{ id: "e1", status: "preparing" }],
                                        "/errands/e1/consent": { status: "committing" } });
  const c = new AgentClient({ url: "http://agent" }, { fetchFn });
  assert.deepStrictEqual((await c.circle()).members[0].member, "Maya");
  assert.strictEqual((await c.errands())[0].id, "e1");
  await c.errandNew("Renew", "https://pharmacy.example.com/");
  assert.deepStrictEqual(calls.find(x => x.path === "/errands" && x.method === "POST").body, { goal: "Renew", url: "https://pharmacy.example.com/" });
  assert.strictEqual((await c.errandConsent("e1", true)).status, "committing");
  assert.deepStrictEqual(calls.find(x => x.path === "/errands/e1/consent").body, { approve: true });
  await c.errandCancel("e1");
  assert.ok(calls.some(x => x.path === "/errands/e1/cancel" && x.method === "POST"));
});

test("a Relay that isn't running means an empty circle and no errands, not a crash", async () => {
  const c = new AgentClient({ url: "http://x" }, { fetchFn: () => Promise.reject(new Error("ECONNREFUSED")) });
  assert.deepStrictEqual((await c.circle()).members, []);
  assert.deepStrictEqual(await c.errands(), []);
  assert.strictEqual((await c.errandConsent("e1", true)).status, "unavailable");
});

test("the errand watcher announces a change once", async () => {
  let list = [];
  const { fetchFn } = recorder({ "/errands": () => list });
  const c = new AgentClient({ url: "http://agent" }, { fetchFn });
  const seen = [];
  c.on("errands", l => seen.push(l.map(e => e.status).join(",")));
  c.watchErrands(10);
  await new Promise(r => setTimeout(r, 40));
  list = [{ id: "a", status: "preparing" }];
  await new Promise(r => setTimeout(r, 60));
  list = [{ id: "a", status: "needs_hands" }];
  await new Promise(r => setTimeout(r, 60));
  c.stopErrands();
  assert.deepStrictEqual(seen, ["preparing", "needs_hands"]);
});

test("the circle on screen: who was asked, who wasn't, and why", () => {
  const swarm = { mode: "circle", winner: "Raj", transport: { Maya: "agentverse", Raj: "agentverse" }, why: ["Raj: 6 min away", "Maya: in a meeting until 3:00 PM"],
                  bids: [{ member: "Maya", can_help: false, eta_min: 20, reason: "in a meeting until 3:00 PM" }, { member: "Raj", can_help: true, eta_min: 6, reason: "6 min away" }] };
  const rows = circleRows(swarm, ["Raj", "Maya"]);
  assert.deepStrictEqual(rows.map(r => r.name), ["Raj", "Maya"]);
  assert.strictEqual(rows[0].tag, "asked first");
  assert.strictEqual(rows[1].tag, "left alone");
  assert.match(rows[1].reason, /meeting/);
  assert.strictEqual(rows[0].via, "agent on Agentverse");
});

test("with no circle answer the screen says the care plan's order was used", () => {
  const rows = circleRows({ mode: "fallback", bids: [], why: [], transport: {} }, ["Maya", "Raj"]);
  assert.strictEqual(rows[0].tag, "care plan order");
  assert.deepStrictEqual(circleRows(null, ["Maya"]), []);
});

test("an errand that needs the person's yes is picked out, once", () => {
  const seen = new Set();
  const list = [{ id: "a", status: "awaiting_consent", final_action: "Submit refill" }, { id: "b", status: "preparing" }];
  assert.strictEqual(errandNeeds(list, seen).id, "a");
  seen.add("a");
  assert.strictEqual(errandNeeds(list, seen), null);
});

test("only Browser Use's own live link is ever opened", () => {
  assert.strictEqual(safeLiveUrl("https://live.browser-use.com/abc"), "https://live.browser-use.com/abc");
  assert.strictEqual(safeLiveUrl("https://evil.example.net/x"), null);
  assert.strictEqual(safeLiveUrl("http://live.browser-use.com/x"), null);
  assert.strictEqual(safeLiveUrl("javascript:alert(1)"), null);
  assert.strictEqual(safeLiveUrl(null), null);
});
