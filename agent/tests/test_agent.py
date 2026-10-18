import asyncio
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from models import Intent  # noqa: E402
from orchestrator import Orchestrator  # noqa: E402
from parser import parse  # noqa: E402
from policy import Contact, Policy, classify_reply  # noqa: E402
from tools.photon import PhotonClient, SimulatedPhoton, parse_inbound, verify_signature  # noqa: E402

MAYA, RAJ = "+15555550101", "+15555550102"


def policy(after=0.3):
    return Policy(person="Dev", contacts=[Contact("Maya", MAYA, "daughter"), Contact("Raj", RAJ, "son")],
                  emergency_order=["Maya", "Raj"], caregiver_order=["Maya", "Raj"],
                  escalate_after_s=after, emergency_after_s=after, demo=False, max_emergency_rounds=2)


def orch(photon=None, after=0.3):
    return Orchestrator(policy(after), photon or SimulatedPhoton())


async def until(cond, timeout=3.0):
    end = asyncio.get_event_loop().time() + timeout
    while not cond():
        if asyncio.get_event_loop().time() > end:
            raise AssertionError("timed out")
        await asyncio.sleep(0.01)


HELP = {"intent": "request_caregiver_help", "need": "repositioning", "urgency": "normal",
        "preferred_contact": "Maya", "requires_confirmation": True}


# ------------------------------------------------------------ the closed loop
@pytest.mark.asyncio
async def test_help_request_acknowledged_without_escalation():
    ph = SimulatedPhoton()
    o = orch(ph, after=5)
    res = await o.handle_intent(HELP, {"source": "residual_ui"})
    assert res["status"] in ("contacting", "waiting") and res["order"] == ["Maya", "Raj"]
    await until(lambda: len(ph.sent) == 1)
    assert ph.sent[0]["contact"] == "Maya" and "repositioning" in ph.sent[0]["text"]
    out = await o.on_inbound({"event": "messages", "space": {"id": "sim-space-maya"},
                              "message": {"id": "m1", "direction": "inbound", "sender": {"id": MAYA},
                                          "content": {"type": "text", "text": "On my way"}}})
    assert out["matched"] and out["verdict"] == "ack"
    t = o.tasks[res["task_id"]]
    assert t.status == "acknowledged" and t.outcome["contact"] == "Maya"
    await asyncio.sleep(0.2)
    assert [s["contact"] for s in ph.sent] == ["Maya"]          # Raj never contacted


@pytest.mark.asyncio
async def test_silence_escalates_to_second_contact():
    ph = SimulatedPhoton()
    o = orch(ph, after=0.2)
    res = await o.handle_intent(HELP, {"source": "residual_ui"})
    await until(lambda: len(ph.sent) == 2)
    assert [s["contact"] for s in ph.sent] == ["Maya", "Raj"]
    o.reply("Raj", "coming now")
    t = o.tasks[res["task_id"]]
    assert t.status == "acknowledged" and t.outcome["contact"] == "Raj"
    assert any(e.kind == "escalate" and "no answer" in e.text for e in t.events)


@pytest.mark.asyncio
async def test_decline_escalates_immediately():
    ph = SimulatedPhoton()
    o = orch(ph, after=30)
    await o.handle_intent(HELP, {"source": "residual_ui"})
    await until(lambda: len(ph.sent) == 1)
    o.reply("Maya", "Sorry, can't come right now")
    await until(lambda: len(ph.sent) == 2)
    assert ph.sent[1]["contact"] == "Raj"


@pytest.mark.asyncio
async def test_inbound_maps_to_the_right_active_task():
    ph = SimulatedPhoton()
    o = orch(ph, after=30)
    a = await o.handle_intent({**HELP, "preferred_contact": "Maya"}, {"source": "residual_ui"})
    b = await o.handle_intent({"intent": "send_message", "contact": "Raj", "message": "Can you call me?",
                               "requires_confirmation": True}, {"source": "residual_ui"})
    await until(lambda: len(ph.sent) == 2)
    r = await o.on_inbound({"event": "messages", "space": {"id": "sim-space-raj"},
                            "message": {"id": "x9", "sender": {"id": RAJ}, "content": {"type": "text", "text": "Sure"}}})
    assert r["task_id"] == b["task_id"]
    assert o.tasks[a["task_id"]].status == "waiting"
    # the same delivery twice is ignored
    r2 = await o.on_inbound({"event": "messages", "space": {"id": "sim-space-raj"},
                             "message": {"id": "x9", "sender": {"id": RAJ}, "content": {"type": "text", "text": "Sure"}}})
    assert not r2["matched"] and r2["why"] == "duplicate delivery"


