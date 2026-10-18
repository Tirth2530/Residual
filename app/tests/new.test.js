// Run: npm test
const test = require("node:test");
const assert = require("node:assert");
const { AI, pickModel, parseJSON, extractive } = require("../lib/ai");
const { parseFeed, extractText, News } = require("../lib/news");
const { rank } = require("../lib/intents");
const { parseWeather } = require("../lib/weather");
const { Store } = require("../lib/store");
const os = require("os"), fs = require("fs"), path = require("path");

test("model picker prefers Qwen, skips embedding models, honours an explicit choice", () => {
  assert.strictEqual(pickModel(["nomic-embed-text:latest", "llama3.2:3b", "qwen2.5:3b"], "auto"), "qwen2.5:3b");
  assert.strictEqual(pickModel(["qwen3:4b", "qwen2.5:3b"], "auto"), "qwen3:4b");
  assert.strictEqual(pickModel(["nomic-embed-text:latest", "mistral:7b"], "auto"), "mistral:7b");
  assert.strictEqual(pickModel(["llama3.2:3b", "qwen2.5:3b"], "llama3.2:3b"), "llama3.2:3b");
  assert.strictEqual(pickModel([], "auto"), null);
});

test("parseJSON strips a reasoning trace before the JSON", () => {
  assert.deepStrictEqual(parseJSON('<think>hmm</think>\n{"answer":"hi","next":["a"]}'), { answer: "hi", next: ["a"] });
});

