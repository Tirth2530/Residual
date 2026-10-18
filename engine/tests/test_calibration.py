"""Calibration tells the truth: counted gestures, per-movement verdicts, an honest preflight."""

import numpy as np

from residual.calibration import Calibration, fit_calibration
from residual.config import Config
from residual.engine import Engine

FPS = 30


class Face:
    """Synthetic raw features. `script` maps a step name to a function (t_in_step) -> overrides."""

    def __init__(self, seed=0, face=True, hand=True):
        self.rng = np.random.default_rng(seed)
        self.face, self.hand = face, hand

    def raw(self, over=None):
        r = {}
        if self.hand:
            r["hand"] = 0.25 + self.rng.normal(0, 0.01)
        if self.face:
            r["brow"] = 0.10 + self.rng.normal(0, 0.008)
            r["brow_geo"] = 0.60 + self.rng.normal(0, 0.002)
            r["blink"] = 0.05 + self.rng.normal(0, 0.01)
        for k, v in (over or {}).items():
            if k in r:
                r[k] = v + self.rng.normal(0, 0.005)
        return r


def pulses(feature, value, every=1.5, width=0.6, start=0.5):
    def f(tt):
        k = (tt - start) % every
        return {feature: value} if tt >= start and k < width else {}
    return f


def run(cal, face, script, t0=0.0, max_s=120, camera=None, gaze=None):
    t = t0
    cam = camera or {"open": True, "age": 0.0}
    while not cal.done and not cal.failed and t < t0 + max_s:
        t += 1 / FPS
        name = cal.name
        over = script.get(name, lambda tt: {})(t - cal.step_t0)
        raw = face.raw(over) if cam.get("age") is not None else {}
        gh = gaze(name, t - cal.step_t0) if gaze else None
        cal.step(t, raw if face.face or face.hand else {}, face=face.face, yaw=0.0, gaze_hv=gh, camera=cam)
    return t


GOOD = {"hand": pulses("hand", 0.85), "brow": pulses("brow", 0.55), "blink": pulses("blink", 0.9, width=0.5)}


def test_all_movements_valid_and_finishes_early():
    cal = Calibration(0.0, preflight=True, gaze=False)
    t = run(cal, Face(), GOOD)
    assert cal.done and not cal.failed
    for c in ("hand", "brow", "blink"):
        r = cal.results[c]
        assert r.status == "VALID", (c, r)
        assert r.count == 3
    # success-driven: 3 gestures at 1.5 s spacing take ~4 s each, far under the 15 s timeout
    assert t < 1.5 + 4.0 + 3 * 6.0


def test_eyebrow_that_never_moves_fails_and_is_not_eligible():
    cal = Calibration(0.0, preflight=False, gaze=False)
    run(cal, Face(), {"hand": GOOD["hand"], "blink": GOOD["blink"]})
    assert cal.results["brow"].status == "FAILED"
    norm, rest, gestures, results = fit_calibration(cal)
    eng = Engine(Config())
    eng.calibrate(rest, gestures, 0.0, eligible={c: results[c].usable for c in results})
    assert eng.eligible == {"hand": True, "brow": False, "blink": True}
    assert all("brow" not in o for o in eng.policy.options())


def test_one_gesture_is_not_enough():
    cal = Calibration(0.0, preflight=False, channels=["brow"], gaze=False)
    once = lambda tt: {"brow": 0.55} if 1.0 <= tt < 1.6 else {}
    run(cal, Face(), {"brow": once})
    r = cal.results["brow"]
    assert r.count == 1 and r.status == "FAILED"


def test_no_face_during_calibration_is_not_visible():
    cal = Calibration(0.0, preflight=False, gaze=False)
    run(cal, Face(face=False), GOOD)
    assert cal.results["hand"].status == "VALID"
    assert cal.results["brow"].status == "NOT_VISIBLE"
    assert cal.results["blink"].status == "NOT_VISIBLE"


def test_camera_opened_but_no_frames_fails_preflight():
    cal = Calibration(0.0, preflight=True, gaze=False)
    run(cal, Face(), GOOD, camera={"open": True, "age": None})
    assert cal.failed == "no_frames"
    assert not cal.results


def test_camera_not_open_fails_preflight():
    cal = Calibration(0.0, preflight=True, gaze=False)
    run(cal, Face(), GOOD, camera={"open": False, "age": None})
    assert cal.failed == "camera_not_open"


