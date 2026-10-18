"""The Care Circle: one agent per family member, deciding for its human.

A call for help used to go to Maya first, then (after two minutes of silence) to Raj. The circle works differently:
every member's agent hears the call and answers for its own person: "in a meeting until 3", "8 minutes away, can lift",
"has done the last three nights". The Relay then asks the right person first, and the rest stay undisturbed.

  * a member's agent learns where its human is from the human's own texts ("in a meeting till 3", "driving", "10 min away")
  * it never decides to contact anyone outside the care plan: it only answers who is best placed among the allowlist
  * if nobody answers, the care plan's own order is used, unchanged (choose(): fallback)
  * emergencies never wait for this: they follow the care plan's fixed order, as before

Pure logic: no network, no clock of its own (every function takes `now`).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

LIFT_NEEDS = {"repositioning", "bathroom"}          # needs a pair of capable hands
WEEK_S = 7 * 24 * 3600
NIGHT = range(22, 24), range(0, 6)                    # hours that count double


# ---------------------------------------------------------------------------------- reading a text
_TIME = re.compile(r"\b(?:until|till|til|'til|by|back (?:at|by|around)|free (?:at|after)|home (?:at|by|around)|up (?:at|by))\s+"
                   r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?", re.I)
_DUR = re.compile(r"\bfor\s+(?:about\s+|around\s+)?(?:(an?|half an)\s+hour|(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|m)\b)", re.I)
_ETA = re.compile(r"\b(\d{1,3})\s*(?:min|mins|minutes)\s*(?:away|out|from (?:you|him|dev|there|there))\b", re.I)
_NO_LIFT = re.compile(r"\b(?:can'?t|cannot|can not|unable to|shouldn'?t|not able to)\s+lift|hurt (?:my )?back|back (?:is |really )?(?:hurts?|sore|out)|sprain", re.I)
_LIFT = re.compile(r"\b(?:can|able to|ok to|fine to)\s+lift\b|back (?:is )?(?:fine|better|ok|okay|good)", re.I)

_QUESTION = re.compile(r"\?\s*$|^\s*(?:is|are|was|were|where|how|what|what's|whats|when|who|who's|does|do|did|will|show|tell me|status)\b", re.I)

_MODES = [   # first match wins
    ("asleep", re.compile(r"\b(sleeping|asleep|sleep|going to bed|heading to bed|napping|nap|in bed)\b", re.I), "asleep"),
    ("driving", re.compile(r"\b(driving|on the road|commuting|in the car|behind the wheel)\b", re.I), "driving"),
    ("away", re.compile(r"\b(out of town|traveling|travelling|on vacation|vacation|on a flight|flying|abroad|away for)\b", re.I), "away"),
    ("busy", re.compile(r"\b(meeting|in class|class|exam|interview|appointment|at work|working|on shift|shift|busy|can'?t talk|presenting|surgery)\b", re.I), None),
    ("busy", re.compile(r"\bout for\b", re.I), "busy"),
    ("free", re.compile(r"\b(free|available|at home|home|around|off work|done for the day|here)\b", re.I), "free"),
]
_LABELS = {"meeting": "in a meeting", "in class": "in class", "class": "in class", "exam": "in an exam", "interview": "in an interview",
           "appointment": "at an appointment", "at work": "at work", "working": "at work", "on shift": "on shift", "shift": "on shift",
           "busy": "busy", "presenting": "presenting", "surgery": "in surgery"}


def _clock(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def _next_at(now: datetime, hour: int, minute: int, ampm: str | None) -> datetime | None:
    if minute > 59 or hour > 24:
        return None
    ap = (ampm or "").lower().replace(".", "")
    if ap in ("am", "pm"):
        if hour > 12 or hour == 0:
            return None
        h = hour % 12 + (12 if ap == "pm" else 0)
        cands = [h]
    elif hour > 12:
        cands = [hour % 24]
    else:                                                    # "till 3": the next 3 o'clock, am or pm
        cands = [hour % 12, hour % 12 + 12]
    best = None
    for h in cands:
        t = now.replace(hour=h, minute=minute, second=0, microsecond=0)
        if t <= now:
            t += timedelta(days=1)
        best = t if best is None or t < best else best
    return best


def parse_status(text: str, now: datetime) -> dict | None:
    """What a family member's text says about where they are. None if it isn't a status at all.
    -> {mode?, until, label?, eta_min?, can_lift?, ack}"""
    t = (text or "").strip()
    if not t or len(t) > 280 or _QUESTION.search(t):
        return None                                   # empty, too long, or a question ("is Maya available?" asks; it doesn't tell)
    out: dict = {"until": None}
    mode, label, hit = None, None, None
    for name, rx, fixed in _MODES:
        m = rx.search(t)
        if m:
            mode, hit = name, m.group(1).lower() if m.groups() else None
            label = fixed or _LABELS.get(hit or "", "busy")
            break
    eta = _ETA.search(t)
    if eta:
        out["eta_min"] = int(eta.group(1))
        mode = mode or "free"
    if _NO_LIFT.search(t):
        out["can_lift"] = False
    elif _LIFT.search(t):
        out["can_lift"] = True
    until = None
    tm = _TIME.search(t)
    if tm:
        until = _next_at(now, int(tm.group(1)), int(tm.group(2) or 0), tm.group(3))
    else:
        d = _DUR.search(t)
        if d:
            if d.group(1):
                mins = 30 if d.group(1).lower().startswith("half") else 60
            else:
                n, unit = float(d.group(2)), d.group(3).lower()
                mins = n * (60 if unit.startswith("h") else 1)
            until = now + timedelta(minutes=mins)
    if mode is None and "can_lift" not in out and "eta_min" not in out:
        return None
    if mode == "asleep" and until is None:
        until = _next_at(now, 7, 0, "am")
    if mode in ("free",):
        until = None
    if mode:
        out["mode"], out["label"] = mode, label
    out["until"] = until
    out["ack"] = _ack(out, until)
    return out


def _ack(u: dict, until: datetime | None) -> str:
    bits = []
    mode, label = u.get("mode"), u.get("label")
    when = f" until {_clock(until)}" if until else ""
    if mode == "busy":
        bits.append(f"Got it: {label}{when}. I won't ask you for ordinary help until then; only for something urgent.")
    elif mode == "asleep":
        bits.append(f"Sleep well. I'll keep you out of ordinary requests{when} and only wake you for something urgent.")
    elif mode == "driving":
        bits.append("Drive safe. I won't ask you for anything while you're driving.")
    elif mode == "away":
        bits.append(f"Understood, you're away{when}. I'll route around you.")
    elif mode == "free":
        bits.append("Thanks, I'll count you as available" + (f" ({u['eta_min']} min away)." if u.get("eta_min") else "."))
    if u.get("can_lift") is False:
        bits.append("I'll skip you for anything that needs lifting.")
    elif u.get("can_lift") is True and not mode:
        bits.append("Noted: you can lift again.")
    if u.get("eta_min") and mode != "free":
        bits.append(f"Noted: {u['eta_min']} min away.")
    return " ".join(bits) or "Noted."


# ---------------------------------------------------------------------------------- the load
class Ledger:
    """Who has actually helped, this week. Nights count double: being woken is the real cost."""

    def __init__(self, entries: list | None = None):
        self.entries: list[list] = [list(e) for e in (entries or [])]

    @staticmethod
    def weight(ts: float) -> float:
        return 2.0 if datetime.fromtimestamp(ts).hour in (*range(22, 24), *range(0, 6)) else 1.0

    def add(self, ts: float, need: str) -> None:
        self.entries.append([ts, need])
        self.entries = self.entries[-200:]

    def load(self, now: float) -> float:
        return sum(self.weight(ts) for ts, _ in self.entries if 0 <= now - ts <= WEEK_S)


# ---------------------------------------------------------------------------------- one member's agent
@dataclass
class Presence:
    mode: str = "free"                 # free | busy | asleep | driving | away
    label: str = ""                    # "in a meeting"
    until: float | None = None
    eta_min: int | None = None
    can_lift: bool = True
    updated: float = 0.0


class CaregiverBrain:
    """Everything one member's agent knows and decides. The uAgent around it (swarm/agents.py) only moves messages."""

    def __init__(self, name: str, *, base_eta_min: int = 15, can_lift: bool = True, state_path: str | Path | None = None,
                 clock=time.time):
        self.name = name
        self.base_eta_min = base_eta_min
        self.clock = clock
        self.state_path = Path(state_path) if state_path else None
        self.presence = Presence(can_lift=can_lift)
        self.ledger = Ledger()
        self._load()

    # --- state
    def _load(self) -> None:
        if not self.state_path or not self.state_path.exists():
            return
        try:
            d = json.loads(self.state_path.read_text())
            self.presence = Presence(**{k: v for k, v in d.get("presence", {}).items() if k in Presence.__dataclass_fields__})
            self.ledger = Ledger(d.get("ledger"))
        except Exception:
            pass

    def _save(self) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"presence": asdict(self.presence), "ledger": self.ledger.entries}))
        tmp.replace(self.state_path)

    def _now(self) -> float:
        return self.clock()

    def current(self) -> Presence:
        p = self.presence
        if p.until is not None and p.until <= self._now() and p.mode != "free":
            self.presence = Presence(eta_min=p.eta_min, can_lift=p.can_lift, updated=self._now())
        return self.presence

    # --- what the human said
    def hear(self, text: str) -> str | None:
        u = parse_status(text, datetime.fromtimestamp(self._now()))
        if u is None:
            return None
        p = self.current()
        if "mode" in u:
            p.mode, p.label = u["mode"], u.get("label", "")
            p.until = u["until"].timestamp() if u["until"] else None
            if u["mode"] != "free":
                p.eta_min = None
        if "eta_min" in u:
            p.eta_min = u["eta_min"]
        if "can_lift" in u:
            p.can_lift = u["can_lift"]
        p.updated = self._now()
        self._save()
        return u["ack"]

    def record(self, status: str, need: str) -> None:
        """Only help that really happened counts toward the load."""
        if status == "acknowledged":
            self.ledger.add(self._now(), need or "general")
            self._save()

    # --- the bid
    def bid(self, call: dict) -> dict:
        p = self.current()
        need, urgency = call.get("need") or "general", call.get("urgency") or "normal"
        urgent = urgency in ("urgent", "emergency")
        needs_lift = need in LIFT_NEEDS
        load = self.ledger.load(self._now())
        until = f" until {_clock(datetime.fromtimestamp(p.until))}" if p.until else ""
        can, cost, eta, reason = True, 0.0, p.eta_min or self.base_eta_min, ""
        if p.mode == "free":
            reason = f"{eta} min away" if p.eta_min else f"about {eta} min away"
        elif p.mode == "busy":
            cost, eta = 0.8, (p.eta_min or self.base_eta_min) + 5
            can = urgent
            reason = f"{p.label or 'busy'}{until}" + (" (would step out for an urgent need)" if urgent else "")
        elif p.mode == "asleep":
            cost, eta = 1.0, (p.eta_min or self.base_eta_min) + 5
            can = urgent
            reason = f"asleep{until}" + (" (an urgent need does wake them)" if urgent else "")
        elif p.mode == "driving":
            cost, can, eta = 1.0, False, (p.eta_min or self.base_eta_min) + 10
            reason = "driving: not asked while on the road"
        else:                                                    # away
            cost, can, eta = 0.6, False, max(60, self.base_eta_min * 4)
            reason = f"away{until}"
        fit = 1.0
        if needs_lift and not p.can_lift:
            fit, can = 0.2, False
            reason = "can't lift right now" + (f" ({reason})" if reason and p.mode != "free" else "")
        return {"call_id": call.get("call_id", ""), "member": self.name, "can_help": bool(can), "eta_min": int(eta), "fit": fit,
                "load": round(load, 2), "interrupt_cost": cost, "reason": reason}

    def summary(self) -> dict:
        p = self.current()
        return {"member": self.name, "mode": p.mode, "label": p.label, "until": p.until, "eta_min": p.eta_min,
                "can_lift": p.can_lift, "load": round(self.ledger.load(self._now()), 2)}


