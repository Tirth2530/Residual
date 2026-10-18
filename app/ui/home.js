// Residual Home: what the person sees.
//
// Everything is reachable with Back / Select / Next, or with Select alone (auto-scan with
// row-then-item group scanning, the standard AAC technique that cuts steps from n to about 2 sqrt n).
// Which movement does which job is decided by the Residual engine (Select) and by what else is
// reliable (Next / Back: the eyes while they're fresh, otherwise other movements, otherwise the
// screen scans by itself). This file never shows a percentage to the person: scores live in the Lab.

const MOV = {
  hand: { name: "Hand pinch", icon: "hand" }, brow: { name: "Eyebrow raise", icon: "brow" },
  blink: { name: "Long blink", icon: "blink" }, gaze_r: { name: "Look right", icon: "eye" }, gaze_l: { name: "Look left", icon: "eye" }
};
const CH = ["hand", "brow", "blink"];
const DROP = 60, RISE = 70;      // no-engine presenter mode only: a slider stops counting below 60, comes back at 70
// With the engine on, the engine decides every role (runtime.roles); this file only renders it.
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const I = (n, c) => window.ICON(n, c);
const stage = $("#stage");
const sleep = ms => new Promise(r => setTimeout(r, ms));
const sameSel = (a, b) => (a || []).join("+") === (b || []).join("+");
const movName = s => (s && s.length ? s.map(c => MOV[c]?.name || c).join(" + ") : "No movement");

let CFG = null;
const S = {
  rel: { hand: 96, brow: 91, blink: 88 }, usable: { hand: true, brow: true, blink: true },
  roles: { select: ["hand"], next: "brow", back: "blink" }, mode: 3,
  stack: [{ id: "home", p: {}, focus: { g: 0, i: 0 } }], level: "item", groups: [],
  active: null, title: "", open: [], sheet: null, feed: { typed: [], watched: [], chats: [] },
  suggestions: [], weather: null, signals: { unread: 0 }, companion: "", speaking: false,
  lifeline: null, engine: { ws: null, on: false, control: ["hand"], source: null, state: null, proc: null },
  eyes: null, eyesNav: false, paused: false, ai: null, news: { sections: [], cur: null, items: {} },
  ask: { history: [], next: [], busy: false, about: null }, selections: 0,
  agent: { health: { ok: false }, tasks: {}, said: {} }, cal: { seq: null, was: false }
};
const tel = (kind, d = {}) => { try { window.shell.telemetry({ kind, ...d }); } catch (e) {} };

/* ================================================================ voice */
// Everything spoken goes through one priority queue (speech.js). Nothing calls
// speechSynthesis.cancel() directly any more, so features can't cut each other off, and the same
// line can't be stacked up by repeated Selects.
const P = { EMERGENCY: 0, CONTROL: 1, PERSON: 2, CONTENT: 3, AMBIENT: 4 };
let curAudio = null;
let elevenOk = null;
// A small, permanent note of which voice speaks the person's own words, so it is never a guess.
function setVoiceBadge(kind, why) {
  const el = document.getElementById("two-voice"); if (!el) return;
  el.textContent = kind === "eleven" ? "Voice: ElevenLabs" : kind === "ready" ? "Voice: ElevenLabs ready" : "Voice: system" + (why ? ` (${why})` : "");
  el.className = kind; el.title = why || "";
}
// Every line Residual speaks, from the first greeting to the last word, uses the ElevenLabs voice in app/.env. The system voice is
// only the safety net for when ElevenLabs can't be reached, so something is always said.
let speakSeq = 0;
// "ok" | "cancelled" | "fail: why"
async function playEleven(part) {
  const b64 = await window.shell.tts(part);
  if (!b64) return "fail";
  const a = new Audio(URL.createObjectURL(new Blob([Uint8Array.from(atob(b64), c => c.charCodeAt(0))], { type: "audio/mpeg" })));
  return new Promise(resolve => {
    curAudio = a; a._done = () => resolve("cancelled");
    a.onended = () => { if (curAudio === a) curAudio = null; resolve("ok"); };
    a.onerror = () => { if (curAudio === a) curAudio = null; resolve("fail: couldn't play the audio"); };
    a.play().catch(e => { if (curAudio === a) curAudio = null; resolve("fail: " + (e && e.message || e)); });
  });
}
function rawSpeak(text, item) {
  return new Promise(async res => {
    // Whatever goes wrong below, this promise must settle: a line that never finishes blocks every line behind it.
    const mine = ++speakSeq;
    let started = false, why = "";
    try {
      if (elevenOk !== false && window.shell.tts) {
        for (const part of HomeView.chunkText(text, 380)) {
          if (mine !== speakSeq) return res();                  // stopped meanwhile
          const r = await playEleven(part);
          if (r === "cancelled") return res();
          if (r !== "ok") { why = r; break; }
          if (!started) { started = true; if (elevenOk !== true) status("Speaking in your ElevenLabs voice"); elevenOk = true; setVoiceBadge("eleven"); }
        }
        if (!why || started) return res();                      // all said (or said as far as it got)
        window.shell.ttsStatus().then(st => {
          if (!st.ready) { elevenOk = false; setVoiceBadge("system", "no key"); status("No ElevenLabs key found, so I'm using the system voice"); }
          else { setVoiceBadge("system", st.lastError || why); status(`ElevenLabs didn't answer (${st.lastError || why}), so I'm using the system voice this time`); }
        }).catch(() => {});
      }
    } catch (e) {
      if (started) return res();
      setVoiceBadge("system", String(e && e.message || e));      // fall through to the system voice
    }
    try {
      const u = new SpeechSynthesisUtterance(text);
      if (!Voice.voice) Voice.pick();
      if (Voice.voice) u.voice = Voice.voice;
      u.rate = CFG?.voice?.rate || 0.98;
      u.onend = u.onerror = () => res();
      speechSynthesis.speak(u);
    } catch (e) { res(); }
  });
}
function rawCancel() {
  speakSeq++;                                                  // a stopped line must not go on to its next chunk
  try { speechSynthesis.cancel(); } catch (e) {}
  if (curAudio) { const a = curAudio; curAudio = null; try { a.pause(); } catch (e) {} a._done && a._done(); }
}
const speech = new window.SpeechManager({
  speak: rawSpeak, cancel: rawCancel,
  onState: st => { setSpeaking(st.speaking, st.speaking && st.priority >= P.CONTENT ? st.text : null); tel("speech", st); }
});
const flight = new window.SingleFlight();
const Voice = {
  voice: null,
  pick() {
    const vs = speechSynthesis.getVoices();
    const want = CFG?.voice?.name;
    this.voice = (want && vs.find(v => v.name === want)) ||
      vs.find(v => /Personal Voice/i.test(v.name)) ||
      vs.find(v => /^(Ava|Zoe|Samantha|Daniel|Karen|Moira)/.test(v.name) && /en/i.test(v.lang)) ||
      vs.find(v => /en[-_]US/i.test(v.lang)) || vs[0] || null;
  },
  say(text, { priority = P.CONTENT, key } = {}) { return speech.say(text, { priority, key }); },
  // navigation stops what was requested (news, answers); never an alarm or a control change
  stop(min = P.CONTENT) { speech.stop(min); }
};
speechSynthesis.onvoiceschanged = () => Voice.pick();
function setSpeaking(on, text) {
  S.speaking = on;
  if (text) S.companion = text;
  const v = $(".voice"); if (!v) return;
  v.classList.toggle("speaking", on);
  if (text) v.querySelector("p").textContent = text;
}

/* ================================================================ navigation */
const cur = () => S.stack[S.stack.length - 1];
function go(id, p = {}) { Voice.stop(); S.stack.push({ id, p, focus: { g: 0, i: 0 } }); S.level = "item"; render(); tel("screen", { id }); }
function up() {
  if (S.stack.length > 1) { Voice.stop(); S.stack.pop(); S.level = "item"; render(); tel("screen", { id: cur().id }); return true; }
  return false;
}
function home() { S.stack = [S.stack[0]]; S.level = "item"; render(); }

const backItem = () => ({ label: "Back", run: () => up(), back: true });
const helpItem = () => ({ label: "Get help", ext: "#help", run: () => askHelp() });

function screenHead(title, sub) {
  return `<div class="head"><button class="f backbtn" data-k="back">${I("back")}Back</button><div><h2>${esc(title)}</h2>${sub ? `<p>${esc(sub)}</p>` : ""}</div></div>`;
}

