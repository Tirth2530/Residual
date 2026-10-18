"""The one orchestration function.

    handle_intent(intent, context)

Residual Home (a person selecting "Move me" with an eyebrow) and ASI (a judge typing a request)
both end here, and both use the same Photon tool. Nothing about the source changes what happens,
except who may cancel.

A task runs as:  plan (policy) -> send to the first contact (Photon, idempotent) -> wait for a reply
-> 'on my way' stops it; 'can't' or silence for escalate_after_s moves to the next contact ->
for an emergency, after everyone, alert everyone again until someone answers or the person cancels.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Callable

from models import Attempt, Intent, Task
from policy import Policy, PolicyError, classify_reply
from tools.photon import PhotonError, parse_inbound

log = logging.getLogger("residual.orchestrator")


class Orchestrator:
    def __init__(self, policy: Policy, photon, state_dir: Path | None = None,
                 on_event: Callable[[Task, dict], None] | None = None, sleep=asyncio.sleep, caller=None):
        self.policy = policy
        self.photon = photon
        self.caller = caller          # optional: emergency message -> call (tools/calls.py)
        self.commerce = None          # optional: sandbox ordering (commerce.py); approvals arrive as replies
        self.hands = None             # optional: errands in a browser (swarm/hands.py); a family member's DONE reply resumes one
        self.circle = None            # optional: the care circle (swarm/runtime.py): orders ordinary help by who is best placed
        self.state_dir = state_dir
        self.tasks: dict[str, Task] = {}
        self._wake: dict[str, asyncio.Event] = {}
        self._runners: dict[str, asyncio.Task] = {}
        self._seen_inbound: set[str] = set()
        self.on_event = on_event or (lambda task, ev: None)
        self.sleep = sleep
        self.calls = 0                 # how many times handle_intent ran (tests: one entry point)
        self._load()

    # ---------------------------------------------------------------- entry
    async def handle_intent(self, intent: Intent | dict, context: dict | None = None) -> dict:
        self.calls += 1
        context = context or {}
        if isinstance(intent, dict):
            try:
                intent = Intent(**intent)
            except Exception as e:
                return {"status": "rejected", "error": f"not a valid intent: {e}"}
        if context.get("source"):
            intent = intent.model_copy(update={"source": context["source"]})
        if intent.intent == "get_status":
            t = self.tasks.get(intent.task_id or "") or self.latest(context.get("session"))
            return t.summary() if t else {"status": "none", "error": "no request found"}
        if intent.intent == "cancel_task":
            return await self.cancel(intent)
        try:
            task = self.policy.plan(intent)
        except PolicyError as e:
            return {"status": "rejected", "error": str(e)}
        task.session = context.get("session")
        if task.kind == "help" and self.circle is not None:
            await self._consult_circle(task, intent)
        self.tasks[task.id] = task
        self._emit(task, task.log("planned", "; ".join(task.plan), plan=task.plan,
                                  demo_timing=task.demo_timing, escalate_after_s=task.escalate_after_s))
        self._wake[task.id] = asyncio.Event()
        self._runners[task.id] = asyncio.create_task(self._run(task))
        await asyncio.sleep(0)            # let the first send start before replying
        self._save()
        return task.summary()

    async def _consult_circle(self, task: Task, intent: Intent) -> None:
        """Ordinary help only: emergencies follow the fixed order and never wait on anyone."""
        call = {"call_id": task.id, "person": self.policy.person, "need": intent.need or "general", "urgency": intent.urgency, "text": task.text}
        explicit = bool(intent.preferred_contact or intent.contacts)
        try:
            plan = await self.circle.plan(call, list(task.order), explicit)
        except Exception as e:                               # the circle must never be able to stop a request
            task.log("note", f"The care circle couldn't be asked ({e}); using the care plan's order.")
            return
        task.swarm = plan
        if plan["mode"] == "circle":
            task.order = plan["order"]
            task.plan = self.policy.help_steps(task.order, task.escalate_after_s)
        task.log("circle", "; ".join(plan["why"]) or "No answers from the care circle; using the care plan's order.", swarm=plan)

    def _circle_record(self, name: str, status: str, need: str | None) -> None:
        if self.circle is None:
            return
        try:
            asyncio.get_running_loop().create_task(self.circle.record(name, status, need or "general"))
        except RuntimeError:
            pass

    def latest(self, session: str | None = None) -> Task | None:
        ts = [t for t in self.tasks.values() if session is None or t.session == session]
        return max(ts, key=lambda t: t.created) if ts else None

    # ---------------------------------------------------------------- running
    async def _send(self, task: Task, name: str) -> bool:
        contact = self.policy.resolve(name)
        key = f"{task.id}:{name}:{task.rounds}"
        a = Attempt(contact=name, tool="Photon.send_message", idempotency_key=key, started=time.time())
        task.attempts.append(a)
        t0 = time.monotonic()
        try:
            res = await self.photon.send_message(name, contact.phone, task.text, key)
            a.ok, a.result, a.simulated = True, res, bool(res.get("simulated"))
            a.ms = int((time.monotonic() - t0) * 1000)
            self._emit(task, task.log("tool", f"Photon.send_message to {name}: {res.get('status', 'accepted')}"
                                      + (" (SIMULATED)" if a.simulated else ""), contact=name, ok=True,
                                      ms=a.ms, simulated=a.simulated, message_id=res.get("message_id")))
            return True
        except (PhotonError, Exception) as e:      # network, auth, validation: all reported
            a.ok, a.error, a.ms = False, str(e)[:300], int((time.monotonic() - t0) * 1000)
            self._emit(task, task.log("error", f"Photon.send_message to {name} failed: {a.error}",
                                      contact=name, ok=False, ms=a.ms))
            return False

    async def _call(self, task: Task, name: str) -> bool:
        contact = self.policy.resolve(name)
        key = f"{task.id}:{name}:{task.rounds}:call"
        a = Attempt(contact=name, tool="voice_call", idempotency_key=key, started=time.time())
        task.attempts.append(a)
        t0 = time.monotonic()
        spoken = f"This is Residual for {self.policy.person}. {task.text.replace('URGENT: ', '')}"
        try:
            res = await self.caller.place_call(name, contact.phone, spoken, key)
            a.ok, a.result, a.simulated, a.ms = True, res, bool(res.get("simulated")), int((time.monotonic() - t0) * 1000)
            self._emit(task, task.log("tool", f"Calling {name}: {res.get('status', 'started')}" + (" (SIMULATED)" if a.simulated else ""),
                                      contact=name, ok=True, ms=a.ms, simulated=a.simulated, call=True, call_id=res.get("call_id")))
            return True
        except Exception as e:
            a.ok, a.error, a.ms = False, str(e)[:300], int((time.monotonic() - t0) * 1000)
            self._emit(task, task.log("error", f"Call to {name} failed: {a.error}", contact=name, ok=False, call=True))
            return False

    async def _run(self, task: Task) -> None:
        try:
            if task.kind == "message":
                task.status = "contacting"
                ok = await self._send(task, task.order[0])
                if not ok:
                    return self._finish(task, "failed", {"error": "Photon unavailable", "fallback": "local"})
                if not task.intent.requires_confirmation:
                    return self._finish(task, "sent", {"contact": task.order[0]})
                task.status = "waiting"
                self._save()
                return            # a reply (if any) is reported by on_inbound
            failures = 0
            while not task.terminal:
                name = task.order[task.current]
                task.status = "contacting"
                ok = await self._send(task, name)
                failures = 0 if ok else failures + 1
                if failures >= len(task.order):
                    # nobody reachable through Photon: say so, and let Residual's local alarm and
                    # fallback Lifeline take over. Never silently "succeed".
                    return self._finish(task, "failed", {"error": "Photon unavailable for every contact",
                                                         "fallback": "local"})
                task.status = "waiting"
                self._save()
                if ok and task.kind == "emergency" and self.caller is not None:
                    # an emergency message that isn't answered in half the interval becomes a call to the same
                    # person, then the rest of the interval to answer, then the next person
                    half = task.escalate_after_s / 2
                    verdict = await self._wait(task, name, half)
                    if task.terminal or verdict == "ack":
                        return
                    if verdict == "timeout":
                        self._emit(task, task.log("escalate", f"{name} hasn't answered the message in {int(half)} s. Calling {name}.",
                                                  from_contact=name, to_contact=name, reason="no answer to message", call=True))
                        await self._call(task, name)
                        verdict = await self._wait(task, name, half)
                else:
                    verdict = await self._wait(task, name if ok else None, task.escalate_after_s if ok else 0)
                if task.terminal:
                    return
                if verdict == "ack":
                    return
                nxt = task.current + 1
                if nxt >= len(task.order):
                    if task.kind == "emergency" and task.rounds + 1 < self.policy.max_emergency_rounds:
                        task.rounds += 1
                        task.current = 0
                        self._emit(task, task.log("escalate", f"Nobody has answered yet. Alerting everyone again (round {task.rounds + 1})."))
                        continue
                    outcome = "declined_all" if all(r["verdict"] == "decline" for r in task.replies) and task.replies else "exhausted"
                    return self._finish(task, outcome, {"contacted": task.order,
                                                        "message": "No one could come." if outcome == "declined_all"
                                                        else "No one answered."})
                why = "said they can't" if verdict == "decline" else f"no answer in {int(task.escalate_after_s)} s" if ok \
                    else "couldn't be reached"
                task.current = nxt
                self._emit(task, task.log("escalate", f"{name} {why}. Contacting {task.order[nxt]}.",
                                          from_contact=name, to_contact=task.order[nxt], reason=why))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # never leave a task hanging in "waiting" silently
            self._finish(task, "failed", {"error": f"internal error: {e}", "fallback": "local"})

    async def _wait(self, task: Task, name: str | None, secs: float) -> str:
        """Wait for a reply from `name` (or timeout). Returns 'ack' | 'decline' | 'timeout'."""
        ev = self._wake[task.id]
        end = time.monotonic() + secs
        while not task.terminal:
            left = end - time.monotonic()
            if left <= 0:
                return "timeout"
            ev.clear()
            try:
                await asyncio.wait_for(ev.wait(), timeout=left)
            except asyncio.TimeoutError:
                return "timeout"
            last = task.replies[-1] if task.replies else None
            if last and last.get("contact") == name and not last.get("consumed"):
                last["consumed"] = True
                if last["verdict"] in ("ack", "decline"):
                    return last["verdict"]
        return "ack" if task.status == "acknowledged" else "timeout"

    def _finish(self, task: Task, status: str, outcome: dict) -> None:
        task.status = status
        task.outcome = {"status": status, **outcome}
        self._emit(task, task.log("outcome", outcome.get("message") or status, **outcome))
        self._save()

    # ---------------------------------------------------------------- inbound
    async def on_inbound(self, payload: dict) -> dict:
        m = parse_inbound(payload)
        if m is None:
            return {"matched": False, "why": "not an inbound text message"}
        if m["message_id"] and m["message_id"] in self._seen_inbound:
            return {"matched": False, "why": "duplicate delivery"}
        if m["message_id"]:
            self._seen_inbound.add(m["message_id"])
        name = self.photon.contact_for(m["space_id"], m["sender"])
        if name is None and m["sender"]:
            c = self.policy.by_handle(m["sender"])
            name = c.name if c else None
        if name is None:
            return {"matched": False, "why": "sender isn't a known contact"}
        return await self.from_contact(name, m["text"], m.get("message_id"))

    async def from_contact(self, name: str, text: str, message_id: str | None = None, simulated: bool = False) -> dict:
        """Everything a known contact's text can mean, in order: a "DONE" for a browser they were asked to lend their hands to, a reply to a
        request or an order, and what their own agent learns about where they are. A real Photon message and a simulated one both end here."""
        if self.hands is not None:
            busy = any(not t.terminal and any(a.contact == name for a in t.attempts) for t in self.tasks.values())
            h = self.hands.try_reply(name, text, allow_decline=not busy)
            if h:
                return {**h, "hands": True, "simulated": simulated}
        r = self.reply(name, text, message_id, simulated=simulated)
        if self.circle is not None:                           # their own agent learns from what they say, whether or not it also answered a request
            ack = await self.circle.hear(name, text)
            if ack and not r.get("matched"):
                try:
                    c = self.policy.resolve(name)
                    await self.photon.send_message(name, c.phone, ack, f"circle:{message_id or time.time()}")
                except Exception as e:
                    log.warning("couldn't answer %s's status text: %s", name, e)
                return {"matched": False, "circle": True, "ack": ack, "why": r.get("why"), "simulated": simulated}
        return r

    def reply(self, name: str, text: str, message_id: str | None = None, simulated: bool = False) -> dict:
        """Route a reply from a contact to their most recent active task."""
        # "YES 4821": a caregiver approving (or declining) a specific order. Only a reply naming a waiting
        # order's code is taken this way; anything else is an ordinary reply.
        if self.commerce is not None:
            r = self.commerce.try_reply(name, text)
            if r and r.get("matched"):
                if r.get("pay"):
                    asyncio.get_event_loop().create_task(self.commerce.pay_approved(r["order_id"]))
                return {**r, "simulated": simulated}
        # any active task that actually contacted this person (in an emergency's later rounds,
        # everyone has been contacted, whatever the current position is)
        active = [t for t in self.tasks.values() if not t.terminal and any(a.contact == name for a in t.attempts)]
        if not active:
            return {"matched": False, "why": f"no active request involving {name}"}
        task = max(active, key=lambda t: t.created)
        verdict = classify_reply(text)
        r = {"contact": name, "text": text, "verdict": verdict, "t": time.time(), "message_id": message_id,
             "simulated": simulated}
        task.replies.append(r)
        self._emit(task, task.log("reply", f"{name}: “{text}”" + (" (SIMULATED reply)" if simulated else ""),
                                  contact=name, text=text, verdict=verdict, simulated=simulated))
        if verdict in ("ack", "decline"):
            self._circle_record(name, "acknowledged" if verdict == "ack" else "declined", task.intent.need)
        if verdict == "ack":
            c = next((c for c in self.policy.contacts if c.name == name), None)
            self._finish(task, "acknowledged", {"contact": name, "message": text,
                                                "relation": c.relation if c else "", "pronoun": c.pronoun if c else None,
                                                "escalation_stopped": task.current + 1 < len(task.order) or task.kind == "emergency"})
        elif task.kind == "message" and task.status == "waiting":
            self._finish(task, "sent", {"contact": name, "reply": text})
        self._wake.get(task.id, asyncio.Event()).set()
        self._save()
        return {"matched": True, "task_id": task.id, "verdict": verdict, "status": task.status}

    # ---------------------------------------------------------------- cancel
    async def cancel(self, intent: Intent) -> dict:
        task = self.tasks.get(intent.task_id or "")
        if task is None:
            return {"status": "rejected", "error": "no such request"}
        ok, why = self.policy.may_cancel(task, intent)
        if not ok:
            self._emit(task, task.log("note", f"Cancel refused ({intent.source}): {why}"))
            return {"status": "rejected", "error": why, "task_id": task.id}
        self._finish(task, "cancelled", {"by": intent.source, "message": f"Cancelled by {intent.source}"})
        self._wake.get(task.id, asyncio.Event()).set()
        return task.summary()

    # ---------------------------------------------------------------- state
    def _emit(self, task: Task, ev) -> None:
        try:
            self.on_event(task, ev.model_dump())
        except Exception:
            pass

    def _save(self) -> None:
        if not self.state_dir:
            return
        self.state_dir.mkdir(parents=True, exist_ok=True)
        p = self.state_dir / "tasks.json"
        tmp = p.with_suffix(".tmp")
        recent = sorted(self.tasks.values(), key=lambda t: t.created)[-100:]
        tmp.write_text(json.dumps([t.model_dump() for t in recent]))
        tmp.replace(p)

    def _load(self) -> None:
        if not self.state_dir:
            return
        p = self.state_dir / "tasks.json"
        if not p.exists():
            return
        try:
            for d in json.loads(p.read_text()):
                t = Task(**d)
                if not t.terminal:
                    # The agent restarted mid-request. Never pretend it carried on: mark it.
                    t.status, t.outcome = "failed", {"status": "failed", "error": "agent restarted mid-request",
                                                     "fallback": "local"}
                    t.log("outcome", "The agent restarted while this was running.")
                self.tasks[t.id] = t
        except Exception:
            pass

    def snapshot(self, limit: int = 20) -> list[dict]:
        return [t.summary() for t in sorted(self.tasks.values(), key=lambda t: -t.created)[:limit]]
