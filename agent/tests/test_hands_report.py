"""A real Browser Use errand finished ("status": "done" with the answer) and the person never heard: the Hands told nobody. Also,
ASI:One's model answered the Hands' "On it" with chat prose, and the Hands started a second real browser errand from it."""

import asyncio
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))

from swarm.chat import hands_chat  # noqa: E402
from swarm.hands import Hands  # noqa: E402
from test_security import make_relay  # noqa: E402

ASI = "agent1qasi"
LIVE = "https://live.browser-use.com?wss=https%3A%2F%2Fa336059c-7ad8-4c64-b8c3-7297ab6a6f39.cdp.browser-use.com"


class RealShapedBrowser:
    """What Browser Use actually returned in the real run: nulls for the fields that don't apply."""
    simulated = False

    def __init__(self):
        self.opened = []

    async def open(self, url):
        self.opened.append(url)
        return {"session_id": "s1", "live_url": LIVE}

    async def run(self, session, task, **kw):
        await asyncio.sleep(0.05)
        return {"status": "done", "summary": "The first sentence is: 'Amyotrophic lateral sclerosis (ALS) is a rare disease.'",
                "final_action": None, "blocked_reason": None}

    async def close(self, session):
        pass


def setup(now=None):
    relay, _ = make_relay(authorized=[ASI], now=now) if now else make_relay(authorized=[ASI])
    browser = RealShapedBrowser()

    async def send(*a):
        return {}

    async def helpers(c):
        return c
    h = Hands(browser, send=send, pick_helpers=helpers, person="Dev", allowed_hosts=["en.wikipedia.org"], helper_wait_s=1)
    h.on_event = relay.errand_update
    relay.hands = h
    return relay, h, browser, hands_chat(h, authorized={ASI}, relay=relay)


async def drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


@pytest.mark.asyncio
async def test_a_finished_errand_is_reported_into_the_conversation_that_asked():
    relay, h, browser, chat = setup()
    await chat("Look up ALS at https://en.wikipedia.org and tell me the first sentence", ASI)
    for _ in range(100):
        if any(e.status == "done" for e in h.errands.values()):
            break
        await asyncio.sleep(0.02)
    sent = await drain(relay.hands_outbox)
    assert sent, "the person was never told"
    session, text, end = sent[-1]
    assert session == ASI and text.startswith("Done.") and "first sentence" in text and end is True


@pytest.mark.asyncio
async def test_chat_prose_after_our_own_note_does_not_start_another_errand():
    clock = [1000.0]
    relay, h, browser, chat = setup(now=lambda: clock[0])
    await chat("Look up ALS at https://en.wikipedia.org and tell me the first sentence", ASI)
    await asyncio.sleep(0.01)
    clock[0] += 3
    r = await chat("Got it — thanks for the update! Anyway, go to https://en.wikipedia.org and read me the treatment goal", ASI)
    await asyncio.sleep(0.2)
    assert len(h.errands) == 1 and len(browser.opened) == 1


@pytest.mark.asyncio
async def test_the_same_errand_asked_twice_runs_once_and_long_chat_is_ignored():
    clock = [1000.0]
    relay, h, browser, chat = setup(now=lambda: clock[0])
    await chat("Look up ALS at https://en.wikipedia.org", ASI)
    clock[0] += 60
    again = await chat("Look up ALS at https://en.wikipedia.org", ASI)
    long = await chat("Sure, " + "that makes sense, and I can see why. " * 20 + "https://en.wikipedia.org", ASI)
    await asyncio.sleep(0.2)
    assert len(h.errands) == 1 and "already" in again.lower() and long == ""