// Each screen returns { html, groups }. Groups are scan rows; item elements carry data-k="g:i".
const SCREENS = {
  home() {
    const sug = S.suggestions.slice(0, 3);
    const g0 = sug.map(s => ({ label: s.label, run: () => runSuggestion(s) }));
    const unread = S.signals.unread;
    const tiles = [
      { k: "talk", name: "Talk", sub: unread ? `${unread} unread` : "Maya, Raj, everyone", icon: "chat", run: () => go("talk"), count: unread },
      { k: "say", name: "Say", sub: "Out loud, right now", icon: "voice", run: () => go("say") },
      { k: "ask", name: "Ask", sub: "Ask Residual anything", icon: "spark", run: () => go("ask") },
      { k: "news", name: "News", sub: "Listen to what's happening", icon: "news", run: () => go("news") },
      { k: "watch", name: "Watch", sub: "Reels, YouTube, Netflix", icon: "play", run: () => go("watch") },
      { k: "listen", name: "Listen", sub: "Music and sounds", icon: "music", run: () => go("listen") },
      { k: "routines", name: "Routines", sub: "One move does it all", icon: "routine", run: () => go("routines") },
      { k: "help", name: "Get help", sub: `${CFG.lifeline?.contacts?.[0]?.name || "Your carer"} first, then everyone`, icon: "help", run: () => askHelp() }
    ];
    const groups = [g0, tiles.slice(0, 4).map(t => ({ label: t.name, run: t.run })), tiles.slice(4).map(t => ({ label: t.name, run: t.run }))].filter(g => g.length);
    const gi = g0.length ? 0 : -1;
    const html = `<section class="screen home">
      <div class="hello"><div><h1>${greeting()}</h1>
        <div class="voice ${S.speaking ? "speaking" : ""}"><div class="wave"><b></b><b></b><b></b><b></b><b></b></div><p>${esc(S.companion || companionLine())}</p></div></div></div>
      ${g0.length ? `<div><p class="label">Right now</p><div class="now grp" data-grp="${gi + 0}">${sug.map((s, i) => `
        <button class="f sug" data-k="0:${i}"><span class="ic">${I(s.icon || "play")}</span><span><b>${esc(s.label)}</b><small>${esc(s.sub || "")}</small></span></button>`).join("")}</div></div>` : ""}
      <div class="tiles">${tiles.map((t, j) => {
        const g = (g0.length ? 1 : 0) + (j < 4 ? 0 : 1), i = j % 4;
        return `<button class="f tile ${t.k === "help" ? "help" : ""}" data-k="${g}:${i}">${t.count ? `<span class="count">${t.count}</span>` : ""}${I(t.icon)}<span><b>${t.name}</b><small>${esc(t.sub)}</small></span></button>`;
      }).join("")}</div></section>`;
    return { html, groups };
  },

  talk() {
    const people = CFG.people || [];
    const g1 = people.map(p => ({ label: `Talk to ${p.name}`, run: () => go("person", { name: p.name }) }));
    const g2 = [{ label: "Open WhatsApp", run: () => openApp("whatsapp") }, { label: "Say it out loud", run: () => go("say") }];
    const last = n => S.feed.chats.find(c => c.name.toLowerCase().startsWith(n.toLowerCase()));
    return {
      groups: [g1, g2],
      html: `<section class="screen">${screenHead("Talk", "Who do you want to talk to?")}
        <div class="grid g3 grp" data-grp="0">${people.map((p, i) => `<button class="f card" data-k="0:${i}"><span class="avatar">${esc(p.name[0])}</span>
          <span><b>${esc(p.name)}</b><small>${esc(p.relation || "")}${last(p.name) ? ` · last chat ${ago(last(p.name).t)}` : ""}</small></span></button>`).join("")}</div>
        <div class="grid g2 grp" data-grp="1">
          <button class="f card" data-k="1:0">${I("chat")}<span><b>Open WhatsApp</b><small>All your chats, with suggested replies</small></span></button>
          <button class="f card" data-k="1:1">${I("voice")}<span><b>Say it out loud</b><small>For whoever's in the room</small></span></button></div></section>`
    };
  },

  person({ name }) {
    const p = (CFG.people || []).find(x => x.name === name) || { name, phrases: [] };
    const phrases = (p.phrases || []).slice(0, 4);
    const g1 = phrases.map(t => ({ label: t, run: () => messagePerson(p, t) }));
    const g2 = [{ label: "Write a message", run: () => openKeyboard({ target: "message", person: p }) },
                { label: `Open chat`, run: () => openChat(p) }];
    return {
      groups: [g1, g2],
      html: `<section class="screen">${screenHead(p.name, p.phone ? "Pick something to send. You'll have 3 seconds to stop it." : `Add ${p.name}'s number in config.json to send on WhatsApp. Until then, I'll say these out loud.`)}
        <div class="grid g2 grp fill" data-grp="0" style="grid-auto-rows:1fr">${phrases.map((t, i) => `<button class="f phr" data-k="0:${i}">${esc(t)}</button>`).join("")}</div>
        <div class="grid g2 grp" data-grp="1">
          <button class="f card" data-k="1:0">${I("type")}<span><b>Write a message</b><small>Scanning keyboard with word prediction</small></span></button>
          <button class="f card" data-k="1:1">${I("chat")}<span><b>Open chat with ${esc(p.name)}</b><small>See what they said, with suggested replies</small></span></button></div></section>`
    };
  },

  watch() {
    const cont = S.feed.watched.filter(w => w.app !== "music").slice(0, 2);
    const apps = ["reels", "shorts", "youtube", "netflix"].map(appById).filter(Boolean);
    const items = [...cont.map(w => ({ label: w.title, sub: `Continue on ${appById(w.app)?.name || ""}`, icon: "play", run: () => openApp(w.app, w.url) })),
                   ...apps.map(a => ({ label: a.name, sub: a.sub, icon: a.id === "reels" ? "reels" : a.id === "netflix" ? "tv" : "play", run: () => openApp(a.id) }))];
    const g2 = [{ label: "Search YouTube", run: () => openSearch("youtube") }, { label: "Search Netflix", run: () => openSearch("netflix") }];
    return {
      groups: [items.slice(0, 3), items.slice(3), g2].filter(g => g.length),
      html: `<section class="screen">${screenHead("Watch", "Something to smile at")}${cardsGrid(items, 3, 0)}
        <div class="grid g2 grp" data-grp="${Math.ceil(items.length / 3)}">
          <button class="f card" data-k="${Math.ceil(items.length / 3)}:0">${I("search")}<span><b>Search YouTube</b><small>Ideas picked for you, no typing</small></span></button>
          <button class="f card" data-k="${Math.ceil(items.length / 3)}:1">${I("search")}<span><b>Search Netflix</b><small>Shows and films</small></span></button></div></section>`
    };
  },

  listen() {
    const cont = S.feed.watched.filter(w => w.app === "music").slice(0, 1);
    const rs = (CFG.routines || []).filter(r => ["music", "moon"].includes(r.icon));
    const items = [...cont.map(w => ({ label: w.title, sub: "Play again", icon: "music", run: () => openApp("music", w.url) })),
                   { label: "YouTube Music", sub: "Everything you like", icon: "music", run: () => openApp("music") },
                   ...rs.map(r => ({ label: r.name, sub: r.sub, icon: r.icon, run: () => runRoutine(r) })),
                   { label: "Search music", sub: "Ideas picked for you", icon: "search", run: () => openSearch("music") }];
    const rows = chunk(items, 3);
    return { groups: rows.map(r => r.map(x => ({ label: x.label, run: x.run }))),
             html: `<section class="screen">${screenHead("Listen", "Music, and sounds to sleep to")}${cardsGrid(items, 3, 0)}</section>` };
  },

  routines() {
    const items = (CFG.routines || []).map(r => ({ label: r.name, sub: r.sub, icon: r.icon || "routine", run: () => runRoutine(r), help: (r.steps || []).some(s => s.help) }));
    const rows = chunk(items, 4);
    return { groups: rows.map(r => r.map(x => ({ label: x.label, run: x.run }))),
             html: `<section class="screen">${screenHead("Routines", "One select does the whole thing")}${cardsGrid(items, 4, 0)}</section>` };
  },

  say() {
    const needs = CFG.needs || [];
    const big = needs.filter(n => n.big), rest = needs.filter(n => !n.big);
    const rows = [big, ...chunk(rest, 5)];
    const groups = rows.map(r => r.map(n => ({ label: n.label || n.say, run: () => sayNeed(n) })));
    groups.push([{ label: "Type it and I'll say it", run: () => openKeyboard({ target: "speak" }) }]);
    let html = `<section class="screen">${screenHead("Say", "I'll say it out loud, right away").replace(/<\/div>$/, S.said ? `<div class="said">“${esc(S.said)}”</div></div>` : "</div>")}<div class="needs">`;
    rows.forEach((r, g) => r.forEach((n, i) => {
      html += `<button class="f need ${n.big ? "big" : ""} ${n.care ? "care" : ""}" data-k="${g}:${i}">${esc(n.label || n.say)}</button>`;
    }));
    html += `<button class="f need typ" data-k="${rows.length}:0">${I("type")} Type it and I'll say it</button></div></section>`;
    return { groups, html };
  },

  news() {
    const N = S.news, secs = N.sections;
    if (!N.cur && secs.length) N.cur = secs[0].id;
    const items = N.items[N.cur];
    const g0 = secs.map(s => ({ label: s.name, run: () => { N.cur = s.id; loadNews(s.id); render(); setFocus(1, 0); } }));
    const stories = (items || []).slice(0, 8);
    const g1 = stories.map((it, i) => ({ label: it.title, run: () => go("story", { section: N.cur, index: i }) }));
    const g2 = [{ label: "Brief me", run: () => briefMe() }];
    const groups = [g0, ...(g1.length ? [g1.slice(0, 4), g1.slice(4)] : []), g2].filter(g => g.length);
    const gStories = 1, gBrief = groups.length - 1;
    return {
      groups,
      html: `<section class="screen">${screenHead("News", "Pick a story and I'll read it to you")}
        <div class="tabs grp" data-grp="0">${secs.map((s, i) => `<button class="f tab ${s.id === N.cur ? "cur" : ""}" data-k="0:${i}">${esc(s.name)}</button>`).join("")}
          <button class="f tab" data-k="${gBrief}:0">${I("spark")} Brief me</button></div>
        <div class="stories">${items === undefined ? `<p class="label">Getting the headlines…</p>` : !stories.length ? `<p class="label">Couldn't get the news. Is the internet on?</p>` :
          stories.map((it, i) => `<button class="f story" data-k="${gStories + (i < 4 ? 0 : 1)}:${i % 4}">${it.img ? `<img src="${esc(it.img)}" alt="" onerror="this.remove()">` : ""}
            <span><b>${esc(it.title)}</b><span class="src">${esc(it.source)}${it.when ? ` · ${ago(it.when)}` : ""}</span></span></button>`).join("")}</div></section>`
    };
  },

  story({ section, index }) {
    const it = (S.news.items[section] || [])[index];
    if (!it) return SCREENS.news();
    const st = S.story && S.story.link === it.link ? S.story : null;
    const g = [{ label: "Read it again", run: () => st && Voice.say(st.summary, { priority: P.CONTENT }) },
               { label: "Ask about this", run: () => { S.ask = { history: [], next: st?.questions?.length ? st.questions : ["Why does this matter?", "What happens next?", "Explain it simply"], busy: false, about: `${it.title}\n${st?.summary || it.desc || ""}` }; go("ask"); } },
               { label: "Next story", run: () => { const n = (index + 1) % (S.news.items[section] || []).length; S.stack.pop(); go("story", { section, index: n }); } },
               { label: "Open the full article", run: () => openArticle(it) }];
    if (!st) setTimeout(() => readStory(it), 0);
    return {
      groups: [g.slice(0, 2), g.slice(2)],
      html: `<section class="screen">${screenHead("News", it.source)}
        <div class="read"><article><h3>${esc(it.title)}</h3><span class="src">${esc(it.source)}${it.when ? ` · ${ago(it.when)}` : ""}</span>
          <div class="body ${st ? "" : "wait"}">${st ? esc(st.summary) : "Reading it for you…"}</div></article>
          <div class="stack">${g.map((x, i) => `<button class="f opt" data-k="${i < 2 ? 0 : 1}:${i % 2}">${I(["again", "spark", "next", "open"][i])}${esc(x.label)}</button>`).join("")}</div></div></section>`
    };
  },

  ask() {
    const A = S.ask;
    if (!A.history.length && !A.next.length && !A.loading) loadStarters();
    const choices = A.busy ? [] : A.next.slice(0, 4);
    const g0 = choices.map(t => ({ label: t, run: () => askSay(t) }));
    const g1 = [{ label: "Type my own", run: () => openKeyboard({ target: "ask" }) }, { label: "Read it again", run: () => { const last = [...A.history].reverse().find(h => h.role === "residual"); if (last) Voice.say(last.text, { priority: P.CONTENT }); } }];
    const hist = A.history.slice(-6);
    return {
      groups: [g0, g1].filter(g => g.length),
      html: `<section class="screen">${screenHead("Ask", S.ai?.ok ? `Thinking on this computer with ${S.ai.model}` : "The thinking model is off: I can still read news and play things")}
        <div class="ask"><div class="convo">${!hist.length && !A.busy ? `<div class="msg r"><span class="by">Residual</span>${A.about ? "What would you like to know about this story?" : `Hi ${esc(CFG.person)}. Pick something to ask, or type your own question.`}</div>` : ""}
          ${hist.map(h => `<div class="msg ${h.role === "person" ? "p" : "r"}"><span class="by">${h.role === "person" ? esc(CFG.person) : "Residual"}</span>${esc(h.text)}</div>`).join("")}
          ${A.busy ? `<div class="msg r think"><span class="by">Residual</span>Thinking…</div>` : ""}</div>
          <div class="stack">${g0.length ? `<div class="stack grp" data-grp="0">${choices.map((t, i) => `<button class="f opt" data-k="0:${i}">${esc(t)}</button>`).join("")}</div>` : ""}
            <div class="grid g2 grp" data-grp="${g0.length ? 1 : 0}"><button class="f opt" data-k="${g0.length ? 1 : 0}:0">${I("type")}Type my own</button><button class="f opt" data-k="${g0.length ? 1 : 0}:1">${I("again")}Say it again</button></div></div></div></section>`
    };
  }
};
function cardsGrid(items, cols, g0) {
  return chunk(items, cols).map((row, g) => `<div class="grid g${cols} grp" data-grp="${g0 + g}">${row.map((x, i) =>
    `<button class="f card ${x.help ? "helpc" : ""}" data-k="${g0 + g}:${i}">${I(x.icon || "play")}<span><b>${esc(x.label)}</b><small>${esc(x.sub || "")}</small></span></button>`).join("")}</div>`).join("");
}
const chunk = (a, n) => { const o = []; for (let i = 0; i < a.length; i += n) o.push(a.slice(i, i + n)); return o; };
function ago(t) {
  const m = Math.round((Date.now() - t) / 60000);
  return m < 1 ? "just now" : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`;
}
function greeting() {
  const h = new Date().getHours(), n = esc(CFG.person);
  return h < 5 ? `Still up, ${n}?` : h < 12 ? `Good morning, ${n}.` : h < 17 ? `Good afternoon, ${n}.` : h < 21 ? `Good evening, ${n}.` : `Winding down, ${n}?`;
}
function greetingText() {
  const h = new Date().getHours(), n = CFG.person;
  return h < 5 ? `Still up, ${n}?` : h < 12 ? `Good morning, ${n}.` : h < 17 ? `Good afternoon, ${n}.` : h < 21 ? `Good evening, ${n}.` : `Hi, ${n}.`;
}
function companionLine() {
  const w = S.weather, u = S.signals.unread, h = new Date().getHours(), bits = [];
  if (w) bits.push(`It's ${w.temp}° and ${w.desc} in ${w.place}.`);
  if (u) bits.push(`You have ${u} unread message${u > 1 ? "s" : ""}.`);
  bits.push(h < 11 ? "Want me to brief you on the news?" : h >= 21 ? "Some rain sounds to sleep?" : "What do you feel like doing?");
  return bits.join(" ");
}
const appById = id => CFG.apps.find(a => a.id === id);

/* ================================================================ render + focus */
function render() {
  if (S.active) {
    const a = appById(S.active);
    stage.innerHTML = `<div class="loading">Opening ${esc(a ? a.name : "")}…</div>`;
    S.groups = [];
    return;
  }
  const t = cur(), sc = SCREENS[t.id] || SCREENS.home;
  const { html, groups } = sc(t.p);
  const extra = [];
  if (t.id !== "home") extra.push([helpItem()]);
  S.groups = [...(t.id !== "home" && S.mode < 3 ? [[backItem()]] : []), ...groups, ...extra];
  S.groupOffset = t.id !== "home" && S.mode < 3 ? 1 : 0;
  stage.innerHTML = html;
  stage.querySelectorAll("[data-k]").forEach(el => el.addEventListener("click", () => {
    const k = el.dataset.k;
    if (k === "back") { up(); return; }
    const [g, i] = k.split(":").map(Number);
    t.focus = { g: g + S.groupOffset, i }; S.level = "item"; markFocus(); activate();
  }));
  const f = t.focus; f.g = Math.min(f.g, S.groups.length - 1); f.i = Math.min(f.i, (S.groups[f.g] || []).length - 1);
  if (f.g < 0) f.g = 0; if (f.i < 0) f.i = 0;
  markFocus();
}
function itemEl(g, i) {
  const it = S.groups[g]?.[i]; if (!it) return null;
  if (it.ext) return $(it.ext);
  if (it.back) return stage.querySelector('[data-k="back"]');
  return stage.querySelector(`[data-k="${g - S.groupOffset}:${i}"]`);
}
function markFocus() {
  document.querySelectorAll(".f.on, #help.on").forEach(e => e.classList.remove("on"));
  stage.querySelectorAll(".gon").forEach(e => e.classList.remove("gon"));
  if (S.sheet || S.active) return;
  const f = cur().focus;
  if (S.mode === 1 && S.level === "group" && (S.groups[f.g] || []).length > 1) {
    const els = S.groups[f.g].map((_, i) => itemEl(f.g, i)).filter(Boolean);
    els.forEach(e => e.classList.add("on"));
    return;
  }
  const el = itemEl(f.g, f.i);
  if (el) { el.classList.add("on"); el.scrollIntoView?.({ block: "nearest" }); }
}
function setFocus(g, i) { cur().focus = { g: g + S.groupOffset, i }; markFocus(); }
function moveNext() {
  const f = cur().focus, G = S.groups;
  if (!G.length) return;
  if (S.mode === 1) {
    if (S.level === "group") { f.g = (f.g + 1) % G.length; f.i = 0; }
    else { f.i++; if (f.i >= G[f.g].length) { f.i = 0; S.level = "group"; } }   // one pass, then back out to rows
  } else {
    f.i++;
    if (f.i >= G[f.g].length) { f.g = (f.g + 1) % G.length; f.i = 0; }
  }
  markFocus();
}
function activate() {
  const f = cur().focus, G = S.groups;
  if (S.mode === 1 && S.level === "group" && (G[f.g] || []).length > 1) { S.level = "item"; f.i = 0; markFocus(); restartScan(); return; }
  const it = G[f.g]?.[f.i];
  if (!it) return;
  S.selections++;
  tel("activate", { screen: cur().id, label: it.label });
  S.level = S.mode === 1 ? "group" : "item";
  it.run();
}

/* ================================================================ actions from movements */
function available() { return S.mode === 3 ? ["back", "select", "next"] : S.mode === 2 ? ["select", "next"] : ["select"]; }
async function act(a, src = "key") {
  tel("act", { action: a, source: src, screen: S.active ? `app:${S.active}` : cur().id, sheet: S.sheet?.type || null });
  if (a === "help") return askHelp();
  if (a === "home") { if (S.sheet && S.sheet.type !== "lifeline") closeSheet(); if (S.active) await window.shell.home(); else home(); return; }
  if (src !== "auto") { if (!available().includes(a) && !(S.sheet && a === "back")) return shake(a); flash(a); }
  if (S.sheet) return sheetAct(a);
  if (S.active) {
    const res = await window.shell.cmd(a);
    if (!res) return;
    if (res.confirm) confirmRisky(res);
    else if (res.open === "search") openSearch(["music", "netflix", "food"].includes(res.app) ? res.app : "youtube");
    else if (res.open === "keyboard") openKeyboard({ target: res.target, app: res.app });
    else if (res.done && src !== "auto") status(res.done);
    return;
  }
  if (a === "next") moveNext();
  else if (a === "select") activate();
  else if (a === "back") { if (!up()) Voice.stop(P.PERSON); }
}

/* ================================================================ doing things */
async function openApp(id, url) { Voice.stop(); await window.shell.openApp(id, url); }
async function runSuggestion(s) {
  window.shell.used(s.id);
  const a = s.action || {};
  if (a.briefing) return briefMe();
  if (a.screen === "person") return go("person", { name: a.person });
  if (a.screen) return go(a.screen);
  if (a.routine) { const r = (CFG.routines || []).find(x => x.name === a.routine); if (r) return runRoutine(r); }
  if (a.open) return openApp(a.open, a.url);
}
// Only ever because the person chose it, and only one at a time.
function briefMe() {
  const p = flight.run("briefing", async () => {
    setSpeaking(true, "Putting your briefing together…");
    if (cur().id !== "home") home();
    const r = await window.shell.briefing();
    if (r && r.busy) return;
    await Voice.say(r.text, { priority: P.CONTENT, key: "briefing" });
    refreshSuggestions();
  });
  if (!p) status("Already getting your briefing");
  return p;
}
function sayNeed(n) {
  S.said = n.say; render();
  Voice.say(n.say, { priority: P.PERSON });
  if (n.care) window.shell.care("need", { say: n.say });
  clearTimeout(S._saidT); S._saidT = setTimeout(() => { S.said = null; if (cur().id === "say") render(); }, 3500);
}
async function messagePerson(p, text) {
  // Residual Relay (Photon) knows its own contacts, so no number is needed here for them; only WhatsApp needs one.
  if (!S.agent.health.ok) { try { await refreshAgentHealth(); } catch (e) {} }
  const viaRelay = S.agent.health.ok && (S.agent.health.contacts || []).some(n => n.toLowerCase() === String(p.name).toLowerCase());
  if (!p.phone && !viaRelay) { status(`No number for ${p.name} yet, so I said it out loud`); Voice.say(text, { priority: P.PERSON }); return; }
  window.shell.used(`talk:${p.name}`);
  await sendMessage({ to: p.name, text }, ++routineRun);
}
function openChat(p) {
  if (p.phone) return openApp("whatsapp", `https://web.whatsapp.com/send?phone=${encodeURIComponent(p.phone.replace(/[^\d]/g, ""))}`);
  return openApp("whatsapp");
}
async function openArticle(it) {
  Voice.stop();
  await window.shell.openApp("web", it.link);   // a plain browser view, scanned like any page
}
async function loadNews(id) {
  if (S.news.items[id]) return;
  const items = await window.shell.newsSection(id);
  S.news.items[id] = items || [];
  if (cur().id === "news") { const f = cur().focus; render(); cur().focus = f; markFocus(); }
}
async function readStory(it) {
  if (S.story?.link === it.link && S.story.summary) return;
  S.story = { link: it.link };
  const r = await window.shell.article(it);
  if (S.story?.link !== it.link) return;
  S.story = { link: it.link, summary: r.summary, questions: r.questions, source: r.source };
  if (cur().id === "story") { const f = cur().focus; render(); cur().focus = f; markFocus(); }
  Voice.say(`${it.title}. ${r.summary}`, { priority: P.CONTENT });
}
async function loadStarters() {
  S.ask.loading = true;
  S.ask.next = await window.shell.askStarters();
  S.ask.loading = false;
  if (cur().id === "ask") render();
}
async function askSay(text) {
  const A = S.ask;
  if (A.busy) { status("Still thinking about the last one"); return; }
  A.history.push({ role: "person", text }); A.busy = true; render();
  const r = await window.shell.ask(A.history, A.about);
  A.history.push({ role: "residual", text: r.answer }); A.next = r.next; A.busy = false;
  if (cur().id === "ask") { render(); setFocus(0, 0); }
  Voice.say(r.answer, { priority: P.CONTENT });
}

/* ================================================================ routines */
let routineRun = 0;
function runRoutine(r) {
  const p = flight.run("routine", () => runRoutineSteps(r));
  if (!p) status(`Still doing ${S.routineName || "the last routine"}`);
  return p;
}
async function runRoutineSteps(r) {
  const run = ++routineRun;
  S.routineName = r.name;
  window.shell.used(`routine:${r.name}`);
  status(`${r.name}…`);
  for (const step of r.steps) {
    if (run !== routineRun) return;
    if (step.say !== undefined) await Voice.say(step.say, { priority: P.PERSON });
    else if (step.keyboard) { openKeyboard({ target: step.keyboard }); return; }
    else if (step.help) { await startHelp("general", true); return; }
    else if (step.message) { const ok = await sendMessage(step.message, run); if (!ok) return; }
    else if (step.app) {
      const a = appById(step.app);
      if (!a) { status(`No app "${step.app}" in config`); return; }
      const url = step.search && a.search ? a.search.replace("{q}", encodeURIComponent(step.search)) : step.url || undefined;
      await openApp(a.id, url);
      if (step.play) {
        let res = null;
        for (let k = 0; k < 24 && run === routineRun; k++) {      // up to ~12 s for the page to load
          await sleep(500);
          res = await window.shell.pageCall("playFirst");
          if (res && res.done) break;
        }
        status(res && res.done ? res.done : "Couldn't start it by itself. Use Select on what you want.");
      }
    } else if (step.wait) await sleep(step.wait);
  }
}
function sendMessage(m, run) {
  const p = flight.run("send", () => sendMessageNow(m, run));
  if (!p) { status("Still sending the last message"); return Promise.resolve(false); }
  return p;
}
async function sendMessageNow({ to, text }, run) {
  // Through Residual Relay (Photon) when it's running and knows this person; WhatsApp otherwise.
  if (S.agent.health.ok && (S.agent.health.contacts || []).some(n => n.toLowerCase() === String(to).toLowerCase())) {
    const go = await countdown(`Sending to ${to}`, `“${text}”`, 3);
    if (!go) { status("Not sent"); return false; }
    Voice.say(text, { priority: P.PERSON });        // his words, out loud, in his voice, as they go
    const res = await window.shell.agentIntent({ intent: "send_message", contact: to, message: text });
    trackTask(res);
    if (res.status === "rejected" || res.status === "unavailable") status(`Couldn't send through Residual Relay: ${res.error || res.status}. Trying WhatsApp.`);
    else { status(`Sending to ${to}…`); return true; }
  }
  const p = (CFG.people || []).find(x => x.name.toLowerCase() === String(to).toLowerCase());
  if (!p || !p.phone) { status(`Add ${to}'s WhatsApp number in config.json, under people`); return false; }
  const url = `https://web.whatsapp.com/send?phone=${encodeURIComponent(p.phone.replace(/[^\d]/g, ""))}&text=${encodeURIComponent(text)}`;
  await openApp("whatsapp", url);
  let ready = false;
  for (let k = 0; k < 40 && run === routineRun; k++) {
    await sleep(500);
    ready = await window.shell.pageCall("composerHas", text.slice(0, 12));
    if (ready) break;
  }
  if (!ready) { status("WhatsApp didn't open the chat. Is it logged in?"); return false; }
  const go = await countdown(`Sending to ${to}`, `“${text}”`, 3);
  if (!go) { status("Not sent"); return false; }
  await window.shell.pressKey("Return");
  status(`Sent to ${to}`);
  return true;
}

/* ================================================================ sheets */
// A sheet takes over input until it closes. Choices are scanned like any screen.
function openSheet(s) { S.sheet = { focus: 0, ...s }; window.shell.viewHidden(true); renderSheet(); markFocus(); tel("sheet", { type: s.type || "choice" }); }
function closeSheet(force = false) {
  if (!force && S.sheet && S.sheet.type === "lifeline" && S.lifeline && S.lifeline.active && !S.lifeline.ackBy) return;
  if (!force && S.sheet && S.sheet.type === "task" && S.agent.tasks[S.sheet.id]?.kind === "emergency" && !TERMINAL.has(S.agent.tasks[S.sheet.id].status)) return;
  S.sheet = null; $("#overlay").className = ""; $("#overlay").innerHTML = ""; window.shell.viewHidden(false); markFocus();
}
function choiceHTML(items, focus, cls = "", danger = -1) {
  return `<div class="choices ${cls}">${items.map((t, i) => `<button class="f btn ${i === focus ? "on" : ""} ${i === danger ? "danger" : ""}" data-c="${i}">${esc(t)}</button>`).join("")}</div>`;
}
function renderSheet() {
  const o = $("#overlay"), s = S.sheet;
  if (!s) return closeSheet(true);
  o.className = "show";
  let h = "";
  if (s.type === "migrate") {
    const from = s.from || [], to = s.to || [];
    const fromName = movName(from), toName = movName(to);
    h = `<div class="sheet"><p class="kicker">${s.ok ? "Control moved" : s.failed ? "Staying put" : "Residual noticed something"}</p>
      <h2>${s.ok ? `${esc(toName)} selects now. You're still in control.` : s.failed ? `Okay, staying with ${esc(fromName.toLowerCase())}.` : esc(s.why)}</h2>
      <div class="migr"><span class="mv old">${I(MOV[from[0]]?.icon || "hand")}${esc(fromName)}</span><span class="arrow"></span>
        <span class="mv new ${s.ok ? "ok" : ""}">${I(MOV[to[0]]?.icon || "brow")}${esc(toName)}</span></div>
      ${s.ok || s.failed ? "" : `<p>${esc(toName.split(" + ").map(n => n.replace(/^(Hand pinch|Eyebrow raise|Long blink)$/, m => ({ "Hand pinch": "Pinch", "Eyebrow raise": "Raise your eyebrow", "Long blink": "Do a long blink" }[m]))).join(" and "))} ${s.need || 2} times, so I know it's on purpose. You don't have to move strongly.</p>
      <div class="dots">${Array.from({ length: s.need || 2 }, (_, i) => `<i class="${i < (s.got || 0) ? "got" : ""}"></i>`).join("")}</div>`}</div>`;
  } else if (s.type === "probe") {
    h = `<div class="sheet"><p class="kicker">Quick check</p><h2>Is your ${esc(movName(s.option).toLowerCase())} still easy?</h2>
      <p>Do it ${s.need || 2} times. If it's hard, don't worry: I'll find another way.</p>
      <div class="dots">${Array.from({ length: s.need || 2 }, (_, i) => `<i class="${i < (s.got || 0) ? "got" : ""}"></i>`).join("")}</div></div>`;
  } else if (s.type === "calib") {
    h = calibHTML(s);
  } else if (s.type === "calib-result") {
    h = calibResultHTML(s);
  } else if (s.type === "countdown") {
    h = `<div class="sheet"><p class="kicker">${esc(s.title)}</p><h2>${esc(s.body)}</h2><div class="big-n">${s.left}</div><p>Any movement stops it.</p></div>`;
  } else if (s.type === "keyboard") {
    h = `<div class="sheet wide kb">${s.kb.html()}</div>`;
  } else if (s.type === "lifeline") {
    h = lifelineHTML(s);
  } else if (s.type === "task") {
    h = taskHTML(s);
  } else if (s.type === "verify-local") {
    h = `<div class="sheet"><p class="kicker">Residual noticed something</p><h2>${esc(s.why)}</h2>
      <div class="migr"><span class="mv old">${esc(movName(s.from))}</span><span class="arrow"></span><span class="mv new">${esc(movName(s.to))}</span></div>
      <p>Do it twice so I know it's on purpose.</p><div class="dots"><i class="${s.got > 0 ? "got" : ""}"></i><i class="${s.got > 1 ? "got" : ""}"></i></div></div>`;
  } else {
    h = `<div class="sheet ${s.wide ? "wide" : ""}"><p class="kicker">${esc(s.kicker || "")}</p><h2>${esc(s.title)}</h2>${s.body ? `<p>${esc(s.body)}</p>` : ""}
      ${choiceHTML(s.items, s.focus, s.cls || "", s.danger ?? -1)}</div>`;
  }
  o.innerHTML = h;
  o.querySelectorAll("[data-c]").forEach(el => el.addEventListener("click", () => { s.focus = +el.dataset.c; sheetAct("select"); }));
}
function sheetAct(a) {
  const s = S.sheet;
  if (s.type === "migrate" || s.type === "probe") return;   // the engine drives these
  if (s.type === "calib" || s.type === "calib-result") {
    const items = s.type === "calib" ? calibChoices(s.cal || {}) : s.choices || [];
    if (!items.length) return;
    if (a === "next") { s.focus = (s.focus + 1) % items.length; renderSheet(); }
    else if (a === "select") items[Math.min(s.focus, items.length - 1)].run();
    return;
  }
  if (s.type === "verify-local") {
    if (a !== "select") return shake(a);
    s.got++; renderSheet();
    if (s.got >= 2) setTimeout(() => { closeSheet(); status(`${movName(s.to)} selects now`); }, 600);
    return;
  }
  if (s.type === "keyboard") { s.kb.act(a); if (S.sheet === s) renderSheet(); return; }
  if (s.type === "countdown") { const r = s.resolve; closeSheet(); r(false); return; }
  if (s.type === "lifeline") return lifelineAct(a);
  if (s.type === "task") return taskAct(a);
  if (a === "next") { s.focus = (s.focus + 1) % s.items.length; renderSheet(); }
  else if (a === "select") s.run(s.focus);
  else if (a === "back") closeSheet();
}
/* ---------------------------------------------------------------- calibration */
// Driven by runtime.calibration (live) and runtime.calibration_result (verdicts). It never says
// "all set" unless every movement it tried was learned (VALID).
const PRE = { camera_not_open: "The camera didn't open. On a Mac: System Settings, Privacy & Security, Camera, and allow Residual (or Terminal).",
              no_frames: "The camera opened but isn't sending pictures. Another app may be using it.",
              no_face: "The camera works, but I can't see a face. Sit facing the camera, with light on your face.",
              unstable: "I can see you, but the picture keeps moving. Rest your head and stay still for a second.",
              face_ok: "Camera and face look good." };
function calibChoices(c) {
  if (c.failed) return [{ label: "Retry camera", run: () => sendEngine({ type: "camera_retry" }) },
                        { label: "Use keyboard for now", run: () => { sendEngine({ type: "calibration_cancel" }); closeSheet(true); status("Keys work: arrows for Next and Back, space for Select"); } }];
  if (c.channel) return [{ label: "Skip this movement", run: () => sendEngine({ type: "calibration_skip" }) }];
  return [];
}
function calibHTML(s) {
  const c = s.cal || {};
  const steps = `<div class="steps">${Array.from({ length: c.total || 4 }, (_, i) => `<i class="${i < (c.index || 0) ? "done" : i === (c.index || 0) ? "cur" : ""}"></i>`).join("")}</div>`;
  const ch = calibChoices(c);
  const choices = ch.length ? choiceHTML(ch.map(x => x.label), Math.min(s.focus, ch.length - 1), ch.length > 1 ? "two" : "") : "";
  if (c.step === "preflight" || c.phase === "preflight") {
    return `<div class="sheet">${steps}<p class="kicker">${c.failed ? "The camera isn't ready" : "Checking the camera"}</p>
      <h2>${esc(PRE[c.detail] || c.reason || "Starting the camera…")}</h2>${c.failed ? "" : `<p>${esc(c.reason || "")}</p>`}${choices}</div>`;
  }
  if (c.channel) {
    const need = c.needed || 3, got = c.count || 0;
    return `<div class="sheet">${steps}<p class="kicker">Let's find what feels comfortable today</p><h2>${esc(c.prompt)}</h2>
      <div class="big-n">${got} / ${need}</div>
      <div class="dots">${Array.from({ length: need }, (_, i) => `<i class="${i < got ? "got" : ""}"></i>`).join("")}</div>
      <p>${esc(c.hint || "You don't have to move strongly. Just move comfortably, the way you would all day.")}</p>
      <p class="label">${c.remaining != null ? `${Math.ceil(c.remaining)} s left for this one` : ""}</p>${choices}</div>`;
  }
  return `<div class="sheet">${steps}<p class="kicker">Let's find what feels comfortable today</p><h2>${esc(c.prompt || "Getting ready…")}</h2>
    <div class="big-n">${c.remaining != null ? Math.ceil(c.remaining) : ""}</div><p>${esc(c.hint || "")}</p></div>`;
}
function openCalibResult(r) {
  const v = window.calibVerdict(r.results || {}, r.gaze || null);
  const choices = v.choices.map(c => ({ label: c.label, run: () => { closeSheet(true); if (c.retry) sendEngine({ type: "calibrate", channel: c.retry }); } }));
  openSheet({ type: "calib-result", v, choices });
  if (v.allOk) setTimeout(() => { if (S.sheet?.type === "calib-result") closeSheet(true); }, 3500);
}
function calibResultHTML(s) {
  const v = s.v;
  return `<div class="sheet"><p class="kicker">What I learned</p><h2>${esc(v.title)}</h2>
    <ul class="calres">${v.rows.map(r => `<li class="${r.ok ? "ok" : "bad"}"><b>${esc(r.name)}</b> ${r.mark}${r.reason ? `<small>${esc(r.reason)}</small>` : ""}</li>`).join("")}</ul>
    ${v.sub ? `<p>${esc(v.sub)}</p>` : ""}${choiceHTML(s.choices.map(x => x.label), Math.min(s.focus, s.choices.length - 1), s.choices.length > 1 ? "two" : "")}</div>`;
}

function countdown(title, body, secs) {
  return new Promise(resolve => {
    openSheet({ type: "countdown", title, body, left: secs, resolve });
    const iv = setInterval(() => {
      if (!S.sheet || S.sheet.type !== "countdown") { clearInterval(iv); return; }
      S.sheet.left--; renderSheet();
      if (S.sheet.left <= 0) { clearInterval(iv); closeSheet(); resolve(true); }
    }, 1000);
  });
}
function confirmRisky(res) {
  openSheet({ kicker: "Check before it happens", title: `${res.label}?`,
    body: `On “${res.page || appById(S.active)?.name || "this page"}”. This spends money or can't be undone, so it needs a yes.`,
    items: ["Yes, do it", "No, cancel"], cls: "two", danger: 0,
    run: async i => { closeSheet(); await window.shell.confirmPending(i === 0); status(i === 0 ? "Done: " + res.label : "Cancelled"); } });
}
async function openSearch(appId) {
  const a = appById(appId) || appById("youtube");
  openSheet({ kicker: `Search ${a.name}`, title: "What do you feel like?", body: "Finding ideas…", items: ["Type something", "Cancel"], cls: "two", wide: true, run: () => {} });
  const r = await window.shell.searchIdeas(a.id);
  if (!S.sheet || !S.sheet.kicker?.startsWith("Search")) return;
  const ideas = r.list;
  Object.assign(S.sheet, {
    body: r.source === "ai" ? "Picked from what you like and the time of day." : "From what you like and the time of day.",
    items: [...ideas, "Type something", "Cancel"], cls: "three",
    run: i => {
      if (i < ideas.length) { closeSheet(); window.shell.search(a.id, ideas[i]); status("Searching " + ideas[i]); }
      else if (i === ideas.length) openKeyboard({ target: "search", app: a.id });
      else closeSheet();
    }
  });
  renderSheet();
}
function openKeyboard({ target, app, person }) {
  const a = app && appById(app);
  const title = target === "chat" ? "Write a message" : target === "message" ? `Message ${person?.name}` : target === "search" ? `Search ${a ? a.name : ""}`
    : target === "speak" ? "Type it and I'll say it" : target === "ask" ? "Ask Residual" : "Type";
  const kb = new window.Keyboard({
    title, history: S.feed.typed || [],
    continueText: S.ai && S.ai.ok ? t => window.shell.continueText(t) : null,
    onCancel: () => closeSheet(),
    onDone: async text => {
      closeSheet();
      if (!text) return;
      if (target === "speak") return Voice.say(text, { priority: P.PERSON });
      if (target === "ask") return askSay(text);
      if (target === "message") return messagePerson(person, text);
      if (target === "search" && a && a.search) { window.shell.search(a.id, text); status("Searching " + text); }
      else { const r = await window.shell.typeInto(text, target !== "field"); status(r && r.done ? r.done : "Typed"); }
    }
  });
  kb.paint = () => { if (S.sheet && S.sheet.kb === kb) renderSheet(); };
  openSheet({ type: "keyboard", kb });
}

/* ================================================================ help and Lifeline */
// Every choice here is a structured intent for Residual Relay (../agent), the same agent ASI users
// reach on Agentverse: one orchestrator, one Photon action layer. If the agent or Photon is down,
// an emergency falls back to the local Lifeline (ntfy) and the alarm in the room; nothing is
// silently dropped.
const HELP = [
  { label: "Help now", need: "general", emergency: true },
  { label: "Move me", need: "repositioning" },
  { label: "I'm in pain", need: "pain", urgency: "urgent" },
  { label: "Trouble breathing", need: "breathing", emergency: true },
  { label: "Suction", need: "suction", urgency: "urgent" },
  { label: "Cancel" }
];
const NEED_TEXT = { repositioning: "needs help repositioning", pain: "is in pain", suction: "needs suction",
                    breathing: "is having trouble breathing", general: "needs help now", thirsty: "is thirsty", bathroom: "needs help with the bathroom" };
function askHelp() {
  const open = activeHelpTask();
  if (open) return showTask(open.task_id);
  if (S.lifeline && S.lifeline.active && !S.lifeline.ackBy) return showLifeline();
  const first = (S.agent.health.ok && S.agent.health.contacts?.[0]) || CFG.lifeline?.contacts?.[0]?.name || "your carer";
  openSheet({ kicker: "Get help", title: `${first} first, then the next person, until someone answers.`,
    items: HELP.map(h => h.label), cls: "two", danger: 0,
    run: async i => { const h = HELP[i]; closeSheet(); if (h.need) await startHelp(h.need, !!h.emergency, h.urgency); } });
}
const activeHelpTask = () => Object.values(S.agent.tasks).find(t => ["help", "emergency"].includes(t.kind) && !TERMINAL.has(t.status));
const TERMINAL = new Set(["acknowledged", "declined_all", "exhausted", "sent", "failed", "cancelled"]);
function startHelp(need, emergency, urgency) {
  const p = flight.run("help", async () => {
    const why = NEED_TEXT[need] || "needs help";
    if (emergency) { alarm(true, true); Voice.say(`${CFG.person} ${why}. Getting help.`, { priority: P.EMERGENCY }); }
    if (!S.agent.health.ok) await refreshAgentHealth();
    const res = S.agent.health.ok
      ? await window.shell.agentIntent(emergency ? { intent: "emergency", need, urgency: "emergency" }
                                                 : { intent: "request_caregiver_help", need, urgency: urgency || "normal", requires_confirmation: false })
      : { status: "unavailable", error: S.agent.health.error || "Residual Relay isn't running" };
    trackTask(res);
    if (res.task_id) { showTask(res.task_id); return; }
    // the agent couldn't take it
    if (emergency) { status(`Residual Relay unavailable (${res.error || res.status}): using the backup alert`); await startLifeline(why); }
    else { status(`Couldn't reach anyone through Residual Relay: ${res.error || res.status}`); Voice.say(`${CFG.person} ${why}.`, { priority: P.PERSON }); }
  });
  if (!p) { const t = activeHelpTask(); if (t) showTask(t.task_id); }
  return p;
}
// The local Lifeline (ntfy push + alarm). The backup when the agent or Photon can't take an emergency.
function startLifeline(why) {
  return flight.run("lifeline", async () => {
    if (S.lifeline && S.lifeline.active && !S.lifeline.ackBy) return showLifeline();
    S.lifeline = await window.shell.lifelineStart(why);
    showLifeline(); alarm(true);
    Voice.say(`${CFG.person} ${why}. Calling ${S.lifeline.reached?.slice(-1)[0] || "for help"}.`, { priority: P.EMERGENCY });
  });
}

/* ================================================================ agent tasks */
async function refreshAgentHealth() { S.agent.health = await window.shell.agentHealth(); return S.agent.health; }
function trackTask(t) { if (t && t.task_id) { S.agent.tasks[t.task_id] = t; onTask(t); } }
function showTask(id) { if (S.sheet?.type === "task" && S.sheet.id === id) return renderSheet(); openSheet({ type: "task", id, confirmCancel: 0 }); }
// "Your daughter answered. She's on her way." Said the way the person knows them: by relation if there is one.
function ackLine(o) {
  const pr = (o.pronoun || "").toLowerCase();
  const who = o.relation ? `Your ${o.relation}` : o.contact;
  const name = o.relation ? `, ${o.contact},` : "";
  if (pr === "she") return `${who} answered. She's on her way.`;
  if (pr === "he") return `${who} answered. He's on his way.`;
  if (pr === "they") return `${who} answered. They're on their way.`;
  return `${who}${name} answered and is on the way.`;
}
function onTask(t) {
  S.agent.tasks[t.task_id] = t;
  const said = S.agent.said, o = t.outcome || {};
  const urgent = t.kind === "emergency";
  if (TERMINAL.has(t.status) && said[t.task_id] !== t.status) {
    said[t.task_id] = t.status;
    if (urgent) alarm(false);
    const pr = urgent ? P.EMERGENCY : P.PERSON;
    if (t.status === "acknowledged") {
      const line = t.kind === "message" ? `${o.contact} replied: ${o.message}` : ackLine(o);
      Voice.say(line, { priority: pr, key: "ack:" + t.task_id }); banner(line, 9000);
    } else if (t.status === "sent" && o.reply) Voice.say(`${o.contact} replied: ${o.reply}`, { priority: P.PERSON });
    else if (t.status === "sent") status(`Sent to ${o.contact}`);
    else if (t.status === "declined_all" || t.status === "exhausted") Voice.say(o.message || "No one answered.", { priority: pr });
    else if (t.status === "failed") {
      status(`Residual Relay: ${o.error || "failed"}`);
      if (urgent && o.fallback === "local") startLifeline(NEED_TEXT[t.intent?.need] || "needs help now");
    }
  }
  if (S.sheet?.type === "task" && S.sheet.id === t.task_id) renderSheet();
  $("#help").classList.toggle("calling", urgent && !TERMINAL.has(t.status));
}
function circleHTML(t) {
  const rows = (window.SwarmView && SwarmView.circleRows(t.swarm, t.order)) || [];
  if (!rows.length) return "";
  const head = t.swarm.mode === "circle" ? "The care circle answered" : t.swarm.mode === "advice" ? "The care circle's view (you chose who to ask)" : "The care circle didn't answer: care plan order";
  return `<div class="circle"><p class="kicker">${head}</p>${rows.map((r, i) => `<div class="cr ${r.tag === "asked first" ? "first" : r.tag === "left alone" ? "alone" : ""}">
    <i class="av">${esc((r.name || "?").slice(0, 1).toUpperCase())}</i><div class="cb"><div class="top"><b>${esc(r.name)}</b><span class="tag">${esc(r.tag)}</span></div><span class="why">${esc(r.reason || "")}</span>${r.via ? `<span class="via">${esc(r.via)}</span>` : ""}</div></div>`).join("")}</div>`;
}
function taskHTML(s) {
  const t = S.agent.tasks[s.id] || {}, o = t.outcome || {};
  const done = TERMINAL.has(t.status), ok = t.status === "acknowledged";
  const attempted = new Set((t.attempts || []).map(a => a.contact));
  const people = (t.order || []).map(n => `<span class="who ${attempted.has(n) ? "reached" : ""} ${ok && o.contact === n ? "acked" : ""}">${esc(n)}${ok && o.contact === n ? " is coming" : attempted.has(n) ? " messaged" : ""}</span>`).join("");
  const sim = (t.attempts || []).some(a => a.simulated);
  const head = ok ? (t.kind === "message" ? `${o.contact} replied: “${o.message}”` : `${o.contact} is on the way.`)
    : t.status === "cancelled" ? "Cancelled. Nobody else will be contacted."
    : t.status === "failed" ? `Couldn't send: ${o.error || "error"}` + (o.fallback === "local" && t.kind === "emergency" ? ". Using the backup alert." : "")
    : done ? (o.message || "No one answered.")
    : `Asking ${t.current || "…"}`;
  const last = (t.events || []).slice(-4).reverse();
  const cancelLabel = done ? "Okay" : s.confirmCancel ? "Select again to cancel" : t.kind === "emergency" ? "I'm okay, cancel" : "Cancel request";
  return `<div class="sheet lifeline ${ok ? "ok" : ""}">
    <p class="kicker">${ok ? "Help is coming" : done ? "Request finished" : t.kind === "emergency" ? "Calling for help" : "Asking for help"}${sim ? " (SIMULATED Photon: no real message sent)" : ""}</p>
    <h2>${esc(head)}</h2><div class="who-row">${people}</div>
    ${!done && t.order?.length > 1 ? `<p>If ${esc(t.current || "they")} can't come or doesn't answer, I'll try the next person${t.demo_timing ? ` (demo timing: ${Math.round(t.escalate_after_s)} s)` : ""}.</p>` : ""}
    ${circleHTML(t)}
    <ul class="llog">${last.map(e => `<li>${new Date(e.t * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" })}  ${esc(e.text)}</li>`).join("")}</ul>
    ${choiceHTML([cancelLabel], 0, "", done ? -1 : 0)}</div>`;
}
async function taskAct(a) {
  const s = S.sheet, t = S.agent.tasks[s.id] || {};
  if (a !== "select") return;
  if (TERMINAL.has(t.status)) { closeSheet(true); return; }
  if (!s.confirmCancel) { s.confirmCancel = 1; renderSheet(); setTimeout(() => { if (S.sheet === s) { s.confirmCancel = 0; renderSheet(); } }, 5000); return; }
  const res = await window.shell.agentCancel(s.id, true);
  if (res.task_id) trackTask(res); else status(res.error || "Couldn't cancel");
  alarm(false);
}
function showLifeline() { openSheet({ type: "lifeline", confirmCancel: 0 }); }
function lifelineHTML(s) {
  const L = S.lifeline || {};
  const acked = L.ackBy, cancelled = !L.active && !acked;
  const people = (L.contacts || []).map(n => `<span class="who ${L.reached?.includes(n) ? "reached" : ""} ${acked === n ? "acked" : ""}">${esc(n)}${acked === n ? " is coming" : L.reached?.includes(n) ? " alerted" : ""}</span>`).join("");
  return `<div class="sheet lifeline ${acked ? "ok" : ""}">
    <p class="kicker">${acked ? "Help is coming" : cancelled ? "Cancelled" : "Calling for help"}${L.simulated ? " (practice mode: no phones set up yet)" : ""}</p>
    <h2>${acked ? `${esc(acked)} is on the way.` : cancelled ? "Okay. Nobody else will be called." : `${esc(CFG.person)} ${esc(L.reason || "needs help")}.`}</h2>
    <div class="who-row">${people}</div>
    ${!acked && L.active ? `<p>${L.nextContact ? `If nobody answers, ${esc(L.nextContact)} is next in ${L.secondsToNext ?? "…"} s.` : `Calling everyone again in ${L.secondsToNext ?? "…"} s, until someone answers.`}</p>` : ""}
    <ul class="llog">${(L.log || []).slice(0, 4).map(l => `<li>${new Date(l.t).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" })}  ${esc(l.text)}</li>`).join("")}</ul>
    ${choiceHTML([acked || cancelled ? "Okay" : s.confirmCancel ? "Select again to cancel" : "I'm okay, cancel"], 0, "", acked || cancelled ? -1 : 0)}</div>`;
}
async function lifelineAct(a) {
  const s = S.sheet, L = S.lifeline;
  if (a !== "select") return;
  if (!L.active || L.ackBy) { closeSheet(true); return; }
  if (!s.confirmCancel) { s.confirmCancel = 1; renderSheet(); setTimeout(() => { if (S.sheet === s) { s.confirmCancel = 0; renderSheet(); } }, 5000); return; }
  S.lifeline = await window.shell.lifelineCancel(); alarm(false);
  renderSheet();
}
// A soft two-tone alarm in the room while nobody has answered, so someone nearby hears it too.
let alarmT = null, actx = null;
function alarm(on, agentTask = false) {
  clearInterval(alarmT); alarmT = null;
  if (!on) return;
  const beep = () => {
    const lifelineOn = S.lifeline?.active && !S.lifeline.ackBy;
    if (!lifelineOn && activeHelpTask()?.kind !== "emergency" && !(agentTask && flight.has("help"))) return alarm(false);
    try {
      actx = actx || new AudioContext();
      [0, 0.35].forEach((dt, k) => { const o = actx.createOscillator(), g = actx.createGain(); o.frequency.value = k ? 660 : 880;
        g.gain.setValueAtTime(0.0001, actx.currentTime + dt); g.gain.exponentialRampToValueAtTime(0.18, actx.currentTime + dt + 0.03);
        g.gain.exponentialRampToValueAtTime(0.0001, actx.currentTime + dt + 0.3); o.connect(g).connect(actx.destination); o.start(actx.currentTime + dt); o.stop(actx.currentTime + dt + 0.32); });
    } catch (e) {}
  };
  beep(); alarmT = setInterval(beep, 2500);
}

/* ================================================================ movement roles */
function setMode(m) {
  if (m === S.mode) return;
  const was = S.mode;
  S.mode = m; window.shell.mode(m); restartScan();
  if (was) status(m === 3 ? "Back, Select and Next all work" : m === 2 ? "Back is a button on screen now" : "The screen moves for you. Select when it lights up what you want");
  if (!S.sheet || S.sheet.type !== "keyboard") render();
  tel("mode", { mode: m });
}
function recomputeLocal() {
  for (const c of CH) { const v = S.rel[c]; if (S.usable[c] && v < DROP) S.usable[c] = false; else if (!S.usable[c] && v >= RISE) S.usable[c] = true; }
  const usable = CH.filter(c => S.usable[c]).sort((a, b) => S.rel[b] - S.rel[a]);
  const want = Math.max(1, Math.min(3, usable.length));
  const cur = S.roles;
  const sel = cur.select.every(c => S.usable[c]) ? cur.select : (usable[0] ? [usable[0]] : []);
  const rest = usable.filter(c => !sel.includes(c));
  const next = want >= 2 ? (rest.includes(cur.next) ? cur.next : rest[0]) : null;
  const back = want >= 3 ? rest.find(c => c !== next) || null : null;
  const prevSel = cur.select;
  S.roles = { select: sel, next, back };
  if (sel.length && !sameSel(sel, prevSel) && !(S.sheet && S.sheet.type === "lifeline"))
    openSheet({ type: "verify-local", from: prevSel, to: sel, got: 0, why: `Your ${movName(prevSel).toLowerCase()} is getting harder to read.` });
  setMode(applyEyes() ? 3 : want);
  renderControls();
}
// Engine mode: render the engine's own role assignment. Channels that failed calibration never
// appear here, because the engine never assigns them.
function recomputeEngine() {
  const r = S.engine.state?.runtime?.roles;
  if (!r) { S.roles = { select: S.engine.control || [], next: null, back: null }; setMode(1); renderControls(); return; }
  const wasEyes = S.eyesNav;
  S.roles = { select: r.select || [], next: r.next || null, back: r.back || null };
  S.eyesNav = r.next === "gaze_r";
  const tired = S.eyes?.fatigue?.level === "tired";
  if (wasEyes && !S.eyesNav && tired) {
    banner(`Your eyes look tired, so let them rest. ${S.roles.next ? `${MOV[S.roles.next].name} moves forward now.` : `I'll move the highlight for you. ${MOV[S.roles.select[0]]?.name || "Your movement"} chooses.`} Looking comes back when they're fresh.`);
    window.shell.care("tired", { reason: S.eyes.fatigue.reason });
  }
  if (!wasEyes && S.eyesNav && S._eyesWereTired) banner("Your eyes are rested. Look right for Next, left for Back.");
  S._eyesWereTired = tired;
  setMode(r.mode || 1);
  renderControls();
}
const recompute = () => (S.engine.on ? recomputeEngine() : recomputeLocal());   // local = no-engine presenter mode

