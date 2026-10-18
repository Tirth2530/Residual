// Residual: the Electron app.
//
//   Home window   what the person sees. Our UI around a real browser view running the real app
//                 (Instagram, YouTube, WhatsApp, Netflix...). Real sites refuse iframes, but a
//                 WebContentsView is a full browser tab: they load normally and stay logged in.
//   Lab window    the technical screen: everything the engine and the app are deciding, live.
//                 Opens on a second display if there is one (Cmd/Ctrl+L, or the Lab button).
//
// The main process also owns everything that must keep working if a screen freezes: Lifeline,
// the Python engine process, AI calls, news and weather.

const { app, BrowserWindow, WebContentsView, ipcMain, session, screen, Menu, Notification, globalShortcut, systemPreferences } = require("electron");
const path = require("path");
const fs = require("fs");
const { AI } = require("./lib/ai");
const { Lifeline } = require("./lib/lifeline");
const { Store } = require("./lib/store");
const { News } = require("./lib/news");
const { Weather } = require("./lib/weather");
const { rank, top } = require("./lib/intents");
const { EngineProc } = require("./lib/engine-proc");
const { AgentClient } = require("./lib/agent-client");
const { startEyesServer } = require("./lib/eyes-server");
const { scrollPage } = require("./lib/scroll");
const { hiddenBounds } = require("./lib/layout");
const { setMusic } = require("./lib/music");
const { backTarget } = require("./ui/nav-rules");
const { safeLiveUrl } = require("./ui/swarm-view");
const { BrowserUse, readKey } = require("./lib/browser-use");
const { SingleFlight, once } = require("./ui/speech");

const CONFIG = JSON.parse(fs.readFileSync(path.join(__dirname, "config.json"), "utf8"));
const INJECT = fs.readFileSync(path.join(__dirname, "inject.js"), "utf8");
// Some sites (WhatsApp Web) refuse anything that doesn't look like Chrome.
const UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
           "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36";

app.commandLine.appendSwitch("autoplay-policy", "no-user-gesture-required");

let win = null, lab = null, store = null;
const ai = new AI(CONFIG.ai || {});
const lifeline = new Lifeline({ person: CONFIG.person, ...(CONFIG.lifeline || {}) });
const news = new News(CONFIG.news || {});
const weather = new Weather(CONFIG.location || {});
const engine = new EngineProc({ ...(CONFIG.engine || {}), person: (CONFIG.person || "default").toLowerCase() }, __dirname);
const agent = new AgentClient({ ...(CONFIG.agent || {}), tokenFile: (CONFIG.agent && CONFIG.agent.tokenFile) || path.join(__dirname, "..", "agent", "state", "local_token") });
const flight = new SingleFlight();   // main-process long actions: one at a time, whatever the UI sends
const welcomeOnce = once();
// AI web mode (Browser Use): only for sites without built-in actions. Key from the environment or app/.env.
const bu = new BrowserUse({ apiKey: readKey(__dirname) });
const { ElevenTTS, readEnv } = require("./lib/tts");
const tts = new ElevenTTS({ apiKey: readEnv(__dirname, "ELEVENLABS_API_KEY"), voiceId: readEnv(__dirname, "ELEVENLABS_VOICE_ID") });
bu.on("state", st => {
  send("aiweb", st); tele("aiweb", { status: st.status, url: st.url, actions: st.actions.length, error: st.error });
  if (st.liveUrl && active === "aiweb" && views.aiweb && views.aiweb.webContents.getURL() !== st.liveUrl) views.aiweb.webContents.loadURL(st.liveUrl);
});          // the spoken welcome: once per launch, never on reload or reconnect
const views = {};          // app id -> WebContentsView (kept alive, so music keeps playing)
let active = null;         // app id on screen, or null for Home
let stageRect = { x: 0, y: 0, width: 800, height: 600 };
let viewHidden = false;    // true while a shell sheet (confirm, search, keyboard) covers the stage
let mode = 3;              // usable movements: 3 direct, 2, 1 auto-scan
const signals = { unread: 0, unreadFrom: null, briefedToday: false };

const send = (ch, data) => { if (win && !win.isDestroyed()) win.webContents.send(ch, data); };
const appCfg = id => CONFIG.apps.find(a => a.id === id);

/* ------------------------------------------------------------------ telemetry for the Lab */
const teleLog = [];
function tele(kind, data = {}) {
  const e = { t: Date.now(), kind, ...data };
  // the two-choice live feed (camera thumbnails, meters ~10 a second) goes straight to the Lab, not the backlog
  if (kind !== "two-state" && kind !== "two-frame") { teleLog.push(e); if (teleLog.length > 400) teleLog.shift(); }
  if (lab && !lab.isDestroyed()) lab.webContents.send("telemetry", e);
}
ai.on("call", c => tele("ai", c));
ai.on("status", s => tele("ai-status", s));
lifeline.on("state", s => { send("lifeline", s); tele("lifeline", { active: s.active, ackBy: s.ackBy, reached: s.reached, secondsToNext: s.secondsToNext, simulated: s.simulated }); });
engine.on("state", s => { send("engine-proc", s); tele("engine-proc", s); });
agent.on("task", t => { send("agent-task", t); tele("agent-task", { task: t }); });
agent.on("call", c => tele("agent-call", c));
agent.on("health", h => { send("agent-health", h); tele("agent-health", h); });
agent.on("orders", l => { send("orders", l); tele("orders", { orders: (l || []).slice(0, 8) }); });
agent.watchOrders();
agent.on("errands", l => send("errands", l));
agent.watchErrands();

