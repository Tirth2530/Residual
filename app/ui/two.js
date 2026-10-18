// Two choices, eyes only.
//
// Everything in Residual is a choice between two big halves of the screen:
//   LEFT  = blink twice          RIGHT = raise your eyebrows
// (or, if the person prefers, glance left / right and hold). Anything with more options is split in two
// again, most likely first, so 8 options take 3 picks and 32 take 5.
//
// Real apps (Instagram, YouTube, WhatsApp, Netflix) stay real: the page lists everything that can be
// picked on it (AGENCY's approach), each half is outlined on the page in blue and amber, and the app
// sits in the middle with the two halves on either side.
//
// The face tracking is Residual Eyes (AGENCY's MediaPipe pipeline + Residual's switches), served by the
// main process from ui/eyes. Keys and clicks always work for a helper: Left / Right arrows, or click a side.
(function () {
  const $ = s => document.querySelector(s);
  const escH = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const busyNow = () => NavRules.isBusy(T, performance.now());   // a choice that never finished can't lock everything for ever

  const T = { on: false, eyes: null, tracker: null, list: null, stack: [], path: [], split: null, sig: "",
              pageSig: "", busy: false, setup: false, paused: false, lastPick: 0, mode: "home", gen: 0, navAt: 0, lastBackAt: 0, cameFrom: null };

  /* ------------------------------------------------------------ how the person picks */
  const INPUT_KEY = "residual.two.inputs";
  const SCHEMES = {
    face: { left: "doubleBlink", right: "brow", leftName: "Blink twice", rightName: "Raise eyebrows", leftIcon: "eye", rightIcon: "brow" },
    wink: { left: "doubleBlink", right: "winkRight", leftName: "Blink twice", rightName: "Wink", leftIcon: "eye", rightIcon: "wink" },
    glance: { left: "lookLeft", right: "lookRight", leftName: "Look left and hold", rightName: "Look right and hold", leftIcon: "left", rightIcon: "right" },
  };
  function schemeName() { try { const n = localStorage.getItem(INPUT_KEY); return SCHEMES[n] ? n : "face"; } catch (e) { return "face"; } }
  function scheme() {
    const n = schemeName();
    if (n !== "wink") return SCHEMES[n];
    const eye = (prefs().wink || {}).eye === "L" ? "L" : "R";
    return { ...SCHEMES.wink, right: eye === "L" ? "winkLeft" : "winkRight", rightName: `Wink your ${eye === "L" ? "left" : "right"} eye` };
  }
  const isFace = () => schemeName() === "face", isWink = () => schemeName() === "wink";
  // the signal on the RIGHT side, the one whose readability is watched (blink twice is the steadier one)
  const rightSignal = () => isWink() ? "wink" : isFace() ? "eyebrow" : null;
  function setScheme(name) { try { localStorage.setItem(INPUT_KEY, name); } catch (e) {} applyScheme(); draw(); }
  function applyScheme() {
    if (!T.tracker) return;
    const s = scheme();
    T.tracker.settings.enabled = [s.left, s.right, "longBlink"];   // long blink = Back
    T.tracker.setWinkSafe && T.tracker.setWinkSafe(isWink());     // a held wink must never count toward help
    if (T.q) T.q.name = rightSignal() || "eyebrow";
  }
  // The person's choices: eyebrows once or twice, two signals or one (scanning), adapt automatically.
  const PREF_KEY = "residual.two.prefs";
  const PREF_DEFAULT = { browTwice: false, signals: "two", oneSignal: "doubleBlink", adapt: true, scanMs: 2200, largeText: false };
  function prefs() { try { return { ...PREF_DEFAULT, ...JSON.parse(localStorage.getItem(PREF_KEY) || "{}") }; } catch (e) { return { ...PREF_DEFAULT }; } }
  function setPref(k, v) { const p = prefs(); p[k] = v; try { localStorage.setItem(PREF_KEY, JSON.stringify(p)); } catch (e) {} draw(); }
  const scanOn = () => prefs().signals === "one";
  const ICONS = {
    eye: '<svg viewBox="0 0 48 48"><path d="M4 24s7-12 20-12 20 12 20 12-7 12-20 12S4 24 4 24z"/><circle cx="24" cy="24" r="6"/></svg>',
    brow: '<svg viewBox="0 0 48 48"><path d="M8 18c5-6 12-8 18-6"/><path d="M40 18c-4-5-9-7-14-6" opacity=".5"/><path d="M6 30s6-7 14-7 14 7 14 7-6 7-14 7S6 30 6 30z"/><circle cx="20" cy="30" r="3.5"/></svg>',
    left: '<svg viewBox="0 0 48 48"><path d="M30 10L16 24l14 14"/></svg>',
    right: '<svg viewBox="0 0 48 48"><path d="M18 10l14 14-14 14"/></svg>',
    wink: '<svg viewBox="0 0 48 48"><path d="M4 26s5-9 14-9 14 9 14 9-5 9-14 9-14-9-14-9z"/><circle cx="18" cy="26" r="4"/><path d="M34 26c3 2 7 2 10 0"/></svg>',
  };

  /* ------------------------------------------------------------ what can be chosen */
  const item = (label, run, o = {}) => ({ label, run, ...o });
  const menu = (label, children, o = {}) => ({ label, children, ...o });

  // Pause / play whatever is playing, from anywhere. Only offered once a song has been started.
  async function musicItems() {
    let s = null;
    try { s = await window.shell.musicState(); } catch (e) {}
    const label = s && s.has ? (s.playing ? "Pause music" : "Play music") : null;
    if (!label) return [];
    return [item(label, async () => { const r = await window.shell.musicToggle(s.playing ? "pause" : "play"); if (r && r.done) status(r.done); },
      { sub: s.title || "", weight: 3 })];
  }
  function homeMenu() {
    const needs = (CFG.needs || []).map(n => item(n.label || n.say, () => sayNeed(n), { sub: n.label ? `“${n.say}”` : "" }));
    const people = (CFG.people || []).map(p => menu(p.name, () => (p.phrases || []).map(t => item(t, () => messagePerson(p, t))), { sub: p.relation || "", avatar: HomeView.avatar(p.name) }));
    const app = id => { const a = appById(id); return a ? item(a.name, () => openApp(a.id), { sub: a.sub }) : null; };
    const demo = prefs().demo ? [menu("Demo", () => demoMenu(), { sub: "The rehearsed demo, nothing faked", weight: 2.6 })] : [];
    const back = NavRules.returnCard({ cameFrom: T.cameFrom, now: performance.now(), active: S.active });
    return [
      ...(back ? [item(`Back to ${back.name}`, () => openApp(back.id), { sub: "Where you just were", weight: 14 })] : []),
      ...demo,
      menu("Say something", () => sayMenu(needs), { sub: heardLast() ? `Reply to ${heardLast().contact}, or anything` : "Out loud, right now", weight: 3 }),
      menu("Get help", () => [
        item("Move me", () => startHelp("repositioning", false), { weight: 2 }),
        item("Help now", () => startHelp("general", true), { weight: 2 }),
        item("I'm in pain", () => startHelp("pain", false, "urgent")),
        item("Trouble breathing", () => startHelp("breathing", true)),
        item("Suction", () => startHelp("suction", false, "urgent")),
      ], { sub: `${(S.agent?.health?.contacts || [])[0] || CFG.lifeline?.contacts?.[0]?.name || "Your carer"} first`, weight: 2.5 }),
      menu("Watch", () => ["reels", "youtube", "shorts", "netflix"].map(app).filter(Boolean), { sub: "Reels, YouTube, Netflix", weight: 2 }),
      menu("Talk", () => people, { sub: (CFG.people || []).map(p => p.name).join(", "), weight: 1.5 }),
      menu("Browse the web", () => (CFG.web || []).map(w => item(w.name, () => openWeb(w.url), { sub: T.aiwebReady ? "AI web mode (Browser Use)" : "Needs a Browser Use key" })),
        { sub: "Any site, through Browser Use", weight: 0.9 }),
      item("Order my usual", () => orderUsual(), { sub: "Food, sandbox test: you see the total and confirm first", weight: 0.7 }),
      menu("Listen", async () => [...await musicItems(), app("music"), ...(CFG.routines || []).filter(r => ["music", "moon"].includes(r.icon)).map(r => item(r.name, () => runRoutine(r)))].filter(Boolean), { sub: "Music, sleep sounds", weight: 1 }),
      menu("Errands", () => errandsMenu(), { sub: "Browser Use does it, you confirm the last step", weight: 0.9 }),
      menu("Care circle", () => circleMenu(), { sub: "Who is free right now", weight: 0.7 }),
      menu("Settings", () => {
        const p = prefs();
        const cal = calibrated();
        return [
          item("Test my voice", () => { Voice.say("Hello. This is my voice.", { priority: P.PERSON }); }, { sub: "Plays the voice from your settings", weight: 3 }),
          item("Test my controls", () => testControls(), { sub: "About 7 minutes: prompts, then 2 minutes of normal talking and looking around", weight: 1.2 }),
          menu("How I choose", () => [
            item("Blink twice + eyebrows", () => setScheme("face"), { sub: isFace() ? "In use" : "" }),
            cal.wink ? item(`Blink twice + wink (${cal.wink.eye === "L" ? "left" : "right"} eye)`, () => setScheme("wink"), { sub: isWink() ? "In use" : "Learned in Setup" })
                     : item("Learn a wink", () => openSetup(3, "wink"), { sub: "Can stand in for eyebrows" }),
            cal.glances ? item("Glance left / right", () => setScheme("glance"), { sub: schemeName() === "glance" ? "In use" : "Learned in Setup" })
                        : item("Learn your glances", () => openSetup(3, "glances")),
          ], { sub: scheme().leftName + " + " + scheme().rightName.toLowerCase() }),
          item(Rec.on ? "Stop recording" : "Record session", () => (Rec.on ? recStop() : recConsent()), { sub: Rec.on ? "Recording numbers only" : "Off. Asks first, numbers only" }),
          item("Delete recordings", () => recDelete()),
          item(p.largeText ? "Normal text size" : "Larger text", () => setLarge(!p.largeText), { sub: "Bigger choices, and websites zoomed in" }),
          item("Set up again", () => openSetup(0)),
          item(p.demo ? "Demo mode off" : "Demo mode", () => { setPref("demo", !p.demo); document.body.classList.toggle("two-demo", !p.demo); tel("two-demo", { on: !p.demo }); }, { sub: "Adds a Demo menu that works without logins" }),
          item(p.signals === "two" ? "Use one signal (scanning)" : "Use two signals again", () => setSignals(p.signals === "two" ? "one" : "two", "you chose it")),
          item(p.browTwice ? "Eyebrows: raise once" : "Eyebrows: raise twice", () => setPref("browTwice", !p.browTwice)),
          item(p.adapt ? "Stop adapting by itself" : "Adapt by itself", () => setPref("adapt", !p.adapt)),
          item(T.tracker && T.tracker.browGain < 1 ? "Stop simulated weakening" : "Simulate eyebrows weakening (demo)", () => simulateWeak()),
          item("Pause my eyes", () => pauseEyes()),
        ];
      }, { weight: 0.4 }),
    ];
  }

  /* ------------------------------------------------------------ the care circle and the Hands */
  const MODE_WORDS = { free: "free", busy: "busy", asleep: "asleep", driving: "driving", away: "away" };
  async function circleMenu() {
    const c = await window.shell.circle();
    if (!c.members.length) return [item("The care circle isn't running", () => status("Start the Relay (cd agent && python residual_agent.py) to bring the care circle online"), { sub: "Start the Relay" })];
    return c.members.map(m => {
      const where = m.mode === "free" ? (m.eta_min ? `${m.eta_min} min away` : "free") : `${m.label || MODE_WORDS[m.mode] || m.mode}${m.until ? " until " + new Date(m.until * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : ""}`;
      const how = m.transport === "agentverse" ? "their own agent on Agentverse" : "local agent";
      return item(`${m.member}: ${where}`, () => say2(`${m.member} is ${where}. Care load this week: ${m.load}.`), { sub: `${how} · load ${m.load}`, avatar: HomeView.avatar(m.member) });
    });
  }
  async function errandsMenu() {
    const list = (await window.shell.errands()) || [];
    const live = list.filter(e => !["done", "failed", "cancelled", "declined", "refused", "no_hands"].includes(e.status));
    const out = [];
    for (const e of live) {
      out.push(item(`Stop: ${e.goal}`, async () => { await window.shell.errandCancel(e.id); status("Stopped. Nothing was submitted."); }, { sub: errandWords(e), weight: 3 }));
      const url = e.live_url && SwarmView.safeLiveUrl(e.live_url);
      if (url) out.push(item("Watch the browser", () => window.shell.errandLive(url), { sub: "Browser Use's live view of this errand", weight: 2 }));
    }
    const presets = (CFG.errands || []).map(p => item(p.name, () => startErrand(p), { sub: p.sub || "" }));
    return [...out, ...presets].length ? [...out, ...presets] : [item("No errands set up", () => status("Add errands to app/config.json"), {})];
  }
  const errandWords = e => ({ preparing: "Working on it", needs_hands: `Waiting for ${e.helper || "someone"} to lend a hand`, awaiting_consent: "Waiting for your yes",
                              committing: "Doing the last step" }[e.status] || e.status);
  async function startErrand(p) {
    status(`Starting: ${p.name}…`);
    const r = await window.shell.errandNew(p.goal || p.name, p.url);
    if (!r || r.status === "refused" || r.status === "unavailable") { const why = (r && r.error) || "the Relay didn't answer"; say2(`I can't do that: ${why}`); return; }
    say2(`${p.name}: I've started${r.simulated ? " (SIMULATED browser: no Browser Use key yet)" : ""}. I'll stop one click before anything final and ask you.`);
  }
  T.errandSeen = {};
  T.errandAsked = new Set();
  function onErrands(list) {
    for (const e of list || []) {
      const was = T.errandSeen[e.id];
      T.errandSeen[e.id] = e.status;
      if (was === e.status) continue;
      if (e.status === "needs_hands") say2(`${e.host} needs a ${e.blocked_reason === "captcha" ? "CAPTCHA" : e.blocked_reason === "2fa" ? "verification code" : "login"}. I've asked ${e.helper} to lend a hand, from their phone, in the same browser.`);
      else if (e.status === "awaiting_consent" && SwarmView.errandNeeds([e], T.errandAsked)) {
        T.errandAsked.add(e.id);
        ask(`${e.summary} The last step: ${e.final_action}. Nothing has been done yet.`, async () => {
          const r = await window.shell.errandConsent(e.id, true);
          if (r && r.error) status(r.error);
        }, async () => { await window.shell.errandCancel(e.id); status("Cancelled. Nothing was submitted."); },
        { kicker: "Waiting for your yes", hero: "One click left.", body: `${e.summary} The last step is: ${e.final_action}. Nothing has been done yet.`,
          say: `${e.summary} The last step is: ${e.final_action}. Confirm or cancel?`, yesLabel: "Confirm", yesSub: e.final_action, noLabel: "Cancel", noSub: "Nothing is submitted" });
      } else if (e.status === "done" && was) say2(`Done. ${(e.result && (e.result.confirmation || e.result.summary)) || e.summary}${e.simulated && !/simulated/i.test(JSON.stringify(e.result || {})) ? " (simulated)" : ""}`);
      else if (["failed", "no_hands", "refused"].includes(e.status) && was) say2(e.summary || "That didn't work.");
    }
  }

  /* ------------------------------------------------------------ Demo Mode */
  // The real camera and the real gesture engine, with steps that don't depend on consumer-site logins.
  // Anything simulated says so on screen (the weakening banner, "SIMULATED Photon" when the Relay has no keys).
  function demoMenu() {
    const relay = S.agent?.health, live = relay && relay.ok && relay.photon && relay.photon.mode === "photon";
    const first = (relay?.contacts || [])[0] || "your carer";
    return [
      item("Browse a page", async () => openApp("web", await window.shell.demoUrl()), { sub: "A bundled news page: real links, real scrolling", weight: 2 }),
      item(T.tracker && T.tracker.browGain < 1 ? "Stop the weakening" : "Weaken my eyebrows", () => simulateWeak(), { sub: "SIMULATED, and labelled on screen", weight: 1.5 }),
      item(`Ask ${first} to help me move`, () => startHelp("repositioning", false), { sub: live ? "Live: a real text through Photon" : relay && relay.ok ? "SIMULATED Photon: add keys for a real text" : "Relay isn't running", weight: 1.5 }),
      item("Browser Use: Wikipedia", () => openWeb("https://en.wikipedia.org"), { sub: T.aiwebReady ? "General web fallback" : "Needs a Browser Use key" }),
      item("Order my usual (sandbox)", () => orderUsual(), { sub: "Quotes from three restaurants, caps, caregiver approval" }),
      ...(relay && relay.circle ? [
        item("Maya texts: in a meeting till 3", () => circleSay("Maya", "in a meeting till 3pm"), { sub: "SIMULATED text to her own agent: the circle will leave her alone", weight: 1.5 }),
        item("Raj texts: home, 6 min away", () => circleSay("Raj", "home, 6 min away"), { sub: "SIMULATED text to his own agent", weight: 1.5 }),
        item("Everyone is free again", async () => { for (const m of relay.circle.members) await window.shell.circleSay(m, "free now, 15 min away"); say2("Everyone in the care circle is free."); }, { sub: "SIMULATED" }),
      ] : []),
      item("Test my controls", () => testControls()),
    ];
  }

  async function circleSay(member, text) {
    const r = await window.shell.circleSay(member, text);
    if (r && r.error) return status(r.error);
    say2(`${member}'s agent: ${r.ack || "that didn't sound like a status"}`);
  }

  /* ------------------------------------------------------------ ordering (sandbox, through Residual Relay) */
  // The person sees merchant, items, total and delivery, and picks Confirm or Cancel. Over a spending limit,
  // the approved caregiver is asked through Photon; only their reply to that order lets it go through.
  const money = c => "$" + (c / 100).toFixed(2);
  const orderLines = o => (o.lines || []).map(l => `${l.qty > 1 ? l.qty + " × " : ""}${l.name}`).join(", ");
  async function orderUsual() {
    status("Preparing your usual order…");
    const o = await window.shell.orderNew("usual");
    if (!o || o.status === "rejected" || o.status === "unavailable") { const why = (o && o.error) || "the Relay didn't answer"; status(`Can't order: ${why}`); Voice.say(`I can't order that: ${why}.`, { priority: P.CONTENT }); return; }
    showOrder(o);
  }
  function showOrder(o) {
    T.orderAsked = T.orderAsked || new Set(); T.orderAsked.add(o.id);
    const where = o.delivery ? ` to ${o.delivery.label || "home"}` : "";
    const title = `${o.merchant_name}: ${orderLines(o)}. ${money(o.total_cents)} with delivery${where}.${o.negotiation ? " " + o.negotiation : ""}${o.approval_required ? ` That's ${o.approval_reason}, so ${o.approval_from || "your caregiver"} will be asked to approve.` : ""}`;
    ask(title, async () => {
      const r = await window.shell.orderConfirm(o.id, true);
      if (r && r.error) status(r.error);
    }, async () => { await window.shell.orderConfirm(o.id, false); status("Order cancelled"); },
    { kicker: "Confirm your order", hero: `${o.merchant_name}: ${money(o.total_cents)}`, body: `${orderLines(o)}, with delivery${where}.${o.negotiation ? " " + o.negotiation : ""}${o.approval_required ? ` That's ${o.approval_reason}, so ${o.approval_from || "your caregiver"} will be asked to approve.` : ""}`,
      say: `${orderLines(o)} from ${o.merchant_name}, ${money(o.total_cents)}. Confirm or cancel?`, yesLabel: "Confirm order", yesSub: `${money(o.total_cents)} · sandbox test, no real money`, noLabel: "Cancel", noSub: "Nothing is ordered" });
  }
  T.orderSeen = {};
  function onOrders(list) {
    for (const o of list || []) {
      const was = T.orderSeen[o.id];
      T.orderSeen[o.id] = o.status;
      if (was === undefined && Date.now() / 1000 - o.created > 120) continue;    // old history: don't replay it
      if (was === o.status) continue;
      if (o.status === "awaiting_confirmation" && o.source === "asi" && !(T.orderAsked && T.orderAsked.has(o.id))) {
        status("An order was prepared through ASI:One. You decide.");
        showOrder(o);
      } else if (o.status === "awaiting_approval") say2(`That's ${o.approval_reason}, so I've asked ${o.approval_from} to approve it.`);
      else if (o.status === "paid") say2(`Order placed with ${o.merchant_name}${o.approved_by ? `, approved by ${o.approved_by}` : ""}. It's a sandbox test, no real money.`);
      else if (o.status === "declined") say2(`${o.approval_from} declined the order.`);
      else if (o.status === "expired") say2("The approval request expired, so nothing was ordered.");
      else if (o.status === "failed" && was) say2(`The order didn't go through: ${o.error}`);
    }
  }
  function say2(text) { status(text); banner(text, 8000); Voice.say(text, { priority: P.CONTENT }); tel("two-order", { text }); }

  /* ------------------------------------------------------------ saying something */
  // Fixed categories first, so the same choice is always in the same place; two picks narrow each one.
  // When someone has just said something to the person (a caregiver's reply through the Relay), AI can
  // suggest answers, but they're only suggestions: the person picks what is actually said.
  const SAY = CFG => (CFG.sayCategories || [
    { name: "Favorites", weight: 3.6, phrases: ["I love you", "Thank you", "Yes", "No", "I'm okay"] },
    { name: "Yes / No", weight: 3, phrases: ["Yes", "No", "Maybe", "I don't know", "Wait, please"] },
    { name: "Care", weight: 2.4, phrases: ["I need water", "I'm in pain", "Please move me", "I need the bathroom", "I'm too hot", "I'm too cold", "Please fix my pillow", "Suction, please"] },
    { name: "Conversation", weight: 2, phrases: ["Hello", "Thank you", "I love you", "How are you?", "Tell me more", "Please say that again", "Give me a moment to answer", "Goodbye"] },
    { name: "Comfort", weight: 1.2, phrases: ["Dim the lights, please", "Turn on some music", "I want to rest", "Open the window", "Turn on the TV", "I'm comfortable now"] },
  ]);
  function heardLast() {
    const ts = Object.values(S.agent?.tasks || {});
    let best = null;
    for (const t of ts) for (const r of t.replies || []) if (!best || r.t > best.t) best = { contact: r.contact, text: r.text, t: r.t };
    return best && Date.now() / 1000 - best.t < 600 ? best : null;
  }
  function speakIt(text) {
    Voice.say(text, { priority: P.PERSON });
    status(`Said: “${text}”`);
    tel("two-say", { text });
    recLine({ k: "say", chars: text.length });
  }
  function sayMenu(needs) {
    const cats = SAY(CFG).map(c => menu(c.name, () => c.phrases.map(t => item(t, () => speakIt(t))), { weight: c.weight, sub: c.phrases.slice(0, 3).join(", ") + "…" }));
    if (needs.length) cats.push(menu("My phrases", () => needs, { weight: 1, sub: needs.slice(0, 3).map(n => n.label).join(", ") }));
    const h = heardLast();
    if (h) cats.unshift(menu(`Reply to ${h.contact}`, async () => {
      status(`Suggesting replies to “${h.text}”…`);
      let r = null;
      try { r = await window.shell.suggestReplies({ contact: h.contact, messages: [{ me: false, text: h.text }] }); } catch (e) {}
      const list = ((r && r.list) || []).slice(0, 6);
      const fixed = ["Thank you", "Yes please", "No thank you", "I'm okay"];
      return [...list.map(t => item(t, () => speakIt(t), { sub: r && r.source === "ai" ? "AI suggestion" : "" })), ...fixed.filter(f => !list.includes(f)).slice(0, 3).map(t => item(t, () => speakIt(t)))];
    }, { weight: 3, sub: `They said: “${h.text.slice(0, 50)}”` }));
    return cats;
  }

  /* ------------------------------------------------------------ AI web mode (Browser Use) */
  // Sites without built-in actions: Browser Use reads the page once and offers a few meaningful actions;
  // the chosen one runs as one task, which also reads the next page. No agent call per blink or eyebrow.
  async function openWeb(url) {
    if (!T.aiwebReady) { status("AI web mode needs a Browser Use key: add BROWSER_USE_API_KEY to app/.env"); return; }
    T.aiweb = { status: "reading", url, actions: [] };
    const r = await window.shell.aiwebOpen(url);
    if (r && r.status === "error") status(r.error);
  }
  function aiwebMenu() {
    const st = T.aiweb || { status: "reading", actions: [] };
    const home = item("Back to Residual", () => leaveApp(), { weight: 1.2, sub: "Ends the AI web session" });
    if (st.status === "reading" || st.status === "running")
      return [item("Wait", () => {}, { sub: st.status === "running" ? `Browser Use is doing: ${st.doing || "your choice"}` : "Browser Use is reading the page" }), home];
    if (!st.actions || !st.actions.length)
      return [item("Try again", () => openWeb(st.url), { sub: st.error || "No actions found on this page" }), home];
    return [...st.actions.map((a, i) => item(a.label, () => { status(`Browser Use: ${a.label}…`); window.shell.aiwebDo(i); }, { weight: i === 0 ? 2 : 1 })), home];
  }

  // Inside a real app: an action layer, not the raw page. The page's meaningful actions come first (Next reel,
  // Like, Play / pause, Next video, Back to videos...), then what's on the page (videos), then everything else
  // that can be clicked, grouped under "More on this page", then scrolling and leaving.
  async function appMenu() {
    const got = ((await window.shell.pageChoices()) || []).filter(c => !(c.action && /^back$/i.test(c.label)));
    T.pageSig = got.map(c => c.label).join("|");
    T.pageLabels = got.map(c => c.label);
    const pick = c => item(c.label.replace(/^[^\w“"]+\s*/, ""), async () => afterApp(await window.shell.pagePick(c.id)),
      { pageId: c.id, weight: /^(↓\s*)?next/i.test(c.label) ? 12 : c.action ? 2 : 1 });
    const actions = got.filter(c => c.action).map(pick);
    const content = got.filter(c => c.content).map(pick);
    const other = got.filter(c => !c.action && !c.content).map(pick);
    const mus = S.active === "music" ? await musicItems() : [];
    const list = [...mus, ...(mus.length ? actions.filter(a => !/play\s*\/\s*pause/i.test(a.label)) : actions)];
    if (content.length) list.push(content.length <= 3 ? content : menu("Videos on this page", () => content, { weight: 2, sub: `${content.length} here` }));
    if (other.length) {
      if (!actions.length && !content.length) {
        // an ordinary page: its first few distinct, named links and buttons in reading order; the rest under More
        const seen = new Set(), direct = [], rest = [];
        for (const it of other) {
          const k = it.label.toLowerCase();
          if (direct.length < 6 && !seen.has(k) && !/^(link|button)$/i.test(it.label)) { seen.add(k); direct.push(it); } else rest.push(it);
        }
        list.push(...direct);
        if (rest.length) list.push(menu("More on this page", () => rest, { weight: 0.6, sub: `${rest.length} more buttons and links` }));
      } else list.push(menu("More on this page", () => other, { weight: 0.6, sub: `${other.length} buttons and links` }));
    }
    return list.flat().concat([
      item("Scroll down", async () => afterApp(await window.shell.pageCall("scroll", "down")), { weight: 0.9, sub: "Then new choices from what's on screen" }),
      item("Scroll up", async () => afterApp(await window.shell.pageCall("scroll", "up")), { weight: 0.3 }),
      item("Previous page", async () => afterApp(await window.shell.pageBack()), { weight: 0.3 }),
      item("Back to Residual", () => leaveApp(), { weight: 1.2, sub: "Close your eyes a moment also goes back" })]);
  }
  function afterApp(res) {
    if (!res) return;
    if (res.done && /moved/i.test(res.done)) { announceRefresh("The page changed under that choice"); return; }
    if (res.confirm) confirmRisky(res);
    else if (res.open === "search") openSearch(["music", "netflix", "food"].includes(res.app) ? res.app : "youtube");
    else if (res.open === "keyboard") status("Typing isn't available with two choices yet. Pick a suggestion instead.");
    else if (res.done) status(res.done);
  }

  // A sheet (a question, a countdown, a call for help) takes over until it closes.
  function sheetMenu() {
    const s = S.sheet;
    if (s.type === "countdown") return [item("Stop, don't send", () => sheetAct("select")), item("Send it", () => {})];
    if (s.type === "lifeline" || s.type === "task") return [item(s.confirmCancel ? "Yes, cancel" : "I'm okay, cancel", () => sheetAct("select")), item("Keep it going", () => {})];
    if (s.type === "keyboard") return [item("Close the keyboard", () => closeSheet()), item("Keep it open", () => {})];
    if (s.items) return s.items.map((label, i) => item(label, () => { s.focus = i; sheetAct("select"); }));
    return [item("Close", () => closeSheet(true)), item("Wait", () => {})];
  }

  /* ------------------------------------------------------------ split in two */
  // Keep the order (most likely first) and split where the two sides are closest in weight.
  const splitTwo = NavRules.splitTwo;
  const backItem = () => item("Back", () => back(), { weight: 0.5, isBack: true, sub: "One step back" });
  // Home is one pick from anywhere you've drilled into, so Escape is never the only way out.
  const homeItem = () => item("Home", () => goHome(), { weight: 0.5, isHome: true, sub: "Back to the start" });
  function withBack(list) { return T.stack.length && T.mode !== "question" ? [...list, backItem(), homeItem()] : list; }

  // Leaving an app for Home remembers where from, so an accidental Back is one choice to undo.
  function leaveApp() {
    if (S.active && S.active !== "aiweb") T.cameFrom = { id: S.active, name: appById(S.active)?.name || S.active, at: performance.now() };
    window.shell.home();
  }

  // A yes / no question from Residual itself (switching control modes). Asked as two choices.
  function ask(title, yes, no, o = {}) {
    T.question = { title, yes, no, ...o };
    tel("two-question", { title });
    recLine({ k: "question", title });
    Voice.say(o.say || title, { priority: P.CONTROL });
    T.list = null; refresh({ force: true });
  }
  async function currentList() {
    if (T.question) {
      const q = T.question, done = () => { T.question = null; };
      return { key: "question:" + q.title, title: q.title, mode: "question",
        list: [item(q.yesLabel || "Yes", () => { done(); q.yes(); }, { sub: q.yesSub || q.title }), item(q.noLabel || "Not now", () => { done(); q.no && q.no(); }, { sub: q.noSub || "" })] };
    }
    if (S.sheet) return { key: "sheet:" + S.sheet.type + ":" + (S.sheet.items || []).join("|") + (S.sheet.confirmCancel || ""), title: S.sheet.title || S.sheet.kicker || "", list: sheetMenu(), mode: "sheet" };
    if (S.active === "aiweb") return { key: "aiweb:" + (T.aiweb && T.aiweb.status) + (T.aiweb && T.aiweb.actions ? T.aiweb.actions.length : 0), title: "AI web", list: aiwebMenu(), mode: "app" };
    if (S.active) return { key: "app:" + S.active, title: appById(S.active)?.name || "", list: await appMenu(), mode: "app" };
    return { key: "home", title: "Home", list: homeMenu(), mode: "home" };
  }

  /* ------------------------------------------------------------ choosing */
  async function choose(side) {
    if (!T.split || busyNow() || T.setup) return;
    if (performance.now() - T.lastPick < 600) return;
    T.lastPick = T.navAt = performance.now();
    const group = T.split[side === "left" ? 0 : 1];
    if (!group || !group.length) return;
    flashSide(side);
    blip(side === "left" ? 620 : 780);
    tel("two-pick", { side, labels: group.map(g => g.label).slice(0, 6) });
    recLine({ k: "pick", side, labels: group.map(g => g.label).slice(0, 6) });
    if (group.length > 1) { T.stack.push(T.list); T.path.push(group.length + " options"); T.list = group.filter(g => !g.isBack && !g.isHome); draw(); return; }
    const it = group[0];
    if (it.isBack) return back();
    if (it.children) { T.stack.push(T.list); T.path.push(it.label); T.list = await it.children(); draw(); return; }
    T.busy = true; T.busyAt = performance.now();
    try { await Promise.race([it.run(), wait(15000)]); } finally { T.busy = false; }
    // after doing something, start again from the top of where we are
    const pickedAt = T.lastPick;
    T.stack = []; T.path = []; T.list = null;
    setTimeout(() => refresh({ since: pickedAt }), S.active ? 900 : 0);   // unless the person has already moved on by then
  }
  function back() {
    T.list = T.stack.pop() || null;
    T.path.pop();
    if (!T.list) return refresh({ force: true });
    draw();
  }
  // Declining a question runs its "no" (cancel the order, keep two signals...) and rebuilds the choices.
  function declineQuestion() {
    const q = T.question; T.question = null;
    try { q && q.no && q.no(); } catch (e) { /* the question's own cleanup must not trap the person */ }
    T.stack = []; T.path = []; T.list = null; refresh({ force: true });
  }
  // Home, whatever is open: a question is declined, a sheet closes (a help request still waiting for an answer stays, on purpose),
  // an app is left, and the choices start again from the top. This is what Escape does.
  function goHome() {
    if (T.setup) return;
    if (T.question) declineQuestion();
    if (S.sheet) closeSheet();
    T.stack = []; T.path = [];
    if (S.active) leaveApp(); else { T.list = null; refresh({ force: true }); }
  }
  // Back, from anywhere: one step back in the choices; at the top of an app, back to Residual's Home.
  function goBack(how) {
    const now = performance.now();
    const task = S.sheet && S.sheet.type === "task" ? S.agent.tasks[S.sheet.id] : null;
    const d = NavRules.backDecision({ how, busy: T.busy, busyAt: T.busyAt, setup: T.setup, stackLen: T.stack.length, sheetType: S.sheet && S.sheet.type,
                                     sheetDone: !!(task && TERMINAL.has(task.status)), question: !!T.question,
                                     active: S.active, now, openedAt: T.appOpenedAt || 0, lastPickAt: T.lastPick || 0, lastBackAt: T.lastBackAt || 0 });
    if (d === "ignore" || d === "stay") { if (d === "stay") status("This is Home"); return; }
    T.lastBackAt = T.navAt = now;
    if (d === "decline") { blip(520); status("Not now"); return declineQuestion(); }
    if (d === "step") { blip(520); return back(); }
    if (d === "sheet") { closeSheet(); blip(520); return; }
    if (d === "home") { blip(520); status("Back to Residual" + (how ? ` (${how})` : "")); leaveApp(); return; }
  }
  // Rebuilds the choices from what is on screen. It can take a moment (it asks the page), and the person can act meanwhile:
  // see NavRules.refreshPlan for what then wins. `force` is for a real change; `since` is when the thing that asked for it happened.
  async function refresh(o = {}) {
    const gen = ++T.gen, since = o.since ?? performance.now();
    const c = await currentList();
    const d = NavRules.refreshPlan({ gen, curGen: T.gen, force: !!o.force, navAt: T.navAt, since, depth: T.stack.length, sameKey: c.key === T.sig });
    if (d === "skip") { if (!T.list && !T.stack.length) { T.sig = c.key; T.mode = c.mode; T.title = c.title; T.list = c.list; draw(); } return; }
    T.sig = c.key; T.mode = c.mode; T.title = c.title; T.stack = []; T.path = [];
    T.list = c.list;
    draw();
  }

  /* ------------------------------------------------------------ drawing */
  function card(side, group) {
    const el = $("#two-" + side), sc = scheme();
    const rightName = isFace() && prefs().browTwice ? "Raise eyebrows twice" : sc.rightName;
    const how = scanOn() ? `<div class="two-how"><span class="two-hint">${side === "left" ? "Lights up first" : "Lights up second"}</span></div>`
      : `<div class="two-how"><span class="two-ic">${ICONS[side === "left" ? sc.leftIcon : sc.rightIcon]}</span><span class="two-hint">${side === "left" ? sc.leftName : rightName}</span></div>`;
    if (!group || !group.length) { el.innerHTML = how; return; }
    const one = group.length === 1, g0 = group[0];
    const face = (a, big) => a ? `<i class="av${big ? " big" : ""}" style="background:${a.bg};color:${a.ink}">${escH(a.ch)}</i>` : "";
    el.innerHTML = `${how}<div class="two-body">
      ${one ? `${face(g0.avatar, true)}<h2>${escH(g0.label)}${g0.children ? '<span class="two-more">›</span>' : ""}</h2>${g0.sub ? `<p class="two-sub">${escH(g0.sub)}</p>` : ""}`
            : `<p class="two-count">${group.length} options</p><ul>${group.slice(0, 6).map(g => `<li${g.avatar ? ' class="face"' : ""}>${face(g.avatar)}${escH(g.label)}</li>`).join("")}</ul>${group.length > 6 ? `<p class="two-sub">and ${group.length - 6} more</p>` : ""}`}
      </div><div class="two-state"></div><i class="two-fill"></i>`;
  }
  // Who is around, for Home's greeting. Asked of the care circle at most every 30 s, and only redrawn when it changes.
  let circleAt = 0;
  async function refreshCircleLine() {
    if (performance.now() - circleAt < 12000) return;       // an agent that isn't running just answers with nobody
    circleAt = performance.now();
    try {
      const c = await window.shell.circle();
      const line = HomeView.circleLine(c && c.members);
      if (line !== T.circleLine) { T.circleLine = line; if (T.mode === "home" && !T.path.length) draw(); }
    } catch (e) { /* the greeting is fine without it */ }
  }
  // A question (confirm an order, switch to one signal, the last step of an errand) is a hero sentence with two choices along
  // the bottom. Over a website it keeps the compact cards.
  function drawAsk() {
    const q = T.mode === "question" && T.question && !S.active ? T.question : null;
    document.body.classList.toggle("two-ask-on", !!q);
    const el = $("#two-ask");
    if (!el) return;
    if (!q) { el.hidden = true; el.innerHTML = ""; return; }
    const p = HomeView.askParts(q);
    el.hidden = false;
    el.className = q.visual ? "has-visual" : "";
    el.innerHTML = `<div class="ask-text"><p class="ask-kicker">${escH(p.kicker)}</p><h1 style="font-size:${HomeView.heroSize(p.hero)}px">${escH(p.hero)}</h1>${p.body ? `<p class="ask-body">${escH(p.body)}</p>` : ""}</div>`
      + (q.visual ? `<div class="ask-visual"><p class="ask-cap">${escH(q.visualCaption || "")}</p>${q.visual}</div>` : "");
  }
  function draw() {
    if (!T.on || !T.list) return;
    let list = withBack(T.list);
    if (list.length === 1) list = [list[0], backItem()];
    T.split = NavRules.splitWithNav(list);
    card("left", T.split[0]);
    card("right", T.split[1]);
    const crumbs = [T.mode === "question" ? T.title : T.mode === "app" ? (appById(S.active)?.name || "App") : T.mode === "sheet" ? (S.sheet?.kicker || "Question") : "Home", ...T.path];
    const atHome = T.mode === "home" && !T.path.length;
    document.body.classList.toggle("two-home", atHome);
    if (atHome) {
      $("#two-title").innerHTML = `<span class="greet">${escH(HomeView.greeting(new Date(), CFG.person))}</span><span class="greet-sub">${escH(T.circleLine || "")}</span>`;
      refreshCircleLine();
    } else {
      $("#two-title").innerHTML = crumbs.map((c, i) => `<span class="${i === crumbs.length - 1 ? "cur" : ""}">${escH(c)}</span>`).join('<i>›</i>');
    }
    drawAsk();
    if (S.active && !S.sheet && T.mode === "app") {
      const ids = g => g.filter(x => x.pageId !== undefined).map(x => x.pageId);
      window.shell.pageMark(ids(T.split[0]), ids(T.split[1]));
    }
    dockSync(true);
  }
  function flashSide(side) { const el = $("#two-" + side); el.classList.add("picked"); dockSync(true); setTimeout(() => { el.classList.remove("picked"); dockSync(true); }, 380); }
  // On a website the choices float over the page in their own view (main.js "dock"): mirror the cards there.
  let dockLast = "", dockAt = 0;
  function dockSync(force) {
    const on = !!(T.on && S.active && !S.sheet && T.mode === "app" && !T.setup);
    const now = performance.now();
    if (!force && now - dockAt < 70) return;
    dockAt = now;
    const side = id => { const el = $("#two-" + id); return { html: el.innerHTML, cls: [...el.classList].filter(c => ["looking", "dim", "picked"].includes(c)).join(" "),
      text: el.querySelector(".two-state")?.textContent || "", fill: el.querySelector(".two-fill")?.style.width || "0" }; };
    const l = on ? side("left") : null, r = on ? side("right") : null;
    const st = on ? { on, leftHTML: l.html, rightHTML: r.html, leftClass: l.cls, rightClass: r.cls, left: { text: l.text, fill: l.fill }, right: { text: r.text, fill: r.fill }, large: prefs().largeText } : { on: false };
    const key = JSON.stringify(st);
    if (key === dockLast) return;
    dockLast = key;
    window.shell.dock && window.shell.dock(st);
  }
  function setLarge(on) {
    setPref("largeText", on);
    document.body.classList.toggle("two-large", on);
    window.shell.largeText && window.shell.largeText(on);
    dockSync(true);
  }
  function announceRefresh(why) {
    T.list = null; refresh({ force: true });
    status(`${why ? why + ". " : ""}Choices refreshed`);
    Voice.say("Choices refreshed", { priority: P.AMBIENT });
    blip(440);
    tel("two-refresh", { why: why || "" });
  }
  let actx2 = null;
  function blip(f) { try { actx2 = actx2 || new AudioContext(); const o = actx2.createOscillator(), g = actx2.createGain(); o.frequency.value = f; g.gain.setValueAtTime(0.15, actx2.currentTime); g.gain.exponentialRampToValueAtTime(0.0001, actx2.currentTime + 0.12); o.connect(g).connect(actx2.destination); o.start(); o.stop(actx2.currentTime + 0.13); } catch (e) {} }

  // Live feedback on each side: what the face is doing right now.
  function feedback() {
    requestAnimationFrame(feedback);
    const tr = T.tracker;
    const camEl = $("#two-cam-state");
    if (!tr) return;
    const face = tr.last.face?.face;
    camEl.textContent = tr.cameraError ? "Camera problem" : !tr.webcam || tr.webcam.status !== "running" ? "Starting camera" : face ? "Face found" : "Can't see your face";
    camEl.className = tr.cameraError || (tr.webcam?.status === "running" && !face) ? "bad" : face ? "ok" : "";
    $("#two-camerr").hidden = !tr.cameraError;
    if (tr.cameraError) $("#two-camerr span").textContent = tr.cameraError;
    if (T.setup) return;
    const sc = scheme(), sw = tr.switches, d = tr.dirs;
    const L = $("#two-left .two-state"), R = $("#two-right .two-state");
    const lf = $("#two-left .two-fill"), rf = $("#two-right .two-fill");
    let lOn = false, rOn = false;
    const aw = S.active === "aiweb" && T.aiweb;
    $("#two-mode").textContent = aw && aw.busySince ? `AI web mode · Browser Use working ${Math.round((Date.now() - aw.busySince) / 1000)} s`
      : aw ? "AI web mode · Browser Use" : scanOn() ? "One signal" : "Two signals";
    $("#two-mode").classList.toggle("ai", !!aw);
    document.body.classList.toggle("two-scan", scanOn());
    $("#two-mode").classList.toggle("scan", scanOn());
    if (scanOn()) {
      const lit = T.scanSide, how = prefs().oneSignal === "brow" ? "Raise eyebrows" : "Blink twice";
      for (const side of ["left", "right"]) {
        const el = $("#two-" + side); el.classList.toggle("looking", side === lit); el.classList.toggle("dim", side !== lit);
        const hint = el.querySelector(".two-hint"); if (hint && hint.dataset.scan !== String(side === lit)) { hint.dataset.scan = String(side === lit); hint.textContent = side === lit ? "Lit now" : "Lights up next"; }
        const st = el.querySelector(".two-state"); if (st) st.textContent = side === lit ? `${how} to pick this` : "";
        const f = el.querySelector(".two-fill"); if (f) f.style.width = side === lit ? Math.min(100, (performance.now() - scanT) / prefs().scanMs * 100) + "%" : "0";
      }
      dockSync();
      return;
    }
    $("#two-left").classList.remove("dim"); $("#two-right").classList.remove("dim");
    if (isFace()) {
      const waiting = sw.firstBlinkAt !== null;
      lOn = waiting || sw.closedSince !== null;
      if (L) L.textContent = waiting ? "Blink once more" : "";
      rOn = sw.state.brow === "gesture" || (T.browFirst && performance.now() - T.browFirst < 1400);
      if (R) R.textContent = T.browFirst && performance.now() - T.browFirst < 1400 ? "Raise once more" : sw.state.brow === "wait_release" ? "Relax your eyebrows" : "";
      if (lf) lf.style.width = waiting ? "50%" : "0";
      if (rf) rf.style.width = rOn ? "100%" : "0";
    } else if (isWink()) {
      const waiting = sw.firstBlinkAt !== null, rn = sc.right;
      lOn = waiting; rOn = sw.state[rn] === "gesture";
      if (L) L.textContent = waiting ? "Blink once more" : "";
      if (R) R.textContent = sw.state[rn] === "wait_release" ? "Open both eyes" : "";
      if (lf) lf.style.width = waiting ? "50%" : "0";
      if (rf) rf.style.width = rOn ? "100%" : "0";
    } else {
      lOn = d.pending === "left"; rOn = d.pending === "right";
      if (lf) lf.style.width = (lOn ? d.progress * 100 : 0) + "%";
      if (rf) rf.style.width = (rOn ? d.progress * 100 : 0) + "%";
      if (L) L.textContent = d.state === "wait_release" ? "Back to the middle" : "";
      if (R) R.textContent = d.state === "wait_release" ? "Back to the middle" : "";
    }
    $("#two-left").classList.toggle("looking", lOn);
    $("#two-right").classList.toggle("looking", rOn);
    dockSync();
  }

  /* ------------------------------------------------------------ eyes */
  async function startEyes() {
    try {
      const base = await window.shell.eyesBase();
      T.eyes = await import(base + "residual-eyes.js");
      const settings = { ...T.eyes.DEFAULTS, pointer: "directions", enabled: [], lookHoldMs: 900, speak: false };
      T.tracker = new T.eyes.Tracker(settings);
      Object.assign(T.tracker.dirs.o, { enter: 0.45, leave: 0.2 });
      T.tracker.switches.o.browHoldMs = 200;   // a deliberate raise, not a flicker of expression
      applyScheme();
      // "close your eyes a moment" (0.6 to 1 s) = Back. Help needs 1.5 s closures: see BACK_MS in residual-eyes.
      T.tracker.switches.o.longBlinkMinMs = T.eyes.BACK_MS.min;
      T.tracker.switches.o.longBlinkMaxMs = T.eyes.BACK_MS.max;
      T.tracker.on(e => {
        if (e.type === "input") {
          if (T.onInput) T.onInput(e.input);   // the setup's "try it" step
          onGesture(e.input);
        } else if (e.type === "help-signal") { tel("two-help", {}); recLine({ k: "help" }); startHelp("general", true); }   // three long closures: always on
      });
      T.tracker.onFrame(f => { watchSignal(); T.lastLandmarks = f.landmarks; recFrame(); });
      labFeed();
      await T.tracker.start(base, false);
      // the switches and the help pattern listen whatever is enabled for choosing
      T.tracker.settings.enabled = [...T.tracker.settings.enabled];
      const p = T.tracker.profile;
      if (!localStorage.getItem("residual.two.setupDone") || !p.measured) openSetup(0);
      feedback();
    } catch (e) {
      status("The face tracker didn't start: " + (e.message || e) + ". Arrow keys still work.");
    }
  }
  // A gesture -> left / right / back. Eyebrows can be set to "twice"; in one-signal mode the one working
  // gesture picks whichever side is lit.
  function decode(inp) {
    if (inp === "longBlink") return "back";
    const sc = scheme(), p = prefs();
    if (scanOn()) return inp === p.oneSignal || (schemeName() === "glance" && (inp === "lookLeft" || inp === "lookRight")) ? "scan" : null;
    if (isFace() && inp === "brow" && p.browTwice) {
      const now = performance.now();
      if (T.browFirst && now - T.browFirst < 1400) { T.browFirst = null; return "right"; }
      T.browFirst = now;
      return null;
    }
    return inp === sc.left ? "left" : inp === sc.right ? "right" : null;
  }
  function onGesture(inp) {
    const g = decode(inp);
    T.lastGestureAt = performance.now();
    recLine({ k: "gesture", input: inp, as: g || "ignored", where: T.setup ? "setup" : T.paused ? "paused" : "use" });
    tel("two-gesture", { input: inp, as: g || "ignored", mode: scanOn() ? "one" : "two", lit: scanOn() ? T.scanSide : null,
                         where: T.setup ? "setup" : T.measureHook ? "measuring" : T.paused ? "paused" : "use" });
    if (!g) return;
    if (T.measureHook) return T.measureHook(g === "scan" ? T.scanSide : g, inp);
    if (T.setup || T.paused) return;
    if (g === "back") return goBack("eyes closed");
    choose(g === "scan" ? T.scanSide : g);
  }

  /* ------------------------------------------------------------ one signal: scanning */
  // When only one movement is reliable, the two sides light up in turn and that movement picks the lit one.
  T.scanSide = "left";
  let scanT = 0;
  setInterval(() => {
    if (!T.on || !scanOn() || busyNow() || T.setup || T.paused) return;
    if (performance.now() - scanT > prefs().scanMs) { T.scanSide = T.scanSide === "left" ? "right" : "left"; scanT = performance.now(); }
  }, 100);
  function setSignals(mode, why) {
    const p = prefs();
    if (p.signals === mode) return;
    setPref("signals", mode);
    T.autoSwitched = !!(why && why.startsWith("your"));
    const msg = mode === "one"
      ? `One signal now${why ? ` (${why})` : ""}. The sides light up in turn: ${p.oneSignal === "brow" ? "raise your eyebrows" : "blink twice"} when the one you want is lit.`
      : `Two signals again${why ? ` (${why})` : ""}. Blink twice for left, eyebrows for right.`;
    banner(msg, 9000);
    Voice.say(mode === "one" ? "Switching to one signal." : "Two signals again.", { priority: P.CONTROL });
    tel("two-signals", { mode, why: why || "" });
    recLine({ k: "signals", mode, why: why || "" });
    scanT = performance.now(); T.scanSide = "left";
    draw();
  }

  // Watching how readable the RIGHT-side signal (eyebrows, or a wink) is. Every frame goes to a SignalQuality
  // (ui/quality.js): each attempt's peak against this person's own thresholds, near misses, retries after a
  // near miss, timing consistency, and whether the face was even visible. It keeps watching in one-signal
  // mode too, so it can tell when the signal is reliable again. It never switches anything: it ASKS.
  T.q = new Quality.SignalQuality("eyebrow");
  T.declinedUntil = 0;
  function watchSignal() {
    const tr = T.tracker, f = tr && tr.last.face;
    if (!f || T.setup || !rightSignal()) return;
    const L = tr.switches.levels, th = T.eyes.thresholds;
    let value, t, learned;
    if (isWink()) {
      const eye = (prefs().wink || {}).eye === "L" ? "L" : "R";
      const lv = eye === "L" ? L.eyeL : L.eyeR, other = eye === "L" ? L.eyeR : L.eyeL;
      // only one eye closing counts as a wink attempt; both closing is a blink
      const v = eye === "L" ? f.blinkL : f.blinkR, o = eye === "L" ? f.blinkR : f.blinkL;
      value = o < th(other).off ? v : lv.rest; t = th(lv); learned = lv.peak;
    } else { value = f.brow; t = th(L.brow); learned = L.brow.peak; }
    const att = T.q.update(f.t, f.face, value, t, learned);
    if (att) { recLine({ k: "attempt", signal: T.q.name, ...att }); adaptCheck(); }
  }
  function adaptCheck() {
    const p = prefs();
    if (!p.adapt || !rightSignal() || T.question || T.setup) return;
    const a = T.q.assess();
    T.qLast = a;
    const sug = Quality.suggestion(p.signals, a.verdict, T.declinedUntil, performance.now(), T.q.name);
    if (!sug) return;
    T.adaptWhy = a.reasons;
    tel("two-adapt", { to: sug.to, reasons: a.reasons, stats: a.stats });
    ask(sug.text, () => {
      if (sug.to === "one") { setPref("oneSignal", "doubleBlink"); setSignals("one", `your ${T.q.name} signal got harder to read`); }
      else setSignals("two", `your ${T.q.name} signal is clear again`);
      T.q.reset();
    }, () => { T.declinedUntil = performance.now() + 5 * 60000; T.q.reset(); },
    (() => {
      const up = sug.to === "one", bars = HomeView.attemptBars(T.q.attempts), why = (a.reasons[0] || "").replace(/^./, c => c.toUpperCase());
      return { kicker: "Residual noticed", hero: up ? "Switch to one signal?" : "Bring back two signals?", say: sug.text,
        body: up ? `${why ? why + ". " : ""}One signal means the choices light up in turn, and you blink twice to pick the lit one.`
                 : `${why ? why + ". " : ""}Two signals are faster: blink twice for the left choice, raise your eyebrows for the right.`,
        visual: bars.bars.length ? HomeView.chartHTML(bars) : "", visualCaption: `Your ${T.q.name} attempts`,
        yesLabel: up ? "Yes, one signal" : "Yes, two signals", yesSub: up ? "Choices light up in turn" : "Blink twice, or raise eyebrows",
        noLabel: "Not yet", noSub: "Keep things as they are" };
    })());
  }
  // Demo only, labelled on screen the whole time it's on: weakens the eyebrow signal before the switches see it.
  function simulateWeak() {
    const tr = T.tracker; if (!tr) return;
    tr.browGain = tr.browGain < 1 ? 1 : 0.55;
    document.body.classList.toggle("two-sim", tr.browGain < 1);
    status(tr.browGain < 1 ? "SIMULATED: eyebrows weakened for the demo" : "Simulation off");
  }

  /* ------------------------------------------------------------ test my controls */
  // Prompted trials (20 blink twice, 20 of the right-side signal, 10 Back, 10 of each learned fallback), then
  // 2 minutes of natural behaviour where anything detected is a false command. Scored by ui/evaluate.js and
  // saved as JSON on this computer. These are the only numbers Residual reports about itself.
  const NAMES = { doubleBlink: "Blink twice", brow: "Raise eyebrows", winkLeft: "Wink your left eye", winkRight: "Wink your right eye",
                  longBlink: "Close your eyes a moment", lookLeft: "Look left and hold", lookRight: "Look right and hold" };
  function calibrated() {
    const p = prefs(), d = T.tracker && T.tracker.profile.dirs;
    return { wink: p.wink && p.wink.ok ? p.wink : null, glances: d && d.ok && d.ok.h ? d : null };
  }
  function testGestures() {
    const sc = scheme(), cal = calibrated(), g = ["doubleBlink"];
    if (schemeName() !== "glance") g.push(sc.right);
    g.push("longBlink");
    if (cal.wink && !g.includes(cal.wink.eye === "L" ? "winkLeft" : "winkRight")) g.push(cal.wink.eye === "L" ? "winkLeft" : "winkRight");
    if (isFace() === false && !g.includes("brow")) g.push("brow");
    if (cal.glances) g.push("lookLeft", "lookRight");
    return g;
  }
  async function testControls() {
    if (!T.tracker || T.setup) return;
    T.setup = true;
    const ov = $("#two-setup"); ov.hidden = false;
    const box = $("#two-setup-body"), tr = T.tracker;
    const gestures = testGestures(), kinds = Evaluate.plan(gestures);
    const prevEnabled = tr.settings.enabled;
    tr.settings.enabled = [...gestures];
    let stop = false, listen = null;
    T.onInput = inp => listen && listen(inp);
    const show = (h, sub = "", big = "") => {
      box.innerHTML = `<h1>${escH(h)}</h1>${big ? `<div class="two-prompt">${big}</div>` : ""}<p>${escH(sub)}</p><div class="two-btns"><button id="two-m-stop">Stop</button></div>`;
      $("#two-m-stop").onclick = () => { stop = true; if (listen && listen.cancel) listen.cancel(); };
    };
    show("Test my controls", `${kinds.length} prompts (${gestures.map(g => `${NAMES[g]} ×${kinds.filter(k => k === g).length}`).join(", ")}), then 2 minutes of talking and looking around normally while I count anything picked by accident.`);
    Voice.say("Testing your controls. Do each gesture when it appears.", { priority: P.CONTROL });
    await wait(4500);
    const trials = [];
    for (let i = 0; i < kinds.length && !stop; i++) {
      const want = kinds[i];
      show(`${i + 1} of ${kinds.length}`, "Now.", escH(NAMES[want]));
      blip(700);
      const t0 = performance.now();
      const got = await new Promise(res => {
        const to = setTimeout(() => { listen = null; res(null); }, 6000);
        listen = inp => { clearTimeout(to); listen = null; res({ inp, ms: Math.round(performance.now() - t0) }); };
        listen.cancel = () => { clearTimeout(to); listen = null; res(null); };
      });
      if (stop && !got) break;   // the prompt that Stop interrupted isn't a miss
      trials.push({ want, got: got ? got.inp : null, ms: got ? got.ms : null });
      recLine({ k: "test-trial", want, got: got ? got.inp : null, ms: got ? got.ms : null });
      show(`${i + 1} of ${kinds.length}`, "Relax.", got ? (got.inp === want ? '<span class="ok">✓</span>' : `<span class="bad">✕ ${escH(NAMES[got.inp] || got.inp)}</span>`) : '<span class="bad">Missed</span>');
      await wait(1500);
    }
    const natural = [], NATURAL_MS = 120000;
    let naturalMs = 0;
    if (!stop) {
      Voice.say("Now two minutes of normal behaviour. Talk, look around, blink normally. Don't do the gestures.", { priority: P.CONTROL });
      const t0 = performance.now();
      listen = function rec(inp) { natural.push({ input: inp, at: Math.round(performance.now() - t0) }); recLine({ k: "test-false", input: inp }); listen = rec; };
      while (!stop && performance.now() - t0 < NATURAL_MS) {
        const left = Math.ceil((NATURAL_MS - (performance.now() - t0)) / 1000);
        show(`Normal behaviour: ${left} s`, "Talk, look around the room, read something, blink normally. Don't do any of the gestures.", `<span class="two-small">${natural.length} detected by accident so far</span>`);
        await wait(1000);
      }
      naturalMs = Math.min(NATURAL_MS, Math.round(performance.now() - t0));
      listen = null;
    }
    T.onInput = null;
    tr.settings.enabled = prevEnabled;
    const r = Evaluate.score(trials, natural, naturalMs);
    const data = { kind: "test-my-controls", at: new Date().toISOString(), controls: schemeName(), signals: prefs().signals, gestures,
                   levels: tr.profile.levels, browHoldMs: tr.switches.o.browHoldMs, backMs: T.eyes.BACK_MS, stoppedEarly: stop,
                   simulatedWeakening: tr.browGain < 1, ...r, trials, natural };
    let file = "";
    try { file = await window.shell.saveMetrics(data); } catch (e) {}
    T.lastTest = { at: data.at, falsePerMinute: r.falsePerMinute, perGesture: r.perGesture };
    tel("two-test", T.lastTest);
    const row = g => { const x = r.perGesture[g]; return `<tr><td>${escH(NAMES[g] || g)}</td><td>${x.prompted}</td><td>${x.correct}</td><td>${x.missed}</td><td>${x.wrong}</td><td>${x.medianLatencyMs != null ? x.medianLatencyMs + " ms" : "."}</td><td>${x.falseActivations}</td></tr>`; };
    box.innerHTML = `<h1>Your controls</h1>
      <table class="two-table"><tr><th>Gesture</th><th>Prompted</th><th>Detected</th><th>Missed</th><th>Wrong command</th><th>Median response</th><th>False in 2 min</th></tr>${Object.keys(r.perGesture).map(row).join("")}</table>
      <p>${naturalMs ? `False commands during normal behaviour: <b>${r.falseCommands} in ${r.naturalSeconds} s (${r.falsePerMinute} per minute)</b>` : "Stopped before the normal behaviour part, so no false command rate."}</p>
      ${tr.browGain < 1 ? '<p class="bad">SIMULATED weakening was on during this test.</p>' : ""}
      ${file ? `<p class="two-small">Saved on this computer: ${escH(file)}</p>` : ""}
      <div class="two-btns"><button id="two-m-again">Again</button>${file ? '<button id="two-m-show">Show the file</button>' : ""}<button class="primary" id="two-m-done">Done</button></div>`;
    $("#two-m-again").onclick = () => { T.setup = false; testControls(); };
    if (file) $("#two-m-show").onclick = () => window.shell.showFile(file);
    $("#two-m-done").onclick = () => { T.setup = false; ov.hidden = true; refresh({ force: true }); };
  }
  const measure = testControls;

  /* ------------------------------------------------------------ session recording (asks first) */
  // Off by default. Numbers only: eye closure and eyebrow levels per frame, the gaze position numbers, every
  // gesture, thresholds, choices and control-mode decisions. No camera pictures, no sound, no message text.
  // Saved as JSON lines on this computer; Stop ends the file, Delete removes all recordings.
  const Rec = { on: false, buf: [], file: null, n: 0 };
  function recLine(o) { if (Rec.on) Rec.buf.push({ t: Math.round(performance.now()), ...o }); }
  function recFrame() {
    if (!Rec.on) return;
    const f = T.tracker.last.face, e = T.tracker.last.eye;
    if (!f) return;
    Rec.buf.push({ t: Math.round(f.t), k: "f", face: f.face ? 1 : 0, bl: +f.blinkL.toFixed(3), br: +f.blinkR.toFixed(3), brow: +f.brow.toFixed(3),
                   ...(e ? { eh: +e.h.toFixed(4), ev: +e.v.toFixed(4) } : {}) });
  }
  setInterval(() => { if (Rec.on && Rec.buf.length) { Rec.n += Rec.buf.length; window.shell.recAppend(Rec.buf.splice(0)); } }, 2000);
  function recConsent() {
    if (!T.tracker || Rec.on) return;
    T.setup = true;
    const ov = $("#two-setup"); ov.hidden = false;
    const box = $("#two-setup-body");
    box.innerHTML = `<h1>Record this session?</h1>
      <p>Only on this computer, in Residual's data folder. Nothing is uploaded.</p>
      <p><b>Recorded:</b> numbers only. How closed each eye is and how raised the eyebrows are, about 30 times a second; the eye position numbers used for glances; every gesture Residual detects; your thresholds; the choices made; and any change between one and two signals.</p>
      <p><b>Not recorded:</b> camera pictures or video, sound, or the text of any message.</p>
      <p>You can stop at any time (Settings, Stop recording) and delete every recording (Settings, Delete recordings).</p>
      <div id="two-rec-pick"></div>`;
    Voice.say("Record this session? Numbers only, no pictures. Close your eyes when your choice lights up.", { priority: P.CONTROL });
    chooser($("#two-rec-pick"), [{ label: "Start recording", run: () => recStart() }, { label: "Don't record", run: () => { T.setup = false; ov.hidden = true; refresh({ force: true }); } }]);
  }
  async function recStart() {
    const tr = T.tracker;
    Rec.file = await window.shell.recStart({ at: new Date().toISOString(), consent: "numbers only, no video", controls: schemeName(), prefs: prefs(),
      levels: tr.profile.levels, thresholds: { eyeL: T.eyes.thresholds(tr.profile.levels.eyeL), eyeR: T.eyes.thresholds(tr.profile.levels.eyeR), brow: T.eyes.thresholds(tr.profile.levels.brow) },
      backMs: T.eyes.BACK_MS, helpMs: T.eyes.HELP_PATTERN, dirs: tr.profile.dirs });
    Rec.on = true; Rec.n = 0;
    T.setup = false; $("#two-setup").hidden = true;
    document.body.classList.add("two-rec");
    status("Recording this session (numbers only)");
    tel("two-rec", { on: true, file: Rec.file });
    refresh({ force: true });
  }
  async function recStop() {
    if (!Rec.on) return;
    if (Rec.buf.length) await window.shell.recAppend(Rec.buf.splice(0));
    Rec.on = false;
    const f = await window.shell.recStop();
    document.body.classList.remove("two-rec");
    status(`Recording stopped. Saved: ${f}`);
    tel("two-rec", { on: false, file: f });
    refresh({ force: true });
  }
  async function recDelete() {
    if (Rec.on) { Rec.on = false; Rec.buf = []; document.body.classList.remove("two-rec"); }
    const n = await window.shell.recDelete();
    status(n ? `Deleted ${n} recording${n > 1 ? "s" : ""}` : "There were no recordings");
    tel("two-rec", { on: false, deleted: n });
  }

  /* ------------------------------------------------------------ the Lab's live feed */
  // While the Lab is open: what the tracker sees (~10 a second) and a small camera picture (~5 a second).
  function labFeed() {
    let open = false;
    window.shell.onLabOpen && window.shell.onLabOpen(v => (open = v));
    window.shell.labIsOpen && window.shell.labIsOpen().then(v => (open = !!v));
    const fc = document.createElement("canvas"); fc.width = 320; fc.height = 180;
    const g = fc.getContext("2d");
    const PTS = [33, 133, 159, 145, 263, 362, 386, 374, 468, 473, 70, 63, 105, 66, 107, 300, 293, 334, 296, 336];
    setInterval(() => {
      const tr = T.tracker;
      if (!open || !tr) return;
      const f = tr.last.face, sw = tr.switches, L = sw.levels, th = T.eyes.thresholds, d = tr.last.detail;
      const now = f ? f.t : performance.now();
      tel("two-state", {
        face: !!(f && f.face), fps: Math.round(tr.fps), cameraError: tr.cameraError, status: tr.webcam ? tr.webcam.status : "off",
        eye: f ? Math.min(f.blinkL, f.blinkR) : null, blinkL: f ? f.blinkL : null, blinkR: f ? f.blinkR : null, brow: f ? f.brow : null,
        th: { eyeOn: Math.min(th(L.eyeL).on, th(L.eyeR).on), eyeOff: Math.min(th(L.eyeL).off, th(L.eyeR).off), browOn: th(L.brow).on, browOff: th(L.brow).off },
        closedMs: sw.closedSince !== null && sw.closedSince !== undefined ? Math.round(now - sw.closedSince) : 0,
        firstBlink: sw.firstBlinkAt !== null, browState: sw.state.brow,
        help: d ? d.help.counted : 0, backMs: T.eyes.BACK_MS, helpMs: T.eyes.HELP_PATTERN,
        signals: prefs().signals, lit: T.scanSide, prefs: prefs(), scheme: isFace() ? "blink + eyebrows" : "glances",
        crumbs: $("#two-title") ? $("#two-title").innerText.replace(/\n/g, " ") : "",
        left: T.split ? T.split[0].map(x => x.label) : [], right: T.split ? T.split[1].map(x => x.label) : [],
        paused: T.paused, setup: T.setup, simulated: tr.browGain < 1, episodes: T.q.attempts.slice(-12),
        quality: (() => { const a = T.q.assess(); return { name: T.q.name, verdict: a.verdict, reasons: a.reasons, stats: a.stats }; })(),
        adaptWhy: T.adaptWhy || null, recording: Rec.on, controls: schemeName(), wink: prefs().wink || null,
        glances: tr.profile.dirs && tr.profile.dirs.ok ? { ok: tr.profile.dirs.ok.h, left: tr.profile.dirs.left, right: tr.profile.dirs.right, up: tr.profile.dirs.up } : null,
        winkL: f ? f.blinkL - f.blinkR : null, gaze: tr.dirs.pos || null, lastTest: T.lastTest || null,
        question: T.question ? T.question.title : null, aiweb: S.active === "aiweb" ? T.aiweb : null, active: S.active || null
      });
    }, 110);
    setInterval(() => {
      const v = T.tracker && T.tracker.webcam && T.tracker.webcam.video;
      if (!open || !v || !v.videoWidth) return;
      g.save(); g.translate(fc.width, 0); g.scale(-1, 1);
      g.drawImage(v, 0, 0, fc.width, fc.height);
      const lm = T.lastLandmarks;
      if (lm) { g.fillStyle = "#4fd1a5"; for (const i of PTS) { const p = lm[i]; if (p) g.fillRect(p.x * fc.width - 1.5, p.y * fc.height - 1.5, 3, 3); } }
      g.restore();
      tel("two-frame", { jpg: fc.toDataURL("image/jpeg", 0.55) });
    }, 220);
  }

  function pauseEyes() {
    T.paused = !T.paused;
    status(T.paused ? "Eyes paused. A helper can press P, or click Resume." : "Eyes on");
    $("#two-pause").textContent = T.paused ? "Resume eyes" : "Pause eyes";
  }

  /* ------------------------------------------------------------ setup: camera, learn, try, extras */
  // Usable alone. Every screen moves on by itself when things work (with a spoken countdown). When a choice is
  // needed (Retry / Continue / Skip) the options light up in turn and closing your eyes for a moment picks the
  // lit one. Before your face is learned that uses general eye levels, which work for most people. A helper can
  // always click or use the keyboard instead.
  const STEPS = ["Camera", "Learn your face", "Try it", "Extras"];
  let setupStep = 0, setupGen = 0;
  let vbox = null;
  const vidBox = () => vbox || (vbox = $("#two-vid"));
  const sayS = (text, el) => { if (el) el.textContent = text; Voice.say(text, { priority: P.CONTROL }); };
  function openSetup(step = 0, extra = null) {
    if (!T.tracker) return;
    T.setup = true; setupStep = step; T.setupExtra = extra;
    $("#two-setup").hidden = false;
    const v = T.tracker.webcam?.video, box = vidBox();
    if (v) { v.className = "two-video"; v.muted = true; if (v.parentElement !== box) box.prepend(v); v.play().catch(() => {}); }
    if (!box.querySelector("canvas")) { box.insertAdjacentHTML("beforeend", '<canvas class="two-overlay"></canvas>'); drawOverlay(); }
    // setup hears every gesture (nothing is acted on while it's open)
    T.tracker.settings.enabled = ["doubleBlink", "brow", "longBlink", "winkLeft", "winkRight", "lookLeft", "lookRight"];
    T.onInput = setupInput;
    renderSetup();
  }
  function closeSetup() {
    setupGen++;
    T.setup = false; T.onInput = null; T.setupPick = null; T.tryHook = null;
    $("#two-setup").hidden = true;
    try { localStorage.setItem("residual.two.setupDone", "1"); } catch (e) {}
    const v = T.tracker?.webcam?.video;
    if (v && v.parentElement) v.remove();   // keeps running (the provider resumes it)
    applyScheme();
    Voice.say("Setup done.", { priority: P.CONTROL });
    refresh({ force: true });
  }
  function setupInput(inp) {
    if (inp === "longBlink" && T.setupPick) return T.setupPick();
    if (T.tryHook) T.tryHook(inp);
  }
  function stepsBar() {
    return `<ol class="two-steps">${STEPS.map((s, i) => `<li class="${i === setupStep ? "cur" : i < setupStep ? "done" : ""}">${s}</li>`).join("")}</ol>`;
  }
  // Choices, lit in turn; closing your eyes a moment picks the lit one; a click or Enter works too.
  function chooser(el, options, { periodMs = 3200 } = {}) {
    const gen = setupGen;
    el.innerHTML = `<div class="two-scan">${options.map((o, i) => `<button data-i="${i}"><span>${escH(o.label)}</span><i></i></button>`).join("")}</div>
      <p class="two-small">Close your eyes for a moment when your choice is lit.</p>`;
    let i = 0, t0 = performance.now(), done = false;
    const btns = [...el.querySelectorAll(".two-scan button")];
    const pick = k => { if (done || gen !== setupGen) return; done = true; T.setupPick = null; blip(880); options[k].run(); };
    btns.forEach((b, k) => (b.onclick = () => pick(k)));
    T.setupPick = () => pick(i);
    const onKey = e => { if (e.key === "Enter" && !done) { e.preventDefault(); pick(i); } };
    addEventListener("keydown", onKey);
    const tick = () => {
      if (done || gen !== setupGen || !T.setup) { removeEventListener("keydown", onKey); if (gen !== setupGen && T.setupPick) T.setupPick = null; return; }
      const now = performance.now();
      if (now - t0 > periodMs) { i = (i + 1) % options.length; t0 = now; }
      btns.forEach((b, k) => { b.classList.toggle("lit", k === i); b.querySelector("i").style.width = k === i ? Math.min(100, (now - t0) / periodMs * 100) + "%" : "0"; });
      requestAnimationFrame(tick);
    };
    tick();
  }
  // "Continuing in 3", spoken once, cancelled if the screen changes or ok() stops being true.
  function countdown(el, secs, text, then, ok = () => true) {
    const gen = setupGen;
    let n = secs;
    Voice.say(text, { priority: P.CONTROL });
    const step = () => {
      if (gen !== setupGen || !T.setup) return;
      if (!ok()) { el.innerHTML = ""; el.dataset.cd = ""; return; }
      if (n <= 0) { el.dataset.cd = ""; return then(); }
      el.innerHTML = `<div class="two-cd"><b>${n}</b><span>${escH(text)}</span></div>`;
      n--; setTimeout(step, 1000);
    };
    el.dataset.cd = "1";
    step();
  }
  const goStep = k => { setupStep = k; renderSetup(); };
  function chime() { blip(660); setTimeout(() => blip(880), 120); setTimeout(() => blip(1100), 240); }

  // What Residual sees, drawn over the (mirrored) camera picture: eyes turn blue as they close past this
  // person's threshold, eyebrows amber as they rise past theirs.
  function drawOverlay() {
    requestAnimationFrame(drawOverlay);
    const box = vidBox(), cv = box.querySelector("canvas"), v = box.querySelector("video"), tr = T.tracker;
    if (!cv || !v || !T.setup || !box.isConnected || !v.videoWidth) return;
    const W = box.clientWidth, H = v.clientHeight || box.clientHeight;
    if (cv.width !== W || cv.height !== H) { cv.width = W; cv.height = H; }
    const g = cv.getContext("2d"); g.clearRect(0, 0, W, H);
    const lm = T.lastLandmarks, f = tr.last.face;
    if (!lm || !f || !f.face) return;
    // object-fit: cover, then mirrored
    const sc = Math.max(W / v.videoWidth, H / v.videoHeight), ox = (v.videoWidth * sc - W) / 2, oy = (v.videoHeight * sc - H) / 2;
    const P2 = p => [W - (p.x * v.videoWidth * sc - ox), p.y * v.videoHeight * sc - oy];
    const L = tr.switches.levels, th = T.eyes.thresholds;
    const lClosed = f.blinkL >= th(L.eyeL).on, rClosed = f.blinkR >= th(L.eyeR).on, up = f.brow >= th(L.brow).on;
    const ring = (ids, col, w) => { g.strokeStyle = col; g.lineWidth = w; g.beginPath(); ids.forEach((i, k) => { const [x, y] = P2(lm[i]); k ? g.lineTo(x, y) : g.moveTo(x, y); }); g.stroke(); };
    // MediaPipe's "left eye" landmarks (263...) are the person's left eye
    ring([33, 160, 158, 133, 153, 144, 33], rClosed ? "#6fb3ea" : "rgba(255,255,255,.55)", rClosed ? 4 : 2);
    ring([263, 387, 385, 362, 380, 373, 263], lClosed ? "#6fb3ea" : "rgba(255,255,255,.55)", lClosed ? 4 : 2);
    ring([70, 63, 105, 66, 107], up ? "#f2b84b" : "rgba(255,255,255,.55)", up ? 5 : 2);
    ring([300, 293, 334, 296, 336], up ? "#f2b84b" : "rgba(255,255,255,.55)", up ? 5 : 2);
    for (const i of [468, 473]) if (lm[i]) { const [x, y] = P2(lm[i]); g.fillStyle = "#4fd1a5"; g.beginPath(); g.arc(x, y, 3.5, 0, 7); g.fill(); }
  }

  async function renderSetup() {
    setupGen++;
    T.tracker.settings.enabled = ["doubleBlink", "brow", "longBlink", "winkLeft", "winkRight", "lookLeft", "lookRight"];
    T.setupPick = null; T.tryHook = null;
    const gen = setupGen, box = $("#two-setup-body"), tr = T.tracker;
    const frame = (title, intro, side) => {
      box.innerHTML = `${stepsBar()}<h1>${title}</h1><p>${intro}</p>
        <div class="two-setrow"><div id="two-vidwrap"></div><div class="two-setside">${side}<div id="two-s-auto"></div></div></div>
        <div class="two-btns two-helper"><span>For a helper:</span>${["Camera", "Learn", "Try", "Extras"].map((s, i) => `<button data-step="${i}">${s}</button>`).join("")}<button id="two-s-close">Close setup</button></div>`;
      $("#two-vidwrap").appendChild(vidBox());
      box.querySelectorAll("[data-step]").forEach(b => (b.onclick = () => goStep(+b.dataset.step)));
      $("#two-s-close").onclick = closeSetup;
    };
    if (setupStep === 0) {
      const cams = await tr.cameras();
      if (gen !== setupGen) return;
      frame("Camera check", "Sit about an arm's length away, face the screen, with light on your face (not behind you).",
        `<p class="two-big" id="two-s-face"></p><p id="two-s-err" class="bad"></p>
         ${cams.length > 1 ? `<label>Camera <select id="two-s-cam">${cams.map(c => `<option value="${escH(c.deviceId)}">${escH(c.label)}</option>`).join("")}</select></label>` : ""}
         <div class="two-btns"><button id="two-s-retry">Retry camera</button></div>`);
      const sel = $("#two-s-cam");
      if (sel) { const cur = tr.webcam?.video?.srcObject?.getVideoTracks?.()[0]?.getSettings?.().deviceId; if (cur) sel.value = cur; sel.onchange = async () => { await tr.restartCamera(sel.value); openSetup(0); }; }
      $("#two-s-retry").onclick = async () => { await tr.restartCamera(); openSetup(0); };
      sayS("Let's set up. Look at the screen.");
      const auto = $("#two-s-auto"), t0 = performance.now();
      let faceSince = null, offered = false;
      const tick = () => {
        if (gen !== setupGen || !T.setup) return;
        const face = tr.last.face?.face;
        $("#two-s-face").innerHTML = tr.cameraError ? "" : tr.webcam?.status !== "running" ? "Starting the camera…" : face ? '<b class="ok">✓ Face found</b>' : '<b class="bad">Can\'t see your face yet</b>';
        $("#two-s-err").textContent = tr.cameraError || "";
        faceSince = face ? faceSince ?? performance.now() : null;
        if (faceSince && performance.now() - faceSince > 1500 && !auto.dataset.cd && !offered)
          countdown(auto, 4, "Face found. Continuing", () => goStep(1), () => !!tr.last.face?.face);
        if (!face && !offered && performance.now() - t0 > 15000) {
          offered = true;
          sayS("I can't see your face. Retry the camera, or continue anyway?");
          chooser(auto, [{ label: "Retry camera", run: async () => { await tr.restartCamera(); openSetup(0); } }, { label: "Continue anyway", run: () => goStep(1) }]);
        }
        setTimeout(tick, 300);
      };
      tick();
    } else if (setupStep === 1) {
      frame("Learn your face", "About 20 seconds. Follow the words; there's a beep when to open your eyes.",
        `<p class="two-big" id="two-s-say">Get comfortable</p><div class="two-count" id="two-s-count"></div><div id="two-s-res"></div>
         <div class="two-btns"><button class="primary" id="two-s-go">Start now</button></div>`);
      $("#two-s-go").onclick = () => { setupGen++; learnFace(setupGen); };
      countdown($("#two-s-auto"), 3, "Learning your face starts in", () => learnFace(gen));
    } else if (setupStep === 2) {
      const sc = scheme(), right = schemeName() === "glance" ? "brow" : sc.right;
      const want = { doubleBlink: 0, [right]: 0, longBlink: 0 };
      const label = { doubleBlink: ["Blink twice", "LEFT", "eye"], [right]: [NAMES[right], "RIGHT", right === "brow" ? "brow" : "wink"], longBlink: ["Close your eyes a moment", "BACK", "left"] };
      frame("Try it", `Do each one 3 times. Blink twice for LEFT, ${escH(NAMES[right].toLowerCase())} for RIGHT, close your eyes for about a second for BACK.`,
        `<div class="two-meter"><span>Eyes closed</span><div><i id="two-m-eye"></i><b id="two-t-eye"></b></div></div>
         ${right === "brow" ? '<div class="two-meter"><span>Eyebrows up</span><div><i id="two-m-brow"></i><b id="two-t-brow"></b></div></div>' : ""}
         <div class="two-tries">${Object.keys(want).map(k => `<div data-g="${k}" class="${k === "longBlink" ? "back" : k === "doubleBlink" ? "l" : "r"}"><span class="two-ic">${ICONS[label[k][2]]}</span><small>${label[k][1]}</small><b>${escH(label[k][0])}</b><em>0 / 3</em></div>`).join("")}</div>
         <p class="two-big" id="two-try-msg"></p>`);
      sayS(`Now try it. Blink twice, then ${NAMES[right].toLowerCase()}, then close your eyes for a moment. Three times each.`);
      const msg = $("#two-try-msg"), t0 = performance.now();
      let saidOnce = 0, finished = false, offered = false;
      T.winkDuringBlinks = 0;
      T.tryHook = inp => {
        if (/^wink/.test(inp) && inp !== right) T.winkDuringBlinks++;
        if (!(inp in want) || finished) return;
        want[inp]++;
        const el = box.querySelector(`[data-g="${inp}"]`);
        el.querySelector("em").textContent = `${Math.min(3, want[inp])} / 3`;
        el.classList.toggle("done", want[inp] >= 3);
        el.classList.remove("hit"); void el.offsetWidth; el.classList.add("hit");
        blip(inp === "longBlink" ? 520 : inp === "doubleBlink" ? 620 : 780);
        if (Object.values(want).every(n => n >= 3)) {
          finished = true; chime();
          countdown($("#two-s-auto"), 4, "All three work. Continuing", () => goStep(3));
        }
      };
      const meters = () => {
        if (gen !== setupGen || !T.setup) return;
        const f = tr.last.face, L = tr.switches.levels, th = T.eyes.thresholds, sw = tr.switches;
        if (f) {
          $("#two-m-eye").style.width = Math.min(100, Math.min(f.blinkL, f.blinkR) * 100) + "%";
          $("#two-t-eye").style.left = Math.min(th(L.eyeL).on, th(L.eyeR).on) * 100 + "%";
          if ($("#two-m-brow")) { $("#two-m-brow").style.width = Math.min(100, f.brow * 100) + "%"; $("#two-t-brow").style.left = th(L.brow).on * 100 + "%"; }
        }
        if (!finished) {
          if (sw.firstBlinkAt !== null) { msg.textContent = "Blink once more"; if (saidOnce < 2 && performance.now() - (T.saidMore || 0) > 4000) { saidOnce++; T.saidMore = performance.now(); Voice.say("Blink once more", { priority: P.CONTROL }); } }
          else msg.textContent = "";
          if (!offered && performance.now() - t0 > 75000) {
            offered = true;
            sayS("Something isn't registering. Keep trying, learn again, or skip?");
            chooser($("#two-s-auto"), [{ label: "Keep trying", run: () => { $("#two-s-auto").innerHTML = ""; offered = false; } }, { label: "Learn again", run: () => goStep(1) }, { label: "Skip ahead", run: () => goStep(3) }]);
          }
        }
        requestAnimationFrame(meters);
      };
      meters();
    } else {
      const cal = calibrated();
      frame("Extras", "Optional. Other ways to choose, in case eyebrows get hard to read some days. Residual only uses one after you pick it.",
        `<p>Glances: ${cal.glances ? '<b class="ok">learned</b>' : "not learned"}. Wink: ${cal.wink ? `<b class="ok">learned (${cal.wink.eye === "L" ? "left" : "right"} eye)</b>` : "not learned"}.</p>
         <div id="two-x-work"></div>`);
      const extra = T.setupExtra; T.setupExtra = null;
      if (extra === "glances") return learnGlances(gen);
      if (extra === "wink") return learnWink(gen);
      sayS("Extras. Learn your glances, learn a wink, or finish.");
      chooser($("#two-s-auto"), [
        { label: cal.glances ? "Learn glances again" : "Learn your glances", run: () => learnGlances(setupGen) },
        { label: cal.wink ? "Learn the wink again" : "Learn a wink", run: () => learnWink(setupGen) },
        { label: "Finish setup", run: closeSetup }], { periodMs: 3600 });
    }
  }
  function record(ms, pick = () => T.tracker.last.face) {
    return new Promise(res => {
      const pts = [];
      const off = T.tracker.onFrame(() => { const f = T.tracker.last.face; if (f?.face) { const x = pick(); if (x) pts.push(x); } });
      setTimeout(() => { off(); res(pts); }, ms);
    });
  }
  async function learnFace(gen) {
    const tr = T.tracker, E = T.eyes, sayEl = $("#two-s-say"), res = $("#two-s-res"), cnt = $("#two-s-count"), auto = $("#two-s-auto");
    const alive = () => gen === setupGen && T.setup;
    if (!alive()) return;
    auto.innerHTML = ""; res.innerHTML = ""; $("#two-s-go").disabled = true;
    sayS("Look at the screen and relax your face", sayEl);
    await wait(1800); if (!alive()) return;
    const rest = await record(2000);
    sayS("Close your eyes. Keep them closed until the beep", sayEl);
    await wait(1600); if (!alive()) return;
    const closed = await record(1500);
    blip(880);
    sayS("Open. Relax.", sayEl);
    await wait(1500);
    const restBrow = E.pct(rest.map(f => f.brow), 0.9);
    const raised = [];
    let learned = 0;
    for (let k = 1; k <= 3 && alive(); k++) {
      cnt.innerHTML = `<b>${learned} / 3</b> learned`;
      sayS("Raise your eyebrows and hold", sayEl);
      await wait(900); if (!alive()) return;
      const r = await record(1100);
      blip(660);
      if (r.length >= 4 && E.pct(r.map(f => f.brow), 0.7) - restBrow > 0.04) { learned++; raised.push(...r); blip(990); }
      cnt.innerHTML = `<b>${learned} / 3</b> learned`;
      sayS("Relax", sayEl);
      await wait(1300);
    }
    if (!alive()) return;
    const ok = rest.length >= 8 && closed.length >= 5;
    if (!ok) {
      sayS("I couldn't see your face clearly. Check the light, then learn again.", sayEl);
      $("#two-s-go").disabled = false;
      return chooser(auto, [{ label: "Learn again", run: () => learnFace(++setupGen) }, { label: "Back to the camera", run: () => goStep(0) }]);
    }
    const browPeak = raised.length >= 6 ? E.pct(raised.map(f => f.brow), 0.7) : restBrow + 0.08;
    const levels = {
      eyeL: { rest: E.median(rest.map(f => f.blinkL)), peak: E.pct(closed.map(f => f.blinkL), 0.6) },
      eyeR: { rest: E.median(rest.map(f => f.blinkR)), peak: E.pct(closed.map(f => f.blinkR), 0.6) },
      brow: { rest: restBrow, peak: browPeak },
    };
    const eyeSep = Math.min(levels.eyeL.peak - levels.eyeL.rest, levels.eyeR.peak - levels.eyeR.rest);
    const browSep = levels.brow.peak - levels.brow.rest;
    const goodEyes = eyeSep >= 0.15, goodBrows = browSep >= 0.06 && learned >= 2;
    const row = (name, good, why) => `<p class="${good ? "ok" : "bad"}">${good ? "✓" : "✕"} ${name}${good ? "" : ": " + why}</p>`;
    res.innerHTML = row("Blinks", goodEyes, "open and closed looked too similar. More light on your face, or take off tinted glasses.") +
                    row("Eyebrows", goodBrows, "I couldn't see them move clearly. You can learn a wink or glances instead in Extras.");
    tr.setProfile({ ...tr.profile, levels, measured: true });
    T.q.reset();
    $("#two-s-go").disabled = false; $("#two-s-go").textContent = "Learn again";
    if (goodEyes && goodBrows) { chime(); countdown(auto, 4, "Learned. Now try it", () => goStep(2)); }
    else {
      sayS(goodEyes ? "Blinks are clear but eyebrows weren't. Learn again, try anyway, or learn a wink instead?" : "Blinks weren't clear. Learn again, or try anyway?", sayEl);
      chooser(auto, [{ label: "Learn again", run: () => learnFace(++setupGen) }, { label: "Try anyway", run: () => goStep(2) },
                     ...(goodEyes ? [{ label: "Learn a wink instead", run: () => openSetup(3, "wink") }] : [])]);
    }
  }
  // Glances: centre, left, centre, right, centre, up, centre. Measures this person's comfortable range and
  // which way is which on this camera, instead of fixed thresholds.
  async function learnGlances(gen) {
    const tr = T.tracker, work = $("#two-x-work"), auto = $("#two-s-auto");
    const alive = () => gen === setupGen && T.setup;
    auto.innerHTML = "";
    const seq = [["center", "Look at the middle"], ["left", "Look LEFT with your eyes. Keep your head still"], ["center", "Back to the middle"],
                 ["right", "Look RIGHT with your eyes"], ["center", "Back to the middle"], ["up", "Look UP with your eyes"], ["center", "Back to the middle"]];
    const rec = { center: [], left: [], right: [], up: [], down: [] };
    const arrow = { center: "●", left: "←", right: "→", up: "↑" };
    for (const [k, text] of seq) {
      if (!alive()) return;
      work.innerHTML = `<div class="two-glance">${arrow[k]}</div><p class="two-big">${escH(text)}</p>`;
      Voice.say(text, { priority: P.CONTROL });
      await wait(1100);
      rec[k].push(...(await record(1400, () => (tr.switches.eyesClosing(tr.last.face) ? null : tr.last.eye))));
    }
    if (!alive()) return;
    const cal = T.eyes.calibrateDirections(rec);
    const n = x => Math.round(x * 1000) / 1000;
    if (cal.ok.h) {
      tr.setProfile({ ...tr.profile, dirs: cal });
      chime();
      work.innerHTML = `<p class="ok">✓ Glances learned. Range: left ${n(cal.left)}, right ${n(cal.right)}, up ${n(cal.up)} (eye widths, head movement removed).</p>`;
      sayS("Glances learned. You can switch to them in Settings, How I choose.");
      countdown(auto, 4, "Back to extras", () => goStep(3));
    } else {
      work.innerHTML = `<p class="bad">✕ ${escH(cal.reason || "Left and right weren't clearly different.")}</p>`;
      sayS("That didn't work clearly. Try again, or skip?");
      chooser(auto, [{ label: "Try again", run: () => learnGlances(++setupGen) }, { label: "Skip", run: () => goStep(3) }]);
    }
  }
  // A wink as a stand-in for the eyebrows. Only accepted when the winking eye clearly closes while the other
  // stays open, and normal double blinks didn't look like winks.
  async function learnWink(gen) {
    const auto = $("#two-s-auto"), work = $("#two-x-work");
    auto.innerHTML = "";
    work.innerHTML = '<p class="two-big">Which eye winks more easily?</p>';
    sayS("Which eye winks more easily?");
    chooser(auto, [{ label: "Right eye", run: () => doWink("R", gen) }, { label: "Left eye", run: () => doWink("L", gen) }, { label: "Skip", run: () => goStep(3) }]);
  }
  async function doWink(eye, gen) {
    const tr = T.tracker, E = T.eyes, work = $("#two-x-work"), auto = $("#two-s-auto");
    const alive = () => gen === setupGen && T.setup;
    const L = tr.switches.levels, th = E.thresholds, lv = eye === "L" ? L.eyeL : L.eyeR, ov = eye === "L" ? L.eyeR : L.eyeL;
    const name = eye === "L" ? "winkLeft" : "winkRight";
    let fired = 0, learned = 0, good = 0, total = 0;
    T.tryHook = inp => { if (inp === name) fired++; };
    auto.innerHTML = "";
    for (let k = 1; k <= 3 && alive(); k++) {
      work.innerHTML = `<div class="two-count"><b>${learned} / 3</b> learned</div><p class="two-big" id="two-w-say"></p>`;
      sayS(`Wink your ${eye === "L" ? "left" : "right"} eye and hold`, $("#two-w-say"));
      await wait(700); if (!alive()) return;
      const r = await record(1200);
      const ok = r.filter(f => (eye === "L" ? f.blinkL : f.blinkR) >= th(lv).on && (eye === "L" ? f.blinkR : f.blinkL) < th(ov).off).length;
      good += ok; total += r.length;
      if (r.length && ok / r.length >= 0.4) { learned++; blip(990); }
      sayS("Open both eyes", $("#two-w-say"));
      await wait(1300);
    }
    T.tryHook = null;
    if (!alive()) return;
    const ratio = total ? good / total : 0, confused = T.winkDuringBlinks || 0;
    const ok = learned >= 2 && fired >= 2 && confused === 0;
    const p = prefs(); p.wink = { eye, ok, ratio: +ratio.toFixed(2), fired, learned, blinkConfusions: confused, at: new Date().toISOString() };
    try { localStorage.setItem(PREF_KEY, JSON.stringify(p)); } catch (e) {}
    if (ok) {
      chime();
      work.innerHTML = `<p class="ok">✓ Wink learned (${learned} of 3 clear, separate from your normal blinks).</p>`;
      sayS("Wink learned. Use it for RIGHT instead of eyebrows?");
      chooser(auto, [{ label: "Use the wink for RIGHT", run: () => { setScheme("wink"); goStep(2); } }, { label: "Keep eyebrows", run: () => goStep(3) }]);
    } else {
      work.innerHTML = `<p class="bad">✕ ${confused ? "Some normal blinks looked like winks, so a wink would pick things by accident." : "The wink wasn't clear enough: the other eye needs to stay open."}</p>`;
      sayS("The wink wasn't clear enough. Try again, or skip?");
      chooser(auto, [{ label: "Try again", run: () => doWink(eye, ++setupGen) }, { label: "Skip", run: () => goStep(3) }]);
    }
  }

  /* ------------------------------------------------------------ wiring */
  function enable() {
    T.on = true;
    document.body.classList.add("two");
    document.body.insertAdjacentHTML("beforeend", `
      <div id="two-left" class="two-card left"></div><div id="two-right" class="two-card right"></div>
      <div id="two-head"><nav id="two-title"></nav><span id="two-mode"></span><span id="two-voice"></span><span id="two-rec">● REC</span><span id="two-demo">DEMO</span><span id="two-cam-state"></span><button id="two-pause">Pause eyes</button><button id="two-setup-btn">Setup</button></div>
      <div id="two-ask" hidden></div>
      <div id="two-camerr" hidden><b>Camera</b><span></span><button id="two-camerr-btn">Fix it</button></div>
      <div id="two-setup" hidden><div id="two-setup-body"></div></div><div id="two-vid"></div>`);
    // The greeting's "who is around" line: the agent may not be confirmed up when Home is first drawn, so keep asking.
    const circleTick = () => { if (T.mode === "home" && !T.path.length) refreshCircleLine(); };
    setTimeout(circleTick, 2500); setInterval(circleTick, 15000);
    window.shell.ttsStatus && window.shell.ttsStatus().then(st => setVoiceBadge(st.ready ? "ready" : "system", st.ready ? "" : "no key")).catch(() => {});
    $("#two-left").onclick = () => choose("left");
    $("#two-right").onclick = () => choose("right");
    $("#two-pause").onclick = pauseEyes;
    $("#two-setup-btn").onclick = () => openSetup(0);
    $("#two-camerr-btn").onclick = () => openSetup(0);
    // keys pressed while a real app has focus come here from the main process
    window.shell.onRawKey && window.shell.onRawKey(k => key(k));
    // presenter / helper controls from the Lab
    window.shell.onLabCmd && window.shell.onLabCmd(a => {
      if (!a) return;
      if (a.rec === "start") recConsent(); else if (a.rec === "stop") recStop(); else if (a.rec === "delete") recDelete();
      if (a.two === "simulate") simulateWeak();
      if (a.two === "test") testControls();
      if (a.two === "setup") openSetup(0);
    });
    function key(k) {
      if (T.setup) return false;
      if (k === "ArrowLeft") choose("left");
      else if (k === "ArrowRight") choose("right");
      else if (k === "Backspace") goBack("key");
      else if (k === "Escape") goHome();
      else return false;
      return true;
    }
    addEventListener("keydown", e => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
      if (T.setup) { if (e.key === "Escape") closeSetup(); return; }
      if (e.key === "p" || e.key === "P") { pauseEyes(); return; }
      if (key(e.key)) { e.preventDefault(); e.stopImmediatePropagation(); }
    }, true);
    // home.js's three-movement controls and engine status don't apply here
    window.setMode = () => {};
    window.shell.mode(1);
    const basePill = window.pill;
    window.pill = () => {
      basePill();
      if (S.lifeline?.active) return;
      const p = $("#pill"), tr = T.tracker, face = tr?.last.face?.face;
      p.className = !tr ? "off busy" : tr.cameraError ? "off" : T.paused ? "warn" : face ? "" : "warn";
      p.querySelector("span").textContent = !tr ? "Starting" : tr.cameraError ? "Camera problem" : T.paused ? "Eyes paused" : face ? "Ready" : "Can't see your face";
    };
    setInterval(() => window.pill(), 1000);
    // follow what's on screen: an app opening or closing, a sheet appearing
    let last = "";
    setInterval(() => {
      const k = `${S.active}|${S.sheet ? S.sheet.type + (S.sheet.confirmCancel || "") + (S.sheet.items || []).length : ""}`;
      document.body.classList.toggle("two-app", !!S.active && !S.sheet);
      document.body.classList.toggle("two-narrow", !!S.active || !!S.sheet);
      if (k !== last) { if (S.active && !last.startsWith(S.active + "|")) T.appOpenedAt = performance.now(); last = k; T.list = null; refresh({ force: true }); }
    }, 300);
    // Pages change by themselves (a feed loads, a video ends). The choices stay frozen while the person is
    // deciding: never rebuilt in the middle of a split, never for small changes, and only after a moment
    // without gestures. A real change rebuilds them and says so.
    setInterval(async () => {
      if (!S.active || S.sheet || busyNow() || T.setup || S.active === "aiweb" || T.question) return;
      const got = ((await window.shell.pageChoices()) || []).filter(c => !(c.action && /^back$/i.test(c.label)));
      const now = got.map(c => c.label), old = T.pageLabels || [];
      if (now.join("|") === old.join("|")) return;
      const a = new Set(old), b = new Set(now), inter = [...b].filter(x => a.has(x)).length;
      const same = inter / Math.max(1, new Set([...a, ...b]).size);
      if (T.stack.length || same >= 0.8) return;
      if (!old.length || performance.now() - (T.appOpenedAt || 0) < 6000) { return refresh(); }   // still loading: nothing to announce
      if (performance.now() - Math.max(T.lastPick, T.lastGestureAt || 0) < 2500) return;
      announceRefresh();
    }, 3000);
    window.shell.onDockPick && window.shell.onDockPick(side => choose(side));
    window.shell.onOrders && window.shell.onOrders(onOrders);
    window.shell.onErrands && window.shell.onErrands(onErrands);
    // preflight (npm run preflight, or the Lab): what this window knows about the camera, the face and the controls
    window.shell.onPreflight && window.shell.onPreflight(async () => {
      const t0 = performance.now();
      let face = false;
      while (performance.now() - t0 < 10000 && !face) { face = !!(T.tracker && T.tracker.last.face && T.tracker.last.face.face); if (!face) await wait(250); }
      const tr = T.tracker;
      const track = tr && tr.webcam && tr.webcam.video && tr.webcam.video.srcObject && tr.webcam.video.srcObject.getVideoTracks()[0];
      return { camera: tr && tr.webcam ? tr.webcam.status : null, cameraError: tr ? tr.cameraError : "tracker didn't start", fps: tr ? Math.round(tr.fps) : 0,
               cameraLabel: track ? track.label : "", face, measured: !!(tr && tr.profile.measured), controls: schemeName() };
    });
    if (prefs().largeText) setLarge(true);
    if (prefs().demo) document.body.classList.add("two-demo");
    refresh({ force: true });
    startEyes();
    window.shell.aiwebReady && window.shell.aiwebReady().then(r => (T.aiwebReady = !!r));
    window.shell.onAiweb && window.shell.onAiweb(st => {
      const was = T.aiweb;
      T.aiweb = st;
      if (st.error && (!was || was.error !== st.error)) status(st.error);
      if (S.active === "aiweb" && (!was || was.status !== st.status || (was.actions || []).length !== (st.actions || []).length)) { T.list = null; refresh({ force: true }); }
    });
  }

  window.Two = { enable, choose, state: T, splitTwo, openSetup, goBack, measure, testControls, setSignals, decode, onGesture, adaptCheck, prefs, ask, setScheme, schemeName, Rec, recStart, recStop, chooser };
  const ready = () => (typeof CFG !== "undefined" && CFG ? ((CFG.input?.mode || "two") === "two" && enable()) : setTimeout(ready, 200));
  ready();
})();