# ---------------------------------------------------------------------------------- choosing
def _score(b: dict, urgent: bool) -> float:
    if urgent:
        return b["eta_min"] + 3 * b.get("interrupt_cost", 0)
    return b["eta_min"] + 2.5 * b.get("load", 0) + 15 * b.get("interrupt_cost", 0) + 30 * (1 - b.get("fit", 1))


def choose(bids: dict[str, dict], urgency: str, plan_order: list[str]) -> dict:
    """Order the care plan's people for this call. Only names in the plan can be chosen (the allowlist is not the circle's
    to widen). Available people first, best placed first; then people who never answered (their human may be free); then
    people who said they can't."""
    urgent = urgency in ("urgent", "emergency")
    idx = {n: i for i, n in enumerate(plan_order)}
    mine = {n: b for n, b in bids.items() if n in idx}
    avail = sorted([n for n, b in mine.items() if b["can_help"]], key=lambda n: (_score(mine[n], urgent), idx[n]))
    silent = [n for n in plan_order if n not in mine]
    cant = sorted([n for n, b in mine.items() if not b["can_help"]], key=lambda n: idx[n])
    order = avail + silent + cant
    why: list[str] = []
    if avail:
        w = avail[0]
        line = f"{w}: {mine[w]['reason']}"
        others = [n for n in avail[1:]]
        if not urgent and others:
            o = others[0]
            if mine[w]["load"] < mine[o]["load"] - 1:
                line += f"; has helped less this week ({o} has covered more)"
            elif mine[w]["eta_min"] < mine[o]["eta_min"]:
                line += f"; closer than {o}"
        why.append(line)
        why += [f"{n}: {mine[n]['reason']}" for n in others]
    why += [f"{n}: didn't answer, so the care plan's order applies" for n in silent]
    why += [f"{n}: {mine[n]['reason']}" for n in cant]
    return {"order": order, "why": why, "fallback": not mine, "winner": order[0] if order else None}
