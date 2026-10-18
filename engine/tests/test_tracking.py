import numpy as np

from residual.config import Config
from residual.tracking import BOCPD, ChannelTracker


def _gesture(peak, n=18):
    return list(peak * np.sin(np.pi * np.arange(n) / n))


def _feed(tr, t, amps, relevant=True, prompted=False):
    ep = None
    for a in amps + [0.0, 0.0, 0.0]:
        t += 1 / 30
        e = tr.observe(t, a, True, relevant, prompted)
        ep = e or ep
    return t, ep


def _calibrated(seed=0):
    rng = np.random.default_rng(seed)
    tr = ChannelTracker("hand", Config())
    tr.calibrate(list(rng.normal(0, 0.03, 300)), [_gesture(1.0) for _ in range(4)])
    return tr


def test_calibration_sets_level_and_noise():
    tr = _calibrated()
    assert 0.55 < tr.x[0] < 0.85
    assert tr.s_n < 0.05


def test_kalman_follows_gradual_decline():
    tr = _calibrated()
    t = 0.0
    for peak in np.linspace(1.0, 0.5, 15):
        t, ep = _feed(tr, t + 3.0, _gesture(peak), prompted=True)
    level, _ = tr.level_at(t)
    assert level < 0.55  # followed the decline (started ~0.7)
    assert tr.x[1] < 0  # learned a negative trend


def test_incidental_movement_on_idle_channel_does_not_change_model():
    tr = _calibrated()
    before = tr.x[0]
    t = 0.0
    for _ in range(10):
        t, _ = _feed(tr, t + 3.0, _gesture(0.25), relevant=False)
    assert abs(tr.x[0] - before) < 1e-9


def test_weak_unprompted_movements_are_counted_not_learned():
    tr = _calibrated()
    before = tr.x[0]
    t = 0.0
    for _ in range(3):
        t, _ = _feed(tr, t + 2.0, _gesture(0.2), relevant=True)
    assert abs(tr.x[0] - before) < 1e-9
    assert tr.weak_count(t, 20.0) == 3


def test_bocpd_flags_abrupt_change():
    b = BOCPD(hazard=1 / 25, obs_sd=0.08)
    for _ in range(10):
        b.update(0.7)
    assert b.p_recent(3) < 0.3
    b.update(0.25)
    assert b.p_recent(3) < 0.6  # one outlier is not a change
    b.update(0.25)
    assert b.p_recent(3) > 0.6
    assert abs(b.segment_mean() - 0.25) < 0.12


def test_natural_blink_is_not_an_episode():
    tr = ChannelTracker("blink", Config())
    rng = np.random.default_rng(0)
    tr.calibrate(list(rng.normal(0, 0.03, 300)), [_gesture(1.0, 21) for _ in range(4)])
    _, ep = _feed(tr, 0.0, _gesture(1.0, 4))  # ~0.13 s natural blink
    assert ep is None
