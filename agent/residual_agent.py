"""Residual Relay: an Agentverse / ASI agent that turns low-bandwidth intent into a completed
real-world communication or care outcome, through Photon.

Two ways in, one orchestration:

    ASI:One chat  --(Agent Chat Protocol, via Agentverse mailbox)-->  handle_chat()  --+
                                                                                     +--> Orchestrator.handle_intent()
    Residual Home --(POST http://127.0.0.1:8100/intent, structured JSON)--> rest  ----+        |
                                                                                         Photon.send_message
    Photon webhook --(POST /photon/webhook)--> Orchestrator.on_inbound()  <--- a carer's reply

Run:  python residual_agent.py        (see README.md for .env, contacts.json, Photon, Agentverse)
"""

from __future__ import annotations

import asyncio
import re
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from aiohttp import web

from models import Intent, Task
from orchestrator import Orchestrator
from parser import parse_with_asi1
from policy import Policy, PolicyError
from security import (ConfigError, RateLimiter, check_webhook_config, ensure_token, host_ok, require_loopback,
                      require_seed, token_ok)
from swarm.agents import publish_manifests
from swarm.team import approver_picker, brains_for, embedded_circle, member_config
from tools.photon import PhotonClient, PhotonError, SimulatedPhoton, verify_signature

HERE = Path(__file__).resolve().parent
log = logging.getLogger("residual.agent")

DESCRIPTION = ("Residual Relay turns low-bandwidth user intent into real-world communication for people "
               "with severe motor impairment. It can contact caregivers through Photon, track replies, "
               "escalate unanswered requests, and report the outcome.")
KEYWORDS = ["accessibility", "ALS", "assistive technology", "AAC", "limited mobility", "caregiver communication",
            "care coordination", "emergency escalation", "messaging agent", "communication agent"]


def _env_path() -> Path:
    """agent/.env, unless RESIDUAL_ENV_FILE says otherwise (the tests point it at nothing, so a test run can never pick up
    real keys and can never leak them from one test into the next)."""
    return Path(os.environ.get("RESIDUAL_ENV_FILE") or HERE / ".env")


def _env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path())
    except ImportError:
        p = _env_path()
        if p.exists():
            for line in p.read_text().splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


def build_photon(state: Path):
    mode = os.environ.get("PHOTON_MODE", "auto").lower()
    platform = os.environ.get("PHOTON_PLATFORM", "imessage")
    if mode in ("auto", "live"):
        try:
            return PhotonClient(os.environ.get("PHOTON_PROJECT_ID", ""), os.environ.get("PHOTON_API_KEY", ""),
                                os.environ.get("PHOTON_AGENT_USER_ID", ""), platform=platform,
                                base=os.environ.get("PHOTON_BASE", "https://api.photon.codes/v1"),
                                state_path=state / "photon_map.json")
        except PhotonError as e:
            if mode == "live":
                raise
            log.warning("Photon not configured (%s): running SIMULATED. Nothing will reach a phone.", e)
    return SimulatedPhoton(platform=platform, state_path=state / "photon_map.json")


ACTUATING = ("emergency", "request_caregiver_help", "send_message")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _clean(s: str, n: int = 140) -> str:
    return _CTRL.sub(" ", str(s or "")).strip()[:n]


class _Notes(asyncio.Queue):
    """The notes the Relay sends into a chat. Putting one in remembers when, so what comes straight back can be recognised."""

    def __init__(self, relay):
        super().__init__()
        self._relay = relay

    def put_nowait(self, item):
        super().put_nowait(item)
        self._relay.pushed(item[0])


