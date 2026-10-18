// Run: npm test
// Residual's screen talks to the Relay with a token, and shows Dev the requests strangers on ASI:One have made.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs"), os = require("os"), path = require("path");
const { AgentClient } = require("../lib/agent-client");

const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), "residual-token-"));

function recorder(responses = {}) {
  const calls = [];
  const fetchFn = async (url, opts = {}) => {
    const p = url.replace(/^http:\/\/[^/]+/, "");
    calls.push({ path: p, method: opts.method || "GET", headers: opts.headers || {}, body: opts.body ? JSON.parse(opts.body) : null });
    const r = responses[p] ?? {};
    return { json: async () => (typeof r === "function" ? r() : r) };
  };
  return { calls, fetchFn };
}

test("every request carries the token from the file the Relay wrote", async () => {
  const dir = tmp(), file = path.join(dir, "local_token");
  const { calls, fetchFn } = recorder({ "/tasks": [] });
  const c = new AgentClient({ url: "http://agent", tokenFile: file }, { fetchFn });
  await c.tasks();
  assert.strictEqual(calls[0].headers["X-Residual-Token"], undefined);          // no file yet: no header, nothing crashes
  fs.writeFileSync(file, "abc123token\n");                                       // the Relay starts after the app: picked up
  await c.tasks();
  assert.strictEqual(calls[1].headers["X-Residual-Token"], "abc123token");
});

test("RESIDUAL_LOCAL_TOKEN wins over the file", async () => {
  const file = path.join(tmp(), "local_token");
  fs.writeFileSync(file, "from-file");
  process.env.RESIDUAL_LOCAL_TOKEN = "from-env";
  try {
    const { calls, fetchFn } = recorder({ "/tasks": [] });
    await new AgentClient({ url: "http://agent", tokenFile: file }, { fetchFn }).tasks();
    assert.strictEqual(calls[0].headers["X-Residual-Token"], "from-env");
  } finally { delete process.env.RESIDUAL_LOCAL_TOKEN; }
});

test("a Relay that doesn't recognise the token is reported plainly, not as 'running fine'", async () => {
  const { fetchFn } = recorder({ "/health": { ok: true } });                      // a bare /health: the token wasn't accepted
  const c = new AgentClient({ url: "http://agent" }, { fetchFn });
  const h = await c.check();
  assert.strictEqual(h.ok, true);
  assert.strictEqual(h.auth, false);
  assert.match(h.warning, /token/i);
  const { fetchFn: ok } = recorder({ "/health": { ok: true, contacts: ["Maya"], photon: { mode: "simulated" } } });
  const h2 = await new AgentClient({ url: "http://agent" }, { fetchFn: ok }).check();
  assert.strictEqual(h2.auth, true);
});

test("pending ASI requests are listed and decided through the Relay", async () => {
  const list = [{ id: "p1", summary: "message Maya: “dinner”", kind: "message", expires: 1 }];
  const { calls, fetchFn } = recorder({ "/asi/pending": list, "/asi/pending/p1/decide": { status: "approved" } });
  const c = new AgentClient({ url: "http://agent" }, { fetchFn });
  assert.deepStrictEqual(await c.asiPending(), list);
  assert.deepStrictEqual(await c.asiDecide("p1", true), { status: "approved" });
  const d = calls.find(x => x.path.endsWith("/decide"));
  assert.strictEqual(d.method, "POST");
  assert.deepStrictEqual(d.body, { approve: true });
  await c.asiDecide("p1", false);
  assert.deepStrictEqual(calls.filter(x => x.path.endsWith("/decide")).pop().body, { approve: false });
});

test("a Relay that isn't running means no pending requests, not a crash", async () => {
  const c = new AgentClient({ url: "http://x" }, { fetchFn: () => Promise.reject(new Error("ECONNREFUSED")) });
  assert.deepStrictEqual(await c.asiPending(), []);
  const r = await c.asiDecide("p1", true);
  assert.strictEqual(r.status, "unavailable");
});

test("the watcher announces a request once, again only when the list changes", async () => {
  let list = [];
  const { fetchFn } = recorder({ "/asi/pending": () => list });
  const c = new AgentClient({ url: "http://agent" }, { fetchFn });
  const seen = [];
  c.on("asi", l => seen.push(l.map(x => x.id).join(",")));
  c.watchAsi(10);
  await new Promise(r => setTimeout(r, 40));
  assert.deepStrictEqual(seen, []);                                               // empty and unchanged: silent
  list = [{ id: "a" }];
  await new Promise(r => setTimeout(r, 60));
  list = [{ id: "a" }, { id: "b" }];
  await new Promise(r => setTimeout(r, 60));
  list = [];
  await new Promise(r => setTimeout(r, 60));
  c.stopAsi();
  assert.deepStrictEqual(seen, ["a", "a,b", ""]);
});