/* ------------------------------------------------------------------ windows */
function createWindow() {
  win = new BrowserWindow({
    width: 1440, height: 900, minWidth: 1024, minHeight: 680,
    backgroundColor: "#0e0f12", title: "Residual", titleBarStyle: "hiddenInset",
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true }
  });
  win.loadFile(path.join(__dirname, "ui", "index.html"));
  // A call for help must not end because a window was closed.
  win.on("close", e => {
    if (lifeline.busy) {
      e.preventDefault();
      send("status", { text: "Still calling for help. Residual stays open until someone answers or you cancel." });
      try { new Notification({ title: "Residual is still calling for help", body: "It will close once someone answers." }).show(); } catch (err) {}
    }
  });
}
function openLab() {
  if (lab && !lab.isDestroyed()) { lab.show(); lab.focus(); return; }
  const displays = screen.getAllDisplays(), primary = screen.getPrimaryDisplay();
  const other = displays.find(d => d.id !== primary.id);
  const area = (other || primary).workArea;
  lab = new BrowserWindow({
    x: area.x + (other ? 0 : 60), y: area.y + (other ? 0 : 40),
    width: other ? area.width : Math.min(1680, area.width - 120), height: other ? area.height : Math.min(1000, area.height - 80),
    backgroundColor: "#07090c", title: "Residual Lab",
    webPreferences: { preload: path.join(__dirname, "preload-lab.js"), contextIsolation: true }
  });
  lab.loadFile(path.join(__dirname, "ui", "lab.html"));
  send("lab-open", true);                 // Home only streams the two-choice live feed while the Lab is open
  lab.on("closed", () => { lab = null; send("lab-open", false); });
}

/* ------------------------------------------------------------------ app views */
function getView(id) {
  if (views[id]) return views[id];
  const cfg = appCfg(id);
  const view = new WebContentsView({ webPreferences: { partition: "persist:residual", contextIsolation: true, sandbox: true } });
  if (typeof view.setBorderRadius === "function") view.setBorderRadius(22);
  view.setBackgroundColor("#15171c");
  const wc = view.webContents;
  wc.setUserAgent(UA);
  wc.setWindowOpenHandler(({ url }) => { wc.loadURL(url); return { action: "deny" }; });
  const inject = () => id === "aiweb" ? Promise.resolve() : wc.executeJavaScript(INJECT).then(() => sync(id)).catch(() => {});   // AI web mode shows Browser Use's live view: nothing to inject
  wc.on("dom-ready", () => { if (largeText) wc.setZoomFactor(1.3); inject().then(() => layoutDock()); });
  wc.on("did-navigate-in-page", () => { inject(); remember(id); });
  wc.on("did-navigate", () => remember(id));
  wc.on("page-title-updated", (_e, title) => {
    if (id === "whatsapp") {   // "(3) WhatsApp" = 3 unread chats
      const n = +((title.match(/^\((\d+)\)/) || [])[1] || 0);
      if (n !== signals.unread) { signals.unread = n; send("signals", signals); tele("signal", { unread: n }); }
    }
    sendAppState(); remember(id);
  });
  wc.on("before-input-event", (e, input) => {
    if (input.type !== "keyDown" || input.meta || input.control) return;
    const act = keyToAction(input.key);
    if (act) { e.preventDefault(); send("key", act); send("raw-key", input.key); }
  });
  wc.loadURL(cfg.url);
  views[id] = view;
  return view;
}

function keyToAction(k) {
  if (k === "ArrowRight" || k === "ArrowDown") return "next";
  if (k === " " || k === "Enter") return "select";
  if (k === "ArrowLeft" || k === "Backspace") return "back";
  if (k === "Escape") return "home";
  if (k === "h" || k === "H") return "help";
  return null;
}

function layout() {
  if (!win) return;
  for (const [id, v] of Object.entries(views)) {
    if (id === active && !viewHidden) v.setBounds(stageRect);
    else v.setBounds(hiddenBounds(id, stageRect));   // hidden, still alive (music keeps playing: see lib/layout.js)
  }
  layoutDock();
}

