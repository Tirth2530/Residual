// Residual Lab: the technical screen. Everything here is drawn from live data:
//   engine websocket  state (~12 Hz), per-frame trace (amplitude, LLR, CUSUM, bound), events
//   app telemetry     what the person's screen did with it (roles, actions, AI calls, intents)
// Nothing is animated for effect. If a number moves, the system moved it.

const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const CH = ["hand", "brow", "blink"];
const COL = { hand: "#5aa9e6", brow: "#e9a23b", blink: "#b48cf2", gaze: "#4fd1a5", ok: "#4fd1a5", warn: "#e9a23b", bad: "#f06464", sel: "#f4b63f",
              grid: "#1b232d", line: "#222b36", text: "#d9e0e8", muted: "#8b97a7", faint: "#5a6677" };
const NAME = { hand: "Hand pinch", brow: "Eyebrow", blink: "Long blink", gaze_r: "Look right", gaze_l: "Look left" };
const L = {
  ws: null, on: false, state: null, trace: [], events: [], log: [], tele: { roles: null, intents: null, adapter: null, ai: [], acts: [] },
  aiStatus: null, engineProc: null, agent: { tasks: {}, health: null, calls: [] }, lastMigrate: -1e9, cfg: { engineUrl: "ws://localhost:8000/ws" }
};
const fmt = (x, d = 2) => (x == null || Number.isNaN(x) ? "." : (+x).toFixed(d));
const pct = x => (x == null ? "." : Math.round(x * 100) + "%");
const tclock = t => new Date(t).toLocaleTimeString([], { hour12: false, minute: "2-digit", second: "2-digit" });

