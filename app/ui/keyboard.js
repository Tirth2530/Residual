// Scanning keyboard for one, two or three movements.
// Row-column scanning: Next moves between rows, Select opens a row, Next moves between keys,
// Select types and jumps back to the rows. Letters are ordered by how often they're used
// (E T A O I N ...), so common letters are fewer moves away.
// The top row predicts: finishes the current word from a word list and the person's own past
// messages, and (when an AI model is on) suggests the next few words.

(function () {
  const ROWS = [
    { id: "pred", keys: [] },
    { id: "ctl", keys: ["␣ Space", "⌫ Delete", "✓ Done", "✕ Cancel"] },
    { id: "l1", keys: ["E", "T", "A", "O", "I", "N"] },
    { id: "l2", keys: ["S", "H", "R", "D", "L", "U"] },
    { id: "l3", keys: ["C", "M", "F", "W", "Y", "P"] },
    { id: "l4", keys: ["G", "B", "V", "K", "X", "J", "Q", "Z"] },
    { id: "l5", keys: [".", ",", "?", "!", "'", "1", "2", "3"] }
  ];

  class Keyboard {
    constructor({ title, initial = "", history = [], onDone, onCancel, continueText }) {
      Object.assign(this, { title, onDone, onCancel, continueText, history });
      this.text = initial; this.level = "rows"; this.row = 2; this.col = 0;
      this.aiNext = []; this._t = null;
      this.refreshPredictions();
    }

    words() {
      const m = this.text.match(/([A-Za-z']*)$/), part = (m ? m[1] : "").toLowerCase();
      const out = [];
      if (part) {
        for (const h of this.history) for (const w of h.split(/\s+/)) if (w.toLowerCase().startsWith(part) && w.length > part.length) out.push(w.toLowerCase());
        for (const w of window.WORDS || []) if (w.startsWith(part) && w !== part) out.push(w);
      } else {
        // empty: whole messages they've sent before, then the AI's next words
        if (!this.text.trim()) out.push(...this.history.slice(0, 3));
        out.push(...this.aiNext);
        if (!this.text.trim()) out.push("I need", "Can you", "Thank you", "I love you");
      }
      return [...new Set(out)].slice(0, 4);
    }
    refreshPredictions() {
      ROWS[0].keys = this.words();
      clearTimeout(this._t);
      if (this.continueText && /\s$/.test(this.text) && this.text.trim()) {
        this._t = setTimeout(async () => {
          const next = await this.continueText(this.text.trim()).catch(() => []);
          if (next && next.length) { this.aiNext = next; ROWS[0].keys = this.words(); this.paint && this.paint(); }
        }, 500);
      } else this.aiNext = [];
    }
    rows() { return ROWS.filter(r => r.keys.length); }

    // ---- input: returns nothing; calls onDone / onCancel when finished
    act(a) {
      const rows = this.rows();
      const r = rows[Math.min(this.rowIndex(), rows.length - 1)];
      if (a === "next") {
        if (this.level === "rows") this.setRow((this.rowIndex() + 1) % rows.length);
        else { this.col++; if (this.col >= r.keys.length) { this.level = "rows"; } }
      } else if (a === "select") {
        if (this.level === "rows") { this.level = "keys"; this.col = 0; }
        else { this.press(r.id, r.keys[this.col]); }
      } else if (a === "back") {
        if (this.level === "keys") this.level = "rows";
        else return this.onCancel();
      }
      this.paint && this.paint();
    }
    rowIndex() { const rows = this.rows(); const i = rows.findIndex(r => r === ROWS[this.row]); return i < 0 ? 0 : i; }
    setRow(i) { const rows = this.rows(); this.row = ROWS.indexOf(rows[i]); }

    press(rowId, key) {
      this.level = "rows";
      if (rowId === "pred") {
        const m = this.text.match(/([A-Za-z']*)$/);
        this.text = this.text.slice(0, this.text.length - (m ? m[1].length : 0)) + key + " ";
        this.setRow(this.rows().findIndex(r => r.id === "l1"));
      } else if (rowId === "ctl") {
        if (key.startsWith("␣")) this.text += " ";
        else if (key.startsWith("⌫")) this.text = this.text.slice(0, -1);
        else if (key.startsWith("✓")) return this.onDone(this.text.trim());
        else if (key.startsWith("✕")) return this.onCancel();
      } else {
        const ch = /[A-Z]/.test(key) ? (this.text === "" || /[.?!]\s$/.test(this.text) ? key : key.toLowerCase()) : key;
        this.text += ch;
        // after a letter, predictions are the fastest next step
        this.setRow(0);
      }
      this.refreshPredictions();
      if (this.rows()[this.rowIndex()] !== ROWS[this.row]) this.setRow(0);
    }

    html() {
      const rows = this.rows(), ri = this.rowIndex();
      return `<div class="kbwrap"><p class="kicker">${esc(this.title)}</p>
        <div class="kbtext">${esc(this.text) || '<span class="ph">Start with a letter or a suggestion</span>'}<span class="caret"></span></div>
        <div class="kbrows">${rows.map((r, i) => `<div class="kbrow ${i === ri ? (this.level === "rows" ? "on" : "open") : ""} ${r.id}">
          ${r.keys.map((k, j) => `<span class="key ${i === ri && this.level === "keys" && j === this.col ? "on" : ""} ${r.id === "pred" ? "pred" : ""}">${esc(k)}</span>`).join("")}</div>`).join("")}</div>
        <p class="kbhelp">${this.level === "rows" ? "Next moves between rows. Select opens a row. Back cancels." : "Next moves between keys. Select types. Back returns to rows."}</p></div>`;
    }
  }
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  window.Keyboard = Keyboard;
})();
