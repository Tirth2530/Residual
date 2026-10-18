// Run: npm test
// The small pieces behind Home's greeting, the family faces, the signal chart and the confirmation screens.
const test = require("node:test");
const assert = require("node:assert");
const V = require("../ui/home-view");

const at = h => new Date(2026, 9, 18, h, 0);

test("the greeting follows the time of day and uses the person's name", () => {
  assert.strictEqual(V.greeting(at(8), "Dev"), "Good morning, Dev.");
  assert.strictEqual(V.greeting(at(14), "Dev"), "Good afternoon, Dev.");
  assert.strictEqual(V.greeting(at(19), "Dev"), "Good evening, Dev.");
  assert.strictEqual(V.greeting(at(3), "Dev"), "It's late, Dev.");
  assert.strictEqual(V.greeting(at(8), ""), "Good morning.");
});

test("the circle line says who is around in plain words", () => {
  const members = [
    { member: "Raj", mode: "free", eta_min: 6 },
    { member: "Maya", mode: "busy", label: "in a meeting", until: 0 },
  ];
  assert.strictEqual(V.circleLine(members), "Raj is 6 min away. Maya is in a meeting.");
  assert.strictEqual(V.circleLine([{ member: "Raj", mode: "free" }]), "Raj is free.");
  assert.strictEqual(V.circleLine([{ member: "Maya", mode: "asleep" }]), "Maya is asleep.");
  assert.strictEqual(V.circleLine([]), "");
  assert.strictEqual(V.circleLine(null), "");
});

test("each person always gets the same face: an initial and a colour", () => {
  const a = V.avatar("Maya"), b = V.avatar("Maya"), c = V.avatar("Raj");
  assert.deepStrictEqual(a, b);
  assert.strictEqual(a.ch, "M");
  assert.match(a.bg, /^#[0-9A-F]{6}$/i);
  assert.ok(a.ink);
  assert.strictEqual(V.avatar("").ch, "?");
  assert.notStrictEqual(V.avatar("Maya").bg === c.bg && V.avatar("Raj").ch === a.ch, true);
});

test("the signal chart is drawn from real attempts: weak ones fall below the line", () => {
  const attempts = [
    { margin: 1.0, full: true }, { margin: 0.8, full: true }, { margin: 0.4, full: true },
    { margin: 0.05, full: true }, { margin: -0.2, full: false }, { margin: -0.3, full: false },
  ];
  const c = V.attemptBars(attempts);
  assert.strictEqual(c.bars.length, 6);
  assert.ok(c.bars[0].h > c.bars[2].h && c.bars[2].h > c.bars[5].h, "heights follow the strength");
  assert.deepStrictEqual(c.bars.map(b => b.ok), [true, true, true, true, false, false]);
  assert.ok(c.bars.every(b => b.h >= 6 && b.h <= 100));
  assert.ok(c.threshold > 0 && c.threshold < 100);
  assert.ok(c.bars[3].h >= c.threshold - 1, "an attempt that just cleared the threshold sits on the line");
  assert.ok(c.bars[4].h < c.threshold, "a miss is below it");
  assert.strictEqual(V.attemptBars([]).bars.length, 0);
  assert.strictEqual(V.attemptBars(new Array(20).fill({ margin: 0.5, full: true })).bars.length, 8);   // the most recent eight
});

test("a confirmation screen is a short hero, a kicker and some words", () => {
  const explicit = V.askParts({ title: "x", kicker: "Residual noticed", hero: "Switch to one signal?", body: "Because…" });
  assert.deepStrictEqual([explicit.kicker, explicit.hero, explicit.body], ["Residual noticed", "Switch to one signal?", "Because…"]);
  const short = V.askParts({ title: "Cancel the order?" });
  assert.strictEqual(short.hero, "Cancel the order?");
  assert.strictEqual(short.body, "");
  const long = V.askParts({ title: "Luna Kitchen: 1× soup. $18.47 with delivery. That's over your limit, so Maya will be asked to approve it." });
  assert.strictEqual(long.hero, "Luna Kitchen: 1× soup.");
  assert.match(long.body, /^\$18\.47 with delivery\./);
  assert.ok(V.askParts({ title: "A".repeat(200) }).hero.length <= 70);
  assert.ok(V.askParts({ title: "Hello?" }).kicker.length > 0);
});

test("the hero shrinks as it gets longer so it never overflows", () => {
  assert.ok(V.heroSize("One click left.") > V.heroSize("A much longer question that needs more room?"));
  assert.ok(V.heroSize("x") <= 112 && V.heroSize("y".repeat(300)) >= 44);
});

test("the chart's HTML has one bar per attempt and escapes nothing it shouldn't have", () => {
  const html = V.chartHTML(V.attemptBars([{ margin: 1, full: true }, { margin: -0.1, full: false }]));
  assert.strictEqual((html.match(/class="bar /g) || []).length, 2);
  assert.match(html, /class="bar ok"/);
  assert.match(html, /class="bar low"/);
  assert.match(html, /class="line"/);
});

test("long text is cut into spoken chunks at sentence ends, none longer than the limit", () => {
  assert.deepStrictEqual(V.chunkText("Hello there. How are you?", 380), ["Hello there. How are you?"]);
  const s = "One sentence here. ".repeat(40);                      // ~760 characters
  const parts = V.chunkText(s, 100);
  assert.ok(parts.length > 1 && parts.every(p => p.length <= 100));
  assert.strictEqual(parts.join(" ").replace(/\s+/g, " "), s.trim().replace(/\s+/g, " "), "nothing is lost");
  assert.ok(parts.every(p => /[.!?]$/.test(p)), "each chunk ends where a sentence ends");
});
test("a sentence longer than the limit is split rather than dropped", () => {
  const long = "word ".repeat(120).trim();                         // one 599-character sentence, no full stop
  const parts = V.chunkText(long, 200);
  assert.ok(parts.length >= 3 && parts.every(p => p.length <= 200));
  assert.strictEqual(parts.join(" "), long);
});
test("nothing to say gives no chunks", () => {
  assert.deepStrictEqual(V.chunkText("   ", 380), []);
  assert.deepStrictEqual(V.chunkText("", 380), []);
  assert.deepStrictEqual(V.chunkText(null, 380), []);
});
