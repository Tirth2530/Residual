// Preflight: is everything the demo needs actually working right now? PASS / WARN / FAIL per check.
//
// Run with:  npm run preflight      (opens Residual, checks, prints the table, closes)
// or from the Lab (Two choices tab, "Run preflight").
//
// The central demo (camera, face, your controls, the Relay) must not depend on Ollama, WhatsApp,
// Instagram or Netflix, so those are WARN at worst, never FAIL.
const CENTRAL = new Set(["camera", "face", "calibration", "relay"]);

async function timed(fetchFn, url, opts = {}, ms = 6000) {
  const ctl = new AbortController(), to = setTimeout(() => ctl.abort(), ms);
  try { return await fetchFn(url, { ...opts, signal: ctl.signal }); } finally { clearTimeout(to); }
}

// env: { fetch, browserUseKey, elevenKey, relayUrl, relayToken, ai, renderer: {camera, face, measured, lastTest, cameraError}, cookies: {instagram, whatsapp, youtube} }
async function runChecks(env) {
  const f = env.fetch || globalThis.fetch, out = [];
  const add = (id, name, level, detail) => out.push({ id, name, level, detail, central: CENTRAL.has(id) });
  const r = env.renderer || {};

  // camera, face, calibration: from Home, which runs the tracker
  if (r.cameraError) add("camera", "Camera", "FAIL", r.cameraError);
  else if (r.camera === "running") add("camera", "Camera", "PASS", `${r.fps || "?"} fps${r.cameraLabel ? ", " + r.cameraLabel : ""}`);
  else add("camera", "Camera", "FAIL", r.camera ? `camera is ${r.camera}` : "Home didn't report the camera");
  add("face", "Face detection", r.face ? "PASS" : "FAIL", r.face ? "face found" : "no face seen in 8 s: sit in front of the camera, light on your face");
  add("calibration", "Your controls", r.measured ? (r.lastTest && r.lastTest.falsePerMinute != null && r.lastTest.falsePerMinute > 2 ? "WARN" : "PASS") : "FAIL",
    r.measured ? `learned (${r.controls || "blink + eyebrows"})${r.lastTest ? `, last test ${r.lastTest.falsePerMinute ?? "."} false / min` : ", no control test yet (Settings, Test my controls)"}`
               : "not learned yet: run Setup");

  // internet
  let online = false;
  try { const x = await timed(f, "https://www.google.com/generate_204"); online = x.status === 204 || x.ok; } catch (e) {}
  add("internet", "Internet", online ? "PASS" : "FAIL", online ? "online" : "no internet: Browser Use, Photon and Agentverse need it");

  // Browser Use
  if (!env.browserUseKey) add("browseruse", "Browser Use API", "WARN", "no BROWSER_USE_API_KEY in app/.env (AI web mode off)");
  else {
    try {
      const x = await timed(f, "https://api.browser-use.com/api/v2/tasks?pageSize=1", { headers: { "X-Browser-Use-API-Key": env.browserUseKey } });
      add("browseruse", "Browser Use API", x.ok ? "PASS" : "FAIL", x.ok ? "key accepted" : `HTTP ${x.status}${x.status === 401 || x.status === 403 ? ": the key was rejected" : ""}`);
    } catch (e) { add("browseruse", "Browser Use API", "FAIL", String(e.message || e)); }
  }

  // Residual Relay, Photon, calls, Agentverse
  let h = null;
  try { const x = await timed(f, (env.relayUrl || "http://127.0.0.1:8100") + "/health", { headers: env.relayToken ? { "X-Residual-Token": env.relayToken } : {} }, 3000); h = x.ok ? await x.json() : null; } catch (e) {}
  if (h && !(h.contacts || h.photon)) {
    // a bare {ok:true}: the Relay is up but it didn't accept our token, so Home can't use it
    add("relay", "Residual Relay", "FAIL", "running, but it didn't accept Residual's token: restart the Relay and the app, or check agent/state/local_token");
    add("photon", "Photon", "FAIL", "unknown (token not accepted)");
    add("agentverse", "Agentverse", "WARN", "unknown (token not accepted)");
  } else if (!h) {
    add("relay", "Residual Relay", "FAIL", "not running: cd agent && .venv/bin/python residual_agent.py");
    add("photon", "Photon", "FAIL", "unknown (Relay not running)");
    add("agentverse", "Agentverse", "FAIL", "unknown (Relay not running)");
  } else {
    add("relay", "Residual Relay", "PASS", `${h.agent || "Residual Relay"}${h.address ? " " + h.address.slice(0, 16) + "…" : ""}, contacts ${(h.contacts || []).join(", ")}${h.demo_timing ? ", demo timing" : ""}`);
    const live = h.photon && h.photon.mode === "photon";
    // live without a signed webhook would let anyone forge a caregiver's reply: that is a failure, not a warning
    add("photon", "Photon", live ? (h.webhook_signed ? "PASS" : "FAIL") : "WARN",
      live ? `live (${h.photon.platform})${h.webhook_signed ? ", webhook signed" : ", webhook secret not set: replies could be forged. Set PHOTON_WEBHOOK_SECRET"}` : "SIMULATED: nothing reaches a phone. Add Photon keys to agent/.env");
    const mode = h.asi_mode;
    add("asi", "ASI:One strangers", mode === "closed" || mode === "off" || mode === "approve" ? "PASS" : "WARN",
      mode === "closed" || mode === "off" ? "closed: only senders on the care team's ASI_AUTHORIZED list can make the agent act" :
      mode === "approve" ? `strangers' requests wait for an operator to approve them through the local API${h.asi_pending ? ` (${h.asi_pending} waiting)` : ""}` :
      "the Relay didn't report how it treats strangers: update it");
    add("calls", "Emergency calls", h.calls === "webhook" ? "PASS" : "WARN", h.calls === "webhook" ? "message, then call" : h.calls === "simulated" ? "SIMULATED calls" : "messages only (no VOICE_CALL_WEBHOOK)");
    if (!h.address) add("agentverse", "Agentverse", "WARN", "the Relay didn't report its address");
    else {
      try {
        const x = await timed(f, `https://agentverse.ai/v1/almanac/agents/${h.address}`);
        add("agentverse", "Agentverse", x.ok ? "PASS" : x.status === 404 ? "FAIL" : "WARN",
          x.ok ? "registered in the Almanac" : x.status === 404 ? "not registered: open the Agent Inspector link the Relay prints and connect it" : `couldn't check (HTTP ${x.status})`);
      } catch (e) { add("agentverse", "Agentverse", "WARN", "couldn't check: " + (e.message || e)); }
    }
  }

  // optional
  add("ollama", "Ollama (optional)", env.ai && env.ai.ok ? "PASS" : "WARN", env.ai && env.ai.ok ? `${env.ai.model}` : "off: replies use fixed suggestions");
  if (!env.elevenKey) add("elevenlabs", "ElevenLabs (optional)", "WARN", "no key: the system voice speaks");
  else {
    try {
      const x = await timed(f, "https://api.elevenlabs.io/v1/user", { headers: { "xi-api-key": env.elevenKey } });
      add("elevenlabs", "ElevenLabs (optional)", x.ok ? "PASS" : "WARN", x.ok ? "key accepted" : `HTTP ${x.status}: the system voice will speak`);
    } catch (e) { add("elevenlabs", "ElevenLabs (optional)", "WARN", String(e.message || e)); }
  }
  const c = env.cookies || {};
  for (const [id, name] of [["youtube", "YouTube"], ["instagram", "Instagram"]])
    add("login-" + id, `${name} login (optional)`, c[id] ? "PASS" : "WARN", c[id] ? "signed in" : "not signed in (not needed for the core demo)");
  return out;
}

function table(rows) {
  const w = Math.max(...rows.map(r => r.name.length));
  return rows.map(r => `${r.level.padEnd(4)}  ${r.name.padEnd(w)}  ${r.detail}`).join("\n");
}
const centralFailed = rows => rows.some(r => r.central && r.level === "FAIL");

module.exports = { runChecks, table, centralFailed, CENTRAL };