/* ================================================================ data in */
function connect() {
  let ws;
  try { ws = new WebSocket(L.cfg.engineUrl); } catch (e) { return setTimeout(connect, 2000); }
  L.ws = ws;
  ws.onopen = () => { L.on = true; addLog(Date.now(), "lab", "engine connected"); };
  ws.onclose = () => { if (L.on) addLog(Date.now(), "lab", "engine disconnected"); L.on = false; setTimeout(connect, 2000); };
  ws.onerror = () => {};
  ws.onmessage = m => {
    const msg = JSON.parse(m.data);
    if (msg.type === "trace") { for (const f of msg.frames) L.trace.push(f); if (L.trace.length > 900) L.trace.splice(0, L.trace.length - 900); }
    else if (msg.type === "state") { L.state = msg.state; panels(); }
    else if (msg.type === "events") for (const e of msg.events) onEvent(e);
  };
}
function onEvent(e) {
  L.events.push(e); if (L.events.length > 600) L.events.shift();
  const d = e.data || {};
  const s = {
    fire: () => `fire ${(d.option || []).join("+")}  S=${fmt(d.score)} > ${fmt(d.bound)}`,
    aux_fire: () => `aux fire ${d.channel}  S=${fmt(d.score)} > ${fmt(d.bound)}  (Next/Back test)`,
    attempt: () => `${d.channel} episode strength ${fmt(d.strength)} ${d.fired ? "fired" : "no fire"}${d.accepted ? "" : " (gated)"}`,
    changepoint: () => `changepoint ${d.channel}: level ~${fmt(d.level)}`,
    verify_start: () => `${d.probe ? "check" : "verify"} ${(d.option || []).join("+")}: ${d.reason || ""}`,
    verify_ok: () => `verified ${(d.option || []).join("+")}`, verify_fail: () => `verify failed ${(d.option || []).join("+")}`,
    probe_ok: () => `check passed`, probe_fail: () => `check failed`,
    migrate: () => { L.lastMigrate = e.t; return `MIGRATE ${(d.from || []).join("+")} -> ${(d.to || []).join("+")} (${d.reason})`; },
    gaze: () => `gaze ${d.dir} held ${fmt(d.held_s)} s strength ${fmt(d.strength)}`,
    help: () => `HELP PATTERN: ${d.why || ""}`,
    calibration: () => `calibration ${d.phase}: ${d.prompt || ""}`,
    log: () => d.text, select: () => null, speak: () => null, confirm: () => null, cancel: () => null, alert: () => null
  }[e.kind];
  const text = s ? s() : e.kind;
  if (text && !(e.kind === "attempt" && !L.showAttempts)) addLog(Date.now(), "engine " + e.t.toFixed(1), text, e.kind);
}
/* ================================================================ two-choice mode (fed by Home's two.js) */
const TW = { state: null, hist: [], log: [], lastFrame: 0 };
function onTwo(e) {
  if (e.kind === "two-state") {
    TW.state = e;
    TW.hist.push({ t: e.t, eye: e.eye, brow: e.brow, th: e.th });
    while (TW.hist.length && e.t - TW.hist[0].t > 10000) TW.hist.shift();
    return true;
  }
  if (e.kind === "two-frame") { $("#t-img").src = e.jpg; $("#t-none").hidden = true; TW.lastFrame = Date.now(); return true; }
  const add = (cls, text) => { TW.log.unshift({ t: e.t, cls, text }); TW.log = TW.log.slice(0, 80); TW.marks = (TW.marks || []).concat([{ t: e.t, cls }]).slice(-40); };
  if (e.kind === "two-gesture") {
    const name = { doubleBlink: "blink twice", brow: "eyebrows", longBlink: "eyes closed a moment", lookLeft: "look left", lookRight: "look right" }[e.input] || e.input;
    const as = e.as === "scan" ? `pick the lit side (${e.lit})` : e.as;
    add(e.as === "ignored" ? "g-ignored" : "g-" + (e.as === "scan" ? e.lit : e.as), `${name} → ${as}${e.where !== "use" ? "  (" + e.where + ")" : ""}`);
    return true;
  }
  if (e.kind === "two-pick") { add("g-pick", `chose ${e.side}: ${(e.labels || []).join(", ").slice(0, 70)}`); return true; }
  if (e.kind === "two-help") { add("g-help", "HELP: three long closures"); return true; }
  if (e.kind === "two-signals") { add("g-pick", `control: ${e.mode === "one" ? "one signal (scanning)" : "two signals"}${e.why ? " because " + e.why : ""}`); return true; }
  if (e.kind === "two-question") { add("g-pick", `asked: ${e.title}`); return true; }
  if (e.kind === "two-order") { add("g-pick", `order: ${e.text}`); return true; }
  if (e.kind === "two-say") { add("g-pick", `said: “${e.text}”`); return true; }
  if (e.kind === "two-refresh") { add("g-pick", `choices refreshed${e.why ? " (" + e.why + ")" : ""}`); return true; }
  if (e.kind === "two-adapt") { add("g-help", `adaptation evidence: ${(e.reasons || []).join("; ")}`); return true; }
  if (e.kind === "two-rec") { add("g-pick", e.deleted != null ? `deleted ${e.deleted} recordings` : e.on ? `recording started: ${e.file}` : `recording saved: ${e.file}`); return true; }
  if (e.kind === "two-test") { add("g-pick", `control test finished: ${e.falsePerMinute ?? "."} false commands / min`); return true; }
  return false;
}
function drawTwo() {
  const st = TW.state;
  if (!st) return;
  $("#t-fps").textContent = `${st.fps} fps`;
  $("#t-camstate").innerHTML = st.cameraError ? `<span class="loss">${esc(st.cameraError)}</span>` : st.face ? '<span class="win">face found</span>' : '<span class="loss">no face</span>';
  if (Date.now() - TW.lastFrame > 2000) { $("#t-none").hidden = false; $("#t-none").textContent = st.cameraError || "No camera picture"; }
  $("#t-scheme").textContent = st.scheme + (st.simulated ? "  ·  SIMULATED weakening" : "");
  document.body.classList.toggle("sim", !!st.simulated);
  $("#t-rec").innerHTML = st.recording ? '<span class="loss">● recording (numbers only)</span>' : "not recording";
  const gl = st.glances, wk = st.wink;
  $("#t-cal").innerHTML = `<span>using</span><span>${esc(st.controls)}</span><span>glances</span><span>${gl && gl.ok ? `learned · range L ${gl.left.toFixed(3)} R ${gl.right.toFixed(3)}` : "not learned"}</span>` +
    `<span>wink</span><span>${wk ? (wk.ok ? `learned, ${wk.eye === "L" ? "left" : "right"} eye` : "tried, not clear enough") : "not learned"}</span>` +
    (st.lastTest ? `<span>last test</span><span>${st.lastTest.falsePerMinute ?? "."} false / min</span>` : "");
  const qv = st.quality;
  if (qv) {
    $("#t-epsname").textContent = qv.name[0].toUpperCase() + qv.name.slice(1);
    $("#t-verdict").innerHTML = `<span class="${qv.verdict === "weak" ? "loss" : qv.verdict === "clear" ? "win" : ""}">${qv.verdict}</span>`;
    $("#t-why").innerHTML = (qv.reasons.length ? qv.reasons.map(r => `<div class="${qv.verdict}">· ${esc(r)}</div>`).join("") : '<div style="color:var(--faint)">No sign of weakening in recent attempts</div>') +
      (st.adaptWhy && st.adaptWhy.length ? `<div style="margin-top:6px;color:var(--faint)">Last question was asked because: ${esc(st.adaptWhy.join("; "))}</div>` : "");
    const q = qv.stats;
    $("#t-qstats").innerHTML = `<span>attempts kept</span><span>${q.attempts}</span><span>last ${q.lastN}: missed / near</span><span>${q.missed} / ${q.nearMiss}</span><span>median strength</span><span>${Math.round(q.medianMargin * 100)}%</span><span>retries after a miss (2 min)</span><span>${q.inferredMisses}</span><span>timing spread</span><span>${q.timingCv == null ? "." : Math.round(q.timingCv * 100) + "%"}</span><span>face seen</span><span>${Math.round(q.faceRate * 100)}%</span>`;
  }
  const bm = st.backMs, hm = st.helpMs;
  $("#t-map").innerHTML = st.signals === "one"
    ? `<span>pick</span><span>${st.prefs.oneSignal === "brow" ? "eyebrows" : "blink twice"} when a side is lit</span><span>back</span><span>eyes closed ${bm.min / 1000}–${bm.max / 1000} s</span>`
    : `<span>left</span><span>blink twice</span><span>right</span><span>eyebrows${st.prefs.browTwice ? " twice" : ""}</span><span>back</span><span>eyes closed ${bm.min / 1000}–${bm.max / 1000} s</span>`;
  $("#t-map").innerHTML += `<span>help</span><span>3 closures of ${hm.minMs / 1000}–${hm.maxMs / 1000} s within ${hm.windowMs / 1000} s</span><span>adapts</span><span>${st.prefs.adapt ? "asks when eyebrows weaken" : "off"}</span>`;
  // the closure ruler: where the current closure sits among blink / back / dead zone / help / resting
  const R = $("#t-ruler"), max = 5000, x = ms => (Math.min(ms, max) / max) * 100;
  if (!R.dataset.drawn) {
    R.dataset.drawn = 1;
    const zones = [[0, 500, "#6fb3ea", "blink"], [500, bm.min, "#2a333f", ""], [bm.min, bm.max, "#4fd1a5", "Back"], [bm.max, hm.minMs, "#2a333f", ""], [hm.minMs, hm.maxMs, "#f06464", "help"], [hm.maxMs, max, "#3a4250", "resting"]];
    R.innerHTML = zones.map(([a, b, c, n]) => `<div class="z" style="left:${x(a)}%;width:${x(b) - x(a)}%;background:${c}">${n}</div>`).join("") +
      [0, 1, 2, 3, 4, 5].map(sx => `<span class="tick" style="left:${sx * 20}%">${sx}s</span>`).join("") + '<i class="needle"></i>';
  }
  R.querySelector(".needle").style.left = `calc(${x(st.closedMs)}% - 1px)`;
  $("#t-closed").textContent = st.closedMs ? `${(st.closedMs / 1000).toFixed(2)} s closed` : "eyes open";
  $("#t-help").textContent = `${st.help} / 3`;
  $("#t-first").textContent = st.firstBlink ? "one blink, waiting for the second" : "armed";
  $("#t-browstate").textContent = (st.browState || ".").replace("_", " ");
  $("#t-mode").textContent = (st.signals === "one" ? "one signal" : "two signals") + (st.paused ? ", paused" : "") + (st.setup ? ", in setup" : "");
  $("#t-crumbs").textContent = st.crumbs || "";
  $("#t-left").innerHTML = st.left.map(l => `<li>${esc(l)}</li>`).join("");
  $("#t-right").innerHTML = st.right.map(l => `<li>${esc(l)}</li>`).join("");
  $("#t-left").parentElement.classList.toggle("lit", st.signals === "one" && st.lit === "left");
  $("#t-right").parentElement.classList.toggle("lit", st.signals === "one" && st.lit === "right");
  $("#t-q").textContent = st.question ? "Asking: " + st.question : "";
  const ai = st.aiweb;
  $("#t-ai").innerHTML = !ai ? "Not in use" : `<div>${esc(ai.status)}${ai.busySince ? `, working ${Math.round((Date.now() - ai.busySince) / 1000)} s` : ""}${ai.doing ? ": " + esc(ai.doing) : ""}</div>
    <div style="color:var(--faint)">${esc(ai.url || "")}</div>${ai.error ? `<div class="loss">${esc(ai.error)}</div>` : ""}
    ${(ai.actions || []).map(a => `<div>· ${esc(a.label)}</div>`).join("")}`;
  $("#t-log").innerHTML = TW.log.map(l => `<div class="${l.cls}"><span style="color:var(--faint)">${tclock(l.t)}</span>  ${esc(l.text)}</div>`).join("");
  // signals: eyes closed (both) and eyebrows, with this person's thresholds and the gestures that fired
  const cv = $("#t-sig"), { g, w: W, h: H } = fit(cv), now = st.t;
  const lane = (y0, h, key, on, off, col, name) => {
    g.strokeStyle = "#222b36"; g.strokeRect(0, y0, W, h);
    const y = v => y0 + h - Math.min(1, Math.max(0, v)) * h;
    g.strokeStyle = col; g.setLineDash([]); g.beginPath(); g.moveTo(0, y(on)); g.lineTo(W, y(on)); g.stroke();
    g.setLineDash([4, 4]); g.beginPath(); g.moveTo(0, y(off)); g.lineTo(W, y(off)); g.stroke(); g.setLineDash([]);
    line(g, TW.hist.filter(p => p[key] != null).map(p => [W - ((now - p.t) / 10000) * W, y(p[key])]), col, 2);
    text(g, name, 6, y0 + 14, col);
  };
  const half = (H - 10) / 2;
  lane(0, half, "eye", st.th.eyeOn, st.th.eyeOff, "#6fb3ea", "eyes closed");
  lane(half + 10, half, "brow", st.th.browOn, st.th.browOff, "#f2b84b", "eyebrows");
  for (const m of TW.marks || []) {
    const mx = W - ((now - m.t) / 10000) * W;
    if (mx < 0) continue;
    g.fillStyle = m.cls === "g-left" ? "#6fb3ea" : m.cls === "g-right" ? "#f2b84b" : m.cls === "g-back" ? "#4fd1a5" : m.cls === "g-help" ? "#f06464" : "#5a6677";
    g.fillRect(mx - 1, 0, 2, H);
  }
  // eyebrow strength per deliberate raise (the adaptation signal)
  const ce = $("#t-eps"), { g: ge, w: EW, h: EH } = fit(ce), eps = st.episodes || [];
  const ey = m => EH - 16 - Math.max(-0.5, Math.min(1.5, m)) / 2 * (EH - 26) - (0.5 / 2) * (EH - 26);
  ge.strokeStyle = "#2a333f"; [0, 1].forEach(v => { ge.beginPath(); ge.moveTo(0, ey(v)); ge.lineTo(EW, ey(v)); ge.stroke(); });
  text(ge, "threshold", 4, ey(0) - 3); text(ge, "learned", 4, ey(1) - 3);
  const bw = Math.min(40, (EW - 80) / Math.max(1, eps.length));
  eps.forEach((e, i) => { ge.fillStyle = e.full ? (e.margin < 0.15 ? "#e9a23b" : "#4fd1a5") : "#f06464"; const x0 = 70 + i * (bw + 6); const y1 = ey(e.margin), y0 = ey(0); ge.fillRect(x0, Math.min(y0, y1), bw, Math.max(2, Math.abs(y1 - y0))); });
  if (!eps.length) text(ge, "No deliberate eyebrow raises yet", 70, EH / 2);
}

