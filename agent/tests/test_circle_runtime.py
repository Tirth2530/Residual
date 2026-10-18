"""The Relay asking the circle: members answer, slow members don't hold anything up, nothing outside the care plan
can be contacted, emergencies never wait, and the whole thing falls back to the care plan's order."""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from orchestrator import Orchestrator  # noqa: E402
from policy import Contact, Policy  # noqa: E402
from swarm.circle import CaregiverBrain  # noqa: E402
from swarm.runtime import Circle, LocalMember  # noqa: E402

TS = datetime(2026, 10, 18, 13, 0).timestamp()
MAYA, RAJ = "+14155550001", "+14155550002"


class FakePhoton:
    simulated = False

    def __init__(self):
        self.sent = []

    async def send_message(self, name, phone, text, key):
        self.sent.append((name, text, key))
        return {"status": "accepted", "message_id": f"m{len(self.sent)}"}

    def contact_for(self, space, sender):
        return {MAYA: "Maya", RAJ: "Raj"}.get(sender)

    def health(self):
        return {"mode": "photon"}


def policy():
    return Policy(person="Dev", contacts=[Contact("Maya", MAYA, "daughter", pronoun="she"), Contact("Raj", RAJ, "son", pronoun="he")],
                  emergency_order=["Maya", "Raj"], caregiver_order=["Maya", "Raj"], escalate_after_s=30, emergency_after_s=30, demo=False)


def circle(**brains):
    ms = {n: LocalMember(CaregiverBrain(n, clock=lambda: TS, **kw)) for n, kw in brains.items()}
    return Circle(ms, timeout_s=0.5)


def orch(c):
    o = Orchestrator(policy(), FakePhoton())
    o.circle = c
    return o


async def settle(o, n=50):
    for _ in range(n):
        if o.photon.sent:
            return
        await asyncio.sleep(0.01)


def help_intent(**kw):
    return {"intent": "request_caregiver_help", "need": "repositioning", "urgency": "normal", "source": "residual_ui", **kw}


@pytest.mark.asyncio
async def test_the_member_who_is_free_is_asked_first_and_the_busy_one_is_left_alone():
    c = circle(Maya={}, Raj={})
    c.members["Maya"].brain.hear("in a meeting till 3")
    c.members["Raj"].brain.hear("home, 6 min away")
    o = orch(c)
    res = await o.handle_intent(help_intent(), {"source": "residual_ui"})
    await settle(o)
    assert res["order"] == ["Raj", "Maya"]
    assert o.photon.sent[0][0] == "Raj" and len(o.photon.sent) == 1          # Maya was not disturbed
    sw = res["swarm"]
    assert sw["mode"] == "circle" and sw["winner"] == "Raj"
    assert {b["member"] for b in sw["bids"]} == {"Maya", "Raj"}
    assert any("meeting" in w for w in sw["why"])


@pytest.mark.asyncio
async def test_a_request_that_names_people_keeps_the_persons_choice():
    c = circle(Maya={}, Raj={})
    c.members["Raj"].brain.hear("home, 2 min away")
    o = orch(c)
    res = await o.handle_intent(help_intent(preferred_contact="Maya"), {"source": "asi"})
    await settle(o)
    assert res["order"][0] == "Maya"                                          # asked for Maya, so Maya
    assert res["swarm"]["mode"] == "advice"                                   # the circle only advised


@pytest.mark.asyncio
async def test_emergencies_do_not_wait_for_the_circle():
    c = circle(Maya={}, Raj={})
    c.members["Maya"].brain.hear("sleeping until 7am")
    o = orch(c)
    res = await o.handle_intent({"intent": "emergency", "need": "breathing", "source": "residual_ui"}, {"source": "residual_ui"})
    await settle(o)
    assert res["order"] == ["Maya", "Raj"]                                    # the care plan's fixed emergency order
    assert "swarm" not in res or not res["swarm"]