/* ------------------------------------------------------------------ the choice dock (two-choice mode) */
// A transparent view floating over the bottom of a website, showing the two choices, so the site itself
// can use the whole window. Home (two.js) sends what to show; a click on a side comes back to Home.
let dock = null, dockOn = false, dockH = 0, largeText = false;
function getDock() {
  if (dock) return dock;
  dock = new WebContentsView({ webPreferences: { preload: path.join(__dirname, "preload-dock.js"), contextIsolation: true } });
  dock.setBackgroundColor("#00000000");
  dock.webContents.loadFile(path.join(__dirname, "ui", "dock.html"));
  return dock;
}
function layoutDock() {
  if (!win) return;
  const show = dockOn && active && views[active] && !viewHidden;
  if (!show && !dock) return;
  const d = getDock();
  if (!show) { d.setBounds({ x: 0, y: 0, width: 0, height: 0 }); setInset(0); return; }
  const kids = win.contentView.children;
  if (kids[kids.length - 1] !== d) win.contentView.addChildView(d);   // always on top of the site
  dockH = Math.round(Math.min(largeText ? 380 : 320, Math.max(200, stageRect.height * (largeText ? 0.36 : 0.3))));
  d.setBounds({ x: stageRect.x, y: stageRect.y + stageRect.height - dockH, width: stageRect.width, height: dockH });
  setInset(dockH);
}
// Tell the page how much of its bottom is covered, so covered buttons aren't offered until scrolled into view.
function setInset(px) {
  const v = active && views[active];
  if (!v) return;
  const z = v.webContents.getZoomFactor() || 1;
  v.webContents.executeJavaScript(`window.__residual && __residual.inset && __residual.inset(${Math.round(px / z)})`).catch(() => {});
}
ipcMain.on("dock", (_e, s) => {
  const on = !!(s && s.on);
  if (on !== dockOn) { dockOn = on; layout(); }
  if (on && dock) dock.webContents.send("state", s);
});
ipcMain.on("dock-pick", (_e, side) => send("dock-pick", side));
// Previous page: the page before this one; with none, the app's own start page. Never Home (that has its own choice).
function previousPage(wc) {
  const nav = wc.navigationHistory;
  const what = backTarget({ canGoBack: !!(nav && nav.canGoBack()), url: wc.getURL(), startUrl: (appCfg(active) || {}).url });
  if (what === "none") return { done: "This is the first page" };
  pauseMedia(active); wc.setAudioMuted(false);
  if (what === "back") nav.goBack(); else wc.loadURL(appCfg(active).url);
  return { done: "Previous page" };
}
ipcMain.handle("page-back", () => {
  const v = active && views[active]; if (!v) return { done: "No page open" };
  return previousPage(v.webContents);
});
// Larger text: websites zoom, the choices get bigger
ipcMain.handle("large-text", (_e, on) => {
  largeText = !!on;
  for (const v of Object.values(views)) v.webContents.setZoomFactor(largeText ? 1.3 : 1);
  layout();
  return largeText;
});

function show(id, url) {
  if (active && active !== id) pauseMedia(active);
  const v = getView(id);
  v.webContents.setAudioMuted(false);
  if (!win.contentView.children.includes(v)) win.contentView.addChildView(v);
  if (url) v.webContents.loadURL(url);
  active = id;
  layout(); sync(id); sendAppState();
  v.webContents.focus();
  tele("view", { active: id, url: url || appCfg(id)?.url });
}

// Leaving a video should stop it. Only music is meant to keep playing in the background.
function pauseMedia(id) {
  const v = id && views[id];
  if (!v || id === "music") return;
  v.webContents.executeJavaScript("document.querySelectorAll('video,audio').forEach(m => { try { m.pause(); } catch (e) {} }); true").catch(() => {});
  v.webContents.setAudioMuted(true);   // belt and braces: YouTube's mini-player or an autoplaying preview stays silent
}
function goHome() {
  if (active === "aiweb") bu.close();    // leaving AI web mode ends the cloud browser session
  pauseMedia(active);
  if (active && views[active]) views[active].webContents.executeJavaScript("window.__residual && __residual.clear()").catch(() => {});
  active = null;
  layout();
  win.webContents.focus();
  sendAppState();
  tele("view", { active: null });
}

function sendAppState() {
  const wc = active && views[active] ? views[active].webContents : null;
  send("app-state", { active, title: wc ? wc.getTitle() : "", open: Object.keys(views) });
}

function sync(id) {
  const v = views[id];
  if (!v) return;
  v.webContents.executeJavaScript(`window.__residual && __residual.setup(${JSON.stringify({ mode, replies: CONFIG.quickReplies })})`).catch(() => {});
  if (id === active && dockOn) setInset(dockH);
}

// Remember what was watched and where each app was, for Home's "continue" cards.
async function remember(id) {
  const v = views[id]; if (!v || !store) return;
  const wc = v.webContents, url = wc.getURL(), title = (wc.getTitle() || "").replace(/ - YouTube( Music)?$| \| Netflix$|^\(\d+\) /g, "").trim();
  store.lastUrl(id, url);
  if (/youtube\.com\/watch|music\.youtube\.com\/watch|netflix\.com\/(watch|title)/.test(url) && title && !/^(YouTube|Netflix)/.test(title)) store.watched(id, title, url);
}