@pytest.mark.asyncio
async def test_unknown_contact_cannot_be_messaged():
    ph = SimulatedPhoton()
    o = orch(ph)
    r = await o.handle_intent({"intent": "send_message", "contact": "Stranger", "message": "hi"}, {"source": "asi"})
    assert r["status"] == "rejected" and "isn't one of" in r["error"]
    r = await o.handle_intent({**HELP, "preferred_contact": "Maya", "contacts": ["Eve"]}, {"source": "asi"})
    assert r["status"] == "rejected"
    assert ph.sent == []


@pytest.mark.asyncio
async def test_emergency_order_is_fixed_and_asi_cannot_cancel_it():
    ph = SimulatedPhoton()
    o = orch(ph, after=30)
    r = await o.handle_intent({"intent": "emergency", "need": "breathing", "preferred_contact": "Raj"}, {"source": "asi"})
    assert r["order"] == ["Maya", "Raj"]                                   # the request can't reorder it
    c = await o.handle_intent({"intent": "cancel_task", "task_id": r["task_id"], "confirmed": True}, {"source": "asi"})
    assert c["status"] == "rejected" and "only be cancelled from Residual" in c["error"]
    c = await o.handle_intent({"intent": "cancel_task", "task_id": r["task_id"]}, {"source": "residual_ui"})
    assert c["status"] == "rejected"                                       # needs explicit confirmation
    c = await o.handle_intent({"intent": "cancel_task", "task_id": r["task_id"], "confirmed": True},
                              {"source": "residual_ui"})
    assert c["status"] == "cancelled"


@pytest.mark.asyncio
async def test_emergency_keeps_alerting_until_someone_answers():
    ph = SimulatedPhoton()
    o = orch(ph, after=0.15)
    r = await o.handle_intent({"intent": "emergency"}, {"source": "residual_ui"})
    await until(lambda: len(ph.sent) >= 3)                                 # Maya, Raj, Maya again
    assert [s["contact"] for s in ph.sent[:3]] == ["Maya", "Raj", "Maya"]
    o.reply("Raj", "on my way")
    assert o.tasks[r["task_id"]].status == "acknowledged"


@pytest.mark.asyncio
async def test_photon_unavailable_fails_loudly_with_local_fallback():
    o = orch(SimulatedPhoton(fail=True), after=0.1)
    r = await o.handle_intent({"intent": "emergency"}, {"source": "residual_ui"})
    t = o.tasks[r["task_id"]]
    await until(lambda: t.terminal)
    assert t.status == "failed" and t.outcome["fallback"] == "local"
    assert all(a.ok is False for a in t.attempts)


@pytest.mark.asyncio
async def test_photon_send_is_idempotent_locally_and_at_photon(tmp_path):
    calls = []

    async def post(path, body, headers):
        calls.append((path, headers.get("idempotency-key")))
        return 202 if path == "/messages" else 200, {"id": {"/users": "u1", "/spaces": "s1", "/messages": "msg1"}[path]}

    p = PhotonClient("proj", "key", "agent-user", state_path=tmp_path / "map.json", post=post)
    a = await p.send_message("Maya", MAYA, "hello", "task1:Maya:0")
    b = await p.send_message("Maya", MAYA, "hello", "task1:Maya:0")
    assert a["message_id"] == "msg1" and b["replayed"]
    assert [c[0] for c in calls].count("/messages") == 1
    assert ("/messages", "task1:Maya:0") in calls                          # the key reached Photon too
    # mapping persisted: a new client reuses the user and space
    p2 = PhotonClient("proj", "key", "agent-user", state_path=tmp_path / "map.json", post=post)
    await p2.send_message("Maya", MAYA, "again", "task2:Maya:0")
    assert [c[0] for c in calls].count("/users") == 1
    assert p2.contact_for("s1", None) == "Maya"


@pytest.mark.asyncio
async def test_photon_http_error_is_reported():
    async def post(path, body, headers):
        return 401, {"title": "Unauthorized"}
    o = orch(PhotonClient("p", "k", "a", post=post), after=0.05)
    r = await o.handle_intent({"intent": "send_message", "contact": "Maya", "message": "hi"}, {"source": "residual_ui"})
    t = o.tasks[r["task_id"]]
    await until(lambda: t.terminal)
    assert t.status == "failed" and "401" in t.attempts[0].error


