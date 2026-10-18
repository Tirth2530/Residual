"""The caregiver agents' brains: what a family member's texts mean, how each agent bids on a call for help, and how the
circle shares the load. Pure logic, no network."""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from swarm.circle import CaregiverBrain, Ledger, choose, parse_status  # noqa: E402

NOW = datetime(2026, 10, 18, 13, 0)                       # a Sunday, 1 pm
TS = NOW.timestamp()


def brain(name="Maya", **kw):
    return CaregiverBrain(name, clock=lambda: TS, **kw)


def call(need="repositioning", urgency="normal"):
    return {"call_id": "c1", "person": "Dev", "need": need, "urgency": urgency, "text": f"Dev needs help ({need})"}


# ------------------------------------------------------------------ reading a family member's text
def test_a_meeting_until_a_time():
    u = parse_status("in a meeting till 3", NOW)
    assert u and u["mode"] == "busy" and u["until"] == datetime(2026, 10, 18, 15, 0)
    assert "3:00 PM" in u["ack"]


def test_times_in_several_forms():
    assert parse_status("busy until 3:30pm", NOW)["until"] == datetime(2026, 10, 18, 15, 30)
    assert parse_status("at work until 17:45", NOW)["until"] == datetime(2026, 10, 18, 17, 45)
    assert parse_status("out for 2 hours", NOW)["until"] == NOW + timedelta(hours=2)
    assert parse_status("busy for 45 min", NOW)["until"] == NOW + timedelta(minutes=45)


def test_a_time_already_passed_means_tomorrow():
    assert parse_status("sleeping until 7am", NOW)["until"] == datetime(2026, 10, 19, 7, 0)


def test_driving_asleep_away_and_free():
    assert parse_status("driving", NOW)["mode"] == "driving"
    a = parse_status("going to bed", NOW)
    assert a["mode"] == "asleep" and a["until"].hour == 7
    assert parse_status("I'm out of town this week", NOW)["mode"] == "away"
    f = parse_status("I'm free now", NOW)
    assert f["mode"] == "free" and f["until"] is None


def test_distance_and_lifting():
    u = parse_status("I'm 10 min away if needed", NOW)
    assert u["eta_min"] == 10 and u["mode"] == "free"
    assert parse_status("hurt my back, can't lift today", NOW)["can_lift"] is False
    assert parse_status("back to normal, I can lift", NOW)["can_lift"] is True


def test_ordinary_chatter_is_not_a_status():
    assert parse_status("lol ok see you", NOW) is None
    assert parse_status("", NOW) is None
    assert parse_status("what time is it", NOW) is None


# ------------------------------------------------------------------ bidding
def test_a_free_member_bids_with_her_distance():
    b = brain(base_eta_min=15)
    b.hear("home, 8 min away")
    bid = b.bid(call())
    assert bid["can_help"] is True and bid["eta_min"] == 8 and bid["member"] == "Maya"
    assert "8 min" in bid["reason"]


def test_in_a_meeting_does_not_bid_for_ordinary_help_but_would_for_urgent():
    b = brain()
    b.hear("in a meeting till 3")
    normal = b.bid(call())
    assert normal["can_help"] is False and "meeting" in normal["reason"] and "3:00 PM" in normal["reason"]
    urgent = b.bid(call(urgency="urgent"))
    assert urgent["can_help"] is True and urgent["interrupt_cost"] > 0.5


def test_asleep_and_driving_protect_the_person():
    b = brain(); b.hear("sleeping until 7am")
    assert b.bid(call())["can_help"] is False
    assert b.bid(call(urgency="urgent"))["can_help"] is True        # an urgent need does wake her
    d = brain(); d.hear("driving")
    assert d.bid(call(urgency="urgent"))["can_help"] is False        # never asked to text while driving


def test_a_member_who_cant_lift_declines_lifting_but_can_fetch():
    b = brain(); b.hear("hurt my back, can't lift")
    assert b.bid(call("repositioning"))["can_help"] is False
    assert b.bid(call("thirsty"))["can_help"] is True


def test_a_status_that_ran_out_is_forgotten():
    b = brain(); b.hear("busy for 45 min")
    later = CaregiverBrain("Maya", clock=lambda: TS + 3600, state_path=None)
    later.presence = b.presence
    assert later.bid(call())["can_help"] is True