/* ------------------------------------------------------------------ commands into the page */
// Real (trusted) key presses. Sites ignore keys faked from page scripts.
function pressKey(wc, code, mods = []) {
  wc.focus();
  wc.sendInputEvent({ type: "keyDown", keyCode: code, modifiers: mods });
  const ch = code === "Space" ? " " : code === "Return" ? "\r" : code.length === 1 ? (mods.includes("shift") ? code : code.toLowerCase()) : null;
  if (ch) wc.sendInputEvent({ type: "char", keyCode: ch, modifiers: mods });
  wc.sendInputEvent({ type: "keyUp", keyCode: code, modifiers: mods });
}
// Only these page-helper methods can be called, with JSON arguments. No arbitrary code strings.
const PAGE_METHODS = new Set(["probe", "cmd", "readChat", "setReplies", "typeText", "playFirst", "composerHas", "confirmPending", "info", "clear", "health",
                              "choices", "pick", "mark", "clearMarks", "scroll", "musicState", "musicToggle"]);
async function pageCall(method, ...args) { return pageCallOn(active, method, ...args); }
async function pageCallOn(id, method, ...args) {
  if (!PAGE_METHODS.has(method)) return null;
  const v = id && views[id]; if (!v) return null;
  const js = `window.__residual ? __residual.${method}(${args.map(a => JSON.stringify(a)).join(",")}) : null`;
  try { return await v.webContents.executeJavaScript(js); } catch (e) { return null; }
}
// Pause / play the music from anywhere: Home has no music page on screen, so this talks to the music view directly.
const musicCall = (m, ...a) => pageCallOn("music", m, ...a);
ipcMain.handle("music-state", async () => (await musicCall("musicState")) || { has: false });
ipcMain.handle("music-toggle", (_e, want) => setMusic({ call: musicCall, want: want === "pause" || want === "play" ? want : "toggle" }));
async function command(action) {
  const v = active && views[active];
  if (!v) return { home: true };
  const wc = v.webContents;
  if (action === "home") { goHome(); return { home: true }; }
  if (action === "back") {
    // going back from a video (YouTube keeps playing it in a mini-player otherwise)
    if (active !== "music") await wc.executeJavaScript("document.querySelectorAll('video,audio').forEach(m => { try { m.pause(); } catch (e) {} }); true").catch(() => {});
    const r = await pageCall("cmd", "back");
    if (r && r.key) { pressKey(wc, r.key, r.mods); return r; }
    const nav = wc.navigationHistory;
    if (nav && nav.canGoBack()) { nav.goBack(); return { back: true }; }
    goHome(); return { home: true };
  }
  let res = await pageCall("cmd", action);
  if (!res) { try { await wc.executeJavaScript(INJECT); sync(active); res = await pageCall("cmd", action); } catch (e) {} }
  return afterPage(wc, action, res);
}
// Two-choice mode: the person picked one thing from the page's list (choices()).
async function pickOnPage(id) {
  const v = active && views[active];
  if (!v) return { home: true };
  return afterPage(v.webContents, "pick", await pageCall("pick", id));
}
async function afterPage(wc, action, res) {
  if (res && res.navBack) return previousPage(wc);
  if (res && res.key) pressKey(wc, res.key, res.mods);
  if (res && res.back) { goHome(); return { home: true }; }
  if (res && res.sent) { const info = await pageCall("readChat"); if (info) store.chatted(info.name); store.typed(res.sent); }
  if (res && res.open === "replies") refreshReplies(true);
  tele("page", { app: active, action, res: res ? Object.fromEntries(Object.entries(res).filter(([k]) => k !== "mods")) : null });
  return res;
}

/* ------------------------------------------------------------------ smart replies */
let replyBusy = false;
async function refreshReplies(force = false) {
  if (replyBusy || !active || active !== "whatsapp") return;
  const chat = await pageCall("readChat");
  if (!chat || !chat.messages.length || (!chat.needs && !force)) return;
  replyBusy = true;
  try {
    store.chatted(chat.name);
    send("status", { text: `Writing replies to ${chat.name}…` });
    const r = await ai.replies({ contact: chat.name, messages: chat.messages, person: CONFIG.person, style: CONFIG.writingStyle });
    await pageCall("setReplies", r.list, chat.sig);
    send("status", { text: r.source === "ai" ? `Replies for ${chat.name} ready` : `Quick replies for ${chat.name}` });
  } finally { replyBusy = false; }
}
setInterval(() => refreshReplies(false), 2500);
// site adapter health, for the Lab: are the selectors this app depends on still finding things?
setInterval(async () => { if (active && lab) { const h = await pageCall("health"); if (h) tele("adapter", h); } }, 3000);