class Relay:
    """Holds the shared orchestrator, the ASI sessions waiting for follow-ups, and the gate for strangers.

    This agent is for conversations between the person and their family. Who is on the other end of an ASI:One chat is
    unknown, so by default (ASI_MODE=closed) only a sender the care team has listed (ASI_AUTHORIZED, or authorized_asi in
    the care profile) can make it act. Anyone else can still ask harmless questions (status, who it can contact) and is
    told plainly why it won't send anything. ASI_MODE=approve (an operator tool, no screen prompt) holds a stranger's
    request for approval through the local API instead (PENDING_TTL_S, MAX_PENDING, rate limits)."""

    PENDING_TTL_S = 10 * 60
    MAX_PENDING = 5

    def __init__(self, orch: Orchestrator, asi_authorized=None, asi_mode: str | None = None, now=time.time):
        self.orch = orch
        self._pushed: dict[str, float] = {}                # session -> when a note last went into its chat
        self.outbox: asyncio.Queue = _Notes(self)        # (session, text, ends) to send back over chat
        orch.on_event = self._on_event
        self._authorized = None if asi_authorized is None else set(asi_authorized)
        mode = (asi_mode or os.environ.get("ASI_MODE", "closed")).strip().lower()
        self.asi_mode = "closed" if mode in ("off", "closed", "") else mode         # closed (default) or approve
        self._seen_senders: set[str] = set()
        self.now = now
        # two limits for people we don't know: a generous one on anything they say (so a test run of ordinary questions
        # finishes), and a strict one on requests that would wait on the person's screen or prepare an order
        self.chat_limiter = RateLimiter(30, 600, now=lambda: self.now())
        self.limiter = RateLimiter(6, 600, now=lambda: self.now())
        self.pending: dict[str, dict] = {}
        self.hands_outbox: asyncio.Queue = _Notes(self)
        self.hands = None            # the Hands (swarm/hands.py), if set up
        self._chat_tasks: set = set()                          # chat messages being answered (kept so they aren't garbage-collected)
        self._repeats: dict[tuple, int] = {}
        self._acted: dict[tuple, float] = {}                   # (session, what) -> when the Relay last acted on it
        self._delivered: dict[str, None] = {}                  # chat message ids already handled (ASI:One can deliver one twice)
        self.team = False            # True when the circle's members are separate agents on Agentverse (run in this process)

    def errand_update(self, e: dict) -> None:
        """Tell the ASI conversation that started an errand how it is going."""
        h = self.hands
        errand = h.errands.get(e["id"]) if h else None
        if not errand or not errand.session:
            return
        say = {"needs_hands": f"The site needs a login or check: I've asked {e.get('helper')} to lend a hand.",
               "awaiting_consent": f"Ready for the last step: {e['final_action']}. {self.orch.policy.person} needs to confirm it on their own screen.",
               "done": "Done. " + (e.get("summary") or ""), "failed": e.get("summary") or "It didn't work.", "no_hands": e.get("summary"),
               "refused": e.get("summary"), "declined": e.get("summary")}.get(e["status"])
        if say:
            self.hands_outbox.put_nowait((errand.session, say, e["status"] in ("done", "failed", "no_hands", "refused", "declined", "cancelled")))

    REPEAT_WINDOW_S = 300
    ECHO_WINDOW_S = 20            # ASI:One's model answers our notes within seconds
    MAX_COMMAND_CHARS = 400       # a request is a sentence or two; a paragraph is the model talking

    def pushed(self, session: str) -> None:
        self._pushed[session] = self.now()
        if len(self._pushed) > 1000:
            self._pushed.pop(next(iter(self._pushed)))

    def _echo_of_our_note(self, intent: Intent, session: str) -> str | None:
        """ASI:One's model replies to every note we push ("Maya's on her way, I'll stop here"), and the words in those replies
        (help, urgent, stop, a name) used to be read as commands. Right after one of our own notes, a command-shaped message is
        treated as that reply: say so once, then stay quiet. A question or a repeat a little later is unaffected."""
        if intent.intent not in (*ACTUATING, "cancel_task"):
            return None
        t = self._pushed.get(session)
        if t is None or self.now() - t >= self.ECHO_WINDOW_S:
            return None
        k = ("echo", session)
        if self._acted.get(k) == t:
            return ""
        self._acted[k] = t
        note = "That arrived right after my last update, so I haven't treated it as a new request. Say it again in a moment if you meant it."
        if intent.intent == "emergency":
            note += " If this is an emergency, call your local emergency number now."
        return note

    def first_delivery(self, msg_id) -> bool:
        """False for a chat message this agent has already handled."""
        if not msg_id:
            return True
        k = str(msg_id)
        if k in self._delivered:
            return False
        self._delivered[k] = None
        while len(self._delivered) > 500:
            self._delivered.pop(next(iter(self._delivered)))
        return True

    def _repeat(self, session: str, what: tuple, already: str, silent_after_first: bool = True) -> str | None:
        """None the first time `what` is asked in this conversation within the window; after that `already`, then silence."""
        now = self.now()
        for k in [k for k, t in self._acted.items() if now - t > self.REPEAT_WINDOW_S]:
            del self._acted[k]
            self._repeats.pop(k, None)
        key = (session, what)
        if key in self._acted:
            n = self._repeats[key] = self._repeats.get(key, 0) + 1
            return "" if n > 1 and silent_after_first else already          # one polite answer, then silence
        self._acted[key] = now
        return None

    def guard(self, session: str, what: tuple, already: str) -> str | None:
        """For chat requests that DO something (an errand, an order): None to go ahead, otherwise what to say ("" = nothing).
        A request right after one of our own notes is ASI:One's model talking back; a repeat is not done twice."""
        t = self._pushed.get(session)
        if t is not None and self.now() - t < self.ECHO_WINDOW_S:
            k = ("echo", session)
            if self._acted.get(k) == t:
                return ""
            self._acted[k] = t
            return "That arrived right after my last update, so I haven't treated it as a new request. Say it again in a moment if you meant it."
        return self._repeat(session, what, already)

    def _repeat_of(self, intent: Intent, session: str) -> str | None:
        """A request this conversation made a moment ago, as a plain sentence, or None if it is new. A caregiver's phone is not
        something to retry: ASI:One's model can answer our progress notes with a fresh "ask Maya...", and each one used to send a
        real text, even after "On my way"."""
        who = intent.preferred_contact or intent.contact or ""
        what = (intent.intent, who.lower(), (intent.message or "").strip().lower() if intent.intent == "send_message" else "")
        already = ("I've already alerted the care team about this, so I haven't sent anything again." if intent.intent == "emergency"
                   else f"I already asked {who or 'them'} a moment ago, so I haven't sent it again.")
        return self._repeat(session, what, already)

    @property
    def authorized(self) -> set:
        base = self._authorized if self._authorized is not None else {x.strip() for x in os.environ.get("ASI_AUTHORIZED", "").split(",") if x.strip()}
        com = self.orch.commerce
        return base | (set(com.sp.get("authorized_asi", [])) if com is not None else set())

    def _on_event(self, task: Task, ev: dict) -> None:
        if not task.session:
            return
        text = narrate(task, ev)
        if text:
            self.outbox.put_nowait((task.session, text, task.terminal))

    # ------------------------------------------------------------ strangers' requests wait for the person
    def sweep(self) -> None:
        """Requests nobody approved in time are dropped, and their asker is told nothing was sent."""
        t = self.now()
        for pid in [k for k, p in self.pending.items() if t - p["created"] > self.PENDING_TTL_S]:
            p = self.pending.pop(pid)
            self.outbox.put_nowait((p["session"], f"{self.orch.policy.person} didn't answer in time, so nothing was sent.", True))

    def pending_list(self) -> list[dict]:
        self.sweep()
        return [{"id": p["id"], "created": p["created"], "expires": p["created"] + self.PENDING_TTL_S, "kind": p["kind"],
                 "summary": p["summary"], "from": "someone on ASI:One"} for p in self.pending.values()]

    def _hold(self, intent: Intent, session: str) -> str:
        person = self.orch.policy.person
        try:
            task = self.orch.policy.plan(intent)                 # unknown names are refused here, not queued
        except PolicyError as e:
            return f"I can't do that: {e}"
        first, rest = task.order[0], task.order[1:]
        if task.kind == "emergency":
            summary = f"send an EMERGENCY alert to {first}" + (f", then {', '.join(rest)}" if rest else "")
        elif task.kind == "message":
            summary = f"message {first}: “{_clean(intent.message)}”"
        else:
            summary = f"ask {first} for help" + (f" ({intent.need})" if intent.need and intent.need != "general" else "")
        # the same request from the same conversation is the same question: don't ask the person twice
        if any(p["session"] == session and p["summary"] == summary for p in self.pending.values()):
            return f"I've already asked {person} about this and I'm waiting for their answer. Nothing has been sent yet."
        if len(self.pending) >= self.MAX_PENDING:
            return f"{person} already has several requests waiting. Please try again later. Nothing has been sent."
        pid = uuid4().hex[:10]
        self.pending[pid] = {"id": pid, "created": self.now(), "session": session, "intent": intent, "kind": task.kind, "summary": summary}
        note = " If this is an emergency, call 911 now." if task.kind == "emergency" else ""
        return f"I've asked {person} to approve this on Residual's screen. Nothing has been sent yet.{note}"

    async def decide_pending(self, pid: str, approve: bool) -> dict:
        self.sweep()
        p = self.pending.pop(pid, None)
        if p is None:
            return {"status": "gone"}
        person = self.orch.policy.person
        if not approve:
            self.outbox.put_nowait((p["session"], f"{person} didn't approve this, so nothing was sent.", True))
            return {"status": "declined", "id": pid}
        res = await self.orch.handle_intent(p["intent"], {"source": "asi", "session": p["session"]})
        if res.get("status") == "rejected":
            self.outbox.put_nowait((p["session"], f"I can't do that: {res['error']}", True))
        else:
            self.outbox.put_nowait((p["session"], f"{person} approved. " + self._started_text(p["intent"], res), False))
        return {"status": "approved", "id": pid, "task_id": res.get("task_id")}

    def _started_text(self, intent: Intent, res: dict) -> str:
        first = res["order"][0] if res.get("order") else "them"
        sim = " (simulation mode: Photon isn't configured, so no real message is sent)" if self.orch.photon.simulated else ""
        if res["kind"] == "message":
            return f"Sending that to {first}.{sim}"
        rest = res["order"][1:]
        tail = f" If {first} can't come or doesn't answer in {int(res['escalate_after_s'])} s, I'll try {', then '.join(rest)}." if rest else ""
        demo = " (demo timing: shortened for the presentation)" if res.get("demo_timing") else ""
        return f"I'll contact {first} first.{tail}{demo}{sim}"

    @staticmethod
    def _wait_text(limiter: RateLimiter, session: str) -> str:
        mins = int(limiter.retry_after(session) // 60) + 1
        return f"I'm getting a lot of requests from this conversation. Please wait about {mins} minute{'s' if mins != 1 else ''}."

    # ------------------------------------------------------------ the chat
    async def handle_text(self, text: str, session: str) -> str:
        self.sweep()
        trusted = session in self.authorized
        if session not in self._seen_senders:                  # once per sender: how the owner finds their own address
            if len(self._seen_senders) > 1000:
                self._seen_senders.clear()
            self._seen_senders.add(session)
            log.info("ASI sender %s (%s)", session, "authorised" if trusted else "not authorised")
        if not trusted and not self.chat_limiter.allow(session):
            return self._wait_text(self.chat_limiter, session)
        names = [c.name for c in self.orch.policy.contacts]
        com = self.orch.commerce
        if len(text) > self.MAX_COMMAND_CHARS:
            log.info("ASI message too long to be a request (%d chars): ignored", len(text))
            return ""
        closed_note = (f"I only act on requests from people {self.orch.policy.person}'s care team has authorised, "
                       "so I haven't done anything.")
        if com is not None and not trusted and self.asi_mode == "closed" and \
                re.search(r"\b(order|buy|get|confirm|pay|place)\b.*\b(usual|food|lunch|dinner|meal|soup|order)\b", text, re.I):
            return closed_note                                  # nothing prepared, so nothing ever appears on the person's screen
        if com is not None and re.search(r"\b(order|buy|get)\b.*\b(usual|food|lunch|dinner|meal|soup)\b", text, re.I):
            if not trusted and not self.limiter.allow(session):
                return self._wait_text(self.limiter, session)
            again = self.guard(session, ("order", "usual"), "I already prepared that order a moment ago, so I haven't made another.")
            if again is not None:
                return again
            o = await com.propose_best(which="usual", source="asi", asi_sender=session)
            if o["status"] == "rejected":
                return f"I can't prepare that order: {o['error']}"
            items = ", ".join(f"{l['qty']}× {l['name']}" for l in o["lines"])
            cap = f" It's {o['approval_reason']}, so {com.sp.get('approver')} will be asked to approve it." if o.get("approval_required") else ""
            return (f"I've prepared {com.profile.get('person', 'Dev')}'s usual from {o['merchant_name']}: {items}, "
                    f"${o['total_cents'] / 100:.2f} including delivery. Nothing is paid until {com.profile.get('person', 'Dev')} "
                    f"confirms it on Residual's screen.{cap} (Sandbox test order: no real money.)")
        if com is not None and re.search(r"\b(confirm|pay|place)\b.*\border\b", text, re.I):
            pending = [o for o in com.orders.values() if o["status"] == "awaiting_confirmation"]
            if not pending:
                return "There's no order waiting for confirmation."
            r = await com.confirm(pending[-1]["id"], "asi", True, asi_sender=session)
            return r.get("error") or f"Order {r['status']}."
        intent = await parse_with_asi1(text, names, self.orch.policy.person)
        if isinstance(intent, dict):
            return intent["error"]
        echo = self._echo_of_our_note(intent, session)
        if echo is not None:
            log.warning("ASI message right after our own note, not a command (%s)", intent.intent)
            return echo
        if intent.intent in ACTUATING and not trusted:
            if self.asi_mode == "closed":
                note = " If this is an emergency, call your local emergency number now." if intent.intent == "emergency" else ""
                return (f"I only act on requests from people {self.orch.policy.person}'s care team has authorised, "
                        f"so I haven't sent anything.{note}")
            if not self.limiter.allow(session):
                return self._wait_text(self.limiter, session)
            return self._hold(intent, session)
        if intent.intent == "cancel_task":
            latest = self.orch.latest(session)
            intent = intent.model_copy(update={"task_id": latest.id if latest else None})
        if intent.intent in ACTUATING:
            again = self._repeat_of(intent, session)
            if again is not None:
                log.warning("ASI repeat ignored (%s): %s", session[:14], intent.intent)
                return again
        res = await self.orch.handle_intent(intent, {"source": "asi", "session": session})
        if res.get("status") == "rejected":
            return f"I can't do that: {res['error']}"
        if intent.intent == "get_status":
            return status_line(res)
        if intent.intent == "cancel_task":
            return "Cancelled. I won't contact anyone else."
        return self._started_text(intent, res)


def narrate(task: Task, ev: dict) -> str | None:
    k, d = ev["kind"], ev.get("data", {})
    if k == "tool" and d.get("ok"):
        return f"Message sent to {d['contact']}." + (" (simulated)" if d.get("simulated") else "")
    if k == "error":
        return f"Couldn't reach {d.get('contact', 'them')} through Photon: {ev['text'].split(': ', 1)[-1]}"
    if k == "escalate":
        return ev["text"]
    if k == "reply" and d.get("verdict") != "ack":
        return f"{d['contact']} replied: “{d['text']}”"
    if k == "outcome":
        o = task.outcome or {}
        if task.status == "acknowledged":
            others = [n for n in task.order if n != o.get("contact")]
            stop = f" I won't contact {' or '.join(others)}." if others and o.get("escalation_stopped") else ""
            return f"{o['contact']} replied “{o.get('message', '')}”. They're on their way.{stop}"
        if task.status == "sent":
            return "Done." if not o.get("reply") else f"{o['contact']} replied: “{o['reply']}”"
        if task.status in ("exhausted", "declined_all"):
            return f"{o.get('message', 'No one answered.')} I contacted {', '.join(task.order)}."
        if task.status == "failed":
            return f"I couldn't complete this: {o.get('error', 'unknown error')}."
        if task.status == "cancelled":
            return "Cancelled."
    return None


def status_line(s: dict) -> str:
    st = s.get("status")
    if st == "none":
        return "There's no request from this conversation yet."
    if st == "acknowledged":
        return f"{s['outcome']['contact']} replied that they're on their way."
    if st in ("contacting", "waiting"):
        return f"Waiting for {s.get('current')} to reply."
    return f"Status: {st}."


# ---------------------------------------------------------------------- HTTP (Residual Home + Photon)
# Two separate servers, because they have different callers:
#   LOCAL   (127.0.0.1:HTTP_PORT)  Residual's own screen. Every route but a bare /health needs X-Residual-Token, and the
#           Host must be a local name. It never carries the webhook, so it never needs to be tunnelled.
#   WEBHOOK (WEBHOOK_PORT)         only POST /photon/webhook, signed. This is the one port a tunnel points at.
def make_http(relay: Relay, token: str | None = None, local: bool = True, webhook: bool = True,
              allow_no_auth: bool = False) -> web.Application:
    """`token` is required for the local routes. allow_no_auth is for tests only."""
    orch = relay.orch
    secret = os.environ.get("PHOTON_WEBHOOK_SECRET", "")
    live = not orch.photon.simulated
    allow_sim = os.environ.get("ALLOW_SIMULATED_REPLIES", "1" if orch.photon.simulated else "0") == "1"
    if local and not token and not allow_no_auth:
        raise ConfigError("the local API needs a token (see security.ensure_token)")
    mids = []

    if local and token:
        @web.middleware
        async def guard(req, handler):
            if not host_ok(req.headers.get("Host")):
                return web.json_response({"error": "bad host"}, status=403)
            if not token_ok(req.headers.get("X-Residual-Token"), token):
                if req.path == "/health":
                    return web.json_response({"ok": True})          # up, and nothing else
                return web.json_response({"error": "unauthorised"}, status=401)
            return await handler(req)
        mids.append(guard)
    app = web.Application(middlewares=mids)

    async def intent(req):
        body = await req.json()
        try:
            it = Intent(**{**body, "source": "residual_ui"})
        except Exception as e:
            return web.json_response({"status": "rejected", "error": str(e)}, status=400)
        return web.json_response(await orch.handle_intent(it, {"source": "residual_ui"}))

    async def task(req):
        t = orch.tasks.get(req.match_info["id"])
        return web.json_response(t.summary() if t else {"status": "none"}, status=200 if t else 404)

    async def tasks(_req):
        return web.json_response(orch.snapshot())

    async def cancel(req):
        body = await req.json() if req.can_read_body else {}
        it = Intent(intent="cancel_task", task_id=req.match_info["id"], confirmed=bool(body.get("confirmed")),
                    source="residual_ui")
        return web.json_response(await orch.handle_intent(it, {"source": "residual_ui"}))

    async def hook(req):
        raw = await req.read()
        if live and not secret:                                    # startup refuses this; belt and braces
            return web.json_response({"error": "webhook not configured"}, status=503)
        if secret and not verify_signature(secret, dict(req.headers), raw):
            return web.json_response({"error": "bad signature"}, status=401)
        try:
            payload = json.loads(raw)
        except Exception:
            return web.json_response({"error": "not json"}, status=400)
        return web.json_response(await orch.on_inbound(payload))

    async def sim_reply(req):
        if not allow_sim:
            return web.json_response({"error": "simulated replies are off (ALLOW_SIMULATED_REPLIES=0)"}, status=403)
        b = await req.json()
        return web.json_response(await orch.from_contact(b["contact"], b["text"], simulated=True))

    async def health(_req):
        return web.json_response({"ok": True, "agent": os.environ.get("AGENT_NAME", "Residual Relay"),
                                  "address": relay.address if hasattr(relay, "address") else None,
                                  "photon": orch.photon.health(), "webhook_signed": bool(secret),
                                  "demo_timing": orch.policy.demo, "contacts": [c.name for c in orch.policy.contacts],
                                  "contact_details": [{"name": c.name, "relation": c.relation, "pronoun": c.pronoun} for c in orch.policy.contacts],
                                  "calls": orch.caller.health()["calls"] if orch.caller else "off",
                                  "simulated_replies": allow_sim, "asi_mode": relay.asi_mode,
                                  "hands": (None if not getattr(relay, "hands", None) else ("simulated" if relay.hands.browser.simulated else "browser-use")),
                                  "circle": ({"members": list(orch.circle.members), "mode": "team" if relay.team else "local"} if orch.circle else None),
                                  "asi_pending": len(relay.pending_list())})

    # ---- the care circle
    async def circle_state(_req):
        if orch.circle is None:
            return web.json_response({"members": [], "mode": "off"})
        return web.json_response({"members": await orch.circle.snapshot(), "mode": "team" if relay.team else "local"})

    async def circle_say(req):
        """Simulates a family member texting their own agent ("in a meeting till 3"). Same switch as simulated replies."""
        if not allow_sim:
            return web.json_response({"error": "simulated texts are off (ALLOW_SIMULATED_REPLIES=0)"}, status=403)
        b = await req.json()
        if orch.circle is None or b.get("member") not in orch.circle.members:
            return web.json_response({"error": "no such member"}, status=404)
        ack = await orch.circle.hear(b["member"], str(b.get("text", ""))[:280])
        return web.json_response({"member": b["member"], "ack": ack, "understood": bool(ack), "simulated": True})

    # ---- the Hands (errands in a real browser)
    hands = getattr(relay, "hands", None)

    async def errands(_req):
        return web.json_response(hands.list() if hands else [])

    async def errand_new(req):
        b = await req.json()
        r = await hands.start(str(b.get("goal", ""))[:200], str(b.get("url", ""))[:300], source="residual_ui")
        return web.json_response(r, status=400 if r.get("status") == "refused" else 200)

    async def errand_consent(req):
        b = await req.json() if req.can_read_body else {}
        return web.json_response(await hands.consent(req.match_info["id"], bool(b.get("approve")), source="residual_ui"))

    async def errand_cancel(req):
        return web.json_response(await hands.cancel(req.match_info["id"], source="residual_ui"))

    # ---- sandbox ordering
    com = orch.commerce

    async def order_new(req):
        b = await req.json() if req.can_read_body else {}
        return web.json_response(await com.propose_best(which=b.get("which", "usual"), source="residual_ui"))

    async def orders(_req):
        return web.json_response(com.history())

    async def order_get(req):
        o = com.orders.get(req.match_info["id"])
        return web.json_response(com.summary(o) if o else {"status": "none"}, status=200 if o else 404)

    async def order_confirm(req):
        b = await req.json() if req.can_read_body else {}
        return web.json_response(await com.confirm(req.match_info["id"], "residual_ui", bool(b.get("confirmed"))))

    async def care_caps(req):
        b = await req.json()
        r = com.raise_caps(req.headers.get("X-Care-Token", ""), b.get("per_order_cap_cents"), b.get("daily_cap_cents"))
        return web.json_response(r, status=200 if r["ok"] else 403)

    async def care_profile(req):
        if req.method == "PUT":
            r = com.update_profile(req.headers.get("X-Care-Token", ""), await req.json())
            return web.json_response(r, status=200 if r["ok"] else 403)
        p = {k: v for k, v in com.profile.items() if k != "caregiver_token_sha256"}
        return web.json_response({**p, "spent_today_cents": com.spent_today(), "token_set": bool(com.profile.get("caregiver_token_sha256"))})

    async def audit(_req):
        return web.json_response(com.audit[-200:])

    # ---- strangers' requests that wait for the person
    async def asi_pending(_req):
        return web.json_response(relay.pending_list())

    async def asi_decide(req):
        pid = req.match_info["id"]
        if pid not in relay.pending:
            relay.sweep()
            return web.json_response({"status": "gone"}, status=404)
        b = await req.json() if req.can_read_body else {}
        return web.json_response(await relay.decide_pending(pid, bool(b.get("approve"))))

    if local:
        if com is not None:
            app.router.add_post("/orders", order_new)
            app.router.add_get("/orders", orders)
            app.router.add_get("/orders/{id}", order_get)
            app.router.add_post("/orders/{id}/confirm", order_confirm)
            app.router.add_post("/care/caps", care_caps)
            app.router.add_route("*", "/care/profile", care_profile)
            app.router.add_get("/audit", audit)
        app.router.add_post("/intent", intent)
        app.router.add_get("/tasks", tasks)
        app.router.add_get("/tasks/{id}", task)
        app.router.add_post("/tasks/{id}/cancel", cancel)
        app.router.add_post("/simulate/reply", sim_reply)
        if hands is not None:
            app.router.add_get("/errands", errands)
            app.router.add_post("/errands", errand_new)
            app.router.add_post("/errands/{id}/consent", errand_consent)
            app.router.add_post("/errands/{id}/cancel", errand_cancel)
        app.router.add_get("/circle", circle_state)
        app.router.add_post("/circle/say", circle_say)
        app.router.add_get("/asi/pending", asi_pending)
        app.router.add_post("/asi/pending/{id}/decide", asi_decide)
        app.router.add_get("/health", health)
    if webhook:
        app.router.add_post("/photon/webhook", hook)
    return app


# ---------------------------------------------------------------------- uAgent (Agentverse / ASI)
def build_agent(relay: Relay, loop: asyncio.AbstractEventLoop | None = None, link=None, mailbox: bool = True, port: int | None = None):
    from uagents import Agent, Context, Protocol
    from uagents_core.contrib.protocols.chat import (ChatAcknowledgement, ChatMessage, EndSessionContent,
                                                     StartSessionContent, TextContent, chat_protocol_spec)

    seed = require_seed(os.environ)          # the seed IS the agent's address: refuse to run without one
    agent = Agent(name=os.environ.get("AGENT_NAME", "Residual Relay"),
                  seed=seed,
                  **({"port": int(os.environ.get("AGENT_PORT", "8001"))} if port is None else ({"port": port} if port else {})),
                  mailbox=mailbox, publish_agent_details=True,
                  description=DESCRIPTION, readme_path=str(HERE / "AGENT_PROFILE.md"),
                  handle=os.environ.get("AGENT_HANDLE") or None,
                  metadata={"keywords": KEYWORDS, "category": "accessibility"},
                  **({"loop": loop} if loop is not None else {}))
    relay.address = agent.address
    chat = Protocol(spec=chat_protocol_spec)

    def text_msg(text: str, end: bool = False) -> ChatMessage:
        content = [TextContent(type="text", text=text)]
        if end:
            content.append(EndSessionContent(type="end-session"))
        return ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(), content=content)

    @chat.on_message(ChatMessage)
    async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
        await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id))
        if not relay.first_delivery(msg.msg_id):
            return
        text = " ".join(c.text for c in msg.content if isinstance(c, TextContent)).strip()
        log.info("ASI chat from %s: %r", sender[:14], text[:160])
        if not text:
            if any(isinstance(c, StartSessionContent) for c in msg.content):
                await ctx.send(sender, text_msg("Hi, I'm Residual Relay. Tell me who to contact and what's needed, "
                                                "for example: \"Ask Maya to help reposition him. If she can't come, try Raj.\""))
            return
        # Not awaited here: uAgents handles an agent's messages one at a time, and handling this one can mean waiting for the
        # Care Circle's bids, which arrive as messages to this same agent. Held up behind this handler they always timed out.
        async def answer():
            try:
                reply = await relay.handle_text(text, sender)
                if reply:
                    relay.pushed(sender)                       # what comes straight back is ASI:One's model answering this
                    st = await ctx.send(sender, text_msg(reply))
                    log.info("ASI reply to %s: %r -> %s", sender[:14], reply[:90], getattr(st, "status", st))
            except Exception:
                log.exception("couldn't handle a chat message from %s", sender[:14])
        task = asyncio.get_running_loop().create_task(answer())
        relay._chat_tasks.add(task)
        task.add_done_callback(relay._chat_tasks.discard)

    @chat.on_message(ChatAcknowledgement)
    async def on_ack(_ctx: Context, _sender: str, _msg: ChatAcknowledgement):
        pass

    agent.include(chat, publish_manifest=publish_manifests())
    if link is not None:
        from swarm.agents import attach_relay_side
        attach_relay_side(agent, link)

    @agent.on_interval(period=1.0)
    async def push_updates(ctx: Context):
        relay.sweep()
        while not relay.outbox.empty():
            session, text, end = relay.outbox.get_nowait()
            st = await ctx.send(session, text_msg(text, end=end))
            log.info("ASI note to %s: %r -> %s", session[:14], text[:90], getattr(st, "status", st))

    @agent.on_event("startup")
    async def hello(ctx: Context):
        ctx.logger.info(f"Residual Relay address: {agent.address}")
        ctx.logger.info(f"Photon: {relay.orch.photon.health()}")

    return agent


