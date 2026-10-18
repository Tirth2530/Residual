// Run: npm test
// When "Back" goes to Home and when it doesn't. The reports: Home unreachable sometimes, and jumping to Home from another page.
const test = require("node:test");
const assert = require("node:assert");
const { backTarget, isBusy, backDecision } = require("../ui/nav-rules");

test("Back to videos with no history goes to the app's start page, not Home", () => {
  assert.strictEqual(backTarget({ canGoBack: false, url: "https://www.youtube.com/watch?v=abc", startUrl: "https://www.youtube.com/" }), "start");
});
test("with history it goes back; on the start page already it stays put", () => {
  assert.strictEqual(backTarget({ canGoBack: true, url: "https://www.youtube.com/watch?v=abc", startUrl: "https://www.youtube.com/" }), "back");
  assert.strictEqual(backTarget({ canGoBack: false, url: "https://www.youtube.com/", startUrl: "https://www.youtube.com" }), "none");
  assert.strictEqual(backTarget({ canGoBack: false, url: "https://www.youtube.com/?bp=x#a", startUrl: "https://www.youtube.com/" }), "none");
});
test("no known start page: nothing to do, still not Home", () => {
  assert.strictEqual(backTarget({ canGoBack: false, url: "https://x.test/a", startUrl: "" }), "none");
});

test("a pick that never finished stops blocking after a while", () => {
  assert.strictEqual(isBusy({ busy: true, busyAt: 1000 }, 3000), true);
  assert.strictEqual(isBusy({ busy: true, busyAt: 1000 }, 14000), false);
  assert.strictEqual(isBusy({ busy: false, busyAt: 1000 }, 1500), false);
});

const base = { how: "eyes closed", busy: false, setup: false, stackLen: 0, sheetType: null, active: "youtube", now: 100000, openedAt: 0, lastPickAt: 0 };
test("closing the eyes at the top of an app goes Home", () => {
  assert.strictEqual(backDecision(base), "home");
});
test("...but not in the moments after an app opens or a choice is made", () => {
  assert.strictEqual(backDecision({ ...base, openedAt: 99000 }), "ignore");
  assert.strictEqual(backDecision({ ...base, lastPickAt: 98500 }), "ignore");
  assert.strictEqual(backDecision({ ...base, openedAt: 90000, lastPickAt: 90000 }), "home");
});
test("a keyboard or a click on Back is always honoured", () => {
  assert.strictEqual(backDecision({ ...base, how: "key", openedAt: 99900 }), "home");
});
test("inside a submenu it steps back one level", () => {
  assert.strictEqual(backDecision({ ...base, stackLen: 2, openedAt: 99900 }), "step");
});
test("a stuck busy flag doesn't lock Home out; a real one still waits", () => {
  assert.strictEqual(backDecision({ ...base, busy: true, busyAt: 99000 }), "ignore");
  assert.strictEqual(backDecision({ ...base, busy: true, busyAt: 50000 }), "home");
});
test("setup blocks it; at Home it just says so", () => {
  assert.strictEqual(backDecision({ ...base, setup: true }), "ignore");
  assert.strictEqual(backDecision({ ...base, active: null }), "stay");
});
test("a sheet closes before anything else", () => {
  assert.strictEqual(backDecision({ ...base, sheetType: "keyboard" }), "sheet");
  assert.strictEqual(backDecision({ ...base, sheetType: "lifeline" }), "ignore");
});

// ---- refreshes must not undo what the person just did
const { refreshPlan, returnCard } = require("../ui/nav-rules");
const r0 = { gen: 5, curGen: 5, force: false, navAt: 1000, since: 2000, depth: 0, sameKey: false };
test("a refresh applies when nothing has happened since it began", () => {
  assert.strictEqual(refreshPlan(r0), "apply");
});
test("a refresh that was overtaken by a newer one is dropped", () => {
  assert.strictEqual(refreshPlan({ ...r0, gen: 4 }), "skip");
  assert.strictEqual(refreshPlan({ ...r0, gen: 4, force: true }), "skip");
});
test("the delayed refresh after a pick doesn't wipe a menu the person has since entered", () => {
  assert.strictEqual(refreshPlan({ ...r0, navAt: 2500, since: 2000 }), "skip");
  assert.strictEqual(refreshPlan({ ...r0, navAt: 1500, since: 2000 }), "apply");
});
test("inside a submenu of the same screen, a refresh keeps the person's place", () => {
  assert.strictEqual(refreshPlan({ ...r0, depth: 2, sameKey: true }), "skip");
  assert.strictEqual(refreshPlan({ ...r0, depth: 2, sameKey: false }), "apply");
});
test("a forced refresh (a new app, a question, a sheet) always applies", () => {
  assert.strictEqual(refreshPlan({ ...r0, force: true, navAt: 9999, depth: 3, sameKey: true }), "apply");
});