def test_frames_but_no_face_fails_preflight():
    cal = Calibration(0.0, preflight=True, gaze=False)
    run(cal, Face(face=False), GOOD)
    assert cal.failed == "no_face"


def test_inner_brow_only_face_is_detected():
    """A face whose eyebrow raise shows mostly in browInnerUp: the old 3-way mean averaged it away."""
    from residual.perception import brow_blendshape
    inner_only = lambda tt: GOOD["brow"](tt) and {"brow": brow_blendshape(0.6, 0.08, 0.06)}
    old_mean = (0.6 + 0.08 + 0.06) / 3
    assert brow_blendshape(0.6, 0.08, 0.06) > old_mean * 2
    cal = Calibration(0.0, preflight=False, channels=["brow"], gaze=False)
    run(cal, Face(), {"brow": inner_only})
    assert cal.results["brow"].status == "VALID"


def test_geometric_brow_alone_is_enough():
    """Blendshapes don't move, the brow-to-lid distance does: calibration picks brow_geo."""
    cal = Calibration(0.0, preflight=False, channels=["brow"], gaze=False)
    run(cal, Face(), {"brow": pulses("brow_geo", 0.66)})
    r = cal.results["brow"]
    assert r.status == "VALID" and r.sources == ["brow_geo"]
    norm, *_ = fit_calibration(cal)
    assert norm({"brow": 0.10, "brow_geo": 0.66})["brow"] > 0.8
    assert norm({"brow": 0.10, "brow_geo": 0.60})["brow"] < 0.2


def test_natural_blinks_do_not_count_as_calibration_blinks():
    cal = Calibration(0.0, preflight=False, channels=["blink"], gaze=False, timeout_s=8.0)
    run(cal, Face(), {"blink": pulses("blink", 0.9, every=1.0, width=0.1)})
    assert cal.results["blink"].count == 0
    assert cal.results["blink"].status == "FAILED"


def test_skip_marks_failed():
    cal = Calibration(0.0, preflight=False, channels=["brow"], gaze=False)
    run(cal, Face(), {}, max_s=5)
    cal.skip(6.0)
    assert cal.results["brow"].status == "FAILED" and cal.results["brow"].skipped


def _gaze_script(dl, dr, du=-0.04):
    def g(name, tt):
        if name != "gaze":
            return None
        from residual.calibration import GAZE_STEPS
        el = tt
        for target, secs, _ in GAZE_STEPS:
            if el < secs:
                break
            el -= secs
        h = {"left": dl, "right": dr}.get(target, 0.0)
        v = du if target == "up" else 0.0
        return (h, v)
    return g


def test_gaze_calibration_measures_range_and_direction():
    cal = Calibration(0.0, preflight=False, channels=[], gaze=True)
    run(cal, Face(), {}, gaze=_gaze_script(-0.06, 0.07))
    g = cal.gaze_result
    assert g["calibrated"] and g["sign_h"] == 1 and not g["swapped"]
    assert abs(g["range"]["right"] - 0.07) < 0.005


def test_gaze_calibration_detects_a_swapped_camera():
    cal = Calibration(0.0, preflight=False, channels=[], gaze=True)
    run(cal, Face(), {}, gaze=_gaze_script(0.06, -0.07))
    assert cal.gaze_result["calibrated"] and cal.gaze_result["sign_h"] == -1


def test_gaze_not_calibrated_when_left_and_right_look_the_same():
    cal = Calibration(0.0, preflight=False, channels=[], gaze=True)
    run(cal, Face(), {}, gaze=_gaze_script(0.002, 0.003))
    assert not cal.gaze_result["calibrated"]


def test_calibrated_gaze_reader_uses_the_measured_direction():
    from residual.gaze import EyeFeat, Features, GestureReader
    r = GestureReader()
    r.calibrate({"calibrated": True, "center": [0.0, 0.0], "sign_h": -1,
                 "range": {"left": 0.05, "right": 0.05, "up": 0.04}})
    got = []
    t = 0.0
    for h in [0.0] * 20 + [-0.05] * 20 + [0.0] * 20:     # on this camera, right is negative h
        t += 1 / 30
        f = Features(EyeFeat(h, 0, 0.3), EyeFeat(h, 0, 0.3), 0, 0, 0, True)
        e = r.update(t, f, 1.0)
        if e:
            got.append(e["dir"])
    assert got == ["right"]
