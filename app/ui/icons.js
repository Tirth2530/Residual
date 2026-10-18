// One line-icon set, drawn for this app (24px grid, 1.8 stroke).
(function () {
  const P = {
    chat: '<path d="M5 18l-1 3 4-2h9a3 3 0 0 0 3-3V8a3 3 0 0 0-3-3H7a3 3 0 0 0-3 3v7a3 3 0 0 0 1 2.2"/><path d="M8.5 11.5h.01M12 11.5h.01M15.5 11.5h.01"/>',
    play: '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="M10.5 9.5v5l4-2.5z"/>',
    music: '<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>',
    news: '<path d="M5 5h11v14H6a1 1 0 0 1-1-1z"/><path d="M16 9h3v9a1 1 0 0 1-1 1h-2"/><path d="M8 9h5M8 12h5M8 15h3"/>',
    spark: '<path d="M12 3c.6 4.2 2.8 6.4 7 7-4.2.6-6.4 2.8-7 7-.6-4.2-2.8-6.4-7-7 4.2-.6 6.4-2.8 7-7z"/><path d="M19 17.5c.2 1.3.9 2 2.2 2.2-1.3.2-2 .9-2.2 2.2-.2-1.3-.9-2-2.2-2.2 1.3-.2 2-.9 2.2-2.2z"/>',
    voice: '<path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2"/>',
    routine: '<path d="M4 7h11M4 12h7M4 17h9"/><path d="M17 12l3 2.5-3 2.5z"/>',
    help: '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/>',
    moon: '<path d="M19 14.5A7.5 7.5 0 0 1 9.5 5a7.5 7.5 0 1 0 9.5 9.5z"/>',
    reels: '<rect x="4" y="4" width="16" height="16" rx="4"/><path d="M4 9h16M9 4l3 5M14 4l3 5"/><path d="M10.5 12.5v4l3.5-2z"/>',
    search: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
    type: '<rect x="3" y="6" width="18" height="12" rx="2.5"/><path d="M7 10h.01M10 10h.01M13 10h.01M16 10h.01M8 14h8"/>',
    back: '<path d="M15 6l-6 6 6 6"/>',
    open: '<path d="M14 4h6v6M20 4l-8 8"/><path d="M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/>',
    again: '<path d="M4 12a8 8 0 1 0 2.5-5.8"/><path d="M4 4v4h4"/>',
    next: '<path d="M5 12h12M13 7l5 5-5 5"/>',
    eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
    hand: '<path d="M8 13V6.5a1.5 1.5 0 0 1 3 0V12"/><path d="M11 11V5a1.5 1.5 0 0 1 3 0v6"/><path d="M14 11V6.5a1.5 1.5 0 0 1 3 0V14a6 6 0 0 1-6 6 6 6 0 0 1-5.3-3.2L4 13.5a1.5 1.5 0 0 1 2.6-1.5L8 14"/>',
    brow: '<path d="M4 9c2.5-2 5-2 7 0M13 9c2-2 4.5-2 7 0"/><circle cx="7.5" cy="14" r="1.6"/><circle cx="16.5" cy="14" r="1.6"/>',
    blink: '<path d="M3 12c3 3.5 6 5 9 5s6-1.5 9-5"/><path d="M6 15.5l-1.5 2M12 17v2.5M18 15.5l1.5 2"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
    cloud: '<path d="M7 18a4 4 0 0 1-.5-8 5.5 5.5 0 0 1 10.6 1.5A3.3 3.3 0 0 1 17 18z"/>',
    rain: '<path d="M7 15a4 4 0 0 1-.5-8 5.5 5.5 0 0 1 10.6 1.5A3.3 3.3 0 0 1 17 15z"/><path d="M8 18l-1 2.5M12 18l-1 2.5M16 18l-1 2.5"/>',
    food: '<path d="M4 11h16a8 8 0 0 1-16 0z"/><path d="M8 7c0-1.5 1-1.5 1-3M12 7c0-1.5 1-1.5 1-3M16 7c0-1.5 1-1.5 1-3"/>',
    tv: '<rect x="3" y="5" width="18" height="12" rx="2.5"/><path d="M9 20h6"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    close: '<path d="M6 6l12 12M18 6L6 18"/>',
    person: '<circle cx="12" cy="8.5" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/>'
  };
  window.ICON = (name, cls = "i") => `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${P[name] || P.play}</svg>`;
})();
