"""Structured intents and tasks.

Residual Home never sends a prompt string when it already knows what the person chose: it sends
an Intent. ASI conversations are parsed into the same Intent. Everything after that is shared.
"""

from __future__ import annotations

import time
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field

IntentType = Literal["request_caregiver_help", "emergency", "send_message", "cancel_task", "get_status"]
Source = Literal["residual_ui", "asi", "test"]

NEEDS = {
    "repositioning": "needs help repositioning",
    "pain": "is in pain",
    "suction": "needs suction",
    "breathing": "is having trouble breathing",
    "thirsty": "is thirsty",
    "bathroom": "needs help with the bathroom",
    "general": "needs help",
}


class Intent(BaseModel):
    intent: IntentType
    need: Optional[str] = None                 # one of NEEDS for help / emergency
    urgency: Literal["normal", "urgent", "emergency"] = "normal"
    preferred_contact: Optional[str] = None    # first person to contact
    contacts: list[str] = Field(default_factory=list)   # requested order (help only; validated)
    contact: Optional[str] = None              # send_message recipient
    message: Optional[str] = None              # send_message text (the person's own words)
    requires_confirmation: bool = False        # ask the recipient to confirm ("on my way")
    task_id: Optional[str] = None              # cancel_task / get_status
    confirmed: bool = False                    # explicit confirmation for cancel
    source: Source = "residual_ui"


class Attempt(BaseModel):
    contact: str
    tool: str
    idempotency_key: str
    started: float
    ms: Optional[int] = None
    ok: Optional[bool] = None
    result: dict = Field(default_factory=dict)
    error: Optional[str] = None
    simulated: bool = False


class TaskEvent(BaseModel):
    t: float
    kind: str              # planned | tool | reply | escalate | outcome | error | cancel | note
    text: str
    data: dict = Field(default_factory=dict)


class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created: float = Field(default_factory=time.time)
    intent: Intent
    kind: str                              # help | emergency | message
    status: str = "planned"                # planned | contacting | waiting | acknowledged | declined_all |
                                           # exhausted | sent | failed | cancelled
    order: list[str] = Field(default_factory=list)      # contacts in escalation order
    current: int = 0                       # index into order
    rounds: int = 0                        # emergency: how many times everyone was alerted
    text: str = ""                         # message sent to contacts
    plan: list[str] = Field(default_factory=list)
    attempts: list[Attempt] = Field(default_factory=list)
    replies: list[dict] = Field(default_factory=list)   # {contact, text, verdict, t, message_id}
    events: list[TaskEvent] = Field(default_factory=list)
    outcome: Optional[dict] = None
    escalate_after_s: float = 60.0
    demo_timing: bool = False
    session: Optional[str] = None          # ASI sender address, for follow-up messages
    swarm: dict = Field(default_factory=dict)   # how the care circle ordered the contacts (bids, reasons), if it was asked

    @property
    def terminal(self) -> bool:
        return self.status in ("acknowledged", "declined_all", "exhausted", "sent", "failed", "cancelled")

    def log(self, _kind: str, _text: str, **data) -> TaskEvent:
        e = TaskEvent(t=time.time(), kind=_kind, text=_text, data=data)
        self.events.append(e)
        return e

    def summary(self) -> dict:
        return {"task_id": self.id, "kind": self.kind, "status": self.status, "order": self.order,
                "current": self.order[self.current] if self.order and self.current < len(self.order) else None,
                "text": self.text, "plan": self.plan, "outcome": self.outcome,
                "escalate_after_s": self.escalate_after_s, "demo_timing": self.demo_timing,
                "replies": self.replies, "attempts": [a.model_dump() for a in self.attempts],
                "events": [e.model_dump() for e in self.events][-40:], "created": self.created,
                "source": self.intent.source, "swarm": self.swarm}