# ------------------------------------------------------------ one orchestrator, two routes
@pytest.mark.asyncio
async def test_asi_and_residual_home_call_the_same_orchestrator():
    from aiohttp.test_utils import TestClient, TestServer
    from residual_agent import Relay, make_http

    ph = SimulatedPhoton()
    o = orch(ph, after=30)
    seen = []
    real = o.handle_intent

    async def spy(intent, context=None):
        seen.append(context["source"])
        return await real(intent, context)
    o.handle_intent = spy
    relay = Relay(o, asi_authorized={"agent1qasi"})
    # ASI route (an authorised sender: strangers wait for approval, tests/test_security.py)
    text = await relay.handle_text("My father has ALS and cannot type. Ask Maya to help reposition him. "
                                   "If she cannot come, try Raj.", "agent1qasi")
    assert text.startswith("I'll contact Maya first.") and "Raj" in text
    # Residual Home route
    async with TestClient(TestServer(make_http(relay, allow_no_auth=True))) as c:
        r = await c.post("/intent", json={"intent": "request_caregiver_help", "need": "repositioning",
                                          "preferred_contact": "Maya", "requires_confirmation": True})
        body = await r.json()
        assert body["order"] == ["Maya", "Raj"]
    assert seen == ["asi", "residual_ui"]
    await until(lambda: len(ph.sent) == 2)
    assert ph.sent[0]["text"] == ph.sent[1]["text"]                        # same workflow, same words


@pytest.mark.asyncio
async def test_asi_session_gets_the_follow_up():
    from residual_agent import Relay

    ph = SimulatedPhoton()
    o = orch(ph, after=30)
    relay = Relay(o, asi_authorized={"agent1qjudge"})
    await relay.handle_text("Ask Maya to help reposition him. If she cannot come, try Raj.", "agent1qjudge")
    await until(lambda: len(ph.sent) == 1)
    o.reply("Maya", "On my way")
    msgs = []
    while not relay.outbox.empty():
        msgs.append(relay.outbox.get_nowait())
    texts = [m[1] for m in msgs]
    assert "Message sent to Maya. (simulated)" in texts
    final = texts[-1]
    assert "Maya replied" in final and "I won't contact Raj" in final
    assert msgs[-1][0] == "agent1qjudge" and msgs[-1][2] is True           # ends the session