/* ------------------------------------------------------------------ "Suggested for now" */
function candidates() {
  const c = [];
  const d = store.d;
  const w = d.watched[0];
  if (w) c.push({ id: "continue", kind: "continue", label: `Continue ${w.title}`.slice(0, 70), sub: appCfg(w.app)?.name || "", icon: w.app === "music" ? "music" : "play", action: { open: w.app, url: w.url }, prior: 0.35 });
  c.push({ id: "briefing", kind: "briefing", label: "Brief me", sub: "Weather, news and messages, read aloud", icon: "spark", action: { briefing: true }, prior: 0.4 });
  c.push({ id: "news", kind: "news", label: "What's happening", sub: "Headlines you can listen to", icon: "news", action: { screen: "news" }, prior: 0.3 });
  for (const p of CONFIG.people || []) c.push({ id: `talk:${p.name}`, kind: "chat", person: p.name, label: `Talk to ${p.name}`, sub: p.relation || "WhatsApp", icon: "chat", action: { screen: "person", person: p.name }, prior: 0.3 });
  for (const r of CONFIG.routines || []) {
    if ((r.steps || []).some(s => s.help)) continue;   // help is always on screen; never "suggested"
    c.push({ id: `routine:${r.name}`, kind: "routine", label: r.name, sub: r.sub || "", icon: r.icon || "play", tags: r.tags || [], action: { routine: r.name }, prior: 0.25 });
  }
  return c;
}
function suggestions() {
  signals.briefedToday = new Date(store.d.briefed || 0).toDateString() === new Date().toDateString();
  const ranked = rank(candidates(), store.d.usage, { signals });
  const chosen = top(ranked, 3);
  tele("intents", { top: ranked.slice(0, 6).map(r => ({ id: r.id, label: r.label, score: r.score, parts: r.parts, why: r.why, shown: chosen.includes(r) })) });
  return chosen;
}

/* ------------------------------------------------------------------ IPC: home */
ipcMain.handle("config", () => CONFIG);
ipcMain.handle("open-app", (_e, id, url) => { show(id, url); store.used(`app:${id}`); return true; });
ipcMain.handle("home", () => { goHome(); return true; });
ipcMain.handle("cmd", (_e, action) => command(action));
ipcMain.handle("confirm-pending", (_e, yes) => pageCall("confirmPending", !!yes));
ipcMain.handle("ai-status", () => ai.status);
// The person's words in the ElevenLabs voice (if set up). null = use the system voice.
ipcMain.handle("tts", async (_e, text) => { const b = await tts.speak(text); tele("tts", { ok: !!b, chars: String(text || "").length, ...(b ? {} : { why: tts.ready ? tts.lastError : "no key" }) }); return b ? b.toString("base64") : null; });
ipcMain.handle("tts-status", () => tts.status());