function drawPreflight(rows) {
  $("#t-pf-box").hidden = false;
  $("#t-pf-at").textContent = new Date().toLocaleTimeString([], { hour12: false });
  $("#t-pf").innerHTML = rows.map(r => `<div><b class="${r.level}">${r.level}</b><span>${esc(r.name)}</span><span style="color:var(--muted)">${esc(r.detail)}</span></div>`).join("");
}
function onTele(e) {
  if (e.kind === "preflight") return drawPreflight(e.rows || []);
  if (e.kind && e.kind.startsWith("two-") && onTwo(e)) return;
  if (e.kind === "aiweb" && TW) { TW.log.unshift({ t: e.t || Date.now(), cls: e.error ? "g-help" : "g-pick", text: `Browser Use: ${e.status}${e.url ? " " + e.url : ""}${e.actions ? `, ${e.actions} actions` : ""}${e.error ? ", " + e.error : ""}` }); TW.log = TW.log.slice(0, 80); }
  if (e.kind === "tts" && TW) { TW.log.unshift({ t: e.t || Date.now(), cls: "g-pick", text: e.ok ? "ElevenLabs voice used" : `system voice (ElevenLabs: ${e.why || "off"})` }); TW.log = TW.log.slice(0, 80); }
  const T = L.tele;
  if (e.kind === "roles") T.roles = e;
  else if (e.kind === "intents") T.intents = e;
  else if (e.kind === "adapter") T.adapter = e;
  else if (e.kind === "ai") { T.ai.unshift(e); T.ai = T.ai.slice(0, 30); addLog(e.t, "ai", `${e.task}: ${e.provider}/${e.model || "-"} ${e.ms} ms ${e.ok ? "used" : "rule fallback" + (e.note ? " (" + e.note + ")" : "")}`); }
  else if (e.kind === "ai-status") L.aiStatus = e;
  else if (e.kind === "agent-task") { L.agent.tasks[e.task.task_id] = e.task; drawAgent(); }
  else if (e.kind === "agent-health") { L.agent.health = e; drawAgent(); }
  else if (e.kind === "agent-call") { L.agent.calls.unshift(e); L.agent.calls = L.agent.calls.slice(0, 20);
    addLog(e.t, "agent", `${e.intent.intent}${e.intent.need ? " " + e.intent.need : ""}${e.intent.contact ? " to " + e.intent.contact : ""} -> ${e.res.status}${e.res.error ? " (" + e.res.error + ")" : ""} in ${e.ms} ms`);
    if (e.res.task_id) L.agent.tasks[e.res.task_id] = e.res; drawAgent(); }
  else if (e.kind === "engine-proc") L.engineProc = e;
  else if (e.kind === "act") { T.acts.unshift(e); T.acts = T.acts.slice(0, 12); addLog(e.t, "app", `${e.action.toUpperCase()} from ${e.source} on ${e.screen}${e.sheet ? " (" + e.sheet + ")" : ""}`); }
  else if (e.kind === "activate") addLog(e.t, "app", `chose "${e.label}" on ${e.screen}`);
  else if (["screen", "sheet", "mode", "status", "banner", "view", "lifeline", "news", "signal"].includes(e.kind)) {
    const txt = { screen: () => `screen ${e.id}`, sheet: () => `overlay ${e.type}`, mode: () => `mode ${e.mode} movements`, status: () => e.text, banner: () => e.text,
                  view: () => e.active ? `opened ${e.active}` : "back to Home", lifeline: () => `lifeline ${e.active ? (e.ackBy ? "answered by " + e.ackBy : "calling: " + (e.reached || []).join(", ")) : "idle"}`,
                  news: () => `news ${e.section}: ${e.n} stories in ${e.ms} ms`, signal: () => `WhatsApp unread ${e.unread}` }[e.kind]();
    addLog(e.t, "app", txt);
  }
  if (["roles", "intents", "adapter", "ai", "ai-status"].includes(e.kind)) panelsSlow();
}
function addLog(t, src, text, kind) {
  L.log.unshift({ t, src, text, kind }); if (L.log.length > 400) L.log.pop();
  logDirty = true;
}
let logDirty = false;

/* ================================================================ canvases */
function fit(cv) {
  const r = cv.getBoundingClientRect(), d = devicePixelRatio || 1;
  const w = Math.max(10, Math.round(r.width * d)), h = Math.max(10, Math.round(r.height * d));
  if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
  const g = cv.getContext("2d"); g.setTransform(d, 0, 0, d, 0, 0);
  return { g, w: r.width, h: r.height };
}
function line(g, pts, color, w = 1.4, dash = []) {
  if (!pts.length) return; g.save(); g.strokeStyle = color; g.lineWidth = w; g.setLineDash(dash); g.beginPath();
  pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y))); g.stroke(); g.restore();
}
function text(g, s, x, y, color = COL.muted, font = "11px PlexMono, monospace", align = "left") { g.fillStyle = color; g.font = font; g.textAlign = align; g.fillText(s, x, y); }

function drawChannels() {
  const { g, w, h } = fit($("#chan"));
  g.clearRect(0, 0, w, h);
  const tr = L.trace, st = L.state;
  if (!tr.length) { text(g, L.on ? "waiting for frames…" : "engine offline: start it, or run python -m residual.server --source autopilot --app", 12, 20, COL.faint, "12px Plex, sans-serif"); return; }
  const tEnd = tr[tr.length - 1].t, tStart = tEnd - 10, X = t => 150 + (t - tStart) / 10 * (w - 160);
  const laneH = h / 3;
  CH.forEach((c, k) => {
    const y0 = k * laneH, ampH = laneH * 0.56, cusH = laneH * 0.34, yA = y0 + 6, yC = y0 + ampH + 12;
    const ch = st?.channels?.[c] || {};
    const pts = tr.filter(f => f.t >= tStart);
    // watched shading
    let run = null;
    for (const f of pts) {
      const on = f.w.includes(c);
      if (on && run === null) run = f.t;
      if ((!on || f === pts[pts.length - 1]) && run !== null) { g.fillStyle = "rgba(244,182,63,0.05)"; g.fillRect(X(run), y0 + 2, X(f.t) - X(run), laneH - 4); run = null; }
    }
    // amplitude
    const AY = v => yA + ampH - 6 - (Math.max(-0.2, Math.min(1.3, v)) + 0.2) / 1.5 * (ampH - 6);
    g.strokeStyle = COL.grid; g.lineWidth = 1; g.beginPath(); g.moveTo(150, AY(0)); g.lineTo(w - 10, AY(0)); g.stroke();
    if (ch.onset != null) { line(g, [[150, AY(ch.onset)], [w - 10, AY(ch.onset)]], COL.muted, 1, [4, 4]); text(g, "onset " + fmt(ch.onset), w - 12, AY(ch.onset) - 3, COL.faint, "10px PlexMono", "right"); }
    if (ch.level != null) { line(g, [[150, AY(ch.level)], [w - 10, AY(ch.level)]], COL[c], 1, [1, 3]); text(g, "Kalman level " + fmt(ch.level), 156, AY(ch.level) - 3, COL[c], "10px PlexMono"); }
    line(g, pts.map(f => [X(f.t), AY(f.v[CH.indexOf(c)] ? f.a[CH.indexOf(c)] : 0)]), COL[c], 1.5);
    // evidence: the channel's own CUSUM when it is being listened to, else the Next/Back accumulator
    const watched = pts.length && pts[pts.length - 1].w.includes(c);
    const bound = watched ? pts[pts.length - 1].b : (st?.evidence?.aux_bound || 4.5);
    const top = bound * 1.6, CY = v => yC + cusH - Math.min(top, v) / top * cusH;
    g.fillStyle = "rgba(255,255,255,0.025)"; g.fillRect(150, yC, w - 160, cusH);
    line(g, [[150, CY(bound)], [w - 10, CY(bound)]], watched ? COL.sel : COL.faint, 1, [5, 3]);
    const ci = CH.indexOf(c);
    g.save(); g.fillStyle = (watched ? "rgba(244,182,63,0.22)" : "rgba(139,151,167,0.18)"); g.beginPath(); g.moveTo(X(pts[0].t), CY(0));
    for (const f of pts) g.lineTo(X(f.t), CY(f.w.includes(c) ? f.s[ci] : (f.x ? f.x[ci] : 0)));
    g.lineTo(X(pts[pts.length - 1].t), CY(0)); g.closePath(); g.fill(); g.restore();
    line(g, pts.map(f => [X(f.t), CY(f.w.includes(c) ? f.s[ci] : (f.x ? f.x[ci] : 0))]), watched ? COL.sel : COL.muted, 1.2);
    text(g, watched ? `S vs bound ${fmt(bound)}` : `Next/Back test vs ${fmt(bound)}`, w - 12, yC + 10, watched ? COL.sel : COL.faint, "10px PlexMono", "right");
    // event markers
    for (const e of L.events) {
      if (e.t < tStart || e.t > tEnd) continue;
      const d = e.data || {};
      let col = null, lab = "";
      if (e.kind === "fire" && (d.option || []).includes(c)) { col = COL.sel; lab = "fire"; }
      else if (e.kind === "aux_fire" && d.channel === c) { col = COL.gaze; lab = "aux"; }
      else if (e.kind === "changepoint" && d.channel === c) { col = COL.bad; lab = "changepoint"; }
      else if (e.kind === "attempt" && d.channel === c && !d.fired && (st?.control || []).includes(c)) { col = COL.warn; lab = "missed"; }
      else if (e.kind === "migrate") { col = COL.ok; lab = "migrate"; }
      if (!col) continue;
      line(g, [[X(e.t), y0 + 4], [X(e.t), y0 + laneH - 4]], col, 1.3);
      text(g, lab, X(e.t) + 3, y0 + 14, col, "10px PlexMono");
    }
    // label block
    const s = ch.stats || {};
    text(g, NAME[c], 0, y0 + 18, COL[c], "600 13px Plex, sans-serif");
    text(g, (st?.control || []).includes(c) ? "selects" : roleOf(c) || "idle", 0, y0 + 34, COL.muted, "11px Plex, sans-serif");
    const rows = [["p_hit", `${pct(s.p_hat)} [${pct(s.p_lo)}, ${pct(s.p_hi)}]`], ["level", `${fmt(ch.level)} ± ${fmt(ch.level_sd)}`],
                  ["trend", `${fmt((ch.trend || 0) * 60, 3)}/min`], ["P(change)", fmt(ch.cp_prob)], ["hit rate", `${pct(ch.obs_rate)} n=${fmt(ch.obs_n, 1)}`]];
    rows.forEach(([k2, v], i) => { text(g, k2, 0, y0 + 54 + i * 15, COL.faint, "10px PlexMono"); text(g, v, 140, y0 + 54 + i * 15, COL.text, "10px PlexMono", "right"); });
    if (k < 2) { g.strokeStyle = COL.line; g.beginPath(); g.moveTo(0, y0 + laneH - 0.5); g.lineTo(w, y0 + laneH - 0.5); g.stroke(); }
  });
  // time axis ticks
  for (let s = Math.ceil(tStart); s <= tEnd; s += 2) text(g, `${s.toFixed(0)}s`, X(s), h - 2, COL.faint, "9px PlexMono", "center");
}
function roleOf(c) {
  const r = L.tele.roles?.roles; if (!r) return "";
  return r.next === c ? "Next" : r.back === c ? "Back" : "";
}

