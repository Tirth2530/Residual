"""Before a real Photon account or a public Agentverse listing: who can reach what.

  1. the local API needs a token (a web page in the person's browser can't use it) and the right Host
  2. the Photon webhook is its own small server, and live mode refuses to start unsigned
  3. strangers on ASI:One can ask, but nothing is sent until Dev approves it on Residual's screen
  4. approval codes are random, six digits, and can't be guessed by trying
  5. the agent's identity (AGENT_SEED) can't be left to chance
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from orchestrator import Orchestrator  # noqa: E402
from policy import Contact, Policy  # noqa: E402
from tools.photon import SimulatedPhoton  # noqa: E402

TOKEN = "t" * 43
MAYA, RAJ = "+15555550101", "+15555550102"


def make_orch(ph=None, after=30):
    pol = Policy(person="Dev", contacts=[Contact("Maya", MAYA, "daughter"), Contact("Raj", RAJ, "son")],
                 emergency_order=["Maya", "Raj"], caregiver_order=["Maya", "Raj"],
                 escalate_after_s=after, emergency_after_s=after, demo=False, max_emergency_rounds=2)
    return Orchestrator(pol, ph or SimulatedPhoton())


def make_relay(authorized=(), mode="approve", ph=None, now=None):
    from residual_agent import Relay
    ph = ph or SimulatedPhoton()
    kw = {"now": now} if now else {}
    return Relay(make_orch(ph), asi_authorized=set(authorized), asi_mode=mode, **kw), ph


# ------------------------------------------------------------------ helpers
def test_helpers_codes_tokens_hosts_limits(tmp_path):
    import security as sec
    codes = {sec.new_code() for _ in range(200)}
    assert all(len(c) == 6 and c.isdigit() for c in codes) and len(codes) > 150
    t = sec.ensure_token(tmp_path, {})
    assert len(t) >= 32 and sec.ensure_token(tmp_path, {}) == t                      # kept between runs
    assert (tmp_path / "local_token").stat().st_mode & 0o077 == 0                    # readable by the owner only
    assert sec.ensure_token(tmp_path, {"RESIDUAL_LOCAL_TOKEN": "x" * 40}) == "x" * 40
    assert sec.token_ok(t, t) and not sec.token_ok("", t) and not sec.token_ok(t, "") and not sec.token_ok("nope", t)
    assert sec.host_ok("127.0.0.1:8100", 8100) and sec.host_ok("localhost:8100", 8100) and sec.host_ok("[::1]:8100", 8100)
    assert not sec.host_ok("evil.example:8100", 8100) and not sec.host_ok("127.0.0.1.evil.example", 8100)
    assert not sec.host_ok("127.0.0.1:9999", 8100) and not sec.host_ok("", 8100)
    clock = [0.0]
    rl = sec.RateLimiter(3, 60, now=lambda: clock[0])
    assert [rl.allow("a") for _ in range(4)] == [True, True, True, False] and rl.allow("b")
    clock[0] = 61
    assert rl.allow("a")


def test_loopback_only_and_live_webhook_config():
    import security as sec
    sec.require_loopback("127.0.0.1")
    sec.require_loopback("localhost")
    with pytest.raises(sec.ConfigError):
        sec.require_loopback("0.0.0.0")
    with pytest.raises(sec.ConfigError):
        sec.check_webhook_config(live=True, secret="")
    sec.check_webhook_config(live=True, secret="whsec_abc")
    sec.check_webhook_config(live=False, secret="")


def test_seed_is_required_so_the_agent_keeps_its_address():
    import security as sec
    with pytest.raises(sec.ConfigError):
        sec.require_seed({})
    with pytest.raises(sec.ConfigError):
        sec.require_seed({"AGENT_SEED": "short"})
    assert sec.require_seed({"AGENT_SEED": "a long random phrase that is mine alone"}) == "a long random phrase that is mine alone"
    assert sec.require_seed({"ALLOW_EPHEMERAL_AGENT": "1"}) is None


def test_the_agent_will_not_build_without_a_seed(monkeypatch):
    from residual_agent import Relay, build_agent
    import security as sec
    monkeypatch.delenv("AGENT_SEED", raising=False)
    monkeypatch.delenv("ALLOW_EPHEMERAL_AGENT", raising=False)
    with pytest.raises(sec.ConfigError):
        build_agent(Relay(make_orch()))


# ------------------------------------------------------------------ 1. the local API
@pytest.mark.asyncio
async def test_local_api_needs_the_token_and_a_local_host():
    from aiohttp.test_utils import TestClient, TestServer
    from residual_agent import make_http
    relay, ph = make_relay()
    async with TestClient(TestServer(make_http(relay, token=TOKEN, webhook=False))) as c:
        assert (await c.get("/tasks")).status == 401
        assert (await c.get("/tasks", headers={"X-Residual-Token": "wrong"})).status == 401
        assert (await c.get("/tasks", headers={"X-Residual-Token": TOKEN})).status == 200
        # what a web page in the person's browser can do: a "simple" cross-site POST with no custom header
        r = await c.post("/intent", data=json.dumps({"intent": "request_caregiver_help", "need": "general"}),
                         headers={"Content-Type": "text/plain", "Origin": "https://evil.example"})
        assert r.status == 401
        assert not relay.orch.tasks and ph.sent == []
        # DNS rebinding: a hostile name pointing at 127.0.0.1 still arrives with its own Host header
        r = await c.get("/tasks", headers={"X-Residual-Token": TOKEN, "Host": "evil.example"})
        assert r.status == 403
        # the care profile and audit trail aren't public either
        for path in ("/care/profile", "/audit", "/orders"):
            assert (await c.get(path)).status in (401, 404)


@pytest.mark.asyncio
async def test_health_tells_strangers_nothing_but_that_it_is_up():
    from aiohttp.test_utils import TestClient, TestServer
    from residual_agent import make_http
    relay, _ = make_relay()
    async with TestClient(TestServer(make_http(relay, token=TOKEN, webhook=False))) as c:
        bare = await (await c.get("/health")).json()
        assert bare == {"ok": True}
        full = await (await c.get("/health", headers={"X-Residual-Token": TOKEN})).json()
        assert "contacts" in full and "photon" in full


@pytest.mark.asyncio
async def test_the_authorised_app_can_still_do_everything():
    from aiohttp.test_utils import TestClient, TestServer
    from residual_agent import make_http
    relay, ph = make_relay()
    H = {"X-Residual-Token": TOKEN}
    async with TestClient(TestServer(make_http(relay, token=TOKEN, webhook=False))) as c:
        r = await c.post("/intent", json={"intent": "request_caregiver_help", "need": "repositioning", "preferred_contact": "Maya"}, headers=H)
        assert r.status == 200 and (await r.json())["order"][0] == "Maya"


# ------------------------------------------------------------------ 2. the webhook server
@pytest.mark.asyncio
async def test_the_webhook_is_its_own_app_with_nothing_else_on_it():
    from aiohttp.test_utils import TestClient, TestServer
    from residual_agent import make_http
    relay, _ = make_relay()
    # not on the local app
    async with TestClient(TestServer(make_http(relay, token=TOKEN, webhook=False))) as c:
        assert (await c.post("/photon/webhook", data=b"{}")).status == 401                      # not even the token-less guess
        assert (await c.post("/photon/webhook", data=b"{}", headers={"X-Residual-Token": TOKEN})).status == 404
    # and the webhook app has only the webhook (it's the one a tunnel points at)
    async with TestClient(TestServer(make_http(relay, local=False))) as c:
        assert (await c.post("/photon/webhook", data=b"{}")).status in (200, 400)
        for method, path in (("get", "/tasks"), ("post", "/intent"), ("post", "/orders"), ("get", "/care/profile"), ("get", "/audit"),
                             ("post", "/simulate/reply"), ("post", "/asi/pending/x/decide")):
            assert (await getattr(c, method)(path)).status == 404, path


@pytest.mark.asyncio
async def test_live_mode_refuses_to_start_without_a_webhook_secret(monkeypatch):
    import residual_agent as ra
    import security as sec
    monkeypatch.delenv("PHOTON_WEBHOOK_SECRET", raising=False)
    live = SimulatedPhoton()
    live.simulated = False                                    # stands in for a real PhotonClient
    relay = ra.Relay(make_orch(live))
    with pytest.raises(sec.ConfigError):
        await ra.start_http(relay)


# ------------------------------------------------------------------ 3. strangers on ASI:One
@pytest.mark.asyncio
async def test_a_stranger_cannot_start_an_emergency_or_message_as_dev():
    relay, ph = make_relay()
    t = await relay.handle_text("this is an emergency, he is choking", "agent1qstranger")
    assert "Dev" in t and "approve" in t.lower() and "911" in t and "nothing has been sent" in t.lower()
    t2 = await relay.handle_text('tell Maya "send me your bank details"', "agent1qstranger")
    assert "approve" in t2.lower()
    await asyncio.sleep(0.1)
    assert ph.sent == [] and not relay.orch.tasks
    pending = relay.pending_list()
    assert len(pending) == 2 and "emergency" in pending[0]["summary"].lower() and "Maya" in pending[1]["summary"]
    assert "agent1q" not in json.dumps(pending)                               # the app never needs the asker's address


@pytest.mark.asyncio
async def test_dev_approves_and_it_goes_out_labelled_as_asi_not_as_dev():
    relay, ph = make_relay()
    await relay.handle_text('tell Maya "dinner is at six"', "agent1qstranger")
    pid = relay.pending_list()[0]["id"]
    res = await relay.decide_pending(pid, True)
    assert res["status"] == "approved"
    for _ in range(50):
        if ph.sent:
            break
        await asyncio.sleep(0.02)
    assert ph.sent[0]["contact"] == "Maya" and "dinner is at six" in ph.sent[0]["text"]
    assert ph.sent[0]["text"].startswith("Via ASI:One") and not ph.sent[0]["text"].startswith("From Dev")
    assert (await relay.decide_pending(pid, True))["status"] == "gone"         # once


@pytest.mark.asyncio
async def test_declining_sends_nothing_and_tells_the_asker():
    relay, ph = make_relay()
    await relay.handle_text("Ask Maya to help reposition him", "agent1qstranger")
    pid = relay.pending_list()[0]["id"]
    assert (await relay.decide_pending(pid, False))["status"] == "declined"
    await asyncio.sleep(0.1)
    assert ph.sent == []
    msgs = []
    while not relay.outbox.empty():
        msgs.append(relay.outbox.get_nowait())
    assert msgs[-1][0] == "agent1qstranger" and msgs[-1][2] is True and "nothing was sent" in msgs[-1][1].lower()


@pytest.mark.asyncio
async def test_unanswered_requests_expire():
    clock = [1000.0]
    relay, ph = make_relay(now=lambda: clock[0])
    await relay.handle_text("Ask Maya to help reposition him", "agent1qstranger")
    pid = relay.pending_list()[0]["id"]
    clock[0] += 11 * 60
    assert relay.pending_list() == []
    assert (await relay.decide_pending(pid, True))["status"] == "gone" and ph.sent == []


@pytest.mark.asyncio
async def test_authorised_senders_work_as_before_and_strangers_are_rate_limited():
    relay, ph = make_relay(authorized={"agent1qdev"})
    t = await relay.handle_text("Ask Maya to help reposition him. If she cannot come, try Raj.", "agent1qdev")
    assert t.startswith("I'll contact Maya first.")
    for _ in range(50):
        if ph.sent:
            break
        await asyncio.sleep(0.02)
    assert len(ph.sent) == 1
    replies = [await relay.handle_text("Ask Maya to help", "agent1qspam") for _ in range(9)]
    assert any("wait" in r.lower() for r in replies[6:])
    assert len(relay.pending_list()) <= 5                                       # and never a pile on Dev's screen
    assert "wait" not in (await relay.handle_text("Ask Maya to help reposition him", "agent1qdev")).lower()


@pytest.mark.asyncio
async def test_the_gate_can_be_closed_completely():
    relay, ph = make_relay(mode="off")
    t = await relay.handle_text("Ask Maya to help reposition him", "agent1qstranger")
    assert "authoris" in t.lower() and relay.pending_list() == [] and ph.sent == []


@pytest.mark.asyncio
async def test_strangers_can_still_ask_for_status_and_unknown_names_are_refused_up_front():
    relay, ph = make_relay()
    t = await relay.handle_text("status", "agent1qstranger")
    assert "no request" in t.lower()
    t = await relay.handle_text("tell Stranger hello", "agent1qstranger")
    assert "Stranger" in t and "isn't on the care team's list" in t and relay.pending_list() == []


@pytest.mark.asyncio
async def test_the_app_decides_over_http_with_the_token():
    from aiohttp.test_utils import TestClient, TestServer
    from residual_agent import make_http
    relay, ph = make_relay()
    H = {"X-Residual-Token": TOKEN}
    await relay.handle_text("Ask Maya to help reposition him", "agent1qstranger")
    async with TestClient(TestServer(make_http(relay, token=TOKEN, webhook=False))) as c:
        assert (await c.get("/asi/pending")).status == 401
        lst = await (await c.get("/asi/pending", headers=H)).json()
        assert len(lst) == 1 and "Maya" in lst[0]["summary"]
        assert (await c.post("/asi/pending/nope/decide", json={"approve": True}, headers=H)).status == 404
        r = await c.post(f"/asi/pending/{lst[0]['id']}/decide", json={"approve": True}, headers=H)
        assert (await r.json())["status"] == "approved"


# ------------------------------------------------------------------ 4. approval codes
@pytest.mark.asyncio
async def test_approval_codes_are_six_digits_and_wrong_guesses_lock_the_approver_out():
    from commerce import Commerce, MockRestaurantAgent, Registry, SandboxPayments, DEFAULT_PROFILE
    import copy
    prof = copy.deepcopy(DEFAULT_PROFILE)
    prof["spending"].update(per_order_cap_cents=1000, approver="Maya")
    t = [5000.0]
    sent = []

    async def send(contact, text, key):
        sent.append(text)
        return {"ok": True}
    c = Commerce(prof, Registry([MockRestaurantAgent()]), SandboxPayments(), send=send, now=lambda: t[0])
    o = await c.propose("usual")
    await c.confirm(o["id"], "residual_ui", True)
    code = c.orders[o["id"]]["approval_code"]
    assert len(code) == 6 and code.isdigit() and f"Reply YES {code}" in sent[0]
    wrong = [f"{(int(code) + k) % 1000000:06d}" for k in range(1, 6)]
    for w in wrong:
        assert c.try_reply("Maya", f"YES {w}")["matched"] is False
    locked = c.try_reply("Maya", f"YES {code}")                                # even the right code, now
    assert locked["matched"] is False and "too many" in locked["why"].lower()
    assert c.orders[o["id"]]["status"] == "awaiting_approval"
    t[0] += 16 * 60
    assert c.try_reply("Maya", f"YES {code}")["status"] == "approved"


@pytest.mark.asyncio
async def test_four_digit_codes_are_no_longer_accepted():
    from commerce import Commerce, MockRestaurantAgent, Registry, SandboxPayments, DEFAULT_PROFILE
    import copy
    prof = copy.deepcopy(DEFAULT_PROFILE)
    prof["spending"].update(per_order_cap_cents=1000, approver="Maya")
    c = Commerce(prof, Registry([MockRestaurantAgent()]), SandboxPayments(), send=None)
    o = await c.propose("usual")
    await c.confirm(o["id"], "residual_ui", True)
    code = c.orders[o["id"]]["approval_code"]
    assert c.try_reply("Maya", f"YES {code[:4]}") is None


# ------------------------------------------------------------------ what a stranger is told (Agentverse's generated test questions)
NAMES = ["Maya", "Raj"]


def test_an_unknown_contact_is_refused_by_name_with_who_can_be_contacted():
    from parser import parse
    for text, who in [("Ask Priya to bring my medication when she has a minute.", "Priya"),
                      ("Contact Sam to help me get dressed. If he doesn't answer, try Maya.", "Sam"),
                      ("Tell Tom that dinner is ready", "Tom")]:
        r = parse(text, NAMES)
        assert isinstance(r, dict), text
        assert who in r["error"] and "Maya" in r["error"] and "Raj" in r["error"], r["error"]


def test_known_names_and_pronouns_are_not_mistaken_for_strangers():
    from parser import parse
    for text in ["Ask Maya to help reposition him. If she cannot come, try Raj.", "Tell Raj that Dev says thank you for today.",
                 "Please tell me what happened", "Ask her to come", "Alert everyone", "Contact someone to help"]:
        r = parse(text, NAMES, person="Dev")
        assert not (isinstance(r, dict) and "isn't one of" in r["error"]), (text, r)


def test_an_emergency_is_an_emergency_even_if_it_names_a_stranger():
    from parser import parse
    r = parse("This is an emergency, contact Sam, I have fallen", NAMES)
    assert not isinstance(r, dict) and r.intent == "emergency"


def test_asking_to_change_the_contact_list_is_refused_plainly():
    from parser import parse
    for text in ["Can you add my neighbor Tom to my contact list so you can reach him?", "add Priya to the care team", "Please include my sister as a contact"]:
        r = parse(text, NAMES)
        assert isinstance(r, dict) and "can't add" in r["error"].lower() and "care team" in r["error"].lower(), (text, r)


def test_status_questions_in_ordinary_words_are_status_questions():
    from parser import parse
    for text in ["Has anyone responded to my request from earlier? Tell me what they said.", "Did anyone answer?", "Have they replied yet?",
                 "Any news from Maya?", "has anyone heard back", "status"]:
        r = parse(text, NAMES)
        assert not isinstance(r, dict) and r.intent == "get_status", text


@pytest.mark.asyncio
async def test_a_test_run_of_harmless_questions_is_not_rate_limited_but_actions_still_are():
    relay, ph = make_relay()
    for i in range(25):
        t = await relay.handle_text("status", "agent1qtester")
        assert "lot of requests" not in t, i
    t = ""
    for i in range(40):
        t = await relay.handle_text("status", "agent1qtester")
    assert "lot of requests" in t                                        # a flood of anything is still stopped
    # and the strict limit on things that wait for Dev is separate: five status checks don't use it up
    relay2, _ = make_relay()
    for _ in range(5):
        await relay2.handle_text("status", "agent1qtester")
    assert "approve" in (await relay2.handle_text("Ask Maya to help reposition him", "agent1qtester")).lower()


@pytest.mark.asyncio
async def test_the_five_generated_questions_each_get_a_sensible_answer():
    relay, ph = make_relay()
    qs = ["Ask Priya to bring my medication when she has a minute.",
          "Contact Sam to help me get dressed. If he doesn't answer, try Maya.",
          "This is an emergency — I've fallen and can't get up. Alert my contacts.",
          "Has anyone responded to my request from earlier? Tell me what they said.",
          "Can you add my neighbor Tom to my contact list so you can reach him?"]
    a = [await relay.handle_text(q, "agent1qagentverse") for q in qs]
    assert "Priya" in a[0] and "Maya" in a[0]
    assert "Sam" in a[1] and "Maya" in a[1]
    assert "approve" in a[2].lower() and "911" in a[2] and "nothing has been sent" in a[2].lower()
    assert "no request" in a[3].lower()
    assert "can't add" in a[4].lower()
    await asyncio.sleep(0.1)
    assert ph.sent == []


# ------------------------------------------------------------------ not nagging the person
@pytest.mark.asyncio
async def test_the_same_request_twice_is_one_question_for_dev():
    relay, ph = make_relay()
    a = await relay.handle_text("Ask Maya to help reposition him", "agent1qtester")
    b = await relay.handle_text("Ask Maya to help reposition him", "agent1qtester")
    assert "approve" in a.lower() and "already asked" in b.lower() and "nothing has been sent" in b.lower()
    assert len(relay.pending_list()) == 1
    # a different request, or a different sender, is its own question
    await relay.handle_text("Ask Raj to help reposition him", "agent1qtester")
    await relay.handle_text("Ask Maya to help reposition him", "agent1qsomeoneelse")
    assert len(relay.pending_list()) == 3


@pytest.mark.asyncio
async def test_a_decided_or_expired_request_can_be_asked_again():
    clock = [1000.0]
    relay, ph = make_relay(now=lambda: clock[0])
    await relay.handle_text("Ask Maya to help reposition him", "agent1qtester")
    await relay.decide_pending(relay.pending_list()[0]["id"], False)
    assert "already asked" not in (await relay.handle_text("Ask Maya to help reposition him", "agent1qtester")).lower()
    clock[0] += 11 * 60
    assert "already asked" not in (await relay.handle_text("Ask Maya to help reposition him", "agent1qtester")).lower()


@pytest.mark.asyncio
async def test_summaries_read_naturally_after_wants_residual_to():
    relay, _ = make_relay()
    await relay.handle_text("this is an emergency, he is choking", "agent1qa")
    await relay.handle_text("Ask Maya to help reposition him", "agent1qb")
    await relay.handle_text('tell Raj "dinner is at six"', "agent1qc")
    s = [p["summary"] for p in relay.pending_list()]
    assert s[0].startswith("send an EMERGENCY alert to Maya")
    assert s[1].startswith("ask Maya for help") and "repositioning" in s[1]
    assert s[2].startswith("message Raj: ")


def test_tests_cannot_see_the_real_env_file_or_real_keys():
    import os
    import residual_agent as ra
    assert str(ra._env_path()).startswith("/nonexistent")
    ra._env()                                                              # a no-op: there is no such file
    assert not os.environ.get("PHOTON_API_KEY") and not os.environ.get("PHOTON_WEBHOOK_SECRET") and not os.environ.get("AGENT_SEED")


def test_the_real_env_path_is_the_default_outside_tests(monkeypatch):
    import residual_agent as ra
    monkeypatch.delenv("RESIDUAL_ENV_FILE", raising=False)
    assert ra._env_path() == ra.HERE / ".env"


# ------------------------------------------------------------------ closed to strangers (the default)
def closed_relay(authorized=(), ph=None):
    from residual_agent import Relay
    ph = ph or SimulatedPhoton()
    return Relay(make_orch(ph), asi_authorized=set(authorized)), ph             # no asi_mode given: the default


@pytest.mark.asyncio
async def test_the_default_is_closed_strangers_cannot_make_it_act_at_all():
    relay, ph = closed_relay()
    assert relay.asi_mode == "closed"
    for text in ["Ask Maya to help reposition him", "this is an emergency, he is choking", 'tell Raj "hello"']:
        t = await relay.handle_text(text, "agent1qstranger")
        assert "authorised" in t.lower() and "nothing" in t.lower() or "haven't sent" in t.lower(), t
    assert relay.pending_list() == [] and ph.sent == [] and not relay.orch.tasks
    await asyncio.sleep(0.1)
    assert ph.sent == []


@pytest.mark.asyncio
async def test_closed_still_answers_harmless_questions_and_says_why_it_refused():
    relay, _ = closed_relay()
    assert "no request" in (await relay.handle_text("status", "agent1qstranger")).lower()
    assert "isn't on the care team's list" in await relay.handle_text("Ask Priya to bring my medication", "agent1qstranger")
    assert "can't add" in (await relay.handle_text("add my neighbor Tom to my contact list", "agent1qstranger")).lower()
    t = await relay.handle_text("this is an emergency", "agent1qstranger")
    assert "emergency" in t.lower() and ("911" in t or "emergency number" in t.lower())      # never leave someone in trouble with nothing


@pytest.mark.asyncio
async def test_a_listed_sender_works_in_the_closed_default():
    relay, ph = closed_relay(authorized={"agent1qdev"})
    t = await relay.handle_text("Ask Maya to help reposition him. If she cannot come, try Raj.", "agent1qdev")
    assert t.startswith("I'll contact Maya first.")
    for _ in range(50):
        if ph.sent:
            break
        await asyncio.sleep(0.02)
    assert len(ph.sent) == 1
    # the env var works too, and "off" is the same as "closed"
    import os
    os.environ["ASI_AUTHORIZED"] = "agent1qa, agent1qb"
    try:
        from residual_agent import Relay
        r = Relay(make_orch(), asi_mode="off")
        assert r.asi_mode == "closed" and r.authorized >= {"agent1qa", "agent1qb"}
    finally:
        del os.environ["ASI_AUTHORIZED"]


@pytest.mark.asyncio
async def test_each_sender_address_is_logged_once_so_the_owner_can_find_their_own(caplog):
    import logging
    relay, _ = closed_relay(authorized={"agent1qdev"})
    with caplog.at_level(logging.INFO, logger="residual.agent"):
        await relay.handle_text("status", "agent1qdev")
        await relay.handle_text("status", "agent1qdev")
        await relay.handle_text("Ask Maya to help", "agent1qstranger")
    lines = [r.getMessage() for r in caplog.records if "ASI sender" in r.getMessage()]
    assert len(lines) == 2                                                        # once per sender, not once per message
    assert any("agent1qdev" in l and "authorised" in l for l in lines)
    assert any("agent1qstranger" in l and "not authorised" in l for l in lines)
    assert not any("Ask Maya" in r.getMessage() for r in caplog.records)           # what people typed is never logged


@pytest.mark.asyncio
async def test_health_reports_closed_and_nothing_is_ever_pending_for_the_screen():
    from aiohttp.test_utils import TestClient, TestServer
    from residual_agent import make_http
    relay, _ = closed_relay()
    await relay.handle_text("Ask Maya to help", "agent1qstranger")
    async with TestClient(TestServer(make_http(relay, token=TOKEN, webhook=False))) as c:
        h = await (await c.get("/health", headers={"X-Residual-Token": TOKEN})).json()
        assert h["asi_mode"] == "closed" and h["asi_pending"] == 0


@pytest.mark.asyncio
async def test_closed_means_strangers_cannot_prepare_orders_either(tmp_path):
    import residual_agent as ra
    relay, ph = closed_relay(authorized={"agent1qdev"})
    relay.orch.commerce = ra.build_commerce(relay.orch, tmp_path)
    t = await relay.handle_text("Order Dev's usual lunch please", "agent1qstranger")
    assert "authorised" in t.lower() and relay.orch.commerce.orders == {}               # nothing prepared, so nothing for Dev's screen
    t = await relay.handle_text("confirm the order", "agent1qstranger")
    assert "authorised" in t.lower()
    ok = await relay.handle_text("Order Dev's usual lunch please", "agent1qdev")          # the care team still can
    assert "Nothing is paid until Dev confirms" in ok and len(relay.orch.commerce.orders) == 1
