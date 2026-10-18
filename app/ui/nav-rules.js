// When "Back" goes to Home, and when it doesn't. Pure, so it can be tested.
//
// Reported: Home was sometimes unreachable, and the app jumped to Home while on another page. Causes found:
//  - "Back to videos" with no history went to Home (nothing to go back to).
//  - closing the eyes a moment is Back, and at the top of an app Back is Home, so a slow blink right after an app opened or a
//    choice was made sent the person Home.
//  - a choice that never finished left the "busy" flag on, and nothing, Back included, worked until a restart.
(function (root) {
  const BUSY_MAX_MS = 12000, GRACE_MS = 2500;

  const where = u => { try { const x = new URL(u); return x.origin + x.pathname.replace(/\/+$/, ""); } catch (e) { return String(u || ""); } };

  // Previous page: go back if there is a back; otherwise to the app's start page; if already there, stay put.
  function backTarget({ canGoBack, url, startUrl }) {
    if (canGoBack) return "back";
    if (startUrl && where(url) !== where(startUrl)) return "start";
    return "none";
  }

  function isBusy(T, now) { return !!T.busy && now - (T.busyAt || 0) < BUSY_MAX_MS; }

  // -> "ignore" | "decline" | "step" | "sheet" | "home" | "stay"
  // A confirmation question is declined by Back (the safe answer: nothing happens). A help request, a countdown or the lifeline
  // can't be dismissed by an accidental long blink while they're still live, but a help request that has finished closes.
  function backDecision({ how, busy, busyAt, setup, stackLen, sheetType, sheetDone = false, question = false, active, now, openedAt = 0, lastPickAt = 0, lastBackAt = 0 }) {
    if (setup || isBusy({ busy, busyAt }, now)) return "ignore";
    if (now - lastBackAt < 700) return "ignore";            // one Back, however many ways it was reported
    if (question) return "decline";
    if (stackLen) return "step";
    if (sheetType) return sheetType === "keyboard" || !["lifeline", "task", "countdown"].includes(sheetType) || (sheetType === "task" && sheetDone) ? "sheet" : "ignore";
    if (!active) return "stay";
    if (how === "eyes closed" && (now - openedAt < GRACE_MS || now - lastPickAt < GRACE_MS)) return "ignore";
    return "home";
  }

  // A refresh rebuilds the choices from the page. It must never undo something the person did while it was working:
  //  - a newer refresh has started: this one is stale
  //  - the person made a choice since this refresh was asked for (`since`): their place wins
  //  - they are inside a submenu of the same screen: keep them there
  // `force` is for a real change (another app, a question, a sheet).
  function refreshPlan({ gen, curGen, force, navAt = 0, since = 0, depth = 0, sameKey = false }) {
    if (gen !== curGen) return "skip";
    if (force) return "apply";
    if (navAt > since) return "skip";
    if (depth > 0 && sameKey) return "skip";
    return "apply";
  }

  // After leaving an app, Home puts the way back first for a while: an accidental Back costs one choice, not a search.
  const RETURN_MS = 120000;
  function returnCard({ cameFrom, now, active }) {
    if (!cameFrom || active || now - cameFrom.at > RETURN_MS) return null;
    return { id: cameFrom.id, name: cameFrom.name };
  }

  // Keep the order (most likely first) and split where the two sides are closest in weight.
  function splitTwo(list) {
    if (list.length === 2) return [[list[0]], [list[1]]];
    const w = list.map((x, i) => x.weight ?? 1 / (1 + 0.15 * i));
    const total = w.reduce((a, b) => a + b, 0);
    let best = 1, bestD = Infinity, acc = 0;
    for (let k = 1; k < list.length; k++) { acc += w[k - 1]; const d = Math.abs(2 * acc - total); if (d < bestD - 1e-9) { bestD = d; best = k; } }
    return [list.slice(0, best), list.slice(best)];
  }
  // The same, with Back and Home. They are light, so a plain split could put every real choice on one side and Back and Home on the
  // other, and picking that side would redraw the same two sides for ever (Talk: Maya and Raj). So the real choices are split on their
  // own, and Back and Home go beside the last of them. Every pick then narrows the real choices.
  function splitWithNav(list) {
    const nav = list.filter(x => x.isBack || x.isHome), real = list.filter(x => !x.isBack && !x.isHome);
    if (!nav.length || real.length < 2) return splitTwo(list);
    const [a, b] = splitTwo(real);
    return [a, [...b, ...nav]];
  }

  const api = { backTarget, isBusy, backDecision, refreshPlan, returnCard, splitTwo, splitWithNav, BUSY_MAX_MS, GRACE_MS, RETURN_MS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else Object.assign(root, { NavRules: api });
})(typeof window !== "undefined" ? window : globalThis);