function drawRel() {
  const { g, w, h } = fit($("#rel"));
  g.clearRect(0, 0, w, h);
  const hist = L.state?.history; if (!hist) return;
  const tEnd = L.state.t, tStart = tEnd - 120, X = t => 34 + (t - tStart) / 120 * (w - 44), Y = p => 6 + (1 - p) * (h - 22);
  for (const p of [0, 0.5, 1]) { line(g, [[34, Y(p)], [w - 10, Y(p)]], COL.grid, 1); text(g, p.toFixed(1), 28, Y(p) + 3, COL.faint, "10px PlexMono", "right"); }
  line(g, [[34, Y(0.7)], [w - 10, Y(0.7)]], COL.bad, 1, [4, 4]);
  line(g, [[34, Y(0.85)], [w - 10, Y(0.85)]], COL.ok, 1, [4, 4]);
  let legendX = 44;
  const keys = [...CH]; const ctl = (L.state.control || []).join("+"); if (ctl.includes("+")) keys.push(ctl);
  for (const k of keys) {
    const pts = (hist[k] || []).filter(r => r[0] >= tStart); if (!pts.length) continue;
    const col = COL[k] || COL.sel;
    g.save(); g.fillStyle = col; g.globalAlpha = 0.12; g.beginPath();
    pts.forEach((r, i) => (i ? g.lineTo(X(r[0]), Y(r[3])) : g.moveTo(X(r[0]), Y(r[3]))));
    for (let i = pts.length - 1; i >= 0; i--) g.lineTo(X(pts[i][0]), Y(pts[i][2]));
    g.closePath(); g.fill(); g.restore();
    line(g, pts.map(r => [X(r[0]), Y(r[1])]), col, k === ctl ? 2.2 : 1.4);
    const last = pts[pts.length - 1];
    const label = `${NAME[k] || k} ${pct(last[1])} [${pct(last[2])}, ${pct(last[3])}]`;
    g.font = "10px PlexMono"; const lw = g.measureText(label).width;
    g.fillStyle = col; g.fillRect(legendX, h - 30, 10, 3);
    text(g, label, legendX + 14, h - 26, col, "10px PlexMono");
    legendX += lw + 34;
  }
  for (const e of L.events) if (e.kind === "migrate" && e.t >= tStart) { line(g, [[X(e.t), 4], [X(e.t), h - 16]], COL.ok, 1.2); text(g, "migrate", X(e.t) + 3, h - 18, COL.ok, "10px PlexMono"); }
  for (let s = Math.ceil(tStart / 20) * 20; s <= tEnd; s += 20) text(g, `${s}s`, X(s), h - 2, COL.faint, "9px PlexMono", "center");
}

function drawGaze() {
  const { g, w, h } = fit($("#gaze"));
  g.clearRect(0, 0, w, h);
  const e = L.state?.eyes, cx = w / 2, cy = h * 0.6, sx = (w / 2 - 12) / 2.2, sy = (h * 0.6 - 10) / 2.2;
  const P = (dx, dy) => [cx + dx * sx, cy - dy * sy];
  const enter = e?.cfg?.enter ?? 0.9, leave = e?.cfg?.leave ?? 0.45;
  g.fillStyle = "rgba(255,255,255,0.02)"; g.fillRect(0, 0, w, h);
  // trigger regions: beyond `enter` left/right, above `enter` up
  g.fillStyle = "rgba(79,209,165,0.06)";
  g.fillRect(...P(enter, 2.2), w, h); g.fillRect(0, 0, P(-enter, 0)[0], h); g.fillRect(0, 0, w, P(0, enter)[1]);
  for (const [v, dash, col] of [[enter, [5, 3], COL.gaze], [leave, [2, 3], COL.faint]]) {
    line(g, [P(v, -2.2), P(v, 2.2)], col, 1, dash); line(g, [P(-v, -2.2), P(-v, 2.2)], col, 1, dash); line(g, [P(-2.2, v), P(2.2, v)], col, 1, dash);
  }
  text(g, "look left = Back", 6, h - 6, COL.faint, "10px Plex"); text(g, "look right = Next", w - 6, h - 6, COL.faint, "10px Plex", "right"); text(g, "look up = pause", cx, 12, COL.faint, "10px Plex", "center");
  line(g, [P(-0.15, 0), P(0.15, 0)], COL.muted, 1); line(g, [P(0, -0.15), P(0, 0.15)], COL.muted, 1);
  const tr = L.trace.filter(f => f.g); const tEnd = tr.length ? tr[tr.length - 1].t : 0;
  const pts = tr.filter(f => f.t >= tEnd - 3).map(f => P(Math.max(-2.2, Math.min(2.2, f.g[0])), Math.max(-2.2, Math.min(2.2, f.g[1]))));
  if (pts.length) {
    line(g, pts, "rgba(79,209,165,0.5)", 1.2);
    const [x, y] = pts[pts.length - 1];
    g.fillStyle = e?.gesture?.cand ? COL.sel : COL.gaze; g.beginPath(); g.arc(x, y, 5, 0, 7); g.fill();
  } else text(g, e?.available ? "" : "no face (eye features need the webcam)", cx, h - 22, COL.faint, "11px Plex", "center");
  if (e?.gesture) {
    const gs = e.gesture;
    text(g, `dx ${fmt(gs.dx)}  dy ${fmt(gs.dy)}${gs.cand ? `  ${gs.cand} held ${fmt(gs.held)} / ${e.cfg?.dwell_s ?? 0.28} s` : ""}`, 6, 12, COL.muted, "10px PlexMono");
    text(g, `${gs.eyes_ok === false ? "eyes too closed  " : ""}${gs.head_ok === false ? "head turning" : ""}`, w - 6, 12, COL.bad, "10px PlexMono", "right");
  }
}