// Eyes take Next (look right) and Back (look left) while they're fresh. Select stays on a
// deliberate movement, so looking never selects by itself (no "Midas touch").
function eyesOK() {
  const e = S.eyes;
  return !!(e && e.available && e.quality >= 0.7 && e.fatigue?.level !== "tired" && S.roles.select.length === 1 && CFG.eyes?.enabled !== false);
}
function applyEyes() {
  const was = S.eyesNav, now = eyesOK();
  if (now) { S.roles.next = "gaze_r"; S.roles.back = "gaze_l"; }
  else if (S.roles.next === "gaze_r") { S.roles.next = null; S.roles.back = null; }
  if (was && !now && S.eyes?.fatigue?.level === "tired") {
    banner(`Your eyes look tired, so let them rest. ${S.roles.next ? `${MOV[S.roles.next].name} moves forward now.` : `I'll move the highlight for you. ${MOV[S.roles.select[0]]?.name || "Your movement"} chooses.`} Looking comes back when they're fresh.`);
    window.shell.care("tired", { reason: S.eyes.fatigue.reason });
  }
  if (!was && now && S.eyes?.fatigue && S._eyesWereTired) banner("Your eyes are rested. Look right for Next, left for Back.");
  S._eyesWereTired = S.eyes?.fatigue?.level === "tired";
  S.eyesNav = now;
  return now;
}
function onGaze(dir) {
  // the engine measures which way is which during calibration; swapLeftRight is only a manual fallback
  if (CFG.eyes?.swapLeftRight && !S.engine.state?.eyes?.availability?.gaze_calibrated) dir = dir === "left" ? "right" : dir === "right" ? "left" : dir;
  if (!S.eyesNav) return;
  if (dir === "right") act("next", "gaze");
  else if (dir === "left") act("back", "gaze");
  else if (dir === "up") { S.paused = !S.paused; status(S.paused ? "Paused. Look up again to carry on." : "Carrying on"); }
}
function renderControls() {
  const set = (id, chans, fallback) => {
    const p = $("#r-" + id), how = p.querySelector(".how");
    const list = Array.isArray(chans) ? chans : chans ? [chans] : [];
    const text = list.length ? list.map(c => MOV[c]?.name || c).join(" + ") : fallback;
    if (how.textContent !== text) { p.classList.remove("changed"); void p.offsetWidth; p.classList.add("changed"); how.textContent = text; }
    p.classList.toggle("dim", !list.length);
  };
  set("back", S.roles.back, S.mode === 1 ? "On screen" : "Button on screen");
  set("select", S.roles.select, "No movement yet");
  set("next", S.roles.next, S.mode === 1 ? "Moves by itself" : "Not set");
  const t = S.mode === 3 ? ["Direct control", "Three movements, like a remote"] : S.mode === 2 ? ["Two movements", "Back is on the screen"] : ["Auto-scan", "The screen moves, you choose"];
  $("#mode b").textContent = t[0]; $("#mode span").textContent = t[1];
  const rk = JSON.stringify([S.roles, S.mode, S.eyesNav]);
  if (rk !== S._rolesKey) { S._rolesKey = rk; tel("roles", { roles: S.roles, mode: S.mode, eyesNav: S.eyesNav, rel: S.rel }); }
  pill();
}
function flash(a) { const p = $("#r-" + a); if (!p) return; p.classList.add("flash"); setTimeout(() => p.classList.remove("flash"), 220); }
function shake(a) { const p = $("#r-" + a); if (!p) return; p.classList.remove("shake"); void p.offsetWidth; p.classList.add("shake"); if (a === "back" && S.mode < 3) status("Back is a button on screen now"); }

/* ================================================================ status */
let statusT = null;
function status(t) {
  const c = $("#appname"); c.hidden = false; c.textContent = t; $("#top").classList.add("chip");
  clearTimeout(statusT); statusT = setTimeout(showTitle, 3200);
  tel("status", { text: t });
}
function showTitle() {
  const c = $("#appname"), a = S.active && appById(S.active);
  if (a) { c.hidden = false; c.textContent = `${a.name}${S.title ? "  " + S.title.replace(/^\(\d+\)\s*/, "") : ""}`; } else c.hidden = true;
  $("#top").classList.toggle("chip", !c.hidden);
}
let bannerT = null;
function banner(text, ms = 7000) {
  const b = $("#banner"); b.querySelector("span").textContent = text; b.classList.add("show");
  clearTimeout(bannerT); bannerT = setTimeout(() => b.classList.remove("show"), ms);
  tel("banner", { text });
}
function pill() {
  const p = $("#pill"), t = p.querySelector("span"), E = S.engine, st = E.state, L = S.lifeline;
  let cls = "", text = "You're in control";
  if (L && L.active && !L.ackBy) { cls = "warn busy"; text = `Alerted ${(L.reached || []).join(", ") || "carers"}`; }
  else if (L && L.active && L.ackBy) { cls = ""; text = `${L.ackBy} answered`; }
  else if (S.camDown) { cls = "off"; text = "Camera isn't sending video"; }
  else if (st?.runtime?.calibration) { cls = "warn busy"; text = "Learning your movements"; }
  else if (st?.policy?.state === "VERIFY") { cls = "warn busy"; text = st.policy.probing ? "Checking a movement" : "Trying another movement"; }
  else if (!E.on) {
    const ps = E.proc?.status;
    cls = "off" + (ps === "starting" || ps === "restarting" ? " busy" : "");
    text = ps === "starting" ? "Starting the camera" : ps === "restarting" ? "Restarting the camera" : "Camera off: keys work";
  } else if (E.source === "autopilot") { cls = "warn"; text = "Simulated person (demo)"; }
  else if (E.source === "keyboard") { cls = "warn"; text = "Keyboard as movements"; }
  else if (S.eyes?.fatigue?.level === "tired") { cls = "warn"; text = "Eyes resting"; }
  else if (S.paused) { cls = "warn"; text = "Paused"; }
  p.className = cls; t.textContent = text;
}
function renderWeather() {
  const w = S.weather, el = $("#wx");
  if (!w) { el.innerHTML = ""; return; }
  const ic = w.code >= 51 ? "rain" : w.code >= 2 ? "cloud" : "sun";
  el.innerHTML = `${I(ic)}<span>${w.temp}° ${esc(w.desc)}</span>`;
  el.querySelector("svg").style.cssText = "width:22px;height:22px";
}

/* ================================================================ auto-scan */
let scanT = null;
function restartScan() {
  clearInterval(scanT);
  if (S.mode !== 1) return;
  const tiring = S.eyes?.fatigue?.level === "tiring" ? 1.3 : 1;
  const ms = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--scan")) * 1000 * tiring;
  scanT = setInterval(() => {
    const s = S.sheet;
    if (s && ["migrate", "probe", "calib", "calib-result", "lifeline", "task", "countdown", "verify-local"].includes(s.type)) return;
    if (S.paused || S.speaking) return;
    act("next", "auto");
  }, ms);
}

/* ================================================================ Residual engine */
function connectEngine() {
  let ws;
  try { ws = new WebSocket(CFG.engineUrl); } catch (e) { return setTimeout(connectEngine, 3000); }
  S.engine.ws = ws;
  ws.onopen = () => { S.engine.on = true; if (CFG.engine?.lockControl) sendEngine({ type: "lock", value: true }); recompute(); pill(); tel("engine", { on: true }); };
  ws.onclose = () => { const was = S.engine.on; S.engine.on = false; S.engine.state = null; if (was) { recompute(); tel("engine", { on: false }); } pill(); setTimeout(connectEngine, 2500); };
  ws.onerror = () => {};
  ws.onmessage = m => {
    const msg = JSON.parse(m.data);
    if (msg.type === "state") onEngineState(msg.state);
    else if (msg.type === "events") for (const e of msg.events) onEngineEvent(e);
  };
}
let relSampleT = 0;
function onEngineState(st) {
  S.engine.state = st;
  S.engine.source = st.runtime?.source;
  for (const c of CH) { const p = st.channels?.[c]?.stats?.p_hat; if (p != null) S.rel[c] = Math.round(p * 100); }
  S.engine.control = st.control || S.engine.control;
  if (st.eyes) S.eyes = st.eyes;
  const cam = st.runtime?.camera;
  const camDown = st.runtime?.source === "webcam" && cam && (cam.open === false || (cam.age == null ? (st.t > 6) : cam.age > 3));
  if (camDown && !S.camDown) banner("The camera isn't sending video. On a Mac: System Settings, Privacy & Security, Camera, and allow Residual. Keys still work.", 15000);
  S.camDown = camDown;
  // calibration: the preflight shows camera problems itself, so it isn't hidden when the camera is down
  const cal = st.runtime?.calibration;
  const busySheet = S.sheet && ["lifeline", "task", "keyboard", "countdown"].includes(S.sheet.type);
  if (cal) {
    S.cal.was = true;
    if (S.sheet?.type === "calib") { S.sheet.cal = cal; renderSheet(); }
    else if (!busySheet) openSheet({ type: "calib", cal });
  } else if (S.sheet?.type === "calib") closeSheet(true);
  const cr = st.runtime?.calibration_result;
  if (cr) {
    if (S.cal.seq === null) S.cal.seq = cr.seq;              // an old result from before this window opened: don't re-announce
    else if (cr.seq !== S.cal.seq) { S.cal.seq = cr.seq; if (S.cal.was && cr.results && !busySheet) openCalibResult(cr); S.cal.was = false; }
  }
  const pol = st.policy || {};
  if (S.sheet && (S.sheet.type === "migrate" || S.sheet.type === "probe") && !S.sheet.ok && !S.sheet.failed && pol.state === "VERIFY") {
    if (S.sheet.got !== pol.verify_count) { S.sheet.got = pol.verify_count; S.sheet.need = pol.verify_needed; renderSheet(); }
  }
  if (Date.now() - relSampleT > 300000) { relSampleT = Date.now(); window.shell.care("rel", { rel: { hand: S.rel.hand, brow: S.rel.brow, blink: S.rel.blink } }); }
  recomputeEngine();
}
function onEngineEvent(e) {
  const sim = S.engine.source === "autopilot";   // a simulated person drives the engine's own board: watch, don't act
  const d = e.data || {};
  if (e.kind === "fire") {
    if (!sim && sameSel(d.option, S.engine.control) && !(S.sheet && ["migrate", "probe"].includes(S.sheet.type))) act("select", "engine:" + d.option.join("+"));
  } else if (e.kind === "aux_fire") {
    if (sim) return;
    if (S.roles.next === d.channel) act("next", "engine:" + d.channel);
    else if (S.roles.back === d.channel) act("back", "engine:" + d.channel);
  } else if (e.kind === "gaze") onGaze(d.dir);
  else if (e.kind === "help") { if (!sim) startHelp("general", true); }
  else if (e.kind === "verify_start") {
    if (S.sheet && ["lifeline", "keyboard", "countdown"].includes(S.sheet.type)) return;
    if (d.probe) openSheet({ type: "probe", option: d.option, got: 0, need: S.engine.state?.policy?.verify_needed || 2 });
    else openSheet({ type: "migrate", from: S.engine.control, to: d.option, got: 0, need: S.engine.state?.policy?.verify_needed || 2,
                     why: `Your ${movName(S.engine.control).toLowerCase()} is getting harder to read. Let's try ${movName(d.option).toLowerCase()}.` });
  } else if (e.kind === "probe_ok") { if (S.sheet?.type === "probe") { closeSheet(); status(`${movName(d.option)} still works`); } }
  else if (e.kind === "probe_fail") { if (S.sheet?.type === "probe") closeSheet(); }
  else if (e.kind === "verify_ok") { if (S.sheet?.type === "migrate") { S.sheet.ok = true; S.sheet.got = S.sheet.need; renderSheet(); } }
  else if (e.kind === "verify_fail") { if (S.sheet?.type === "migrate") { S.sheet.failed = true; renderSheet(); setTimeout(() => S.sheet?.failed && closeSheet(), 2600); } }
  else if (e.kind === "migrate") {
    window.shell.care("migrate", { from: d.from, to: d.to, why: d.reason });
    if (S.sheet?.type === "migrate") { S.sheet.ok = true; renderSheet(); setTimeout(() => S.sheet?.ok && closeSheet(), 2800); }
    else banner(`${movName(d.to)} selects now. You're still in control.`);
    Voice.say(`${movName(d.to)} selects now.`, { priority: P.CONTROL });
  }
}
function sendEngine(o) { if (S.engine.on && S.engine.ws?.readyState === 1) S.engine.ws.send(JSON.stringify(o)); }

/* ================================================================ data refresh */
async function refreshSuggestions() {
  S.suggestions = await window.shell.suggestions();
  S.feed = await window.shell.homeFeed();
  if (!S.active && cur().id === "home" && !S.sheet) render();
}
async function refreshWeather() { S.weather = await window.shell.weather(); renderWeather(); if (!S.speaking && cur().id === "home" && !S.active) { const v = $(".voice p"); if (v) v.textContent = S.companion || companionLine(); } }

/* ================================================================ wiring */
function sendRect() {
  const r = stage.getBoundingClientRect();
  window.shell.stageRect({ x: r.left, y: r.top, width: r.width, height: r.height });
}
const KEYS = { ArrowRight: "next", ArrowDown: "next", " ": "select", Enter: "select", ArrowLeft: "back", Backspace: "back", Escape: "home", h: "help", H: "help" };
document.addEventListener("keydown", e => {
  if (e.target.tagName === "INPUT" || e.metaKey || e.ctrlKey) return;
  if (e.key === "`") { $("#dev").hidden = !$("#dev").hidden; return; }
  if (e.key === "c" && S.engine.on) { sendEngine({ type: "calibrate" }); return; }
  const a = KEYS[e.key]; if (a) { e.preventDefault(); act(a, "key"); }
});
window.shell.onKey(a => { if (!(window.Two && window.Two.state.on)) act(a, "key"); });   // two-choice mode routes keys itself
window.shell.onAppState(s => {
  const changed = s.active !== S.active;
  S.active = s.active; S.title = s.title; S.open = s.open;
  if (changed) { if (!S.active) refreshSuggestions(); render(); }
  showTitle();
});
window.shell.onAI(s => { S.ai = s; });
window.shell.onStatus(s => status(s.text));
window.shell.onSignals(s => { S.signals = s; if (cur().id === "home" && !S.active && !S.sheet) render(); });
window.shell.onEngineProc(p => { S.engine.proc = p; pill(); });
window.shell.onLifeline(s => {
  S.lifeline = s;
  if (s.active && !s.ackBy && (!S.sheet || S.sheet.type !== "lifeline")) { showLifeline(); alarm(true); }
  else if (S.sheet && S.sheet.type === "lifeline") renderSheet();
  if (s.ackBy && !S._ackSaid) { S._ackSaid = s.ackBy; alarm(false); Voice.say(`${s.ackBy} is on the way.`, { priority: P.EMERGENCY }); }
  if (!s.active) S._ackSaid = null;
  $("#help").classList.toggle("calling", !!(s.active && !s.ackBy));
  $("#help span").textContent = s.active && !s.ackBy ? "Calling for help" : s.ackBy && s.active ? `${s.ackBy} is coming` : "Get help";
  pill();
});
window.shell.onAgentTask(t => onTask(t));
window.shell.onAgentHealth(h => { S.agent.health = h; });
window.shell.onLabCmd(a => {
  if (a.agentReply) window.shell.agentSimReply(a.agentReply.contact, a.agentReply.text);
  if (a.simAck) window.shell.lifelineSimAck();
  if (a.calibrate) sendEngine({ type: "calibrate" });
  if (a.say) Voice.say(a.say, { priority: P.CONTENT });
  if (a.key) act(a.key, "lab");
});
$("#r-back").onclick = () => act("back", "click");
$("#r-select").onclick = () => act("select", "click");
$("#r-next").onclick = () => act("next", "click");
$("#help").onclick = () => askHelp();
$("#labBtn").onclick = () => window.shell.openLab();

// presenter drawer (no engine)
for (const c of CH) $("#d-" + c).addEventListener("input", e => { S.rel[c] = +e.target.value; e.target.nextElementSibling.textContent = e.target.value; if (!S.engine.on) recompute(); });
let anim = null;
function animateTo(target, ms = 3500) {
  clearInterval(anim);
  const start = {}; for (const c in target) start[c] = S.rel[c];
  const t0 = Date.now();
  anim = setInterval(() => {
    const p = Math.min(1, (Date.now() - t0) / ms);
    for (const c in target) { S.rel[c] = Math.round(start[c] + (target[c] - start[c]) * p); $("#d-" + c).value = S.rel[c]; $("#d-" + c).nextElementSibling.textContent = S.rel[c]; }
    if (!S.engine.on) recompute();
    if (p >= 1) clearInterval(anim);
  }, 120);
}
$("#d-weak").onclick = () => animateTo({ hand: 25 });
$("#d-one").onclick = () => animateTo({ hand: 20, brow: 88, blink: 40 }, 4000);
$("#d-eyes").onclick = () => { if (S.engine.on) return status("Real eyes are connected"); S.eyes = S.eyes ? null : { available: true, quality: 0.95, fatigue: { score: 0.05, level: "fresh", reason: "" } }; recompute(); };
$("#d-tired").onclick = () => {
  if (S.engine.on || !S.eyes) return status("Turn on simulated eyes first");
  let k = 0; const iv = setInterval(() => { k++; const sc = Math.min(0.7, 0.05 + k * 0.05);
    S.eyes.fatigue = { score: sc, level: sc >= 0.55 ? "tired" : sc >= 0.3 ? "tiring" : "fresh", reason: "lids lower than usual" };
    recompute(); if (sc >= 0.7) clearInterval(iv); }, 300);
};
$("#d-reset").onclick = () => { clearInterval(anim); S.rel = { hand: 96, brow: 91, blink: 88 }; S.usable = { hand: true, brow: true, blink: true }; S.roles = { select: ["hand"], next: "brow", back: "blink" }; if (S.eyes) S.eyes.fatigue = { score: 0.05, level: "fresh" }; for (const c of CH) { $("#d-" + c).value = S.rel[c]; $("#d-" + c).nextElementSibling.textContent = S.rel[c]; } recompute(); };
$("#d-ack").onclick = () => window.shell.lifelineSimAck();

function tick() {
  const d = new Date();
  $("#when").innerHTML = `${d.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" })}<b>${d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</b>`;
}
// session minutes + selections for the care report
setInterval(() => { window.shell.care("session", { minutes: 5, selections: S.selections }); S.selections = 0; }, 300000);

(async () => {
  CFG = await window.shell.config();
  tick(); setInterval(tick, 15000);
  window.shell.mode(3);
  S.ai = await window.shell.aiStatus();
  S.lifeline = await window.shell.lifelineState();
  S.engine.proc = await window.shell.engineProc();
  S.signals = await window.shell.signals();
  window.shell.newsSections().then(s => { S.news.sections = s; if (s[0]) loadNews(s[0].id); });
  await refreshSuggestions();
  render(); renderControls();
  // the greeting on screen stays; the spoken one happens once per app launch (main process
  // remembers), never on reload, reconnect or returning Home, and never starts a briefing
  if (await window.shell.welcomeOnce()) Voice.say(greetingText(), { priority: P.AMBIENT, key: "welcome" });
  refreshAgentHealth();
  refreshWeather(); setInterval(refreshWeather, 15 * 60 * 1000);
  setInterval(refreshSuggestions, 5 * 60 * 1000);
  new ResizeObserver(sendRect).observe(stage); addEventListener("resize", sendRect); sendRect();
  if ((CFG.input?.mode || "two") !== "two") connectEngine();   // two-choice mode runs the eye tracker in this page instead
})();