/* ------------------------------------------------------------------ preflight */
const PREFLIGHT = process.argv.includes("--preflight");
function fromHome(ch, req, ms) {
  return new Promise(res => {
    const done = (_e, d) => { clearTimeout(to); res(d); };
    const to = setTimeout(() => { ipcMain.removeListener(ch + "-reply", done); res(null); }, ms);
    ipcMain.once(ch + "-reply", done);
    send(ch, req);
  });
}
async function preflight() {
  const { runChecks } = require("./lib/preflight");
  const renderer = (await fromHome("preflight", {}, 14000)) || { camera: null };
  try {
    const all = JSON.parse(fs.readFileSync(path.join(app.getPath("userData"), "residual-control-tests.json"), "utf8"));
    const t = all[all.length - 1]; if (t) renderer.lastTest = { at: t.at, falsePerMinute: t.falsePerMinute };
  } catch (e) {}
  const ck = session.fromPartition("persist:residual").cookies;
  const has = async (domain, name) => { try { return (await ck.get({ domain, name })).length > 0; } catch (e) { return false; } };
  const rows = await runChecks({ renderer, browserUseKey: readKey(__dirname), elevenKey: readEnv(__dirname, "ELEVENLABS_API_KEY"),
    relayUrl: (CONFIG.agent && CONFIG.agent.url) || "http://127.0.0.1:8100", relayToken: agent._token(), ai: ai.status,
    cookies: { youtube: await has(".youtube.com", "SID"), instagram: await has(".instagram.com", "sessionid"), whatsapp: false } });
  fs.writeFileSync(path.join(app.getPath("userData"), "preflight.json"), JSON.stringify({ at: new Date().toISOString(), rows }, null, 2));
  tele("preflight", { rows });
  return rows;
}
ipcMain.handle("preflight-run", () => preflight());
// Demo Mode's local page: binary navigation on a real web page with no login and no internet
ipcMain.handle("demo-url", () => require("url").pathToFileURL(path.join(__dirname, "ui", "demo", "news.html")).href);
// Suggested replies to something someone just said to the person. Suggestions only: the person picks.
ipcMain.handle("suggest-replies", async (_e, { contact, messages }) => {
  try { const r = await ai.replies({ contact, messages, person: CONFIG.person, style: CONFIG.writingStyle }); return r; }
  catch (e) { return { list: CONFIG.quickReplies || [], source: "fixed" }; }
});
ipcMain.handle("search-ideas", (_e, appId) => ai.searchIdeas({ app: appId, history: store.history(appId), hour: new Date().getHours() }));
ipcMain.handle("search", (_e, appId, q) => {
  const tpl = appCfg(appId)?.search;
  if (!tpl) return false;
  store.searched(appId, q);
  show(appId, tpl.replace("{q}", encodeURIComponent(q)));
  return true;
});
ipcMain.handle("type-into", async (_e, text, sendIt) => {
  const r = await pageCall("typeText", text, !!sendIt);
  if (r && r.key) pressKey(views[active].webContents, r.key, r.mods);
  if (sendIt) store.typed(text);
  return r;
});
ipcMain.handle("continue-text", (_e, text) => ai.continueText(text));
ipcMain.handle("home-feed", () => {
  const d = store.d;
  return { typed: d.typed.slice(0, 12), watched: d.watched.slice(0, 6), chats: d.chats.slice(0, 6) };
});
ipcMain.handle("suggestions", () => suggestions());
ipcMain.handle("used", (_e, id) => { store.used(id); return true; });
ipcMain.handle("signals", () => signals);
ipcMain.handle("weather", () => weather.get());
ipcMain.handle("news-sections", () => news.list());
ipcMain.handle("news-section", async (_e, id) => { const t0 = Date.now(); const items = await news.section(id); tele("news", { section: id, n: items.length, ms: Date.now() - t0 }); return items; });
ipcMain.handle("article", async (_e, item) => {
  const text = (await news.article(item.link)) || item.desc || "";
  const s = await ai.summarize({ title: item.title, source: item.source, text: text || item.title });
  return { ...s, chars: text.length };
});
ipcMain.handle("briefing", () => flight.run("briefing", async () => {
  const [w, top] = await Promise.all([weather.get(), news.section(CONFIG.news?.briefingSection || "top", 6).catch(() => [])]);
  const r = await ai.briefing({ person: CONFIG.person, now: Date.now(), weather: w, news: top,
                                messages: { unread: signals.unread }, interests: CONFIG.news?.interests });
  store.d.briefed = Date.now(); store.save(); store.used("briefing");
  return r;
}) || { busy: true });
ipcMain.handle("ask-starters", async () => {
  const top = await news.section("top", 4).catch(() => []);
  return ai.starters({ news: top, interests: CONFIG.news?.interests || [], hour: new Date().getHours() });
});
ipcMain.handle("ask", async (_e, history, about) => {
  const [w, top] = await Promise.all([weather.get(), news.section("top", 5).catch(() => [])]);
  const now = new Date();
  const ctx = [`Now: ${now.toLocaleString("en-US", { weekday: "long", hour: "numeric", minute: "2-digit" })}.`,
               w ? `Weather in ${w.place}: ${w.temp}${w.unit}, ${w.desc}.` : "",
               top.length ? "Today's headlines: " + top.map(n => n.title).join(" | ") : "",
               CONFIG.news?.interests?.length ? `${CONFIG.person} likes: ${CONFIG.news.interests.join(", ")}.` : ""].filter(Boolean).join("\n");
  store.used("ask");
  return ai.ask({ person: CONFIG.person, history, context: ctx, about });
});
ipcMain.handle("lifeline-start", (_e, reason) => { store.care("help", { reason }); return lifeline.start(reason); });
ipcMain.handle("lifeline-cancel", () => lifeline.cancel());
ipcMain.handle("lifeline-sim-ack", () => { lifeline.acknowledge(CONFIG.lifeline?.contacts?.[0]?.name || "Maya"); return lifeline.snapshot(); });
ipcMain.handle("lifeline-state", () => lifeline.snapshot());
ipcMain.handle("welcome-once", () => welcomeOnce());
// Eyes-only two-choice mode (ui/two.js): the tracker runs in Home; the page lists what can be picked.
let eyesBase = null;
ipcMain.handle("eyes-base", async () => {
  if (!eyesBase) {
    if (process.platform === "darwin") { try { await systemPreferences.askForMediaAccess("camera"); } catch (e) {} }
    eyesBase = (await startEyesServer(path.join(__dirname, "ui", "eyes"))).base;
  }
  return eyesBase;
});
ipcMain.handle("page-choices", () => pageCall("choices"));
ipcMain.handle("aiweb-ready", () => bu.ready);
ipcMain.handle("aiweb-open", async (_e, url) => {
  if (!bu.ready) return { status: "error", error: "AI web mode needs a Browser Use key: add BROWSER_USE_API_KEY to app/.env" };
  show("aiweb", "data:text/html;charset=utf-8," + encodeURIComponent(`<body style="margin:0;background:#15171c;color:#cfd3dc;font:20px system-ui;display:grid;place-items:center;height:100vh"><div style="text-align:center"><b>AI web mode</b><br><br>Browser Use is opening ${url.replace(/[<>"]/g, "")} in the cloud.<br>Its live view appears here.</div></body>`));
  bu.open(url);
  return { status: "reading" };
});
ipcMain.handle("aiweb-do", (_e, i) => bu.run(i));
ipcMain.handle("aiweb-state", () => ({ ...bu.state, ready: bu.ready }));
ipcMain.handle("page-pick", (_e, id) => pickOnPage(id));
ipcMain.handle("page-mark", (_e, left, right) => pageCall("mark", left, right));
ipcMain.handle("page-clear-marks", () => pageCall("clearMarks"));
// The gesture measurement (two.js "Measure my gestures"): saved next to the app's other data, for the write-up.
ipcMain.handle("save-metrics", (_e, data) => {
  const file = path.join(app.getPath("userData"), data && data.kind === "test-my-controls" ? "residual-control-tests.json" : "residual-gesture-metrics.json");
  let all = [];
  try { all = JSON.parse(fs.readFileSync(file, "utf8")); } catch (e) {}
  all.push(data);
  fs.writeFileSync(file, JSON.stringify(all, null, 2));
  return file;
});
// Session recording (two.js, only after the person agrees): numbers only, one JSON line per sample or event,
// in userData/recordings. Stop ends the file; Delete removes every recording.
const recDir = () => path.join(app.getPath("userData"), "recordings");
let recFile = null;
ipcMain.handle("rec-start", (_e, header) => {
  fs.mkdirSync(recDir(), { recursive: true });
  recFile = path.join(recDir(), `session-${new Date().toISOString().replace(/[:.]/g, "-")}.jsonl`);
  fs.writeFileSync(recFile, JSON.stringify({ kind: "header", ...header }) + "\n");
  return recFile;
});
ipcMain.handle("rec-append", (_e, lines) => { if (recFile && Array.isArray(lines) && lines.length) fs.appendFileSync(recFile, lines.map(l => JSON.stringify(l)).join("\n") + "\n"); return !!recFile; });
ipcMain.handle("rec-stop", () => { const f = recFile; recFile = null; return f; });
ipcMain.handle("rec-delete", () => {
  recFile = null;
  let n = 0;
  try { for (const f of fs.readdirSync(recDir())) if (f.startsWith("session-")) { fs.unlinkSync(path.join(recDir(), f)); n++; } } catch (e) {}
  return n;
});
ipcMain.handle("show-file", (_e, f) => { try { require("electron").shell.showItemInFolder(f); return true; } catch (e) { return false; } });
// Residual Relay (../agent): the same agent ASI users reach on Agentverse
ipcMain.handle("agent-intent", (_e, intent) => agent.intent({ ...intent, source: "residual_ui" }));
ipcMain.handle("agent-cancel", (_e, id, confirmed) => agent.cancel(id, confirmed));
ipcMain.handle("agent-health", () => agent.check());
ipcMain.handle("agent-tasks", () => agent.tasks());
ipcMain.handle("agent-sim-reply", (_e, contact, text) => agent.simulateReply(contact, text));
// Scrolling is a real wheel event over the middle of the visible page (lib/scroll.js): it works on pages that scroll inside a
// container (YouTube Shorts, Instagram Reels) and on smooth-scrolling pages, where window.scrollBy did nothing.
async function scrollActive(dir) {
  const v = active && views[active]; if (!v) return null;
  const wc = v.webContents;
  const res = await scrollPage({ wc, dir, pageCall,
    inject: async () => { await wc.executeJavaScript(INJECT); sync(active); },
    fallback: () => { const z = wc.getZoomFactor() || 1, vh = Math.max(100, stageRect.height - (dockOn ? dockH : 0)) / z;
                      return { cx: stageRect.width / 2 / z, cy: vh / 2, vh }; } });
  tele("scroll", { app: active, dir, url: wc.getURL().slice(0, 80), hidden: viewHidden, visible: wc.isDestroyed() ? null : win && win.isVisible(), res: res && res.done });   // so "it glitched" can be looked up in the Lab's log
  return res;
}
ipcMain.handle("page-call", (_e, method, ...args) => (method === "scroll" ? scrollActive(args[0] === "up" ? "up" : "down") : pageCall(method, ...args)));
ipcMain.handle("press-key", (_e, code) => { const v = active && views[active]; if (v) pressKey(v.webContents, code); return !!v; });
ipcMain.handle("open-lab", () => { openLab(); return true; });
ipcMain.handle("lab-is-open", () => !!(lab && !lab.isDestroyed()));
ipcMain.handle("engine-proc", () => engine.state);
ipcMain.on("telemetry", (_e, d) => tele(d.kind || "shell", d));
ipcMain.on("care", (_e, kind, data) => store.care(kind, data || {}));
ipcMain.on("stage-rect", (_e, r) => {
  stageRect = { x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height) };
  layout();
});
ipcMain.on("view-hidden", (_e, hidden) => { viewHidden = !!hidden; layout(); });
ipcMain.on("mode", (_e, m) => { mode = m; for (const id of Object.keys(views)) sync(id); });

