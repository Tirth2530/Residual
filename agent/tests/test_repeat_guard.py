"""A real iMessage went to a caregiver again and again, even after "On my way". ASI:One's model answers the Relay's progress
notes, and each answer can read as a brand-new request. Whatever the trigger, the Relay must not act twice on the same request:
a caregiver's phone is not something to retry."""

import asyncio
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from test_security import make_relay  # noqa: E402

ASI = "agent1qasi"


async def settle():
    for _ in range(30):
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_the_same_request_twice_in_a_row_sends_one_message():
    clock = [1000.0]
    relay, ph = make_relay(authorized=[ASI], now=lambda: clock[0])
    first = await relay.handle_text("Ask Maya to come and help me", ASI)
    await settle()
    clock[0] += 30
    second = await relay.handle_text("Ask Maya to come and help me", ASI)
    await settle()
    assert first.startswith("I'll contact Maya first")
    assert "already" in second.lower() and "Maya" in second
    assert len(ph.sent) == 1


@pytest.mark.asyncio
async def test_it_still_holds_after_the_caregiver_said_on_my_way():
    """The case from the real run: Maya answered, and a fresh request appeared seconds later anyway."""
    clock = [1000.0]
    relay, ph = make_relay(authorized=[ASI], now=lambda: clock[0])
    await relay.handle_text("Ask Maya to come and help me", ASI)
    await settle()
    relay.orch.reply("Maya", "On my way")
    clock[0] += 30
    again = await relay.handle_text("Ask Maya to come help me", ASI)       # different words, same request
    await settle()
    assert "already" in again.lower()
    assert len(ph.sent) == 1 and len(relay.orch.tasks) == 1


@pytest.mark.asyncio
async def test_a_different_person_is_a_different_request():
    clock = [1000.0]
    relay, ph = make_relay(authorized=[ASI], now=lambda: clock[0])
    await relay.handle_text("Ask Maya to come and help me", ASI)
    clock[0] += 30
    r = await relay.handle_text("Ask Raj to come and help me", ASI)
    await settle()
    assert r.startswith("I'll contact Raj first")
    assert len(relay.orch.tasks) == 2


@pytest.mark.asyncio
async def test_later_on_the_same_request_goes_through_again():
    clock = [1000.0]
    relay, ph = make_relay(authorized=[ASI], now=lambda: clock[0])
    await relay.handle_text("Ask Maya to come and help me", ASI)
    await settle()
    relay.orch.reply("Maya", "On my way")
    clock[0] += 600
    r = await relay.handle_text("Ask Maya to come and help me", ASI)
    assert r.startswith("I'll contact Maya first")
    assert len(relay.orch.tasks) == 2


@pytest.mark.asyncio
async def test_a_repeated_emergency_does_not_alarm_everyone_twice():
    clock = [1000.0]
    relay, ph = make_relay(authorized=[ASI], now=lambda: clock[0])
    await relay.handle_text("Emergency, I can't breathe, alert my family", ASI)
    await settle()
    n = len(ph.sent)
    clock[0] += 30
    r = await relay.handle_text("Emergency, I can't breathe, alert my family", ASI)
    await settle()
    assert n >= 1 and len(ph.sent) == n and "already" in r.lower()


def test_a_chat_message_delivered_twice_is_only_handled_once():
    relay, _ = make_relay(authorized=[ASI])
    assert relay.first_delivery("msg-1") is True
    assert relay.first_delivery("msg-1") is False
    assert relay.first_delivery("msg-2") is True
    assert relay.first_delivery(None) is True                                # nothing to compare: handle it


@pytest.mark.asyncio
async def test_after_one_polite_refusal_a_repeating_sender_gets_silence():
    """Answering every repeat would keep a chatty model talking to us forever."""
    clock = [1000.0]
    relay, ph = make_relay(authorized=[ASI], now=lambda: clock[0])
    await relay.handle_text("Ask Maya to come and help me", ASI)
    clock[0] += 30
    first = await relay.handle_text("Ask Maya to come and help me", ASI)
    rest = [await relay.handle_text("Ask Maya to come and help me", ASI) for _ in range(3)]
    await settle()
    assert "already" in first.lower() and rest == ["", "", ""]
    assert len(ph.sent) == 1


# ------------------------------------------------------------------ ASI:One's model answering the Relay's own notes
PROSE = ("Got it — Maya is on her way, which is great news. That sounds urgent, so if anything else comes up I can help "
         "by contacting Raj too. Let me know whether you want me to stop here or keep going, and whether Maya should "
         "bring anything. " * 2)


@pytest.mark.asyncio
async def test_a_long_chatty_reply_is_never_taken_as_a_command():
    relay, ph = make_relay(authorized=[ASI])
    out = await relay.handle_text(PROSE, ASI)
    await settle()
    assert out == "" and ph.sent == [] and relay.orch.tasks == {}


@pytest.mark.asyncio
async def test_what_comes_right_after_our_own_note_is_not_a_new_command():
    clock = [1000.0]
    relay, ph = make_relay(authorized=[ASI], now=lambda: clock[0])
    await relay.handle_text("Ask Maya to come and help me", ASI)                 # the Relay now pushes notes into the chat
    await settle()
    assert not relay.outbox.empty()
    clock[0] += 5
    r = await relay.handle_text("Maya's on her way, I'll stop here. Raj can help too.", ASI)
    assert "update" in r.lower() or r == ""
    await settle()
    t = next(iter(relay.orch.tasks.values()))
    assert len(ph.sent) == 1 and t.status != "cancelled"                         # nothing sent, and "stop" didn't cancel it


@pytest.mark.asyncio
async def test_an_emergency_word_in_an_echo_never_alarms_anyone_but_points_to_real_help():
    clock = [1000.0]
    relay, ph = make_relay(authorized=[ASI], now=lambda: clock[0])
    await relay.handle_text("Ask Maya to come and help me", ASI)
    await settle()
    n = len(ph.sent)
    clock[0] += 5
    r = await relay.handle_text("That sounds urgent!", ASI)
    await settle()
    assert len(ph.sent) == n and "local emergency number" in r


@pytest.mark.asyncio
async def test_a_real_command_a_little_later_still_works_and_questions_always_do():
    clock = [1000.0]
    relay, ph = make_relay(authorized=[ASI], now=lambda: clock[0])
    await relay.handle_text("Ask Maya to come and help me", ASI)
    await settle()
    clock[0] += 5
    status = await relay.handle_text("Did Maya reply?", ASI)
    assert "Waiting for Maya" in status
    clock[0] += 60
    r = await relay.handle_text("Ask Raj to come and help me", ASI)
    assert r.startswith("I'll contact Raj first")
