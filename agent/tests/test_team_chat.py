"""What anyone on ASI:One can say to the Concierge and the Hands, and what they will and won't do for strangers."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from commerce import DEFAULT_PROFILE, Commerce, MockRestaurantAgent, Registry, SandboxPayments  # noqa: E402
from swarm.chat import concierge_chat, hands_chat, parse_errand  # noqa: E402
from swarm.hands import Hands, SimulatedBrowser  # noqa: E402

TRUSTED, STRANGER = "agent1qtrusted", "agent1qstranger"


async def helpers(_):
    return ["Raj"]


async def send(*a):
    return {}


def hands():
    return Hands(SimulatedBrowser(blocked=None), send=send, pick_helpers=helpers, person="Dev", allowed_hosts=["pharmacy.example.com"], helper_wait_s=1)


def test_an_errand_is_a_goal_and_a_site():
    assert parse_errand("Renew Dev's prescription at https://pharmacy.example.com/refill please") == ("Renew Dev's prescription please", "https://pharmacy.example.com/refill")
    assert parse_errand("check opening hours on pharmacy.example.com")[1] == "https://pharmacy.example.com"
    assert parse_errand("do something useful") == ("do something useful", None)
    assert parse_errand("Look up ALS at https://en.wikipedia.org and tell me the first sentence")[0] == "Look up ALS and tell me the first sentence"
    assert parse_errand("Check the hours on pharmacy.example.com, then tell me")[0] == "Check the hours, then tell me"


@pytest.mark.asyncio
async def test_the_hands_start_an_errand_for_the_care_team_only():
    h = hands()
    chat = hands_chat(h, authorized={TRUSTED})
    r = await chat("Renew the prescription at https://pharmacy.example.com/refill", TRUSTED)
    assert "started" in r.lower() or "on it" in r.lower()
    assert len(h.errands) == 1
    r2 = await chat("Renew the prescription at https://pharmacy.example.com/refill", STRANGER)
    assert "care team" in r2 and len(h.errands) == 1


@pytest.mark.asyncio
async def test_the_hands_say_what_they_need_when_the_request_has_no_site():
    chat = hands_chat(hands(), authorized={TRUSTED})
    r = await chat("book me a taxi", TRUSTED)
    assert "site" in r.lower() or "https" in r.lower()


@pytest.mark.asyncio
async def test_a_stranger_can_ask_what_the_hands_do():
    chat = hands_chat(hands(), authorized={TRUSTED})
    r = await chat("what can you do?", STRANGER)
    assert "browser" in r.lower() and "care team" in r


@pytest.mark.asyncio
async def test_status_of_errands_is_for_the_care_team():
    h = hands()
    chat = hands_chat(h, authorized={TRUSTED})
    await chat("Renew at https://pharmacy.example.com/refill", TRUSTED)
    await asyncio.sleep(0.1)
    assert "waiting" in (await chat("status", TRUSTED)).lower() or "confirm" in (await chat("status", TRUSTED)).lower()
    assert "care team" in await chat("status", STRANGER)


@pytest.mark.asyncio
async def test_the_concierge_quotes_without_ordering():
    p = json.loads(json.dumps(DEFAULT_PROFILE))
    com = Commerce(p, Registry([MockRestaurantAgent(), MockRestaurantAgent("green-bowl", "Green Bowl", eta_min=20, fee_cents=0,
                                                                         items=[{"id": "a", "name": "Soup", "price_cents": 700, "tags": ["soup"]}, {"id": "b", "name": "Juice", "price_cents": 400, "tags": ["drink"]}])]),
                   SandboxPayments())

    class Relay:
        orch = type("O", (), {"commerce": com})()

        async def handle_text(self, text, sender):
            return "RELAY:" + text
    chat = concierge_chat(Relay(), authorized={TRUSTED})
    r = await chat("what would dinner cost?", TRUSTED)
    assert "Luna Kitchen" in r and "Green Bowl" in r and com.orders == {}
    assert "care team" in await chat("what would dinner cost?", STRANGER)
    assert (await chat("order dinner", TRUSTED)).startswith("RELAY:")          # real orders go through the Relay's one gate
    assert (await chat("order dinner", STRANGER)).startswith("RELAY:")         # ...which refuses strangers itself


@pytest.mark.asyncio
async def test_a_members_agent_answers_questions_and_takes_updates_with_or_without_the_at_handle():
    from datetime import datetime
    from swarm.agents import member_chat
    from swarm.circle import CaregiverBrain
    ts = datetime(2026, 10, 18, 13, 0).timestamp()
    b = CaregiverBrain("Maya", clock=lambda: ts)
    chat = member_chat(b, "Dev", authorized={TRUSTED})
    assert "in a meeting" not in await chat("@maya-residual is Maya available?", TRUSTED) and b.presence.mode == "free"
    assert "Got it" in await chat("@maya-residual in a meeting till 3", TRUSTED)
    r = await chat("@maya-residual is Maya available?", TRUSTED)
    assert "in a meeting" in r and "3:00 PM" in r
    assert "care team" in await chat("is Maya available?", STRANGER)
