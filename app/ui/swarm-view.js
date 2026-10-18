// What the screen shows about the agent team. Pure, so it can be tested.
(function (root) {
  // The care circle's answer to a call for help, as rows: who was asked first, who was left alone and why.
  function circleRows(swarm, order) {
    if (!swarm) return [];
    const bids = Object.fromEntries((swarm.bids || []).map(b => [b.member, b]));
    const names = order && order.length ? order : Object.keys(bids);
    return names.map((name, i) => {
      const b = bids[name];
      let tag;
      if (swarm.mode === "fallback") tag = "care plan order";
      else if (swarm.mode === "advice") tag = i === 0 ? "you asked for" : "next";
      else tag = i === 0 && swarm.winner === name ? "asked first" : b && !b.can_help ? "left alone" : b ? "next" : "didn't answer";
      const via = (swarm.transport || {})[name] === "agentverse" ? "agent on Agentverse" : (swarm.transport || {})[name] ? "local agent" : "";
      return { name, tag, reason: b ? b.reason : swarm.mode === "fallback" ? "" : "didn't answer", eta: b ? b.eta_min : null, canHelp: b ? b.can_help : null, via };
    });
  }
  // The first errand that is waiting for the person's yes and hasn't been put to them yet.
  function errandNeeds(list, seen) {
    return (list || []).find(e => e.status === "awaiting_consent" && !seen.has(e.id)) || null;
  }
  // The live view of a browser is only ever opened if it really is Browser Use's.
  function safeLiveUrl(url) {
    try {
      const u = new URL(url);
      return u.protocol === "https:" && (u.hostname === "browser-use.com" || u.hostname.endsWith(".browser-use.com")) ? url : null;
    } catch (e) { return null; }
  }
  const api = { circleRows, errandNeeds, safeLiveUrl };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else Object.assign(root, { SwarmView: api });
})(typeof window !== "undefined" ? window : globalThis);