def test_state_survives_a_restart(tmp_path):
    p = tmp_path / "maya.json"
    a = CaregiverBrain("Maya", clock=lambda: TS, state_path=p); a.hear("in a meeting till 3")
    a.record("acknowledged", "repositioning")
    b = CaregiverBrain("Maya", clock=lambda: TS, state_path=p)
    assert b.bid(call())["can_help"] is False and b.ledger.load(TS) > 0


# ------------------------------------------------------------------ the load
def test_nights_count_double_and_old_help_fades():
    led = Ledger()
    led.add(datetime(2026, 10, 18, 2, 0).timestamp(), "pain")           # 2 am
    led.add(datetime(2026, 10, 18, 11, 0).timestamp(), "thirsty")       # mid-morning
    assert led.load(TS) == pytest.approx(3.0)
    led.add((NOW - timedelta(days=9)).timestamp(), "pain")              # older than a week: not counted
    assert led.load(TS) == pytest.approx(3.0)


def test_only_real_help_counts_toward_the_load():
    b = brain()
    b.record("declined", "repositioning"); b.record("timeout", "repositioning"); b.record("cancelled", "repositioning")
    assert b.ledger.load(TS) == 0
    b.record("acknowledged", "repositioning")
    assert b.ledger.load(TS) == pytest.approx(1.0)


# ------------------------------------------------------------------ choosing
def bids(**kw):
    base = {"call_id": "c1", "can_help": True, "eta_min": 10, "fit": 1.0, "load": 0.0, "interrupt_cost": 0.0, "reason": ""}
    return {n: {**base, "member": n, **v} for n, v in kw.items()}


def test_the_closer_member_wins_an_urgent_call():
    r = choose(bids(Maya={"eta_min": 25}, Raj={"eta_min": 6}), "urgent", ["Maya", "Raj"])
    assert r["order"][0] == "Raj"


def test_for_ordinary_help_the_one_who_has_done_less_wins_when_distance_is_close():
    r = choose(bids(Maya={"eta_min": 9, "load": 7.0}, Raj={"eta_min": 12, "load": 1.0}), "normal", ["Maya", "Raj"])
    assert r["order"][0] == "Raj"
    assert "less" in r["why"][0].lower() or "load" in r["why"][0].lower()


def test_but_a_much_closer_member_still_wins_ordinary_help():
    r = choose(bids(Maya={"eta_min": 3, "load": 7.0}, Raj={"eta_min": 45, "load": 1.0}), "normal", ["Maya", "Raj"])
    assert r["order"][0] == "Maya"


def test_load_is_ignored_when_it_is_urgent():
    r = choose(bids(Maya={"eta_min": 5, "load": 40.0}, Raj={"eta_min": 12, "load": 0.0}), "urgent", ["Maya", "Raj"])
    assert r["order"][0] == "Maya"


def test_someone_who_cant_help_goes_to_the_back_not_off_the_list():
    r = choose(bids(Maya={"can_help": False, "reason": "in a meeting until 3:00 PM"}, Raj={"eta_min": 20}), "normal", ["Maya", "Raj"])
    assert r["order"] == ["Raj", "Maya"]
    assert any("Maya" in w and "meeting" in w for w in r["why"])


def test_nobody_answering_falls_back_to_the_care_plan_order():
    r = choose({}, "normal", ["Maya", "Raj"])
    assert r["order"] == ["Maya", "Raj"] and r["fallback"] is True


def test_a_member_who_didnt_answer_keeps_her_place_in_the_plan_after_the_bidders():
    r = choose(bids(Raj={"eta_min": 20}), "normal", ["Maya", "Raj"])
    assert r["order"] == ["Raj", "Maya"]


def test_ties_follow_the_care_plan_order():
    r = choose(bids(Maya={"eta_min": 10}, Raj={"eta_min": 10}), "normal", ["Maya", "Raj"])
    assert r["order"] == ["Maya", "Raj"]


# ------------------------------------------------------------------ questions are not statuses
def test_a_question_about_availability_is_not_a_status_update():
    for q in ["is Maya available?", "Is she free?", "where is Maya", "are you busy?", "how far away is Raj", "what's Maya's status",
              "status", "is Raj home?", "who is available right now", "Free now?"]:
        assert parse_status(q, NOW) is None, q


def test_statements_still_count():
    for s in ["I'm available", "available now", "free now", "I'm home"]:
        assert parse_status(s, NOW) is not None, s


def test_a_question_never_changes_where_someone_is():
    b = brain(); b.hear("in a meeting till 3")
    assert b.hear("is Maya available?") is None
    assert b.presence.mode == "busy"