/* ================================================================ panels */
function panels() {
  const st = L.state; if (!st) return;
  const rt = st.runtime || {};
  const src = rt.source, fpsOk = (rt.fps || 0) > 10;
  $("#h-src").innerHTML = `<span class="dot ${L.on ? (fpsOk ? "ok" : "warn") : "bad"}"></span>${src === "autopilot" ? "simulated person" : src || "engine"} <b class="n">${fmt(rt.fps, 1)} fps</b>`;
  $("#h-t").innerHTML = `t <b class="n">${fmt(st.t, 1)} s</b>  person <b>${esc(rt.person || "default")}</b>`;
  $("#h-pol").innerHTML = `policy <b>${st.policy?.state}</b>  control <b>${(st.control || []).map(c => NAME[c]).join(" + ")}</b>`;
  $("#h-sim").hidden = !rt.simulated_weakening;
  drawArmCal(st);
  // camera
  if (rt.preview) { $("#cam-img").src = "data:image/jpeg;base64," + rt.preview; $("#cam-img").hidden = false; $("#cam-none").hidden = true; $("#cam-tag").hidden = false;
    $("#cam-tag").textContent = "MediaPipe 478 landmarks, iris, head pose"; }
  else { $("#cam-img").hidden = true; $("#cam-none").hidden = false; $("#cam-tag").hidden = true;
    $("#cam-none").innerHTML = rt.calibration ? `Calibrating: ${esc(rt.calibration.prompt)}` : src === "autopilot" ? "Simulated person: a model of a user drives the engine.<br>Same code path as the webcam after perception." : src === "keyboard" ? "Keyboard source: hold H / B / K to gesture." : "No camera frames."; }
  $("#cam-fps").textContent = `${fmt(rt.fps, 1)} fps`;
  // eyes
  const e = st.eyes;
  if (e) {
    $("#gz-word").textContent = e.word || "";
    $("#gz-yaw").textContent = `${fmt(e.yaw_speed, 0)} / ${e.cfg?.max_yaw_speed ?? 40} deg/s`;
    $("#gz-range").textContent = `${fmt(e.range?.left, 3)} / ${fmt(e.range?.right, 3)} / ${fmt(e.range?.up, 3)}`;
    $("#gz-count").textContent = `${e.counts?.left ?? 0} / ${e.counts?.right ?? 0} / ${e.counts?.up ?? 0}`;
    const f = e.fatigue || {}, W = e.weights || { ptosis: 0.4, perclos: 0.3, blinks: 0.1, slowing: 0.2 };
    const parts = [["lid droop vs own", f.ptosis, W.ptosis], ["PERCLOS 60 s", f.perclos_score, W.perclos], ["blink rate change", f.blink_score, W.blinks], ["glances slower", f.slowing, W.slowing]];
    $("#fat").innerHTML = parts.map(([n, v, wt]) => `<span style="color:var(--muted)">${n}</span><div class="bar"><i style="width:${(v || 0) * 100}%;background:${(v || 0) > 0.5 ? "var(--warn)" : "var(--muted)"}"></i></div><span>${fmt(v)}</span><span class="w">×${wt}</span>`).join("") +
      `<span style="color:var(--muted)">open / baseline</span><span></span><span>${fmt(f.open_ratio)}</span><span class="w">${f.from_profile ? "saved" : "today"}</span>`;
    $("#fat-score").innerHTML = `${fmt(f.score)} <span style="color:${f.level === "tired" ? "var(--bad)" : f.level === "tiring" ? "var(--warn)" : "var(--ok)"}">${f.level || ""}</span>`;
    $("#fat-score").title = `tiring at ${e.levels?.tiring ?? 0.3}, tired at ${e.levels?.tired ?? 0.55}`;
    const H = e.help || {};
    $("#help-bar").style.width = Math.min(100, (H.closed_for || 0) / (H.max_s || 4) * 100) + "%";
    $("#help-held").textContent = `${fmt(H.closed_for, 1)} s`;
    $("#help-rule").textContent = `${H.count ?? 3} closures of ${H.min_s ?? 1.2} to ${H.max_s ?? 4} s within ${H.window_s ?? 15} s`;
    $("#help-n").textContent = `${e.help_progress || 0} / ${H.count ?? 3}`;
  }
  // policy
  const pol = st.policy || {}, dg = pol.diag || {};
  const node = pol.state === "VERIFY" ? "VERIFY" : (st.t - L.lastMigrate < 3) ? "MIGRATE" : dg.degrading ? "DEGRADING" : "ACTIVE";
  document.querySelectorAll(".node").forEach(n => { n.classList.toggle("on", n.dataset.n === node); });
  $('.node[data-n="VERIFY"]').textContent = pol.probing ? "Check" : "Verify";
  $("#pol-sw").textContent = `${pol.switches || 0} switches  mode ${pol.mode}`;
  const th = dg.thresholds || {}, cl = dg.clauses || {};
  const rows = [
    ["p_hit_below_switch", `p_hit ${fmt(dg.p_hat)} < ${th.p_switch}`],
    ["changepoint_and_below_accept", `changepoint ${dg.cp_recent ? "yes" : "no"} and p_hit < ${th.p_accept}`],
    ["observed_hit_rate_low", `observed ${fmt(dg.obs_rate)} (n ${fmt(dg.obs_n, 1)}) < ${th.obs_rate_floor}`],
    ["false_alarms_over_budget", `false fires ${fmt(dg.fa_per_min)}/min > ${th.fa_max_per_min}`],
    ["recent_check_failed", "a check on it just failed"],
    ["better_channel_available", "another channel clearly better"]
  ];
  $("#clauses").innerHTML = rows.map(([k, t]) => `<div class="clause ${cl[k] ? "on" : ""}"><i></i><span>${t}</span><span class="n">${cl[k] ? "true" : ""}</span></div>`).join("");
  $("#dwell").textContent = `${fmt(dg.dwell, 1)} / ${dg.dwell_needed ?? 1.5} s before acting; idle ${fmt(dg.idle_s, 0)} s; weak ${dg.weak ?? 0}`;
  // evidence
  const ev = st.evidence || {}, c = ev.classes?.[ev.cls] || {};
  const frac = ev.bound ? Math.min(1.6, ev.score / ev.bound) / 1.6 : 0;
  $("#ev-i").style.width = frac * 100 + "%"; $("#ev-b").style.left = (1 / 1.6) * 100 + "%";
  $("#ev-s").textContent = `${fmt(ev.score)} on ${(ev.watch || []).join("+")}`;
  $("#ev-f").textContent = `bound ln((1-β)/α), α ${c.alpha}, β ${c.beta}`;
  $("#ev-bd").textContent = fmt(ev.bound, 3);
  $("#ev-cls").textContent = `action class: ${ev.cls}  (${Object.entries(ev.classes || {}).map(([k, v]) => `${k} ${fmt(v.bound, 2)}`).join(", ")})`;
  // decisions
  const decs = [...(pol.decisions || [])].reverse();
  $("#decs").innerHTML = decs.length ? decs.map(d => {
    const res = d.result, cls = /moved|still works/.test(res) ? "good" : res === "pending" ? "pend" : "bad";
    const ci = (p, col) => p ? `<span class="ci"><i style="left:${p[0] * 100}%;width:${Math.max(1, (p[2] - p[0]) * 100)}%;background:${col}"></i><b style="left:${p[1] * 100}%;background:${col}"></b></span><span class="n">${pct(p[1])}</span>` : "";
    return `<div class="dec"><div class="t"><b>${d.kind === "probe" ? "Check" : "Verify"} ${esc(d.to.map(c => NAME[c]).join(" + "))}</b><span class="n" style="color:var(--faint)">t ${d.t}${d.t_end ? `–${d.t_end}` : ""}</span><span class="res ${cls}">${esc(res)} ${d.hits}/${d.needed}</span></div>
      <ul><li>why: ${esc(d.why)}${Object.keys(d.clauses || {}).length ? ` (${Object.keys(d.clauses).map(k => k.replace(/_/g, " ")).join(", ")})` : ""}</li>
      <li>${esc((d.from || []).map(c => NAME[c]).join("+"))} ${ci(d.from_p, COL[d.from[0]] || COL.sel)}</li>
      ${d.kind !== "probe" ? `<li>${esc(d.to.map(c => NAME[c]).join("+"))} ${ci(d.to_p, COL[d.to[0]] || COL.sel)}</li>` : ""}</ul></div>`;
  }).join("") : `<p style="color:var(--faint);margin:0">No checks yet. Run "Hand fades" to watch one happen.</p>`;
  // presenter sliders follow the engine
  for (const inp of document.querySelectorAll(".lvl input")) {
    if (document.activeElement === inp) continue;
    const v = Math.round((rt.levels?.[inp.dataset.c] || 0) * 100); inp.value = v; inp.nextElementSibling.textContent = v;
  }
  document.querySelectorAll("[data-src]").forEach(b => b.classList.toggle("cur", b.dataset.src === src));
  const lk = document.querySelector('[data-x="lock"]'); lk.classList.toggle("cur", pol.mode === "fixed"); lk.textContent = pol.mode === "fixed" ? "Control locked" : "Lock control";
}
// Per movement: calibration verdict, whether it may be used at all, and the arming cycle
// (armed -> gesture -> fire -> wait for release -> armed). Plus the eyebrow's raw parts.
function drawArmCal(st) {
  const rt = st.runtime || {}, res = rt.calibration_result?.results || {};
  const rows = CH.map(c => {
    const ch = st.channels?.[c] || {}, r = res[c] || {};
    return `<tr><td>${NAME[c]}</td><td class="v-${r.status || ""}">${r.status || "not calibrated"}${r.count != null ? ` ${r.count}/3` : ""}</td>
      <td>${r.separation != null ? fmt(r.separation, 1) : "."}</td><td>${ch.eligible === false ? '<span class="loss">no</span>' : "yes"}</td>
      <td class="arm-${ch.arm}">${(ch.arm || ".").replace("_", " ")}</td><td>${fmt(ch.smooth)} &lt; ${fmt(ch.release_below)}</td></tr>`;
  }).join("");
  const B = rt.brow, p = B?.parts;
  const brow = p ? `<div class="kv" style="margin-top:6px"><span>brow inner / outer L / R</span><span>${fmt(p.inner, 3)} / ${fmt(p.outer_l, 3)} / ${fmt(p.outer_r, 3)}</span>
      <span>blendshape max(inner, mean outer)</span><span>${fmt(p.bs, 3)}  rest ${fmt(B.rest?.brow, 3)}  raised ${fmt(B.peak?.brow, 3)}</span>
      <span>geometric brow to eye</span><span>${fmt(p.geo, 4)}  rest ${fmt(B.rest?.brow_geo, 4)}  raised ${fmt(B.peak?.brow_geo, 4)}</span>
      <span>normalized bs / geo, combined</span><span>${fmt(B.normalized?.brow)} / ${fmt(B.normalized?.brow_geo)}  ${fmt(B.combined)}  sources ${esc((B.sources || []).join(", ") || ".")}</span></div>` : "";
  const cam = rt.camera ? `<div style="color:var(--muted);margin-top:4px">camera ${rt.camera.open === false ? '<span class="loss">not open</span>' : "open"}, last frame ${rt.camera.age == null ? "never" : fmt(rt.camera.age, 1) + " s ago"}${rt.camera.error ? " " + esc(rt.camera.error) : ""}</div>` : "";
  $("#armcal").innerHTML = `<table class="armt"><tr><th>movement</th><th>calibration</th><th>sep</th><th>usable</th><th>arming</th><th>release</th></tr>${rows}</table>${brow}${cam}`;
}
function drawAgent() {
  const A = L.agent, h = A.health;
  const head = h ? (h.ok ? `${h.agent || "Residual Relay"}, Photon ${h.photon?.mode === "photon" ? "live (" + (h.photon.platform || "") + ")" : "SIMULATED"}, calls ${h.calls || "off"}${h.demo_timing ? ", demo timing" : ""}` : "not running") : "";
  $("#ag-h").textContent = head;
  if ($("#t-agh")) $("#t-agh").innerHTML = h && h.ok && h.photon?.mode !== "photon" ? `<span class="loss">${esc(head)}</span>` : esc(head);
  const tasks = Object.values(A.tasks).sort((a, b) => b.created - a.created).slice(0, 4);
  if (!tasks.length) return;
  const html = tasks.map(t => {
    const ev = (t.events || []);
    const tools = (t.attempts || []).map(a => `${a.tool} → ${esc(a.contact)} ${a.ok ? "ok" : a.ok === false ? '<span class="loss">failed</span>' : "…"} ${a.ms != null ? a.ms + " ms" : ""}${a.simulated ? " SIMULATED" : ""}${a.error ? " " + esc(a.error) : ""}`).join("<br>");
    const reps = (t.replies || []).map(r => `${esc(r.contact)}: “${esc(r.text)}” (${r.verdict}${r.simulated ? ", SIMULATED reply" : ""})`).join("<br>");
    return `<div class="ag-task">
      <div><span class="k">INTENT</span>${esc(t.kind)} from ${esc(t.source || "?")} <span style="color:var(--faint)">${esc(t.task_id.slice(0, 8))}</span></div>
      <div><span class="k">PLAN</span>${esc((t.plan || []).join("; "))}</div>
      <div><span class="k">TOOL EXECUTION</span>${tools || "."}</div>
      <div><span class="k">RESPONSE</span>${reps || "waiting"}</div>
      <div><span class="k">OUTCOME</span><b>${esc(t.status)}</b> ${t.outcome ? esc(t.outcome.message || t.outcome.error || "") : ""}</div>
      <div>${ev.slice(-3).map(e => `<span class="ag-ev ${e.kind}">${new Date(e.t * 1000).toLocaleTimeString([], { hour12: false })} ${esc(e.text)}</span>`).join("<br>")}</div></div>`;
  }).join("");
  $("#agent").innerHTML = html;
  if ($("#t-agent")) $("#t-agent").innerHTML = html;
}
function panelsSlow() {
  const R = L.tele.roles;
  if (R) {
    const row = (k, v, why) => `<span class="k">${k}</span><span>${v ? `<span class="chipc"><i style="background:${COL[v] || COL.gaze}"></i>${esc(Array.isArray(v) ? v.map(c => NAME[c]).join(" + ") : NAME[v] || v)}</span>` : `<span style="color:var(--faint)">${why}</span>`}</span>`;
    $("#map").innerHTML = row("Select", R.roles.select?.length ? R.roles.select : null, "none") +
      row("Next", R.roles.next, R.mode === 1 ? "screen scans by itself" : "none") + row("Back", R.roles.back, "button on screen") +
      `<span class="k">rule</span><span style="color:var(--muted)">Select: engine control. Next/Back: eyes while fresh, else channels at p_hit ≥ 0.85 (own CUSUM).</span>` +
      `<span class="k">last</span><span class="n" style="color:var(--muted)">${L.tele.acts.slice(0, 3).map(a => `${a.action} ← ${a.source}`).join("  ") || "."}</span>`;
    $("#map-mode").textContent = `${R.mode} movement${R.mode > 1 ? "s" : ""}${R.mode === 1 ? ", auto-scan" : ""}${R.eyesNav ? ", eyes navigate" : ""}`;
  }
  const A = L.aiStatus;
  if (A) $("#h-ai").innerHTML = `AI <b>${A.ok ? esc(A.model) : "off"}</b> ${A.ok ? (A.local ? "on this computer" : "cloud") : esc(A.error || "")}`;
  const calls = L.tele.ai;
  const okN = calls.filter(c => c.ok).length, ms = calls.filter(c => c.ok).map(c => c.ms).sort((a, b) => a - b);
  $("#ai-sum").textContent = calls.length ? `${okN}/${calls.length} answered, median ${ms.length ? ms[ms.length >> 1] : "."} ms` : "";
  $("#ai-t").innerHTML = `<tr><th>time</th><th>task</th><th>model</th><th>ms</th><th>result</th></tr>` + calls.slice(0, 14).map(c =>
    `<tr><td>${tclock(c.t)}</td><td>${esc(c.task)}</td><td>${esc(c.model || "-")}</td><td>${c.ms}</td><td class="${c.ok ? "win" : "loss"}">${c.ok ? "used" : "rule fallback"}</td></tr>`).join("");
  const I = L.tele.intents;
  if (I) $("#int-t").innerHTML = `<tr><th>candidate</th><th>score</th><th>habit</th><th>rec</th><th>ctx</th><th>why</th></tr>` + I.top.map((r, i) =>
    `<tr style="${i < 3 ? "color:var(--text)" : "color:var(--faint)"}"><td>${esc(r.label.slice(0, 22))}</td><td>${fmt(r.score, 3)}</td><td>${fmt(r.parts.habit)}</td><td>${fmt(r.parts.recency)}</td><td>${fmt(r.parts.context)}</td><td>${esc((r.why || []).join(", "))}</td></tr>`).join("");
  const ad = L.tele.adapter;
  if (ad) $("#adapter").innerHTML = `${esc(ad.app)} / ${esc(ad.page)}: ` + Object.entries(ad.checks || {}).map(([k, v]) => `<span class="${v === 0 || v === false ? "loss" : "win"}">${k} ${v === true ? "found" : v === false ? "missing" : v}</span>`).join("  ");
}
function drawLog() {
  if (!logDirty) return; logDirty = false;
  $("#log").innerHTML = L.log.slice(0, 120).map(l => `<div><span class="tm">${tclock(l.t)}</span><span class="src">${esc(l.src)}</span><span class="x" style="${l.kind === "migrate" ? "color:var(--ok)" : l.kind === "help" ? "color:var(--bad)" : /^(fire|aux_fire)$/.test(l.kind) ? "color:var(--sel)" : ""}">${esc(l.text)}</span></div>`).join("");
}

