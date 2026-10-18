"""Next / Back from a second movement go through the same sequential test as Select."""

import numpy as np

from residual.config import Config, preset
from residual.engine import Engine
from residual.events import Frame
from residual.sim import Scenario, run_session, simulated_calibration


def _engine():
    rng = np.random.default_rng(3)
    eng = Engine(Config())
    rest, gestures = simulated_calibration(rng, 30.0)
    eng.calibrate(rest, gestures, 0.0)
    return eng, rng


def _run(eng, rng, gestures, secs=8.0):
    """gestures: list of (channel, t0, dur, peak)."""
    out = []
    for i in range(int(secs * 30)):
        t = i / 30
        amps = {c: float(rng.normal(0, 0.035)) for c in ("hand", "brow", "blink")}
        for c, t0, dur, pk in gestures:
            if t0 <= t <= t0 + dur:
                amps[c] += pk * np.sin(np.pi * (t - t0) / dur)
        out += eng.step(Frame(t, amps, {c: True for c in amps}))
    return out


def test_deliberate_second_movement_fires_aux():
    eng, rng = _engine()
    ev = _run(eng, rng, [("brow", 2.0, 0.6, 1.0), ("brow", 5.0, 0.6, 1.0)])
    aux = [e for e in ev if e.kind == "aux_fire"]
    assert [e.data["channel"] for e in aux] == ["brow", "brow"]
    assert not any(e.kind == "fire" for e in ev)          # Select (hand) never fired


def test_natural_blinks_and_small_brow_twitches_do_not():
    eng, rng = _engine()
    ev = _run(eng, rng, [("blink", 1.0, 0.12, 1.0), ("blink", 3.0, 0.1, 1.0), ("blink", 5.5, 0.15, 1.0),
                         ("brow", 4.0, 0.4, 0.25)])
    assert not [e for e in ev if e.kind == "aux_fire"]


def test_control_channel_never_fires_aux():
    eng, rng = _engine()
    ev = _run(eng, rng, [("hand", 2.0, 0.6, 1.0)])
    assert any(e.kind == "fire" for e in ev)
    assert not [e for e in ev if e.kind == "aux_fire" and e.data["channel"] == "hand"]


def test_sessions_are_deterministic():
    """Same seed, same result: the comparisons in the evaluation are paired, so this must hold.
    (That aux accumulators don't change decisions was checked against the engine before they were
    added: identical correct / wrong / switches on the same seeds.)"""
    sc = Scenario("shrink", 0.95, ("hand",), "ramp", 90)
    a = run_session(preset("residual"), sc, 7)
    b = run_session(preset("residual"), sc, 7)
    assert (a.correct, a.wrong, a.switches) == (b.correct, b.wrong, b.switches)


def test_decisions_are_explained():
    sc = Scenario("shrink", 0.97, ("hand",), "step", 120)
    seen = {}

    def grab(eng, evs):
        if eng.policy.decisions:
            seen["d"] = list(eng.policy.decisions)
    run_session(preset("residual"), sc, 1, on_step=grab)
    d = seen["d"]
    assert any(x["kind"] == "verify" and x["result"] == "verified, moved" for x in d)
    v = next(x for x in d if x["kind"] == "verify")
    assert v["from"] == ["hand"] and v["to_p"] and v["from_p"]
    assert v["clauses"]                                    # at least one reason recorded
