"""Safety policy. Deterministic, and the only thing that decides who can be contacted, in what
order, with what words for help requests, and who may cancel.

A language model may help *parse* an ASI request into an Intent. It never decides:
  * contacts: only names in the allowlist (contacts.json) can be messaged; unknown names are refused
  * emergency order: fixed by the care plan, whatever the request says
  * cancelling an emergency: only from Residual's own screen, with explicit confirmation
  * help wording: fixed templates (the person's own words are sent verbatim for send_message)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from models import NEEDS, Intent, Task

MAX_MESSAGE = 500


class PolicyError(Exception):
    pass


@dataclass
class Contact:
    name: str
    phone: str
    relation: str = ""
    roles: list[str] = field(default_factory=lambda: ["caregiver"])
    platform: str | None = None
    pronoun: str | None = None               # "she" | "he" | "they": only used to say who answered


@dataclass
class Policy:
    person: str
    contacts: list[Contact]
    emergency_order: list[str]
    caregiver_order: list[str]
    escalate_after_s: float = 120.0          # normal help, production
    emergency_after_s: float = 60.0          # emergency, production
    demo: bool = False                       # hackathon demo: shortened, and labelled as such
    demo_after_s: float = 25.0
    max_emergency_rounds: int = 5

    @classmethod
    def load(cls, path: str | Path, demo: bool = False) -> "Policy":
        d = json.loads(Path(path).read_text())
        contacts = [Contact(**c) for c in d["contacts"]]
        names = [c.name for c in contacts]
        return cls(person=d.get("person", "the person"), contacts=contacts,
                   emergency_order=d.get("emergency_order", names),
                   caregiver_order=d.get("caregiver_order", names),
                   escalate_after_s=float(d.get("escalate_after_s", 120)),
                   emergency_after_s=float(d.get("emergency_after_s", 60)),
                   demo=demo, demo_after_s=float(d.get("demo_after_s", 25)))

    # ------------------------------------------------------------- contacts
    def resolve(self, name: str | None) -> Contact:
        if not name:
            raise PolicyError("no contact given")
        key = name.strip().lower()
        for c in self.contacts:
            if c.name.lower() == key:
                return c
        raise PolicyError(f"'{name}' isn't one of {self.person}'s contacts, so I won't message them")

    def by_handle(self, handle: str) -> Contact | None:
        h = _digits(handle)
        for c in self.contacts:
            if h and _digits(c.phone) == h:
                return c
        return None

    # ------------------------------------------------------------- planning
    def plan(self, intent: Intent) -> Task:
        """Validate an intent and turn it into a task. Raises PolicyError when not allowed."""
        it = intent.intent
        if it == "emergency" or intent.urgency == "emergency":
            order = [self.resolve(n).name for n in self.emergency_order]   # fixed, ignores the request
            need = intent.need if intent.need in NEEDS else "general"
            if intent.source == "asi":      # it wasn't their own signal: say where it came from
                text = (f"URGENT: {self.person} {NEEDS[need]} (requested through ASI:One). "
                        f"Please reply 'on my way' if you're going.")
            else:
                text = (f"URGENT: {self.person} {NEEDS[need]} and used their emergency signal. "
                        f"Please reply 'on my way' if you're going.")
            after = self.demo_after_s if self.demo else self.emergency_after_s
            t = Task(intent=intent, kind="emergency", order=order, text=text, escalate_after_s=after,
                     demo_timing=self.demo)
            t.plan = [f"Contact {n}" for n in order[:1]] + ["Wait for 'on my way'"] + \
                     [f"No answer in {int(after)} s: contact {n}" for n in order[1:]] + \
                     ["No answer from anyone: alert everyone again until someone answers"]
            return t
        if it == "request_caregiver_help":
            first = self.resolve(intent.preferred_contact).name if intent.preferred_contact else None
            asked = [self.resolve(n).name for n in intent.contacts]        # unknown names refused here
            order = []
            for n in ([first] if first else []) + asked + self.caregiver_order:
                if n and n not in order:
                    order.append(n)
            need = intent.need if intent.need in NEEDS else "general"
            text = f"{self.person} {NEEDS[need]}. Can you come? Reply 'on my way' or 'can't'."
            after = self.demo_after_s if self.demo else self.escalate_after_s
            t = Task(intent=intent, kind="help", order=order, text=text, escalate_after_s=after,
                     demo_timing=self.demo)
            t.plan = self.help_steps(order, after)
            return t
        if it == "send_message":
            c = self.resolve(intent.contact)
            msg = (intent.message or "").strip()
            if not msg:
                raise PolicyError("empty message")
            if len(msg) > MAX_MESSAGE:
                raise PolicyError("message too long")
            # a message that came through ASI:One is never presented as the person's own words
            label = f"Via ASI:One (not written by {self.person})" if intent.source == "asi" else f"From {self.person}"
            t = Task(intent=intent, kind="message", order=[c.name], text=f"{label}: {msg}",
                     escalate_after_s=0)
            t.plan = [f"Send to {c.name}"] + (["Wait for a reply"] if intent.requires_confirmation else [])
            return t
        raise PolicyError(f"'{it}' isn't something I can plan")

    @staticmethod
    def help_steps(order: list[str], after: float) -> list[str]:
        return [f"Contact {order[0]}", "Wait for a reply"] + \
               [f"If no reply in {int(after)} s or they can't: contact {n}" for n in order[1:]] + ["Report the outcome"]

    def may_cancel(self, task: Task, intent: Intent) -> tuple[bool, str]:
        if task.terminal:
            return False, "that request has already finished"
        if task.kind == "emergency":
            if intent.source == "residual_ui" and intent.confirmed:
                return True, ""
            return False, ("an emergency can only be cancelled from Residual's own screen, "
                           "with an explicit confirmation")
        if intent.source == "residual_ui" or intent.source == task.intent.source:
            return True, ""
        return False, "only the person, or whoever started this request, can cancel it"


# ------------------------------------------------------------- reply reading
ACK = re.compile(r"\b(on my way|omw|coming|be there|i'?ll come|i'?m here|here now|ok(ay)?|sure|yes|yep|will do|"
                 r"on it|heading over|be right there|got it)\b|👍|✅", re.I)
DECLINE = re.compile(r"\b(can'?t|cannot|can not|no|not now|busy|unable|won'?t|not available|sorry)\b", re.I)


def classify_reply(text: str) -> str:
    """'ack' | 'decline' | 'other'. Deterministic on purpose: an ambiguous reply never stops
    escalation. 'can't come' is a decline even though it contains 'come'."""
    t = (text or "").strip()
    if not t:
        return "other"
    if DECLINE.search(re.sub(r"\bno (problem|worries)\b", "", t, flags=re.I)):
        return "decline"
    if ACK.search(t):
        return "ack"
    return "other"


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")[-10:]