function frame() {
  if ($("#live").classList.contains("cur")) { drawChannels(); drawRel(); drawGaze(); drawLog(); }
  if ($("#two").classList.contains("cur")) drawTwo();
  requestAnimationFrame(frame);
}

/* ================================================================ presenter: scenarios on the real engine */
const send = o => { if (L.ws && L.ws.readyState === 1) L.ws.send(JSON.stringify(o)); };
let ramps = [];
function ramp(chs, to, secs) {
  ramps.forEach(clearInterval); ramps = [];
  const from = Object.fromEntries(chs.map(c => [c, L.state?.runtime?.levels?.[c] || 0])), t0 = Date.now();
  const iv = setInterval(() => {
    const p = Math.min(1, (Date.now() - t0) / (secs * 1000));
    for (const c of chs) send({ type: "degrade", channel: c, level: from[c] + (to - from[c]) * p });
    if (p >= 1) clearInterval(iv);
  }, 200);
  ramps.push(iv);
  addLog(Date.now(), "presenter", `degrade ${chs.join(", ")} to ${to} over ${secs} s (raw signal, never the score)`);
}
document.querySelectorAll("[data-s]").forEach(b => b.onclick = () => ({
  handRamp: () => ramp(["hand"], 0.95, 20), handDrop: () => ramp(["hand"], 0.97, 0.3), browRamp: () => ramp(["brow"], 0.95, 20),
  allRamp: () => ramp(CH, 0.85, 25), recover: () => ramp(CH, 0, 1)
}[b.dataset.s]()));
document.querySelectorAll(".lvl input").forEach(inp => inp.oninput = () => { inp.nextElementSibling.textContent = inp.value; send({ type: "degrade", channel: inp.dataset.c, level: inp.value / 100 }); });
document.querySelectorAll("[data-src]").forEach(b => b.onclick = () => { send({ type: "source", value: b.dataset.src }); addLog(Date.now(), "presenter", "source " + b.dataset.src); });
document.querySelector('[data-x="calibrate"]').onclick = () => send({ type: "calibrate" });
document.querySelector('[data-x="lock"]').onclick = () => { const locked = L.state?.policy?.mode === "fixed"; send({ type: "lock", value: !locked }); };
document.querySelector('[data-x="ack"]').onclick = () => window.lab?.toHome({ simAck: true });
document.querySelector('[data-x="agentAck"]').onclick = () => window.lab?.toHome({ agentReply: { contact: "Maya", text: "On my way" } });