test("after leaving an app, Home offers the way back for two minutes", () => {
  const from = { id: "youtube", name: "YouTube", at: 10000 };
  assert.deepStrictEqual(returnCard({ cameFrom: from, now: 60000, active: null }), { id: "youtube", name: "YouTube" });
  assert.strictEqual(returnCard({ cameFrom: from, now: 10000 + 121000, active: null }), null);
  assert.strictEqual(returnCard({ cameFrom: from, now: 60000, active: "youtube" }), null);
  assert.strictEqual(returnCard({ cameFrom: null, now: 60000, active: null }), null);
});

test("two Backs in the same instant count once", () => {
  assert.strictEqual(backDecision({ ...base, lastBackAt: 99800 }), "ignore");
  assert.strictEqual(backDecision({ ...base, lastBackAt: 98000 }), "home");
});

// Escape used to be the only way out of a finished help request and of a confirmation question.
const bk = { how: "eyes closed", busy: false, busyAt: 0, setup: false, stackLen: 0, sheetType: null, active: null, now: 100000, openedAt: 0, lastPickAt: 0, lastBackAt: 0 };
test("a help request that has finished (answered, cancelled, nobody) closes on Back", () => {
  assert.strictEqual(backDecision({ ...bk, sheetType: "task", sheetDone: true }), "sheet");
});
test("...but one still waiting for an answer can't be dismissed by an accidental long blink", () => {
  assert.strictEqual(backDecision({ ...bk, sheetType: "task", sheetDone: false }), "ignore");
  assert.strictEqual(backDecision({ ...bk, sheetType: "lifeline" }), "ignore");
  assert.strictEqual(backDecision({ ...bk, sheetType: "countdown" }), "ignore");
});
test("Back on a confirmation question declines it: the safe answer, nothing happens", () => {
  assert.strictEqual(backDecision({ ...bk, question: true }), "decline");
  assert.strictEqual(backDecision({ ...bk, question: true, active: "youtube" }), "decline");
});
test("a question is still protected from a double-report and from setup", () => {
  assert.strictEqual(backDecision({ ...bk, question: true, lastBackAt: 99800 }), "ignore");
  assert.strictEqual(backDecision({ ...bk, question: true, setup: true }), "ignore");
});

// ---- splitting a list into two halves: every choice must stay reachable, with Back and Home in the list (the Talk bug)
const { splitTwo, splitWithNav } = require("../ui/nav-rules");
const mk = n => Array.from({ length: n }, (_, i) => ({ label: "item" + i }));
const NAV = () => [{ label: "Back", weight: 0.5, isBack: true }, { label: "Home", weight: 0.5, isHome: true }];
const names = g => g.map(x => x.label);

test("two items split into one each; a longer list keeps its order and splits where the two sides weigh the same", () => {
  const [a, b] = splitTwo(mk(2));
  assert.deepStrictEqual([names(a), names(b)], [["item0"], ["item1"]]);
  const [l, r] = splitTwo(mk(8));
  assert.deepStrictEqual(names([...l, ...r]), mk(8).map(x => x.label));
  assert.ok(l.length >= 1 && r.length >= 1 && Math.abs(l.length - r.length) <= 3);
});

test("two real choices plus Back and Home: each person on their own side, Back and Home beside the second", () => {
  const [a, b] = splitWithNav([...mk(2), ...NAV()]);
  assert.deepStrictEqual(names(a), ["item0"]);
  assert.deepStrictEqual(names(b), ["item1", "Back", "Home"]);
});

test("one real choice plus Back and Home: the choice alone, so one pick runs it", () => {
  const [a, b] = splitWithNav([...mk(1), ...NAV()]);
  assert.deepStrictEqual([names(a), names(b)], [["item0"], ["Back", "Home"]]);
});

test("with no Back or Home it is the plain split", () => {
  assert.deepStrictEqual(splitWithNav(mk(5)).map(names), splitTwo(mk(5)).map(names));
});

// follow the picks a person makes: choose the side that holds the target; a one-item side runs it; otherwise drill into it
function picksTo(items, target) {
  let list = [...items, ...NAV()], picks = 0;
  for (; picks < 30; ) {
    const [l, r] = splitWithNav(list);
    const side = names(l).includes(target) ? l : names(r).includes(target) ? r : null;
    if (!side) return -1;
    picks++;
    if (side.length === 1 && side[0].label === target) return picks;
    const real = side.filter(x => !x.isBack && !x.isHome);
    if (real.length === list.filter(x => !x.isBack && !x.isHome).length) return -2;      // no progress: the loop that froze Talk
    list = [...real, ...NAV()];
  }
  return -3;
}
test("every choice can be reached, with Back and Home present at every level, in a few picks (never a loop)", () => {
  for (let n = 1; n <= 16; n++) {
    const items = mk(n);
    for (const it of items) {
      const p = picksTo(items, it.label);
      assert.ok(p > 0, `${n} items: ${it.label} gave ${p}`);
      assert.ok(p <= Math.ceil(Math.log2(Math.max(n, 2))) + 2, `${n} items: ${it.label} took ${p} picks`);
    }
  }
});
