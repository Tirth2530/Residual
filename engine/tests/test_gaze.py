"""Tests for residual/gaze.py with synthetic faces (no camera needed)."""

from types import SimpleNamespace

import numpy as np

from residual.gaze import (LEFT_EYE, RIGHT_EYE, Eyes, Fatigue, Features, EyeFeat, GestureReader,
                           HelpPattern, extract)

FPS = 30


def feat(h=0.0, v=0.0, open_=0.3, yaw=0.0):
    e = EyeFeat(h=h, v=v, openness=open_)
    return Features(left=e, right=e, yaw=yaw, pitch=0.0, roll=0.0, normalized=True)


def run(reader, seq):
    """seq: list of (seconds, h, v, yaw). Returns gesture events."""
    out, t = [], 0.0
    for secs, h, v, yaw in seq:
        for _ in range(int(secs * FPS)):
            t += 1 / FPS
            e = reader.update(t, feat(h, v, yaw=yaw), 1.0)
            if e:
                out.append(e["dir"])
    return out


def test_deliberate_glances_fire_once_each():
    r = GestureReader()
    ev = run(r, [(1, 0, 0, 0), (0.6, 0.15, 0, 0), (0.6, 0, 0, 0), (0.6, -0.15, 0, 0), (0.6, 0, 0, 0),
                 (0.6, 0, -0.14, 0), (0.6, 0, 0, 0)])
    assert ev == ["right", "left", "up"]


def test_quick_glance_is_ignored():
    r = GestureReader()
    assert run(r, [(1, 0, 0, 0), (0.1, 0.15, 0, 0), (1, 0, 0, 0)]) == []


def test_holding_does_not_repeat_and_return_rearms():
    r = GestureReader()
    ev = run(r, [(1, 0, 0, 0), (2.0, 0.15, 0, 0), (0.5, 0, 0, 0), (0.6, 0.15, 0, 0), (0.5, 0, 0, 0)])
    assert ev == ["right", "right"]


def test_head_turn_is_not_a_glance():
    r = GestureReader()
    # head swinging fast (yaw changes 60 deg over 0.6 s) while eyes drift right
    seq = [(1, 0, 0, 0)] + [(1 / FPS, 0.15, 0, i * 3.0) for i in range(18)] + [(0.5, 0, 0, 54)]
    assert run(r, seq) == []


def test_centre_follows_slow_drift():
    r = GestureReader()
    # the resting gaze slowly shifts (person sinks into the pillow): no false "right"
    seq = [(1, 0, 0, 0)] + [(0.5, 0.004 * i, 0, 0) for i in range(30)]
    assert run(r, seq) == []


def test_range_adapts_to_smaller_glances():
    r = GestureReader()
    start = r.range["right"]
    ev = run(r, [(1, 0, 0, 0)] + [(0.6, 0.12, 0, 0), (0.6, 0, 0, 0)] * 6)
    assert len(ev) == 6 and r.range["right"] < start


def test_fatigue_rises_when_lids_drop():
    f = Fatigue(FPS)
    t = 0.0
    for _ in range(int(70 * FPS)):
        t += 1 / FPS
        s = f.update(t, 0.30, [])
    assert s["level"] == "fresh"
    for _ in range(int(40 * FPS)):
        t += 1 / FPS
        s = f.update(t, 0.17, [])
    assert s["ptosis"] > 0.5 and s["level"] in ("tiring", "tired")
    assert s["reason"] == "lids lower than usual"


def test_help_pattern_three_long_blinks():
    h, t, fired = HelpPattern(), 0.0, False

    def hold(closed, secs):
        nonlocal t, fired
        for _ in range(int(secs * FPS)):
            t += 1 / FPS
            fired |= h.update(t, closed)["fired"]

    hold(False, 1)
    for _ in range(3):
        hold(False, 0.1); hold(True, 0.2)   # ordinary blinks: ignored
    assert not fired
    for _ in range(3):
        hold(True, 1.5); hold(False, 0.8)
    assert fired


def test_help_pattern_ignores_long_blink_selects():
    """Three 'long blink' selects in a row (about 0.7 s each) must not call for help."""
    h, t, fired = HelpPattern(), 0.0, False
    for _ in range(4):
        for closed, secs in [(True, 0.75), (False, 1.0)]:
            for _ in range(int(secs * FPS)):
                t += 1 / FPS
                fired |= h.update(t, closed)["fired"]
    assert not fired


def test_profile_round_trip_keeps_baseline():
    from residual.gaze import Eyes
    e = Eyes()
    e.reader.range["right"] = 0.07
    e.fatigue.base_open, e.fatigue.base_blink_rate = 0.31, 14.0
    d = e.to_profile()
    e2 = Eyes()
    e2.load_profile(d)
    assert e2.reader.range["right"] == 0.07
    assert e2.fatigue.prior_open == 0.31 and e2.fatigue.base_blink_rate == 14.0


def test_help_pattern_ignores_resting_eyes():
    h, t, fired = HelpPattern(), 0.0, False
    for closed, secs in [(True, 1.5), (False, 0.5), (True, 6.0), (False, 0.5), (True, 1.5), (False, 0.5)]:
        for _ in range(int(secs * FPS)):
            t += 1 / FPS
            fired |= h.update(t, closed)["fired"]
    assert not fired


def _synthetic_face(iris_shift=0.0):
    """478 landmarks with two eyes; iris moved by iris_shift eye-widths along the eye axis."""
    pts = [SimpleNamespace(x=0.5, y=0.5, z=0.0) for _ in range(478)]
    for eye, cx in ((RIGHT_EYE, 0.40), (LEFT_EYE, 0.60)):
        w = 0.06
        outer, inner = (cx - w / 2, cx + w / 2) if eye is RIGHT_EYE else (cx + w / 2, cx - w / 2)
        pts[eye["outer"]] = SimpleNamespace(x=outer, y=0.45, z=0.0)
        pts[eye["inner"]] = SimpleNamespace(x=inner, y=0.45, z=0.0)
        pts[eye["upper"]] = SimpleNamespace(x=cx, y=0.44, z=0.0)
        pts[eye["lower"]] = SimpleNamespace(x=cx, y=0.46, z=0.0)
        ix = cx + iris_shift * w
        for j, (dx, dy) in zip(eye["iris"], ((0.005, 0), (0, -0.005), (-0.005, 0), (0, 0.005))):
            pts[j] = SimpleNamespace(x=ix + dx, y=0.45 + dy, z=0.0)
    return pts


def test_extract_reads_iris_position_and_openness():
    centre = extract(_synthetic_face(0.0), np.eye(4), aspect=1.0)
    right = extract(_synthetic_face(0.2), np.eye(4), aspect=1.0)
    assert abs(centre.h) < 1e-6
    assert abs(right.h - 0.2) < 1e-6
    assert abs(centre.openness - 0.02 / 0.06) < 1e-6
    assert extract(None, None, 1.0) is None


def test_eyes_module_end_to_end():
    eyes, t, events = Eyes(FPS), 0.0, []
    for secs, h in [(2, 0.0), (0.6, 0.15), (0.6, 0.0)]:
        for _ in range(int(secs * FPS)):
            t += 1 / FPS
            events += eyes.update(t, feat(h))
    assert [e["dir"] for e in events if e["type"] == "gesture"] == ["right"]
    snap = eyes.snapshot()
    assert snap["available"] and snap["quality"] > 0.9 and snap["counts"]["right"] == 1