test("Ask falls back honestly when the model is off", async () => {
  const ai = new AI({ provider: "ollama" }); ai.status = { ok: false };
  const r = await ai.ask({ person: "Dev", history: [{ role: "person", text: "Hi" }], context: "" });
  assert.strictEqual(r.source, "rules");
  assert.match(r.answer, /isn't running/);
  assert.ok(r.next.length >= 3);
});

test("Ask uses model output and keeps follow-ups short", async () => {
  const ai = new AI({ provider: "ollama" }); ai.status = { ok: true }; ai.model = "x";
  ai.json = async () => ({ answer: "Europa is far away.", next: ["Could anything live there?", "x".repeat(200), "Cool", "Mars instead"] });
  const r = await ai.ask({ person: "Dev", history: [{ role: "person", text: "Europa?" }], context: "" });
  assert.strictEqual(r.source, "ai");
  assert.strictEqual(r.next.length, 3);            // the 200-char one is dropped
});

test("Every model call is logged for the Lab, including failures", async () => {
  const ai = new AI({ provider: "ollama" }, { fetchFn: async () => { throw new Error("ECONNREFUSED"); } });
  ai.status = { ok: true, provider: "ollama" }; ai.model = "qwen3:4b";
  const seen = []; ai.on("call", c => seen.push(c));
  const r = await ai.replies({ contact: "Maya", messages: [{ me: false, text: "Hungry?" }], person: "Dev" });
  assert.strictEqual(r.source, "rules");
  assert.strictEqual(seen.length, 1);
  assert.strictEqual(seen[0].task, "replies");
  assert.strictEqual(seen[0].ok, false);
});

test("briefing falls back to a spoken template with the real facts", async () => {
  const ai = new AI({ provider: "off" }); ai.status = { ok: false };
  const r = await ai.briefing({ person: "Dev", now: Date.parse("2026-10-17T08:00:00"), weather: { place: "San Jose", temp: 64, unit: "°F", desc: "partly cloudy", high: 71, low: 52, rain: 0 },
    news: [{ title: "Lakers beat Warriors 118-112 - ESPN", source: "ESPN" }], messages: { unread: 2 } });
  assert.strictEqual(r.source, "rules");
  assert.match(r.text, /64 degrees/); assert.match(r.text, /Lakers beat Warriors/); assert.match(r.text, /2 unread/);
});

test("summary falls back to the article's first real sentences", () => {
  const t = "Short. This is the first long sentence of the article with real content in it. And here is the second long sentence that also matters a lot.";
  assert.match(extractive(t, "x"), /^This is the first long sentence/);
});

test("RSS and Atom feeds parse, including CDATA, entities and Google News titles", () => {
  const rss = `<rss><channel><title>BBC News</title>
    <item><title><![CDATA[Europa &amp; Jupiter: new images]]></title><link>https://bbc.co.uk/a</link><pubDate>Sat, 17 Oct 2026 07:00:00 GMT</pubDate>
      <description><![CDATA[<p>The probe&#8217;s first look</p>]]></description><media:thumbnail url="https://img/x.jpg"/></item>
    <item><title>Lakers win - ESPN</title><link>https://news.google.com/rss/articles/abc</link><source url="https://espn.com">ESPN</source></item>
  </channel></rss>`;
  const items = parseFeed(rss);
  assert.strictEqual(items.length, 2);
  assert.strictEqual(items[0].title, "Europa & Jupiter: new images");
  assert.strictEqual(items[0].desc, "The probe’s first look");
  assert.strictEqual(items[0].img, "https://img/x.jpg");
  assert.ok(items[0].when > 0);
  assert.strictEqual(items[1].title, "Lakers win");
  assert.strictEqual(items[1].source, "ESPN");
  const atom = `<feed><title>Ars</title><entry><title>Chips</title><link href="https://ars/x"/><updated>2026-10-17T07:00:00Z</updated><summary>s</summary></entry></feed>`;
  assert.strictEqual(parseFeed(atom)[0].link, "https://ars/x");
});

test("article text keeps paragraphs, drops scripts and boilerplate", () => {
  const html = `<html><script>var x=1</script><article><p>Subscribe to our newsletter for more stories every day of the week.</p>
    <p>NASA's probe sent back its first close images of Europa on Friday, scientists said.</p></article></html>`;
  assert.strictEqual(extractText(html), "NASA's probe sent back its first close images of Europa on Friday, scientists said.");
});

test("news sections interleave sources and drop duplicate headlines", async () => {
  const feeds = { a: `<rss><item><title>One</title><link>l1</link></item><item><title>Two</title><link>l2</link></item></rss>`,
                  b: `<rss><item><title>One</title><link>l3</link></item><item><title>Three</title><link>l4</link></item></rss>` };
  const n = new News({ sections: [{ id: "s", name: "S", feeds: ["a", "b"] }] }, { fetchFn: async u => ({ ok: true, text: async () => feeds[u] }) });
  const items = await n.section("s");
  assert.deepStrictEqual(items.map(i => i.title), ["One", "Two", "Three"]);
});

test("intent model: habit at this time of day wins, and the reasons are kept", () => {
  const now = Date.parse("2026-10-17T20:00:00");
  const usage = [];
  for (let d = 1; d <= 10; d++) usage.push({ id: "routine:Play my jazz", t: now - d * 864e5 + 5 * 60000 });   // every evening ~8pm
  for (let d = 1; d <= 10; d++) usage.push({ id: "briefing", t: now - d * 864e5 - 12 * 3600e3 });            // every morning
  const c = [{ id: "routine:Play my jazz", kind: "routine", prior: 0.25 }, { id: "briefing", kind: "briefing", prior: 0.4 }, { id: "talk:Maya", kind: "chat", person: "Maya", prior: 0.3 }];
  const r = rank(c, usage, { now });
  assert.strictEqual(r[0].id, "routine:Play my jazz");
  assert.ok(r[0].why.includes("usually around now"));
  // an unread message always gets a slot in the top three, even against a strong habit
  const { top } = require("../lib/intents");
  const many = [...c, ...["a", "b", "c"].map(x => ({ id: "routine:" + x, kind: "routine", prior: 0.6 }))];
  assert.ok(!top(rank(many, usage, { now }), 3).some(r => r.id === "talk:Maya"));
  assert.ok(top(rank(many, usage, { now, signals: { unread: 3 } }), 3).some(r => r.id === "talk:Maya"));
});

test("weather parses Open-Meteo", () => {
  const w = parseWeather({ current: { temperature_2m: 63.6, weather_code: 2, wind_speed_10m: 5.2 }, daily: { temperature_2m_max: [71.2], temperature_2m_min: [52], precipitation_probability_max: [10] } },
                         { place: "San Jose", units: "imperial" });
  assert.deepStrictEqual([w.temp, w.desc, w.high, w.unit], [64, "partly cloudy", 71, "°F"]);
});

test("care report groups by day", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "res-"));
  const s = new Store(dir);
  s.care("rel", { rel: { hand: 90, brow: 80 } }); s.care("rel", { rel: { hand: 70, brow: 80 } });
  s.care("migrate", { from: ["hand"], to: ["brow"], why: "x" }); s.care("tired", {}); s.care("session", { minutes: 5, selections: 12 });
  const r = s.careReport();
  assert.strictEqual(r.length, 1);
  assert.deepStrictEqual(r[0].rel, { hand: 80, brow: 80 });
  assert.strictEqual(r[0].migrations.length, 1);
  assert.strictEqual(r[0].selections, 12);
});