/* ================================================================ tabs: evaluation + care */
document.querySelectorAll(".tabs button").forEach(b => b.onclick = () => {
  document.querySelectorAll(".tabs button").forEach(x => x.classList.toggle("cur", x === b));
  document.querySelectorAll(".view").forEach(v => v.classList.toggle("cur", v.id === b.dataset.v));
  if (b.dataset.v === "eval") renderEval();
  if (b.dataset.v === "care") renderCare();
});
const SYS_COL = { fixed: "#5a6677", threshold: "#b48cf2", hysteresis: "#e9a23b", truth_switch: "#4fd1a5", residual: "#5aa9e6" };
async function renderEval() {
  const R = await window.lab?.evalResults?.();
  const el = $("#eval");
  if (!R) { el.innerHTML = `<h2>Evaluation</h2><p class="prose">No results file. Run <span class="n">python -m residual.evaluate --seeds 30</span> in the engine folder; it writes eval_out/results.json.</p>`; return; }
  const cell = (sc, sev, sy) => R.cells.find(c => c.scenario === sc && c.severity === sev && c.system === sy);
  const sevs = R.severities;
  const charts = R.scenarios.map(sc => {
    const W = 320, H = 190, X = v => 36 + (v - sevs[0]) / (sevs[sevs.length - 1] - sevs[0]) * (W - 50), maxY = Math.max(...R.cells.filter(c => c.scenario === sc.name).map(c => c.cpm[2])) * 1.1, Y = v => 10 + (1 - v / maxY) * (H - 36);
    let s = `<svg viewBox="0 0 ${W} ${H}" width="100%">`;
    for (const v of [0, Math.round(maxY / 2), Math.floor(maxY)]) s += `<line x1="36" x2="${W - 10}" y1="${Y(v)}" y2="${Y(v)}" stroke="#1b232d"/><text x="30" y="${Y(v) + 3}" fill="#5a6677" font-size="9" text-anchor="end" font-family="PlexMono">${v}</text>`;
    for (const v of sevs) s += `<text x="${X(v)}" y="${H - 8}" fill="#5a6677" font-size="9" text-anchor="middle" font-family="PlexMono">${v}</text>`;
    for (const sy of R.systems) {
      const pts = sevs.map(v => cell(sc.name, v, sy)).filter(Boolean);
      if (!pts.length) continue;
      const band = pts.map(c => `${X(c.severity)},${Y(c.cpm[2])}`).join(" ") + " " + [...pts].reverse().map(c => `${X(c.severity)},${Y(c.cpm[1])}`).join(" ");
      s += `<polygon points="${band}" fill="${SYS_COL[sy]}" opacity="0.12"/>`;
      s += `<polyline points="${pts.map(c => `${X(c.severity)},${Y(c.cpm[0])}`).join(" ")}" fill="none" stroke="${SYS_COL[sy]}" stroke-width="${sy === "residual" ? 2.4 : 1.3}" ${sy === "truth_switch" ? 'stroke-dasharray="4 3"' : ""}/>`;
    }
    s += `</svg>`;
    return `<div class="chart"><div class="t"><b>${sc.name}</b><span>${sc.held_out ? "held out" : "tuning set"}: ${esc(sc.about)}</span></div>${s}</div>`;
  }).join("");
  const top = sevs[sevs.length - 1];
  const rows = R.scenarios.map(sc => `<tr><td>${sc.name}${sc.held_out ? "" : " (tuning)"}</td>` + ["fixed", "threshold", "hysteresis", "truth_switch"].map(b => {
    const p = R.paired.find(x => x.scenario === sc.name && x.severity === top && x.baseline === b);
    if (!p) return "<td>.</td>";
    const cls = p.lo > 0 ? "win" : p.hi < 0 ? "loss" : "tie";
    return `<td class="${cls}">${p.diff >= 0 ? "+" : ""}${p.diff.toFixed(2)} [${p.lo.toFixed(2)}, ${p.hi.toFixed(2)}]  win ${Math.round(p.win * 100)}% loss ${Math.round(p.loss * 100)}%</td>`;
  }).join("") + "</tr>").join("");
  el.innerHTML = `<h2>Does adapting actually help? Closed-loop simulation, ${R.seeds} paired seeds per cell</h2>
    <p class="prose">A simulated person picks phrases on a scanning board and gestures on whatever channel the screen says is in control, while that channel's raw signal is degraded (never the score). Every system sees the same person, targets and noise for each seed. <b>Successful selections per minute</b>: the person got what they meant. Generated ${esc(R.generated)}, ${R.duration_s} s sessions.</p>
    <div class="legend">${R.systems.map(s => `<span><i style="background:${SYS_COL[s]}"></i>${esc(R.labels[s] || s)}</span>`).join("")}</div>
    <div class="mult">${charts}</div>
    <h4>Residual minus each baseline at severity ${top}, paired by seed (successful selections / min, 95% bootstrap CI)</h4>
    <table class="n"><tr><th>scenario</th><th>vs calibrated once</th><th>vs switch &lt;50%</th><th>vs switch + dwell</th><th>vs truth switch</th></tr>${rows}</table>
    <p class="prose" style="margin-top:14px">Green: the whole interval is above zero. Red: the whole interval is below zero (Residual is worse). Grey: can't tell apart. <b>truth_switch</b> knows the true degradation schedule and switches instantly with no check; it is a reference point, not an upper bound, because the least-degraded channel is not always the most usable one after adaptation, and an unchecked switch can land on a channel the person isn't ready to use.</p>
    ${R.ablations ? ablationTable(R) : ""}
    <h4>Calibration of the reliability model (predicted p_hit vs observed hit rate)</h4>
    <table class="n" style="max-width:420px"><tr><th>predicted</th><th>observed</th><th>n</th></tr>${(R.calibration || []).map(([p, o, n]) => `<tr><td>${p.toFixed(2)}</td><td>${o.toFixed(2)}</td><td>${n}</td></tr>`).join("")}</table>`;
}
function careChart(days) {
  const W = 900, H = 220, n = days.length, X = i => 50 + (n === 1 ? 0.5 : i / (n - 1)) * (W - 90), Y = v => 14 + (1 - (Math.max(40, v) - 40) / 60) * (H - 44);
  let s = `<div class="chart" style="max-width:960px;margin-top:6px"><div class="t"><b>Command reliability by day</b><span>daily mean of the engine's p_hit for each movement; green ticks: control moved</span></div><svg viewBox="0 0 ${W} ${H}" width="100%">`;
  for (const v of [40, 70, 85, 100]) s += `<line x1="50" x2="${W - 40}" y1="${Y(v)}" y2="${Y(v)}" stroke="${v === 70 ? "#f0646455" : v === 85 ? "#4fd1a555" : "#1b232d"}" ${v === 70 || v === 85 ? 'stroke-dasharray="4 4"' : ""}/><text x="44" y="${Y(v) + 3}" fill="#5a6677" font-size="10" text-anchor="end" font-family="PlexMono">${v}%</text>`;
  days.forEach((d, i) => { s += `<text x="${X(i)}" y="${H - 6}" fill="#5a6677" font-size="10" text-anchor="middle" font-family="PlexMono">${d.day.slice(5)}</text>`;
    if (d.migrations.length) s += `<line x1="${X(i)}" x2="${X(i)}" y1="10" y2="${H - 30}" stroke="#4fd1a5" stroke-width="2"/><text x="${X(i) - 6}" y="${H - 34}" fill="#4fd1a5" font-size="10" text-anchor="end" font-family="PlexMono">control moved ${d.migrations.map(m => (m.from || []).join("+") + "→" + (m.to || []).join("+")).join(", ")}</text>`; });
  for (const c of CH) {
    const pts = days.map((d, i) => d.rel[c] != null ? `${X(i)},${Y(d.rel[c])}` : null).filter(Boolean);
    s += `<polyline points="${pts.join(" ")}" fill="none" stroke="${COL[c]}" stroke-width="2"/>` + pts.map(p => `<circle cx="${p.split(",")[0]}" cy="${p.split(",")[1]}" r="3" fill="${COL[c]}"/>`).join("");
  }
  return s + `</svg><div class="legend">${CH.map(c => `<span><i style="background:${COL[c]}"></i>${NAME[c]}</span>`).join("")}</div></div>`;
}
function ablationTable(R) {
  const A = R.ablations, sevs = R.severities.filter(v => v > 0);
  const get = (sc, sev, v) => A.cells.find(c => c.scenario === sc && c.severity === sev && c.variant === v);
  const scen = [...new Set(A.cells.map(c => c.scenario))];
  const nice = { residual: "Residual", no_adapt: "no adaptation", no_fusion: "no fusion", no_changepoint: "no changepoints", no_verify: "no verification" };
  return `<h4>Ablations: Residual with one piece removed (successful selections / min, ${A.seeds} seeds)</h4>
    <table class="n" style="max-width:900px"><tr><th>scenario</th><th>severity</th>${A.variants.map(v => `<th>${nice[v] || v}</th>`).join("")}</tr>
    ${scen.flatMap(sc => sevs.map(sev => { const base = get(sc, sev, "residual")?.cpm[0];
      return `<tr><td>${sc}</td><td>${sev}</td>${A.variants.map(v => { const c = get(sc, sev, v); if (!c) return "<td>.</td>";
        const d = v === "residual" ? 0 : c.cpm[0] - base; const cls = v === "residual" ? "" : d < -0.3 ? "win" : d > 0.3 ? "loss" : "tie";
        return `<td class="${cls}">${c.cpm[0].toFixed(2)}${v === "residual" ? "" : ` (${d >= 0 ? "+" : ""}${d.toFixed(2)})`}</td>`; }).join("")}</tr>`; })).join("")}</table>
    <p class="prose">Green: removing that piece makes Residual worse (the piece earns its place). Red: Residual would do better without it in that scenario.</p>`;
}
async function renderCare() {
  const days = (await window.lab?.care?.()) || [];
  const el = $("#care");
  el.innerHTML = `<h2>Care report</h2><p class="prose">For the person's care team: how reliable each movement has been, when control moved and why, tired-eye episodes and calls for help, by day. Recorded on this computer only. This is a log of how the interface was used, not a clinical measurement.</p>
    <div class="btns" style="margin:12px 0"><button id="exp-json">Export JSON</button><button id="exp-csv">Export CSV</button></div>
    ${days.length ? careChart(days) : ""}
    ${days.length ? `<table class="n" style="max-width:1100px;margin-top:18px"><tr><th>day</th><th>minutes</th><th>selections</th><th>hand</th><th>eyebrow</th><th>blink</th><th>control moved</th><th>tired eyes</th><th>help</th></tr>
      ${days.map(d => `<tr><td>${d.day}</td><td>${d.minutes}</td><td>${d.selections}</td>${CH.map(c => `<td>${d.rel[c] != null ? d.rel[c] + "%" : "."}</td>`).join("")}<td>${d.migrations.map(m => `${(m.from || []).join("+")} → ${(m.to || []).join("+")} ${new Date(m.t).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`).join("<br>") || "."}</td><td>${d.tired}</td><td>${d.help}</td></tr>`).join("")}</table>`
      : `<p class="prose">Nothing recorded yet. Reliability is sampled every 5 minutes while Residual runs with the engine.</p>`}`;
  const hist = (await window.lab?.history?.()) || [];
  el.insertAdjacentHTML("beforeend", `<h2 style="margin-top:28px">Requests and orders</h2><p class="prose">What was asked for, by whom, through which agent, and what happened. No message text is kept. Orders are sandbox tests: no real money.</p>
    ${hist.length ? `<table class="n" style="max-width:1100px"><tr><th>when</th><th>request</th><th>from</th><th>agent</th><th>service</th><th>amount</th><th>caregiver approval</th><th>outcome</th><th>error</th></tr>
      ${hist.map(h => `<tr><td>${new Date(h.t).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</td><td>${esc(h.intent)}</td><td>${esc(h.source === "residual_ui" ? "Residual (face)" : h.source === "asi" ? "ASI:One" : h.source || ".")}</td><td>${esc(h.agent)}</td><td>${esc(h.service || ".")}</td><td>${h.amount != null ? "$" + (h.amount / 100).toFixed(2) : "."}</td><td>${h.approval ? (h.approvedBy ? "approved by " + esc(h.approvedBy) : "required") : "."}</td><td>${esc(h.outcome)}</td><td class="loss">${esc(h.error || "")}</td></tr>`).join("")}</table>`
      : '<p class="prose">Nothing yet. Requests appear here once Residual Relay is running.</p>'}`);
  const dl = (name, text, type) => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; a.click(); };
  $("#exp-json").onclick = () => dl("residual-care-report.json", JSON.stringify(days, null, 1), "application/json");
  $("#exp-csv").onclick = () => dl("residual-care-report.csv", ["day,minutes,selections,hand,brow,blink,migrations,tired,help", ...days.map(d => [d.day, d.minutes, d.selections, d.rel.hand ?? "", d.rel.brow ?? "", d.rel.blink ?? "", d.migrations.length, d.tired, d.help].join(","))].join("\n"), "text/csv");
}