class _Servers:
    def __init__(self, runners):
        self.runners = runners

    async def cleanup(self):
        for r in self.runners:
            await r.cleanup()


async def start_http(relay: Relay) -> _Servers:
    """The local API (Residual's screen) and the webhook (Photon) start first and never wait on Agentverse:
    the person's own screen must work even when the internet route to ASI doesn't. Refuses to start in an unsafe
    configuration (live Photon with no webhook secret, a local API that isn't on loopback)."""
    live = not relay.orch.photon.simulated
    secret = os.environ.get("PHOTON_WEBHOOK_SECRET", "")
    check_webhook_config(live, secret)
    host = os.environ.get("HTTP_HOST", "127.0.0.1")
    require_loopback(host)
    state = Path(os.environ.get("STATE_DIR", HERE / "state"))
    token = ensure_token(state)
    port = int(os.environ.get("HTTP_PORT", "8100"))
    runners = []
    local = web.AppRunner(make_http(relay, token=token, webhook=False))
    await local.setup()
    await web.TCPSite(local, host, port).start()
    runners.append(local)
    hook = web.AppRunner(make_http(relay, local=False, webhook=True))
    await hook.setup()
    hhost, hport = os.environ.get("WEBHOOK_HOST", "127.0.0.1"), int(os.environ.get("WEBHOOK_PORT", "8101"))
    await web.TCPSite(hook, hhost, hport).start()
    runners.append(hook)
    log.info("Local API for Residual's screen on http://%s:%s (token in %s)", host, port, state / "local_token")
    log.info("Photon webhook on http://%s:%s/photon/webhook (point a tunnel at this port only; signed=%s)", hhost, hport, bool(secret))
    return _Servers(runners)


