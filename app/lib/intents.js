// "Suggested for now": which three things is this person most likely to want right now?
//
// Every selection is physically expensive, so putting the right thing first saves real effort.
// This is a small, explainable scoring model, not a black box: each candidate gets
//
//   score = 0.45 * habit      how often they chose it around this time of day (last 30 days,
//                             Gaussian weight on clock distance, sigma 75 min)
//         + 0.20 * recency    exp(-hours since last use / 36)
//         + 0.25 * context    signals right now: unread messages, morning briefing, night sounds,
//                             something left half-watched
//         + 0.10 * prior      a small hand-set default so a new install isn't empty
//
// One rule sits on top of the score: an unread message from someone is "urgent" and always gets
// one of the three slots, even when habit says otherwise. People come before playlists.
//
// The Lab screen shows the parts for each suggestion, so a reviewer can see why it ranked.

const DAY = 24 * 60;
const W = { habit: 0.45, recency: 0.2, context: 0.25, prior: 0.1 };

function clockDist(a, b) { const d = Math.abs(a - b) % DAY; return Math.min(d, DAY - d); }

function rank(candidates, usage, { now = Date.now(), signals = {} } = {}) {
  const d = new Date(now), minute = d.getHours() * 60 + d.getMinutes(), hour = d.getHours();
  const since = now - 30 * 864e5;
  const recent = usage.filter(u => u.t >= since);
  const habitRaw = {};
  for (const u of recent) {
    const ud = new Date(u.t), um = ud.getHours() * 60 + ud.getMinutes();
    const w = Math.exp(-0.5 * (clockDist(um, minute) / 75) ** 2);
    habitRaw[u.id] = (habitRaw[u.id] || 0) + w;
  }
  const maxHabit = Math.max(1, ...Object.values(habitRaw));
  const lastUse = {};
  for (const u of recent) lastUse[u.id] = Math.max(lastUse[u.id] || 0, u.t);

  const out = candidates.map(c => {
    const habit = (habitRaw[c.id] || 0) / maxHabit;
    const recency = lastUse[c.id] ? Math.exp(-(now - lastUse[c.id]) / 36e5 / 36) : 0;
    let context = 0, why = [];
    let urgent = false;
    if (c.kind === "chat" && signals.unread && (!signals.unreadFrom || c.person === signals.unreadFrom)) { context = 1; urgent = true; why.push(`${signals.unread} unread`); }
    if (c.kind === "briefing" && hour >= 6 && hour < 11 && !signals.briefedToday) { context = 0.9; why.push("morning, not briefed yet"); }
    if (c.kind === "continue") { context = 0.6; why.push("left half-watched"); }
    if (c.tags?.includes("night") && (hour >= 21 || hour < 5)) { context = Math.max(context, 0.8); why.push("late evening"); }
    if (c.tags?.includes("evening") && hour >= 17 && hour < 22) { context = Math.max(context, 0.5); why.push("evening"); }
    if (c.tags?.includes("meal") && [8, 12, 13, 19].includes(hour)) { context = Math.max(context, 0.4); why.push("around a mealtime"); }
    const prior = c.prior ?? 0.3;
    const score = W.habit * habit + W.recency * recency + W.context * context + W.prior * prior;
    if (habit > 0.3) why.unshift("usually around now");
    return { ...c, urgent, score: +score.toFixed(3), parts: { habit: +habit.toFixed(2), recency: +recency.toFixed(2), context: +context.toFixed(2), prior }, why };
  });
  out.sort((a, b) => b.score - a.score);
  return out;
}

// Top n, with at most one urgent item promoted into the last slot if it didn't make it.
function top(ranked, n = 3) {
  const out = ranked.slice(0, n);
  if (!out.some(r => r.urgent)) {
    const u = ranked.find(r => r.urgent);
    if (u) out[n - 1] = u;
  }
  return out;
}

module.exports = { rank, top, WEIGHTS: W };
