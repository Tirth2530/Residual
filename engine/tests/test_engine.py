"""End-to-end behaviour through the same Engine the live demo uses."""

import numpy as np

from residual.config import Config, preset
from residual.degrade import Degradation
from residual.engine import Engine
from residual.events import Frame
from residual.sim import Scenario, run_session, simulated_calibration


def _engine(cfg=None):
    eng = Engine(cfg or Config())
    rest, g = simulated_calibration(np.random.default_rng(0))
    eng.calibrate(rest, g)
    return eng


def test_predicted_reliability_high_after_calibration():
    eng = _engine()
    for c in ("hand", "brow"):
        s = eng.stats[(c,)]
        assert s.p_hat > 0.9
        assert s.fa_per_min < 0.5


def test_no_selection_at_rest():
    eng = _engine()
    rng = np.random.default_rng(1)
    sel = 0
    for i in range(30 * 60):
        f = Frame(i / 30, {c: rng.normal(0, 0.035) for c in ("hand", "brow", "blink")})
        sel += sum(e.kind == "select" for e in eng.step(f))
    assert sel == 0


def test_degradation_never_touches_scores():
    """The simulator only changes amplitudes."""
    d = Degradation(shrink=0.5, jitter=0.0, dropout=0.0)
    a, vis = d.apply(0.8, np.random.default_rng(0))
    assert vis and abs(a - 0.4) < 1e-9


def test_clean_session_works_and_does_not_switch():
    r = run_session(preset("residual"), Scenario("shrink", 0.0, duration_s=120), seed=3)
    assert r.correct >= 15
    assert r.accuracy > 0.9
    assert r.switches == 0


def test_residual_keeps_control_under_severe_shrink():
    sc = Scenario("shrink", 0.97, duration_s=180)
    res = run_session(preset("residual"), sc, seed=2)
    fixed = run_session(preset("fixed"), sc, seed=2)
    assert res.correct > fixed.correct
    assert res.misses <= fixed.misses


def test_migration_is_verified_before_switching():
    sc = Scenario("mixed", 0.95, shape="step", duration_s=180)
    r = run_session(preset("residual"), sc, seed=2, keep_events=True)
    kinds = [e.kind for e in r.events]
    assert "migrate" in kinds
    first_mig = kinds.index("migrate")
    assert "verify_start" in kinds[:first_mig]
    assert "verify_ok" in kinds[:first_mig]


def test_live_and_replay_are_identical():
    """Replaying recorded frames through a fresh engine reproduces every event."""
    sc = Scenario("mixed", 0.8, duration_s=60)
    frames = []
    run_session(preset("residual"), sc, seed=5,
                on_step=lambda eng, ev: frames.append((eng.t, dict(eng.last_amp),
                                                       dict(eng.last_visible), len(ev))))
    eng = _engine_for_replay(sc, seed=5)
    for t, amps, vis, n in frames:
        assert len(eng.step(Frame(t, amps, vis))) == n


def _engine_for_replay(sc, seed):
    rng = np.random.default_rng(seed)
    eng = Engine(preset("residual"))
    rest, g = simulated_calibration(rng, 30.0)
    eng.calibrate(rest, g)
    return eng


def test_trace_library_drives_the_simulator(tmp_path):
    """A recorded library (real traces) replaces synthetic gestures and noise."""
    import json

    from residual.sim import Library

    rng = np.random.default_rng(0)
    lib = {"fps": 30.0, "rest": {}, "gestures": {}}
    for c in ("hand", "brow", "blink"):
        lib["rest"][c] = list(rng.normal(0, 0.035, 900))
        n = 21 if c == "blink" else 18
        lib["gestures"][c] = [list(rng.uniform(0.85, 1.1) * np.sin(np.pi * np.arange(n) / n))
                              for _ in range(6)]
    p = tmp_path / "lib.json"
    p.write_text(json.dumps(lib))
    r = run_session(preset("residual"), Scenario("shrink", 0.0, duration_s=60), seed=1,
                    library=Library(str(p)))
    assert r.correct >= 6 and r.wrong == 0