def build_relay() -> Relay:
    _env()
    state = Path(os.environ.get("STATE_DIR", HERE / "state"))
    policy = Policy.load(os.environ.get("CONTACTS_FILE", HERE / "contacts.json"),
                         demo=os.environ.get("DEMO_TIMING", "0") == "1")
    photon = build_photon(state)
    orch = Orchestrator(policy, photon, state_dir=state, caller=build_caller(photon))
    orch.commerce = build_commerce(orch, state)
    relay = Relay(orch)
    relay.brains = build_brains(policy, state)
    orch.circle = embedded_circle(relay.brains)
    relay.hands = build_hands(relay, orch.commerce.profile)
    orch.hands = relay.hands
    return relay


def build_hands(relay: Relay, profile: dict):
    """The Hands: Browser Use if BROWSER_USE_API_KEY is set, otherwise a clearly-labelled SIMULATED browser."""
    from swarm.hands import Hands, SimulatedBrowser
    from tools.browser_use import BrowserUseCloud
    orch = relay.orch
    key = (os.environ.get("BROWSER_USE_API_KEY") or "").strip()
    browser = BrowserUseCloud(key) if key else SimulatedBrowser()
    hosts = (profile.get("hands") or {}).get("allowed_hosts") or ["en.wikipedia.org", "example.org", "example.com"]

    async def send(name, text, key):
        c = orch.policy.resolve(name)
        return await orch.photon.send_message(name, c.phone, text, key)

    async def pick_helpers(candidates):
        names = [n for n in candidates if n in {c.name for c in orch.policy.contacts}]
        if orch.circle is None or not names:
            return names
        plan = await orch.circle.plan({"call_id": "hands-" + uuid4().hex[:8], "person": orch.policy.person, "need": "general", "urgency": "normal"}, names, explicit=False)
        return plan["order"]
    h = Hands(browser, send=send, pick_helpers=pick_helpers, person=orch.policy.person, allowed_hosts=hosts,
              helper_wait_s=float(os.environ.get("HANDS_HELPER_WAIT_S", "300")))
    h.candidates = list(orch.policy.caregiver_order)
    h.on_event = lambda e: relay.errand_update(e)
    return h