/* ================================================================ boot */
document.addEventListener("keydown", e => { if (e.key === "a") { L.showAttempts = !L.showAttempts; addLog(Date.now(), "lab", `attempt events ${L.showAttempts ? "shown" : "hidden"}`); } });
(async () => {
  if (window.lab) {
    const b = await window.lab.backlog();
    L.cfg = { ...L.cfg, ...b.config }; L.aiStatus = b.ai; L.engineProc = b.engine;
    for (const c of (b.aiCalls || []).reverse()) onTele({ ...c, kind: "ai" });
    for (const t of b.tele || []) onTele(t);
    window.lab.onTelemetry(onTele);
    panelsSlow();
  }
  $("#t-pf-run").onclick = async () => { $("#t-pf-box").hidden = false; $("#t-pf").textContent = "Checking (up to 15 s)…"; const rows = await window.lab.preflight(); drawPreflight(rows || []); };
  document.querySelectorAll("[data-cmd]").forEach(b => (b.onclick = () => window.lab && window.lab.toHome(JSON.parse(b.dataset.cmd))));
  // Two-choice mode: the face tracking runs in the Home window, which streams its live state here.
  // The old engine tabs stay available (they show the Python engine, which this mode doesn't start).
  if (L.cfg.inputMode === "two") {
    const tab = document.getElementById("tab-two");
    tab.hidden = false;
    tab.click();
    $("#h-src").innerHTML = `<span class="dot"></span>two-choice mode`;
  }
  if (L.cfg.inputMode !== "two") connect();
  requestAnimationFrame(frame);
})();