@pytest.mark.asyncio
async def test_a_slow_member_cannot_hold_up_the_call():
    class Slow(LocalMember):
        async def bid(self, call):
            await asyncio.sleep(5)
    c = Circle({"Maya": Slow(CaregiverBrain("Maya", clock=lambda: TS)), "Raj": LocalMember(CaregiverBrain("Raj", clock=lambda: TS))}, timeout_s=0.2)
    t0 = asyncio.get_event_loop().time()
    plan = await c.plan({"call_id": "c", "need": "repositioning", "urgency": "normal"}, ["Maya", "Raj"], explicit=False)
    assert asyncio.get_event_loop().time() - t0 < 1.0
    assert plan["order"][0] == "Raj" and any("Maya" in w and "didn't answer" in w for w in plan["why"])


@pytest.mark.asyncio
async def test_a_member_that_crashes_is_treated_as_silent():
    class Broken(LocalMember):
        async def bid(self, call):
            raise RuntimeError("boom")
    c = Circle({"Maya": Broken(CaregiverBrain("Maya")), "Raj": LocalMember(CaregiverBrain("Raj", clock=lambda: TS))}, timeout_s=0.5)
    plan = await c.plan({"call_id": "c", "need": "thirsty", "urgency": "normal"}, ["Maya", "Raj"], explicit=False)
    assert plan["order"][0] == "Raj"


@pytest.mark.asyncio
async def test_nobody_answering_is_the_old_behaviour_exactly():
    class Dead(LocalMember):
        async def bid(self, call):
            return None
    c = Circle({"Maya": Dead(CaregiverBrain("Maya")), "Raj": Dead(CaregiverBrain("Raj"))}, timeout_s=0.2)
    o = orch(c)
    res = await o.handle_intent(help_intent(), {"source": "residual_ui"})
    assert res["order"] == ["Maya", "Raj"] and res["swarm"]["mode"] == "fallback"


@pytest.mark.asyncio
async def test_a_member_outside_the_care_plan_can_never_be_chosen():
    c = circle(Maya={}, Raj={}, Mallory={})
    c.members["Mallory"].brain.hear("home, 1 min away")
    o = orch(c)
    res = await o.handle_intent(help_intent(), {"source": "residual_ui"})
    assert "Mallory" not in res["order"]


@pytest.mark.asyncio
async def test_help_that_really_happened_moves_the_load_and_the_next_call():
    c = circle(Maya={}, Raj={})
    o = orch(c)
    res = await o.handle_intent(help_intent(), {"source": "residual_ui"})
    await settle(o)
    first = res["order"][0]
    o.reply(first, "on my way")
    await asyncio.sleep(0.05)
    assert c.members[first].brain.ledger.load(TS) > 0
    res2 = await o.handle_intent(help_intent(), {"source": "residual_ui"})
    assert res2["order"][0] != first                                          # the other sibling takes this one


@pytest.mark.asyncio
async def test_a_status_text_updates_the_agent_and_gets_a_plain_answer():
    c = circle(Maya={}, Raj={})
    o = orch(c)
    out = await o.on_inbound({"type": "message.received", "data": {"projectId": "p", "object": {
        "id": "m9", "platform": "imessage", "status": "received", "content": {"type": "text", "text": "in a meeting till 3"},
        "space": {"id": "s1"}, "sentBy": {"id": "u", "handle": MAYA}}}})
    assert out.get("circle") is True and "3:00 PM" in out["ack"]
    assert o.photon.sent[-1][0] == "Maya" and "3:00 PM" in o.photon.sent[-1][1]
    res = await o.handle_intent(help_intent(), {"source": "residual_ui"})
    assert res["order"][0] == "Raj"


@pytest.mark.asyncio
async def test_chatter_from_a_contact_is_not_a_status_and_gets_no_answer():
    c = circle(Maya={}, Raj={})
    o = orch(c)
    out = await o.on_inbound({"type": "message.received", "data": {"projectId": "p", "object": {
        "id": "m10", "platform": "imessage", "status": "received", "content": {"type": "text", "text": "thanks, see you soon"},
        "space": {"id": "s1"}, "sentBy": {"id": "u", "handle": MAYA}}}})
    assert not out.get("circle") and o.photon.sent == []


@pytest.mark.asyncio
async def test_the_circle_can_be_summarised_for_the_screen():
    c = circle(Maya={}, Raj={})
    c.members["Maya"].brain.hear("driving")
    snap = await c.snapshot()
    assert {s["member"] for s in snap} == {"Maya", "Raj"}
    assert next(s for s in snap if s["member"] == "Maya")["mode"] == "driving"