def build_brains(policy: Policy, state: Path) -> dict:
    """The family members' agents' memory (where each is, who has helped), kept in state/circle_<name>.json."""
    try:
        raw = json.loads(Path(os.environ.get("CONTACTS_FILE", HERE / "contacts.json")).read_text())
    except Exception:
        raw = {}
    return brains_for(member_config(policy, raw), state)


def build_commerce(orch: Orchestrator, state: Path):
    """Sandbox ordering: a mock restaurant agent, sandbox payments, the care profile's spending policy."""
    from commerce import Commerce, MockRestaurantAgent, Registry, SandboxPayments, load_profile
    path = Path(os.environ.get("CARE_PROFILE", HERE / "care_profile.json"))
    profile = load_profile(path)
    profile["person"] = orch.policy.person
    names = [c.name for c in orch.policy.contacts]
    if profile["spending"].get("approver") not in names:
        profile["spending"]["approver"] = orch.policy.caregiver_order[0]
    # who may be asked to approve: only people on the care plan, the configured approver first
    profile["spending"]["approvers"] = [n for n in dict.fromkeys([profile["spending"]["approver"], *profile["spending"].get("approvers", [])]) if n in names]

    async def send(contact, text, key):
        c = orch.policy.resolve(contact)
        return await orch.photon.send_message(contact, c.phone, text, key)
    com = Commerce(profile, Registry(mock_merchants()), SandboxPayments(), send=send,
                   audit_path=state / "audit.jsonl", profile_path=path)
    com.approver_picker = approver_picker(orch)
    return com