/* ------------------------------------------------------------------ IPC: lab */
ipcMain.handle("lab-backlog", () => ({ tele: teleLog.slice(-300), config: { engineUrl: CONFIG.engineUrl, person: CONFIG.person, inputMode: CONFIG.input?.mode || "two" },
                                       ai: ai.status, aiCalls: ai.calls.slice(0, 20), engine: engine.state, lifeline: lifeline.snapshot() }));
ipcMain.handle("lab-eval", () => {
  for (const p of [path.join(engine.dir, "eval_out", "results.json"), path.join(__dirname, "..", "engine", "eval_out", "results.json")]) {
    try { return JSON.parse(fs.readFileSync(p, "utf8")); } catch (e) {}
  }
  return null;
});
ipcMain.handle("lab-care", () => store.careReport(14));
// The Care Report's history of what was asked for and what happened (requests, orders): no message text.
ipcMain.handle("lab-history", async () => {
  const tasks = (await agent.tasks()) || [], orders = (await agent.orders()) || [];
  return [
    ...tasks.map(t => ({ t: t.created * 1000, kind: t.kind, intent: t.kind === "emergency" ? "call for help" : t.kind === "help" ? "caregiver help" : "message",
      source: t.source, agent: "Residual Relay", service: "Photon" + ((t.attempts || []).some(a => a.simulated) ? " (SIMULATED)" : ""),
      amount: null, approval: false, outcome: t.status, error: t.outcome && t.outcome.error || null })),
    ...orders.map(o => ({ t: o.created * 1000, kind: "order", intent: o.intent, source: o.source, agent: o.agent || ".", service: o.merchant_name || o.merchant_id,
      amount: o.total_cents, approval: !!o.approval_required, approvedBy: o.approved_by || null, outcome: o.status + (o.receipt ? " (sandbox)" : ""), error: o.error || null })),
  ].sort((a, b) => b.t - a.t);
});
ipcMain.handle("circle", () => agent.circle());
ipcMain.handle("circle-say", (_e, member, text) => agent.circleSay(member, text));
ipcMain.handle("errands", () => agent.errands());
ipcMain.handle("errand-new", (_e, goal, url) => agent.errandNew(String(goal || ""), String(url || "")));
ipcMain.handle("errand-consent", (_e, id, yes) => agent.errandConsent(id, yes));
ipcMain.handle("errand-cancel", (_e, id) => agent.errandCancel(id));
// Watching the browser the Hands are using: Browser Use's own live view, shown in the stage. Nothing else is ever opened here.
ipcMain.handle("errand-live", (_e, url) => { const safe = safeLiveUrl(url); if (!safe) return false; show("aiweb", safe); return true; });
ipcMain.handle("order-new", (_e, which) => agent.order(which));
ipcMain.handle("order-confirm", (_e, id, yes) => agent.confirmOrder(id, yes));
ipcMain.handle("lab-cmd", (_e, a) => { send("lab-cmd", a); return true; });   // presenter controls relayed to Home

