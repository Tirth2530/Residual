// One voice, with priorities. Features used to call speechSynthesis.cancel() on each other, so
// two things speaking would cut each other off forever, and a repeated Select could restart a
// greeting again and again.
//
//   P0 emergency / Lifeline       interrupts anything
//   P1 control migration          interrupts P2 to P4
//   P2 the person's own words     (needs board, typed speech, messages): queued, first in line
//   P3 requested content          (news, answers, briefing): queued
//   P4 ambient status             only if nothing else is speaking or waiting; never queued
//
// Lower number = more important. Equal or less important speech waits its turn; the same text
// already speaking or waiting is not added twice.
(function (root) {
  class SpeechManager {
    constructor({ speak, cancel, onState } = {}) {
      this._speak = speak;          // (text) => Promise resolving when finished
      this._cancel = cancel;        // () => void
      this.onState = onState || (() => {});
      this.current = null;          // {text, priority, resolve}
      this.queue = [];
      this.spoken = [];             // history, for tests and the Lab
    }
    say(text, { priority = 3, key } = {}) {
      text = String(text || "").trim();
      if (!text) return Promise.resolve("empty");
      const k = key || text;
      if (this.current && this.current.key === k) return this.current.promise;
      const queued = this.queue.find(q => q.key === k);
      if (queued) return queued.promise;
      if (priority >= 4 && (this.current || this.queue.length)) return Promise.resolve("dropped");
      const item = { text, priority, key: k };
      item.promise = new Promise(res => { item.resolve = res; });
      if (this.current && priority <= 1 && priority < this.current.priority) {   // only P0 / P1 interrupt
        const cut = this.current;
        this.current = null;
        try { this._cancel && this._cancel(); } catch (e) {}
        cut.resolve("interrupted");
        // anything less important than the new item that was waiting stays queued behind it
      }
      this.queue.push(item);
      this.queue.sort((a, b) => a.priority - b.priority);
      this._pump();
      return item.promise;
    }
    async _pump() {
      if (this.current || !this.queue.length) return;
      const item = this.current = this.queue.shift();
      this.onState({ speaking: true, text: item.text, priority: item.priority });
      this.spoken.push({ text: item.text, priority: item.priority });
      try { await this._speak(item.text, item); } catch (e) {}
      if (this.current !== item) return;          // interrupted: the interrupter pumps
      this.current = null;
      item.resolve("done");
      this.onState({ speaking: false });
      this._pump();
    }
    // Stop what's speaking and waiting at or below a priority (navigation stops news, not an alarm)
    stop(minPriority = 2) {
      this.queue = this.queue.filter(q => { if (q.priority >= minPriority) { q.resolve("stopped"); return false; } return true; });
      if (this.current && this.current.priority >= minPriority) {
        const c = this.current;
        this.current = null;
        try { this._cancel && this._cancel(); } catch (e) {}
        c.resolve("stopped");
        this.onState({ speaking: false });
        this._pump();
      }
    }
    get speaking() { return !!this.current; }
  }

  // Single flight: a long action (a briefing, a routine, a message, a call for help) can't be
  // started again while it's already running, however many Selects arrive.
  class SingleFlight {
    constructor() { this.busy = new Map(); }
    run(key, fn) {
      if (this.busy.has(key)) return null;
      const p = Promise.resolve().then(fn).finally(() => this.busy.delete(key));
      this.busy.set(key, p);
      return p;
    }
    has(key) { return this.busy.has(key); }
  }

  // true the first time only (the spoken welcome: once per app launch)
  function once() { let done = false; return () => { if (done) return false; done = true; return true; }; }

  const api = { SpeechManager, SingleFlight, once };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else Object.assign(root, api);
})(typeof window !== "undefined" ? window : globalThis);