def mock_merchants() -> list:
    """Three sandbox restaurant agents (stand-ins for food agents on Agentverse; replies are untrusted data either way)."""
    from commerce import MockRestaurantAgent
    def menu(prefix, soup, drink):
        return [{"id": f"{prefix}-soup", "name": "Chicken and rice soup", "price_cents": soup, "tags": ["soup", "soft", "warm"]},
                {"id": f"{prefix}-drink", "name": "Fruit smoothie", "price_cents": drink, "tags": ["drink", "soft"]},
                {"id": f"{prefix}-pudding", "name": "Rice pudding", "price_cents": 499, "tags": ["dessert", "soft"]}]
    return [MockRestaurantAgent(),
            MockRestaurantAgent("green-bowl", "Green Bowl", eta_min=20, fee_cents=0, items=menu("gb", 799, 549)),
            MockRestaurantAgent("harbor-deli", "Harbor Deli", eta_min=55, fee_cents=199, items=menu("hd", 699, 449))]


def build_caller(photon):
    """Emergency message -> call. Simulated with simulated Photon; a webhook for real calls; else none."""
    from tools.calls import SimulatedCaller, WebhookCaller
    if getattr(photon, "simulated", False):
        return SimulatedCaller()
    url = os.environ.get("VOICE_CALL_WEBHOOK", "").strip()
    return WebhookCaller(url, os.environ.get("VOICE_CALL_SECRET") or None) if url else None