@pytest.mark.asyncio
async def test_simulated_replies_can_be_switched_off(monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer
    from residual_agent import Relay, make_http
    monkeypatch.setenv("ALLOW_SIMULATED_REPLIES", "0")
    relay = Relay(orch())
    async with TestClient(TestServer(make_http(relay, allow_no_auth=True))) as c:
        r = await c.post("/simulate/reply", json={"contact": "Maya", "text": "on my way"})
        assert r.status == 403


@pytest.mark.asyncio
async def test_webhook_signature_is_enforced(monkeypatch):
    import base64
    import hashlib
    import hmac
    import time
    from aiohttp.test_utils import TestClient, TestServer
    from residual_agent import Relay, make_http
    secret = base64.b64encode(b"s3cret").decode()
    monkeypatch.setenv("PHOTON_WEBHOOK_SECRET", "whsec_" + secret)
    relay = Relay(orch())
    body = json.dumps({"event": "messages", "message": {"id": "z", "sender": {"id": MAYA},
                                                        "content": {"type": "text", "text": "hi"}}}).encode()
    async with TestClient(TestServer(make_http(relay, local=False))) as c:
        r = await c.post("/photon/webhook", data=body)
        assert r.status == 401
        ts = str(int(time.time()))
        sig = base64.b64encode(hmac.new(b"s3cret", f"wid.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
        r = await c.post("/photon/webhook", data=body, headers={"webhook-id": "wid", "webhook-timestamp": ts,
                                                                 "webhook-signature": f"v1,{sig}"})
        assert r.status == 200


@pytest.mark.asyncio
async def test_chat_protocol_agent_builds_with_discovery_metadata(monkeypatch):
    from residual_agent import DESCRIPTION, KEYWORDS, Relay, build_agent
    monkeypatch.setenv("AGENT_SEED", "a test seed that is long enough to be accepted")
    agent = build_agent(Relay(orch()))
    assert agent.address.startswith("agent1")
    assert "AgentChatProtocol" in " ".join(p.name for p in agent.protocols.values())
    assert "Photon" in DESCRIPTION and "ALS" in KEYWORDS and "caregiver communication" in KEYWORDS


# ------------------------------------------------------------ parsing and replies
def test_parse_the_asi_demo_prompt():
    it = parse("My father has ALS and cannot type. Ask Maya to help reposition him. If she cannot come, try Raj.",
               ["Maya", "Raj"])
    assert isinstance(it, Intent)
    assert it.intent == "request_caregiver_help" and it.need == "repositioning"
    assert it.preferred_contact == "Maya" and it.contacts == ["Raj"]


def test_parse_message_and_unknowns():
    it = parse("Tell Raj that Dev says thank you for today", ["Maya", "Raj"])
    assert it.intent == "send_message" and it.contact == "Raj" and it.message.startswith("Dev says")
    assert "error" in parse("Ask someone to help him", ["Maya", "Raj"])
    assert "error" in parse("Tell Bob hello", ["Maya", "Raj"])


@pytest.mark.parametrize("text,verdict", [("On my way", "ack"), ("omw!", "ack"), ("coming", "ack"), ("No problem, coming", "ack"),
                                          ("👍", "ack"), ("can't come, sorry", "decline"), ("I can't", "decline"),
                                          ("busy at work", "decline"), ("what happened?", "other"), ("", "other")])
def test_reply_classification(text, verdict):
    assert classify_reply(text) == verdict


def test_parse_inbound_shapes():
    spectrum = {"event": "messages", "space": {"id": "any;-;+15555550101", "platform": "iMessage"},
                "message": {"id": "m", "direction": "inbound", "sender": {"id": MAYA},
                            "content": {"type": "text", "text": "On my way"}}}
    assert parse_inbound(spectrum)["sender"] == MAYA
    v2 = {"id": "evt", "type": "message.received", "data": {"message": {"id": "m2", "spaceId": "sp1",
          "sentBy": {"id": "pho_user_1", "handle": MAYA}, "content": {"type": "text", "text": "ok"}}}}
    p = parse_inbound(v2)
    assert p["space_id"] == "sp1" and p["sender"] == MAYA and p["text"] == "ok"
    assert parse_inbound({"event": "messages", "message": {"direction": "outbound",
                          "content": {"type": "text", "text": "x"}}}) is None
    assert parse_inbound({"type": "message.delivered", "data": {"id": "m"}}) is None


def test_bad_signature_rejected():
    assert not verify_signature("whsec_c2VjcmV0", {"webhook-id": "a", "webhook-timestamp": "1", "webhook-signature": "v1,xx"}, b"{}")


# ---------------------------------------------------------------- emergency: message -> call -> next person
@pytest.mark.asyncio
async def test_emergency_message_then_call_then_next_contact():
    from tools.calls import SimulatedCaller
    ph, cl = SimulatedPhoton(), SimulatedCaller()
    o = Orchestrator(policy(0.4), ph, caller=cl)
    res = await o.handle_intent({"intent": "emergency", "need": "breathing", "source": "residual_ui"})
    t = o.tasks[res["task_id"]]
    await until(lambda: len(cl.placed) >= 1)
    assert [m["contact"] for m in ph.sent] == ["Maya"] and cl.placed[0]["contact"] == "Maya"
    await until(lambda: len(ph.sent) >= 2)
    assert ph.sent[1]["contact"] == "Raj"            # Maya: message, then call, then Raj
    o.reply("Raj", "on my way")
    assert t.status == "acknowledged"
    assert t.outcome["relation"] == "son"


@pytest.mark.asyncio
async def test_an_answer_to_the_message_means_no_call():
    from tools.calls import SimulatedCaller
    ph, cl = SimulatedPhoton(), SimulatedCaller()
    o = Orchestrator(policy(1.0), ph, caller=cl)
    res = await o.handle_intent({"intent": "emergency", "source": "residual_ui"})
    await until(lambda: len(ph.sent) >= 1)
    o.reply("Maya", "omw")
    await asyncio.sleep(0.7)
    assert cl.placed == [] and o.tasks[res["task_id"]].status == "acknowledged"


@pytest.mark.asyncio
async def test_help_requests_never_call_only_emergencies():
    from tools.calls import SimulatedCaller
    ph, cl = SimulatedPhoton(), SimulatedCaller()
    o = Orchestrator(policy(0.2), ph, caller=cl)
    await o.handle_intent({"intent": "request_caregiver_help", "need": "repositioning", "source": "residual_ui"})
    await until(lambda: len(ph.sent) >= 2)
    assert cl.placed == []


@pytest.mark.asyncio
async def test_calls_are_idempotent():
    from tools.calls import SimulatedCaller
    cl = SimulatedCaller()
    a = await cl.place_call("Maya", MAYA, "x", "k1")
    b = await cl.place_call("Maya", MAYA, "x", "k1")
    assert len(cl.placed) == 1 and b["replayed"] and a["call_id"] == b["call_id"]


@pytest.mark.asyncio
async def test_acknowledgement_says_who_answered():
    p = policy(0.5)
    p.contacts[0].pronoun = "she"
    o = Orchestrator(p, SimulatedPhoton())
    res = await o.handle_intent({"intent": "emergency", "source": "residual_ui"})
    await until(lambda: len(o.photon.sent) >= 1)
    o.reply("Maya", "coming now")
    out = o.tasks[res["task_id"]].outcome
    assert (out["contact"], out["relation"], out["pronoun"]) == ("Maya", "daughter", "she")
