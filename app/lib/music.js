// Pause and play the music from anywhere (Home included), judged by what the player does afterwards.
//
// The click on the player bar's own button is the normal way. If the track is still playing a moment later, the media element
// is told directly. If that doesn't work either, it says so rather than claiming "Paused".

const real = ms => new Promise(r => setTimeout(r, ms));

/**
 * call: (method, ...args) -> the answer from the music page's helper (musicState, musicToggle), or null
 * want: "toggle" | "pause" | "play"
 */
async function setMusic({ call, want = "toggle", sleep = real, settleMs = 350 }) {
  const s0 = await call("musicState");
  if (!s0 || !s0.has) return { done: "No music is open", none: true };
  const target = want === "pause" ? false : want === "play" ? true : !s0.playing;
  if (s0.playing === target) return { done: target ? "Playing" : "Paused", playing: target };
  let s = s0;
  for (const how of ["button", "media"]) {
    await call("musicToggle", how);
    await sleep(settleMs);
    s = (await call("musicState")) || s;
    if (s.playing === target) return { done: target ? "Playing" : "Paused", playing: target, title: s.title };
  }
  return { done: target ? "Couldn't start the music" : "Couldn't pause the music", failed: true, playing: !!s.playing, title: s.title };
}

function musicLabel(s) {
  if (!s || !s.has) return null;
  const t = s.title ? ` · ${String(s.title).slice(0, 40)}` : "";
  return (s.playing ? "Pause music" : "Play music") + t;
}

module.exports = { setMusic, musicLabel };
