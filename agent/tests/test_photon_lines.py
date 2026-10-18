"""Photon 2.0 shared iMessage lines: each caregiver is assigned a line (a number Photon allocates), and the agent a
caregiver talks to is the user for THAT number. So the agent's user id is found per contact, not set once in .env."""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from tools.photon import PhotonClient, PhotonError  # noqa: E402

MAYA, RAJ, STRANGER = "+14155550001", "+14155550002", "+14155550003"
LINE_A, LINE_B = "+14155559001", "+14155559002"


class FakePhoton:
    """The few endpoints the client uses, with the shapes in Photon's OpenAPI spec."""

    def __init__(self, assignments=None, fail=None):
        self.assignments = assignments if assignments is not None else [
            {"assignmentId": "a1", "handle": MAYA, "assignedPhoneNumber": LINE_A},
            {"assignmentId": "a2", "handle": RAJ, "assignedPhoneNumber": LINE_B}]
        self.calls = []
        self.fail = fail or {}

    async def post(self, path, body, headers):
        self.calls.append(("POST", path, body, headers))
        if path in self.fail:
            return self.fail[path], {"title": "Forbidden"}
        if path == "/users":
            return 200, {"id": "usr_" + body["handle"][-4:], "handle": body["handle"], "platform": body["platform"]}
        if path == "/spaces":
            return 200, {"id": "spc_" + "_".join(body["participantIds"])}
        if path == "/messages":
            return 202, {"id": "msg_" + str(len([c for c in self.calls if c[1] == "/messages"])), "status": "accepted"}
        return 404, {}

    async def get(self, path, params=None):
        self.calls.append(("GET", path, params, None))
        if path in self.fail:
            return self.fail[path], {"title": "Forbidden"}
        if path == "/platforms/imessage/assignments":
            return 200, {"assignments": self.assignments, "nextPageToken": ""}
        return 404, {}

    def posted(self, path):
        return [c for c in self.calls if c[0] == "POST" and c[1] == path]


def client(api, tmp_path, agent_user=""):
    return PhotonClient("pho_prj_x", "key", agent_user, state_path=tmp_path / "map.json", post=api.post, get=api.get)


@pytest.mark.asyncio
async def test_the_agent_is_the_user_for_the_contacts_assigned_line(tmp_path):
    api = FakePhoton()
    r = await client(api, tmp_path).send_message("Maya", MAYA, "hello", "k1")
    assert r["status"] == "accepted" and r["simulated"] is False
    handles = [c[2]["handle"] for c in api.posted("/users")]
    assert handles == [LINE_A, MAYA]                                       # the line first (the agent), then the person
    space = api.posted("/spaces")[0][2]
    assert space["participantIds"] == ["usr_9001", "usr_0001"] and space["platform"] == "imessage"   # agent first: it is the creator
    msg = api.posted("/messages")[0][2]
    assert msg["senderId"] == "usr_9001" and msg["spaceId"] == "spc_usr_9001_usr_0001"
    assert msg["content"] == {"type": "text", "format": "plain", "text": "hello"}
    assert api.posted("/messages")[0][3]["idempotency-key"] == "k1"


@pytest.mark.asyncio
async def test_different_contacts_can_be_on_different_lines(tmp_path):
    api = FakePhoton()
    p = client(api, tmp_path)
    await p.send_message("Maya", MAYA, "a", "k1")
    await p.send_message("Raj", RAJ, "b", "k2")
    senders = [c[2]["senderId"] for c in api.posted("/messages")]
    assert senders == ["usr_9001", "usr_9002"]


@pytest.mark.asyncio
async def test_lines_and_spaces_are_looked_up_once_per_contact(tmp_path):
    api = FakePhoton()
    p = client(api, tmp_path)
    await p.send_message("Maya", MAYA, "a", "k1")
    n = len(api.calls)
    await p.send_message("Maya", MAYA, "b", "k2")
    assert [c[1] for c in api.calls[n:]] == ["/messages"]                   # no new assignment, user or space calls
    p2 = client(api, tmp_path)                                              # and it survives a restart
    n = len(api.calls)
    await p2.send_message("Maya", MAYA, "c", "k3")
    assert [c[1] for c in api.calls[n:]] == ["/messages"]


@pytest.mark.asyncio
async def test_a_contact_with_no_assigned_line_gets_a_plain_instruction(tmp_path):
    api = FakePhoton()
    with pytest.raises(PhotonError) as e:
        await client(api, tmp_path).send_message("Stranger", STRANGER, "hi", "k1")
    assert "assign" in str(e.value).lower() and "Stranger" in str(e.value)
    assert api.posted("/messages") == []


@pytest.mark.asyncio
async def test_phone_formats_still_match_their_assignment(tmp_path):
    api = FakePhoton()
    r = await client(api, tmp_path).send_message("Maya", "(415) 555-0001", "hi", "k1")
    assert r["status"] == "accepted"


@pytest.mark.asyncio
async def test_a_released_assignment_is_not_used(tmp_path):
    api = FakePhoton(assignments=[{"assignmentId": "a1", "handle": MAYA, "assignedPhoneNumber": LINE_A, "releasedAt": "2026-10-01T00:00:00Z"}])
    with pytest.raises(PhotonError):
        await client(api, tmp_path).send_message("Maya", MAYA, "hi", "k1")


