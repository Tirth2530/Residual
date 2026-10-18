// Optional higher-quality voice for the person's own words: ElevenLabs text to speech.
// Key and voice come from the environment or app/.env (ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID), never code.
// Anything that goes wrong (no key, offline, slow) returns null and Residual speaks with the system voice.
const fs = require("fs");
const path = require("path");

function readEnv(appDir, name) {
  if (process.env[name]) return process.env[name].trim();
  try {
    const env = fs.readFileSync(path.join(appDir, ".env"), "utf8");
    const m = env.match(new RegExp(`^\\s*${name}\\s*=\\s*"?([^"\\n]+)"?`, "m"));
    return m ? m[1].trim() : null;
  } catch (e) { return null; }
}

class ElevenTTS {
  constructor({ apiKey, voiceId, model = "eleven_flash_v2_5", fetchFn = globalThis.fetch, timeoutMs = 5000 } = {}) {
    this.key = apiKey || null;
    this.voice = voiceId || "21m00Tcm4TlvDq8ikWAM";
    this.model = model; this.fetch = fetchFn; this.timeoutMs = timeoutMs;
    this.cache = new Map();
    this.failures = 0; this.lastError = null;
  }
  get ready() { return !!this.key; }
  status() { return { ready: this.ready, voice: this.voice, failures: this.failures, lastError: this.lastError }; }
  // -> Buffer (mp3) or null
  async speak(text) {
    text = String(text || "").trim().slice(0, 400);
    if (!this.ready || !text) return null;
    const ck = this.voice + "|" + text;                        // audio is remembered per voice, so changing voice never replays the old one
    if (this.cache.has(ck)) return this.cache.get(ck);
    const ctl = new AbortController(), to = setTimeout(() => ctl.abort(), this.timeoutMs);
    try {
      const r = await this.fetch(`https://api.elevenlabs.io/v1/text-to-speech/${encodeURIComponent(this.voice)}?output_format=mp3_44100_64`, {
        method: "POST", signal: ctl.signal,
        headers: { "xi-api-key": this.key, "Content-Type": "application/json", Accept: "audio/mpeg" },
        body: JSON.stringify({ text, model_id: this.model })
      });
      if (!r.ok) throw new Error(`ElevenLabs ${r.status}`);
      const buf = Buffer.from(await r.arrayBuffer());
      if (this.cache.size > 200) this.cache.delete(this.cache.keys().next().value);
      this.cache.set(ck, buf);
      this.lastError = null;
      return buf;
    } catch (e) {
      this.failures++; this.lastError = String(e.message || e);
      return null;
    } finally { clearTimeout(to); }
  }
}
module.exports = { ElevenTTS, readEnv };
