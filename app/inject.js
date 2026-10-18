// Runs inside each real app (Instagram, YouTube, YouTube Music, WhatsApp, Netflix, Uber Eats...).
// Turns Back / Select / Next into actions on the real page:
//  - a remote-control panel of big buttons, different per page (reels, video, chat, browse)
//  - a scanner that walks the page's real buttons and links with a highlight ring
//  - reading the open chat (for smart replies) and typing into the page
//  - a guard that asks before anything that spends money or can't be undone
// DOM APIs only (no innerHTML): YouTube blocks innerHTML from scripts.
(() => {
  if (window.__residual) return true;

  const H = location.hostname;
  const st = { mode: 3, replies: [], cur: null, pending: null, panelKey: "", repliesFor: "" };

  // ---------------------------------------------------------------- highlight ring
  const ring = document.createElement("div");
  Object.assign(ring.style, {
    position: "fixed", zIndex: "2147483646", pointerEvents: "none", display: "none",
    border: "3px solid #ffc56b", borderRadius: "14px",
    boxShadow: "0 0 0 6px rgba(255,197,107,.25), 0 0 34px rgba(255,197,107,.4)",
    transition: "left .14s, top .14s, width .14s, height .14s"
  });
  document.documentElement.appendChild(ring);
  function place(el) {
    if (!el || !document.contains(el)) { ring.style.display = "none"; return; }
    const r = el.getBoundingClientRect();
    Object.assign(ring.style, { display: "block", left: r.left - 6 + "px", top: r.top - 6 + "px",
      width: r.width + 12 + "px", height: r.height + 12 + "px" });
  }
  function focusEl(el) {
    st.cur = el;
    if (el && !el.dataset.residual) el.scrollIntoView({ block: "nearest", inline: "nearest" });
    place(el);
  }
  addEventListener("scroll", () => place(st.cur), true);
  addEventListener("resize", () => place(st.cur));

  // ---------------------------------------------------------------- where are we
  const isYT = H === "www.youtube.com" || H === "m.youtube.com";
  const app = H.includes("instagram") ? "instagram" : H.startsWith("music.youtube") ? "music" : isYT ? "youtube"
    : H.includes("whatsapp") ? "whatsapp" : H.includes("netflix") ? "netflix" : H.includes("ubereats") ? "food" : "web";
  const composer = () => document.querySelector('footer [contenteditable="true"], [data-testid="conversation-compose-box-input"]');
  function video() {
    const vs = [...document.querySelectorAll("video")].filter(v => { const r = v.getBoundingClientRect(); return r.width > 200 && r.height > 120 && r.bottom > 0 && r.top < innerHeight; });
    vs.sort((a, b) => (b.getBoundingClientRect().width * b.getBoundingClientRect().height) - (a.getBoundingClientRect().width * a.getBoundingClientRect().height));
    return vs[0] || null;
  }
  function page() {
    const p = location.pathname;
    if (app === "instagram" && p.startsWith("/reel")) return "reels";
    if (app === "youtube" && p.startsWith("/shorts")) return "reels";
    if (app === "netflix" && p.startsWith("/watch")) return "video";
    if (app === "youtube" && p === "/watch") return "video";
    if (app === "music" && p === "/watch") return "video";
    if (app === "whatsapp" && composer()) return "chat";
    if (app === "whatsapp") return "chats";
    return "browse";
  }
  const SEARCHABLE = new Set(["youtube", "music", "netflix", "food"]);

  // ---------------------------------------------------------------- page elements
  const SEL = 'a[href],button,[role="button"],[role="link"],[role="tab"],[role="menuitem"],' +
    '[role="option"],[role="listitem"],[role="row"],input:not([type="hidden"]),select,' +
    'textarea,[contenteditable="true"]';
  function visible(el) {
    const r = el.getBoundingClientRect();
    if (r.width < 16 || r.height < 16) return false;
    if (r.bottom < 0 || r.top > innerHeight || r.right < 0 || r.left > innerWidth) return false;
    // covered by Residual's choice dock: offered once scrolled up into view
    if (st.inset && r.top + Math.min(r.height, 40) > innerHeight - st.inset) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== "hidden" && cs.display !== "none" && +cs.opacity !== 0;
  }
  // Busy pages (YouTube home has hundreds of links): scan the videos themselves, not every chip.
  const GRID = { youtube: "ytd-rich-item-renderer a#thumbnail, ytd-video-renderer a#thumbnail, ytd-compact-video-renderer a#thumbnail",
                 music: "ytmusic-two-row-item-renderer a, ytmusic-responsive-list-item-renderer" };
  function pageItems() {
    if (GRID[app] && page() === "browse") {
      const g = [...document.querySelectorAll(GRID[app])].filter(visible);
      if (g.length >= 3) return g.sort((a, b) => { const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect(); return Math.abs(ra.top - rb.top) > 10 ? ra.top - rb.top : ra.left - rb.left; });
    }
    let root = document;
    if (page() === "chats") root = document.querySelector("#pane-side") || document;
    let els = [...root.querySelectorAll(page() === "chats" ? '[role="listitem"],[role="row"]' : SEL)]
      .filter(el => !el.closest("[data-residual-panel]") && !el.closest('[aria-hidden="true"]') && visible(el));
    const set = new Set(els);
    els = els.filter(el => { for (let p = el.parentElement; p; p = p.parentElement) if (set.has(p)) return false; return true; });
    els.sort((a, b) => {
      const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
      return Math.abs(ra.top - rb.top) > 10 ? ra.top - rb.top : ra.left - rb.left;
    });
    return els;
  }
  // YouTube's thumbnails are links with no text of their own: use the video's title
  const titleOf = el => { const r = el && el.closest && el.closest("ytd-rich-item-renderer, ytd-video-renderer, ytd-compact-video-renderer, ytd-grid-video-renderer");
    const t = r && r.querySelector("#video-title, #video-title-link"); return t ? (t.getAttribute("title") || t.textContent || "").trim() : ""; };
  const label = el => !el ? "" : (el.dataset.label || titleOf(el) || el.getAttribute("aria-label") || el.innerText || el.value || el.getAttribute("placeholder") || el.title || "")
    .replace(/\s+/g, " ").trim().slice(0, 80);

  // ---------------------------------------------------------------- remote-control panel
  let panel = null;
  function chips() {
    const p = page(), c = [], lim = st.mode < 3;
    if (p === "reels") { if (lim) c.push(["next", "↓  Next"], ["like", "♥  Like"]); c.push(["mute", "🔈 Sound"]); }
    if (p === "video") c.push(["play", "⏯  Play / pause"], ["rew", "⟲ 10s"], ["fwd", "10s ⟳"], ["voldown", "🔉"], ["volup", "🔊"]);
    if (p === "video" && app === "youtube") c.push(["cc", "CC"], ["nextvid", "Next video ⏭"]);
    if (lim && p === "video") c.push(["prevpage", "←  Back to videos"]);
    if (p === "chat") { st.replies.forEach((r, i) => c.push(["reply:" + i, r])); c.push(["type", "⌨  Type"], ["more", "↻  More replies"]); }
    if (SEARCHABLE.has(app) && p !== "video") c.unshift(["search", "🔍  Search"]);
    if (lim) c.push(["back", "←  Back"]);
    return c;
  }
  function syncPanel() {
    const list = chips(), key = page() + "|" + list.map(c => c[0] + c[1]).join(",");
    if (key === st.panelKey && (panel ? document.contains(panel) : !list.length)) return;
    st.panelKey = key;
    const keepAct = st.cur && st.cur.dataset && st.cur.dataset.residual;
    if (panel) panel.remove();
    panel = null;
    if (!list.length) return;
    panel = document.createElement("div");
    panel.dataset.residualPanel = "1";
    Object.assign(panel.style, {
      position: "fixed", left: "50%", bottom: "20px", transform: "translateX(-50%)", zIndex: "2147483645",
      display: "flex", gap: "10px", flexWrap: "wrap", justifyContent: "center", maxWidth: "94vw",
      padding: "10px", borderRadius: "22px", background: "rgba(13,16,20,.86)", backdropFilter: "blur(10px)",
      boxShadow: "0 18px 50px -12px rgba(0,0,0,.6)", border: "1px solid rgba(255,255,255,.08)"
    });
    for (const [act, text] of list) {
      const b = document.createElement("button");
      b.dataset.residual = act; b.dataset.label = text.replace(/^[^\w]+\s*/, "");
      b.textContent = text;
      const reply = act.startsWith("reply:");
      Object.assign(b.style, { font: `600 ${reply ? 17 : 16}px system-ui, -apple-system, sans-serif`, color: "#eef1f5",
        background: reply ? "#1f6b52" : "#222833", border: "1px solid rgba(255,255,255,.1)", borderRadius: "14px",
        padding: "13px 18px", cursor: "pointer", maxWidth: "340px", textAlign: "left" });
      b.addEventListener("click", ev => { ev.preventDefault(); ev.stopPropagation(); });
      panel.appendChild(b);
    }
    if (st.inset) Object.assign(panel.style, { opacity: "0", pointerEvents: "none" });   // the dock shows these instead
    document.documentElement.appendChild(panel);
    if (keepAct) { const again = panel.querySelector(`[data-residual="${CSS.escape(keepAct)}"]`); focusEl(again || null); }
  }
  const panelItems = () => panel ? [...panel.querySelectorAll("[data-residual]")] : [];

  // Scan order: on media/chat pages only the panel; elsewhere the panel's Search first, then the page.
  function items() {
    const p = page();
    if (p === "reels" || p === "video" || p === "chat") return panelItems();
    return [...panelItems(), ...pageItems()];
  }

  // ---------------------------------------------------------------- actions
  function clickLike() {
    const mid = innerHeight / 2;
    const c = [...document.querySelectorAll('svg[aria-label="Like"],svg[aria-label="Unlike"],button[aria-label^="like this"],button[aria-label^="Like"],like-button-view-model button')]
      .map(n => n.closest('button,[role="button"]') || n).filter(visible);
    c.sort((a, b) => Math.abs(a.getBoundingClientRect().top - mid) - Math.abs(b.getBoundingClientRect().top - mid));
    if (c[0]) { c[0].click(); return { done: "Liked" }; }
    return { done: "No like button here" };
  }
  function media(act) {
    if (app === "music" && act === "play") { const s = musicState(); if (s.has) { musicToggle("button"); return { done: s.playing ? "Pausing" : "Playing", music: true }; } }
    const v = video();
    if (!v) return act === "play" ? { key: app === "youtube" ? "K" : "Space" } : { done: "No video playing" };
    if (act === "play") { v.paused ? v.play().catch(() => {}) : v.pause(); return { done: v.paused ? "Paused" : "Playing" }; }
    if (act === "rew") { v.currentTime = Math.max(0, v.currentTime - 10); return { done: "Back 10 seconds" }; }
    if (act === "fwd") { v.currentTime = Math.min(v.duration || 1e9, v.currentTime + 10); return { done: "Forward 10 seconds" }; }
    if (act === "volup") { v.muted = false; v.volume = Math.min(1, v.volume + 0.15); return { done: `Volume ${Math.round(v.volume * 100)}%` }; }
    if (act === "voldown") { v.volume = Math.max(0, v.volume - 0.15); return { done: `Volume ${Math.round(v.volume * 100)}%` }; }
    if (act === "mute") { v.muted = !v.muted; return { done: v.muted ? "Muted" : "Sound on" }; }
    return null;
  }
  // The song, whether or not its video is visible (YouTube Music hides it in song mode). "button" is the player bar's own
  // play/pause; "media" talks to the media element directly.
  function mediaEl() { return [...document.querySelectorAll("video,audio")].find(m => m.src || m.currentSrc || m.readyState > 0) || document.querySelector("video,audio"); }
  function musicState() {
    const m = mediaEl(), b = document.querySelector("ytmusic-player-bar #play-pause-button, #play-pause-button");
    if (!m && !b) return { has: false };
    const titleEl = document.querySelector("ytmusic-player-bar .title");
    const title = ((titleEl && titleEl.textContent) || document.title.replace(/\s*-\s*YouTube Music$/, "")).trim();
    const playing = m ? !m.paused && !m.ended : /pause/i.test((b.getAttribute("title") || b.getAttribute("aria-label") || ""));
    return { has: !!(m && (m.currentSrc || m.src || m.readyState > 0)) || !!title, playing, title };
  }
  function musicToggle(how) {
    if (how === "media") { const m = mediaEl(); if (!m) return { clicked: false }; m.paused ? m.play().catch(() => {}) : m.pause(); return { clicked: true }; }
    const b = document.querySelector("ytmusic-player-bar #play-pause-button, #play-pause-button");
    if (!b) return { clicked: false };
    b.click();
    return { clicked: true };
  }
  function typeText(text, send) {
    const p = page();
    let box = p === "chat" ? composer() : null;
    if (!box) box = document.querySelector('input[name="search_query"], input#search, ytmusic-search-box input, input[type="search"], input[aria-label*="earch"], input[placeholder*="earch"]');
    if (!box) return { done: "No text box here" };
    box.focus();
    if (box.isContentEditable) document.execCommand("insertText", false, text);
    else {
      const proto = Object.getPrototypeOf(box), setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
      setter ? setter.call(box, text) : (box.value = text);
      box.dispatchEvent(new Event("input", { bubbles: true }));
    }
    return send ? { key: "Return", done: p === "chat" ? "Sent: " + text : "Searching " + text } : { done: "Typed" };
  }
  function runChip(act) {
    if (act === "next") return { key: "Down" };
    if (act === "like") return clickLike();
    if (["play", "rew", "fwd", "volup", "voldown", "mute"].includes(act)) return media(act);
    if (act === "cc") return { key: "C" };
    if (act === "nextvid") return { key: "N", mods: ["shift"] };
    if (act === "search") return { open: "search", app };
    if (act === "type") return { open: "keyboard", target: page() === "chat" ? "chat" : "search", app };
    if (act === "more") { st.repliesFor = ""; return { open: "replies" }; }
    if (act === "back") { clear(); return { back: true }; }
    if (act === "prevpage") { clear(); const v = video(); if (v) v.pause(); return { navBack: true }; }
    if (act.startsWith("reply:")) { const r = st.replies[+act.split(":")[1]]; return { ...typeText(r, true), sent: r }; }
    return null;
  }

  // Anything that spends money or can't be undone waits for a confirm in the shell.
  const RISKY = /place order|pay\b|pay now|checkout|check out|buy|purchase|confirm order|delete|unfollow|block|report|log ?out|unsubscribe|subscribe/i;

  function next() {
    const p = page();
    if (st.mode === 3 && p === "reels") return { key: "Down" };
    let list = items();
    if (!list.length) { scrollBy(0, innerHeight * 0.7); return { done: "Scrolled" }; }
    const i = st.cur ? list.indexOf(st.cur) : -1;
    if (i + 1 < list.length) { focusEl(list[i + 1]); return { focus: label(list[i + 1]) }; }
    if (p === "reels" || p === "video" || p === "chat") { focusEl(list[0]); return { focus: label(list[0]) }; }
    // end of what's on screen: scroll down and continue below the last one
    const lastTop = st.cur && !st.cur.dataset.residual ? st.cur.getBoundingClientRect().top : -1;
    const y = scrollY;
    scrollBy(0, innerHeight * 0.7);
    const below = pageItems();
    if (!below.length) { focusEl(null); return { done: "Scrolled" }; }
    if (scrollY === y) { list = items(); focusEl(list[0]); return { focus: label(list[0]), wrapped: true }; }
    const newTop = lastTop - (scrollY - y);
    const el = below.find(e => e.getBoundingClientRect().top > newTop + 2) || below[0];
    focusEl(el);
    return { focus: label(el) };
  }
  function select() {
    const p = page();
    if (st.mode === 3 && p === "reels" && !st.cur) return clickLike();
    if (p === "video" && !st.cur) return media("play");
    const el = st.cur;
    if (!el || !document.contains(el)) return next();
    if (el.dataset.residual) return runChip(el.dataset.residual);
    const l = label(el);
    if (RISKY.test(l)) { st.pending = el; return { confirm: true, label: l, page: document.title }; }
    if (el.matches('input,textarea,[contenteditable="true"],select')) { el.focus(); return { open: "keyboard", target: "field", app }; }
    el.click();
    clear();
    setTimeout(syncPanel, 500);
    return { done: "Opened " + (l || "item") };
  }
  function clear() { st.cur = null; place(null); }

  // ---------------------------------------------------------------- chat reading (WhatsApp)
  function readChat() {
    if (page() !== "chat") return null;
    const name = (document.querySelector("#main header span[title]") || document.querySelector("#main header span[dir='auto']"))?.textContent?.trim() || "them";
    const rows = [...document.querySelectorAll("#main .message-in, #main .message-out")].slice(-10);
    const messages = rows.map(r => ({ me: r.classList.contains("message-out"),
      text: [...r.querySelectorAll("span.selectable-text")].map(s => s.innerText).join(" ").trim() }))
      .filter(m => m.text);
    const sig = name + "|" + messages.map(m => m.text).join("|").slice(-300);
    return { name, messages, sig, needs: sig !== st.repliesFor };
  }

  // ---------------------------------------------------------------- two-choice mode
  // Everything the person could pick on this page right now (the panel's actions first, then the page's own
  // buttons and links), so the shell can offer them two groups at a time. Refs stay valid until the next call.
  const marks = [];
  function choices(limit = 40) {
    syncPanel();
    st.refs = items().slice(0, limit);
    const grid = GRID[app] && page() === "browse" ? new Set([...document.querySelectorAll(GRID[app])]) : null;
    return st.refs.map((el, i) => ({ id: i, label: label(el) || (el.tagName === "A" ? "Link" : "Button"),
      action: !!(el.dataset && el.dataset.residual), content: !!(grid && grid.has(el)), kind: el.dataset && el.dataset.residual, r: (r => [r.left, r.top, r.width, r.height].map(Math.round))(el.getBoundingClientRect()) }));
  }
  function pick(id) {
    const el = (st.refs || [])[id];
    if (!el || !document.contains(el)) return { done: "That moved. Pick again." };
    clearMarks();
    st.cur = el;
    return select();
  }
  // Outline the two groups on the page itself: left group blue, right group amber.
  function clearMarks() { while (marks.length) marks.pop().remove(); }
  function mark(left, right) {
    clearMarks();
    const draw = (ids, color) => ids.forEach(id => {
      const el = (st.refs || [])[id];
      if (!el || !document.contains(el)) return;
      const r = el.getBoundingClientRect();
      const m = document.createElement("div");
      Object.assign(m.style, { position: "fixed", left: r.left - 4 + "px", top: r.top - 4 + "px", width: r.width + 8 + "px", height: r.height + 8 + "px",
        border: `3px solid ${color}`, borderRadius: "12px", zIndex: "2147483644", pointerEvents: "none", boxShadow: `0 0 0 4px ${color}33` });
      document.documentElement.appendChild(m);
      marks.push(m);
    });
    draw(left || [], "#5aa9e6"); draw(right || [], "#f4b63f");
    return marks.length;
  }

  window.__residual = {
    choices, pick, mark, clearMarks,
    // Where a real wheel event should go (the middle of what's visible above the dock) and a signature of every scroll position
    // under that point: the window and each scrollable container around it. If the signature changes, the page scrolled.
    // (Shadow DOM is climbed through its host.) main.js's scrollPage uses this; scroll() below is only the fallback.
    probe() {
      const cx = Math.round(innerWidth / 2), vh = innerHeight - (st.inset || 0), cy = Math.round(Math.max(1, vh / 2));
      const parts = [Math.round(scrollX) + "," + Math.round(scrollY)];
      let el = document.elementFromPoint(cx, cy), guard = 0;
      while (el && el !== document.documentElement && guard++ < 60) {
        const s = getComputedStyle(el);
        if (/(auto|scroll)/.test(s.overflowY + s.overflowX) && (el.scrollHeight > el.clientHeight || el.scrollWidth > el.clientWidth))
          parts.push((el.id || el.tagName) + ":" + Math.round(el.scrollTop) + "," + Math.round(el.scrollLeft));
        el = el.parentElement || (el.getRootNode && el.getRootNode().host) || null;
      }
      return { cx, cy, vh, sig: parts.join("|") };
    },
    scroll(dir) {
      const y = scrollY, h = innerHeight - (st.inset || 0);
      scrollBy({ top: (dir === "up" ? -1 : 1) * h * 0.8, left: 0, behavior: "instant" }); clearMarks();
      if (scrollY === y) return { done: dir === "up" ? "Already at the top" : "That's the end of the page", end: true };
      return { done: dir === "up" ? "Scrolled up" : "Scrolled down", scrolled: true };
    },
    inset(px) { const was = st.inset; st.inset = px; if (was !== px) { st.panelKey = ""; syncPanel(); } return true; },
    setup(o) { st.mode = o.mode; if (o.replies && !st.replies.length) st.replies = o.replies; syncPanel(); return true; },
    cmd(action) {
      syncPanel();
      if (action === "next") return next();
      if (action === "select") return select();
      if (action === "back") {
        clear();
        const p = page();
        if (p === "chat" || (p === "video" && app === "netflix")) return { key: "Escape", done: "Closed" };
        return null;
      }
      return null;
    },
    readChat,
    // Routines: start the first result (YouTube / YouTube Music search pages)
    playFirst() {
      const pick = sel => [...document.querySelectorAll(sel)].find(visible);
      let el = null;
      if (app === "music") el = pick("ytmusic-shelf-renderer ytmusic-responsive-list-item-renderer ytmusic-play-button-renderer, ytmusic-card-shelf-renderer ytmusic-play-button-renderer, ytmusic-responsive-list-item-renderer a[href*='watch']");
      else if (app === "youtube") el = pick("ytd-video-renderer a#thumbnail[href*='watch'], ytd-video-renderer a#video-title, a#thumbnail[href*='watch']");
      else if (app === "netflix") el = pick("a[href*='/watch/'], .title-card a");
      if (!el) return { waiting: true };
      el.click();
      return { done: "Playing " + (label(el.closest("ytd-video-renderer, ytmusic-responsive-list-item-renderer") || el) || "the first result").slice(0, 60) };
    },
    composerHas(start) { const c = composer(); return !!(c && c.innerText.trim().startsWith(start.trim())); },
    setReplies(list, sig) { st.replies = list; st.repliesFor = sig || ""; st.panelKey = ""; syncPanel(); return true; },
    typeText,
    musicState,
    musicToggle,
    info() { return { app, page: page(), title: document.title, url: location.href }; },
    // Site adapters break when sites change. This says, live, what this app's selectors find.
    health() {
      const n = sel => document.querySelectorAll(sel).length;
      const checks = { scanStops: items().length, panel: panelItems().length, video: !!video() };
      if (app === "whatsapp") Object.assign(checks, { composer: !!composer(), chatRows: n("#main .message-in, #main .message-out"), chatList: !!document.querySelector("#pane-side") });
      if (app === "instagram" || (isYT && page() === "reels")) checks.likeButtons = n('svg[aria-label="Like"],svg[aria-label="Unlike"],button[aria-label^="like this"],like-button-view-model button');
      if (isYT) checks.results = n("ytd-video-renderer, ytd-rich-item-renderer");
      if (app === "music") checks.results = n("ytmusic-responsive-list-item-renderer");
      if (app === "netflix") checks.titles = n(".title-card, a[href*='/watch/']");
      return { app, page: page(), checks };
    },
    confirmPending(yes) {
      const el = st.pending; st.pending = null;
      if (yes && el && document.contains(el)) { el.click(); return "clicked"; }
      return "cancelled";
    },
    clear
  };
  setInterval(syncPanel, 1000);
  return true;
})();