@pytest.mark.asyncio
async def test_an_explicit_agent_user_still_works_and_skips_the_lookup(tmp_path):
    api = FakePhoton()
    await client(api, tmp_path, agent_user="agent-user").send_message("Maya", MAYA, "hello", "k1")
    assert not [c for c in api.calls if c[0] == "GET"]
    assert api.posted("/messages")[0][2]["senderId"] == "agent-user"


@pytest.mark.asyncio
async def test_a_missing_permission_says_which_and_never_sends(tmp_path):
    api = FakePhoton(fail={"/platforms/imessage/assignments": 403})
    with pytest.raises(PhotonError) as e:
        await client(api, tmp_path).send_message("Maya", MAYA, "hi", "k1")
    assert "platforms:read" in str(e.value)
    api2 = FakePhoton(fail={"/users": 403})
    with pytest.raises(PhotonError) as e2:
        await client(api2, tmp_path / "b").send_message("Maya", MAYA, "hi", "k1")
    assert "project:write" in str(e2.value)
    assert api.posted("/messages") == [] and api2.posted("/messages") == []


@pytest.mark.asyncio
async def test_a_send_that_was_accepted_is_never_sent_twice(tmp_path):
    api = FakePhoton()
    p = client(api, tmp_path)
    await p.send_message("Maya", MAYA, "hello", "same-key")
    r = await p.send_message("Maya", MAYA, "hello", "same-key")
    assert r.get("replayed") is True and len(api.posted("/messages")) == 1


def test_credentials_need_a_project_and_a_key_but_not_an_agent_user():
    PhotonClient("pho_prj_x", "key", "")                                    # fine now
    with pytest.raises(PhotonError):
        PhotonClient("", "key", "")
    with pytest.raises(PhotonError):
        PhotonClient("pho_prj_x", "", "")


# ------------------------------------------------------------------ inbound events, in the shape Photon's spec defines
from tools.photon import parse_inbound  # noqa: E402


def event(kind="message.received", status="received", text="On my way", space="pho_spc_1", sender=MAYA):
    return {"id": "evt_1", "type": kind, "apiVersion": "2026-07-01", "source": "imessage.photon", "timestamp": "2026-10-18T04:00:00Z",
            "data": {"projectId": "pho_prj_x", "object": {
                "id": "pho_msg_9", "platform": "imessage", "status": status, "projectId": "pho_prj_x",
                "content": {"type": "text", "format": "plain", "text": text},
                "space": {"id": space}, "sentBy": {"id": "pho_usr_1", "handle": sender}, "receivedBy": {"id": "pho_usr_9"}}}}


def test_a_caregivers_reply_is_read_from_a_message_received_event():
    m = parse_inbound(event())
    assert m and m["text"] == "On my way" and m["space_id"] == "pho_spc_1" and m["sender"] == MAYA and m["message_id"] == "pho_msg_9"


def test_events_about_our_own_messages_are_never_read_as_replies():
    for kind, status in [("message.failed", "failed"), ("message.accepted", "accepted"), ("message.dispatched", "dispatched"),
                         ("message.delivered", "delivered"), ("message.sent", "sent")]:
        assert parse_inbound(event(kind, status, text="Dev needs help repositioning. Can you come?")) is None, kind
    assert parse_inbound({"type": "space.updated", "data": {"projectId": "p", "object": {"id": "pho_spc_1"}}}) is None


def test_a_status_alone_is_enough_to_refuse_an_outbound_echo():
    p = event()
    del p["type"]                                   # an envelope without a type, but the message says what it is
    p["data"]["object"]["status"] = "dispatched"
    assert parse_inbound(p) is None
    assert parse_inbound(event(status="received")) is not None


@pytest.mark.asyncio
async def test_a_reply_finds_its_contact_by_space_and_acknowledges(tmp_path):
    """From a real send to a reply that stops the escalation, with the fake Photon API."""
    from orchestrator import Orchestrator
    from policy import Contact, Policy
    api = FakePhoton()
    ph = client(api, tmp_path)
    pol = Policy(person="Dev", contacts=[Contact("Maya", MAYA, "daughter"), Contact("Raj", RAJ, "son")],
                 emergency_order=["Maya", "Raj"], caregiver_order=["Maya", "Raj"], escalate_after_s=30, emergency_after_s=30, demo=False)
    o = Orchestrator(pol, ph)
    res = await o.handle_intent({"intent": "request_caregiver_help", "need": "repositioning", "preferred_contact": "Maya",
                                 "requires_confirmation": True, "source": "residual_ui"}, {"source": "residual_ui"})
    for _ in range(100):
        if api.posted("/messages"):
            break
        import asyncio
        await asyncio.sleep(0.01)
    space = api.posted("/messages")[0][2]["spaceId"]
    out = await o.on_inbound(event(space=space, text="On my way"))
    assert out["matched"] and out["verdict"] == "ack" and o.tasks[res["task_id"]].status == "acknowledged"
    # an echo of our own message must not acknowledge anything
    res2 = await o.handle_intent({"intent": "request_caregiver_help", "preferred_contact": "Raj", "source": "residual_ui"}, {"source": "residual_ui"})
    echo = await o.on_inbound(event("message.failed", "failed", space="spc_usr_9002_usr_0002", text="Dev needs help. Can you come?"))
    assert echo["matched"] is False