app.whenReady().then(async () => {
  // Netflix needs Widevine DRM. Stock Electron doesn't ship it; the castLabs build does (see README).
  try { const { components } = require("electron"); if (components && components.whenReady) await components.whenReady(); } catch (e) {}
  store = new Store(app.getPath("userData"));
  if (store.d.voiceId) { store.d.voiceId = ""; store.save(); }   // an earlier in-app choice must never override the voice in .env
  session.fromPartition("persist:residual").setUserAgent(UA);
  const menu = Menu.buildFromTemplate([
    ...(process.platform === "darwin" ? [{ role: "appMenu" }] : []),
    { label: "View", submenu: [{ label: "Open Lab", accelerator: "CmdOrCtrl+L", click: openLab }, { role: "reload" }, { role: "toggleDevTools" }, { role: "togglefullscreen" }] },
    { role: "editMenu" }, { role: "windowMenu" }
  ]);
  Menu.setApplicationMenu(menu);
  createWindow();
  if (PREFLIGHT) setTimeout(async () => {
    const { table, centralFailed } = require("./lib/preflight");
    const rows = await preflight();
    console.log("\nResidual preflight\n\n" + table(rows) + "\n\n" + (centralFailed(rows) ? "FAIL: the core demo isn't ready." : "Core demo ready.") + "\n");
    app.exit(centralFailed(rows) ? 1 : 0);
  }, 3000);
  ai.check().then(s => send("ai", s));
  setInterval(() => ai.check().then(s => send("ai", s)), 30000);
  // Two-choice mode uses the eye tracker in Home instead of the Python engine (both would want the camera).
  if ((CONFIG.input?.mode || "two") !== "two") { engine.start(); engine.watchHealth(); }
  agent.check(); setInterval(() => agent.check(), 10000);
  // WhatsApp in the background, so unread messages can be noticed and suggested
  if ((CONFIG.preload || []).includes("whatsapp")) setTimeout(() => { const v = getView("whatsapp"); if (!win.contentView.children.includes(v)) win.contentView.addChildView(v); layout(); }, 4000);
});
app.on("before-quit", e => { if (lifeline.busy) { e.preventDefault(); send("status", { text: "Still calling for help. Cancel it first, or wait for an answer." }); } });
app.on("window-all-closed", () => { if (!lifeline.busy) { engine.stop(); app.quit(); } });
app.on("will-quit", () => engine.stop());