def build_team(relay: Relay, loop: asyncio.AbstractEventLoop):
    """The Relay's agent and one agent per family member, in one process. They talk to each other as uAgents (typed Care Circle
    messages); each has its own Agentverse mailbox, so each can also be found and talked to through ASI:One."""
    from uagents import Bureau
    from swarm.agents import Link, RemoteMember, build_member_agent
    from swarm.runtime import Circle
    from swarm.team import derive_seed
    root = require_seed(os.environ) or uuid4().hex + uuid4().hex
    mailbox = os.environ.get("RESIDUAL_MAILBOX", "1") != "0"
    link = Link()
    relay_agent = build_agent(relay, loop, link=link, mailbox=mailbox, port=0)
    agents, members = [relay_agent], {}
    for name, brain in relay.brains.items():
        a = build_member_agent(brain, person=relay.orch.policy.person, seed=derive_seed(root, name), mailbox=mailbox, loop=loop)
        members[name] = RemoteMember(name, a.address, link, timeout_s=3.0)
        agents.append(a)
        log.info("%s's agent: %s", name, a.address)
    relay.orch.circle = Circle(members, timeout_s=3.5)
    from swarm.agents import build_chat_agent
    from swarm.chat import concierge_chat, hands_chat
    agents.append(build_chat_agent(
        name="Residual Concierge", handle_text=concierge_chat(relay), seed=derive_seed(root, "concierge"), mailbox=mailbox, loop=loop,
        description=("Residual's Concierge orders food for a person who can only choose between two things: it asks every approved restaurant "
                     "for a quote, picks the one that costs the fewest choices and needs no caregiver to be woken, and waits for the person to confirm. Sandbox."),
        readme="concierge.md", keywords="food ordering, accessibility, ALS, quotes, spending limits, caregiver approval", category="commerce",
        greeting="Hi, I'm Residual's Concierge. Ask \"what would dinner cost?\" or \"order Dev's usual\"."))
    # Browser errands go straight from Residual to Browser Use (the Hands run inside the Relay, behind its local API). Publishing them
    # as an agent on Agentverse is opt-in: HANDS_ON_ASI=1.
    if os.environ.get("HANDS_ON_ASI", "0") == "1":
        agents.append(build_chat_agent(
            name="Residual Hands", handle_text=hands_chat(relay.hands, relay=relay), seed=derive_seed(root, "hands"), mailbox=mailbox, loop=loop, outbox=relay.hands_outbox,
            description=("Residual's Hands use a real browser (Browser Use) to do things on websites for someone who can't use a mouse. They stop one click before "
                         "anything final, ask the person, and ask a family member to lend their hands when a site needs a login."),
            readme="hands.md", keywords="Browser Use, web agent, accessibility, ALS, human in the loop, errands", category="web automation",
            greeting="Hi, I'm Residual's Hands. Tell me an errand and an approved site, for example: \"Renew Dev's prescription at https://pharmacy.example.com\"."))
    relay.team = True
    relay.team_addresses = {a.name: a.address for a in agents}
    bureau = Bureau(port=int(os.environ.get("AGENT_PORT", "8001")), loop=loop)
    for a in agents:
        bureau.add(a)
    return bureau


def main() -> None:  # pragma: no cover - interactive
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    relay = build_relay()
    loop.run_until_complete(start_http(relay))
    if os.environ.get("RESIDUAL_TEAM", "1") != "0":
        build_team(relay, loop).run()
    else:
        build_agent(relay, loop).run()


if __name__ == "__main__":  # pragma: no cover
    main()
