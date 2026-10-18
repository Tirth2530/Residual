"""One physical gesture episode -> at most one logical action.

Every case drives the real Engine frame by frame with a calibrated simulated face.
"""

import numpy as np
import pytest

from residual.config import Config
from residual.engine import Engine
from residual.events import Frame
from residual.sim import simulated_calibration

FPS = 30


def make(control="hand", **cfg):
    rng = np.random.default_rng(11)
    eng = Engine(Config(initial_control=(control,), **cfg))
    rest, gestures = simulated_calibration(rng, FPS)
    eng.calibrate(rest, gestures, 0.0)
    return eng, rng


def drive(eng, rng, segments, start=0.0):
    """segments: list of (seconds, {channel: amplitude}). Small sensor noise on top."""
    ev, t = [], start
    for secs, amps in segments:
        for _ in range(int(round(secs * FPS))):
            t += 1 / FPS
            a = {c: float(rng.normal(0, 0.02)) + amps.get(c, 0.0) for c in ("hand", "brow", "blink")}
            ev += eng.step(Frame(t, a, {c: True for c in a}))
    return ev


def fires(ev, ch):
    return [e for e in ev if e.kind == "fire" and e.data["option"] == [ch]]


def aux(ev, ch):
    return [e for e in ev if e.kind == "aux_fire" and e.data["channel"] == ch]


@pytest.mark.parametrize("ch", ["hand", "brow"])
def test_held_gesture_fires_exactly_once(ch):
    eng, rng = make(ch)
    ev = drive(eng, rng, [(1.0, {}), (3.0, {ch: 1.0}), (1.0, {})])
    assert len(fires(ev, ch)) == 1


@pytest.mark.parametrize("ch", ["hand", "brow"])
def test_release_and_repeat_fires_twice(ch):
    eng, rng = make(ch)
    ev = drive(eng, rng, [(1.0, {}), (3.0, {ch: 1.0}), (1.0, {}), (3.0, {ch: 1.0}), (1.0, {})])
    assert len(fires(ev, ch)) == 2


def test_held_while_returning_only_partway_does_not_rearm():
    """Dropping to two thirds of the gesture is not a return to neutral."""
    eng, rng = make("brow")
    ev = drive(eng, rng, [(1.0, {}), (1.5, {"brow": 1.0}), (0.6, {"brow": 0.65}), (1.5, {"brow": 1.0}), (1.0, {})])
    assert len(fires(ev, "brow")) == 1


def test_one_long_blink_selects_once_on_reopening():
    eng, rng = make("blink")
    ev = drive(eng, rng, [(1.0, {}), (0.6, {"blink": 1.0}), (1.5, {})])
    f = fires(ev, "blink")
    assert len(f) == 1
    assert f[0].t > 1.6            # acted after the eyes reopened, not mid-closure


def test_eyes_held_closed_never_select():
    eng, rng = make("blink")
    ev = drive(eng, rng, [(1.0, {}), (3.0, {"blink": 1.0}), (1.5, {})])
    assert fires(ev, "blink") == []


def test_natural_blinks_never_select():
    eng, rng = make("blink")
    ev = drive(eng, rng, [(1.0, {}), (0.12, {"blink": 1.0}), (1.0, {}), (0.1, {"blink": 1.0}), (1.0, {})])
    assert fires(ev, "blink") == []


def test_help_closures_do_not_select_but_long_blinks_do():
    """Three 1.5 s closures (the help pattern) and then two 0.6 s long blinks."""
    eng, rng = make("blink")
    segs = [(1.0, {})]
    for _ in range(3):
        segs += [(1.5, {"blink": 1.0}), (1.0, {})]
    for _ in range(2):
        segs += [(0.6, {"blink": 1.0}), (1.2, {})]
    ev = drive(eng, rng, segs)
    assert len(fires(ev, "blink")) == 2


@pytest.mark.parametrize("ch", ["brow", "blink"])
def test_aux_held_fires_once_and_repeat_fires_twice(ch):
    eng, rng = make("hand")
    held = 3.0 if ch == "brow" else 0.7      # a held blink is a hold, so use a long blink for blink
    ev = drive(eng, rng, [(1.0, {}), (held, {ch: 1.0}), (1.2, {})])
    assert len(aux(ev, ch)) == 1
    ev += drive(eng, rng, [(held, {ch: 1.0}), (1.2, {})], start=eng.t)
    assert len(aux(ev, ch)) == 2
    assert fires(ev, "hand") == []           # Select never fired from a secondary movement


def test_aux_blink_held_closed_does_not_fire():
    eng, rng = make("hand")
    ev = drive(eng, rng, [(1.0, {}), (3.0, {"blink": 1.0}), (1.0, {})])
    assert aux(ev, "blink") == []


def test_verification_gestures_need_release():
    """During a verification, holding the candidate counts once, not twice."""
    eng, rng = make("hand")
    eng.policy.state, eng.policy.candidate = "VERIFY", ("brow",)
    eng.policy.verify_t0, eng.policy.verify_count = 0.0, 0
    ev = drive(eng, rng, [(0.5, {}), (3.0, {"brow": 1.0})])
    assert eng.policy.verify_count == 1
    assert not [e for e in ev if e.kind == "verify_ok"]


def test_fused_control_needs_both_released():
    eng, rng = make("hand", policy_mode="fixed")   # hold the fused control still for the test
    eng.policy.control = ("hand", "brow")
    ev = drive(eng, rng, [(1.0, {}), (3.0, {"hand": 0.6, "brow": 0.6}), (1.0, {})])
    fused = [e for e in ev if e.kind == "fire" and e.data["option"] == ["hand", "brow"]]
    assert len(fused) == 1


def test_arm_state_is_exposed():
    eng, rng = make("brow")
    drive(eng, rng, [(1.0, {}), (1.5, {"brow": 1.0})])
    assert eng.snapshot()["channels"]["brow"]["arm"] == "wait_release"
    drive(eng, rng, [(1.0, {})], start=eng.t)
    assert eng.snapshot()["channels"]["brow"]["arm"] == "armed"


def test_ineligible_channel_cannot_select_or_navigate():
    eng, rng = make("hand")
    eng.set_eligible({"hand": True, "brow": False, "blink": True})
    ev = drive(eng, rng, [(1.0, {}), (0.6, {"brow": 1.0}), (1.0, {}), (0.6, {"brow": 1.0}), (1.0, {})])
    assert aux(ev, "brow") == []
    assert all("brow" not in o for o in eng.policy.options())


def test_failed_control_channel_is_replaced_at_calibration():
    eng, rng = make("hand")
    eng.set_eligible({"hand": False, "brow": True, "blink": True})
    assert eng.policy.control == ("brow",)
