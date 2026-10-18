"""Layer 2: ability tracking per channel.

For each channel we keep:
  * resting-noise stats (mean, sd) from frames where nothing is happening
  * episode segmentation: a gesture "episode" is a run of frames above onset
  * a Kalman filter (local linear trend) on each episode's strength,
    so we know the current level, its trend, and our uncertainty
  * BOCPD (Bayesian online changepoint detection) on the same observations,
    so abrupt drops (fatigue, hand leaving frame) re-anchor the Kalman filter
  * an observed hit rate (Beta posterior with forgetting) used to validate
    the model's predicted reliability
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from .config import Config
from .reliability import default_shape


@dataclass
class Episode:
    channel: str
    t0: float
    t1: float
    strength: float  # mean amplitude over the above-onset frames
    sd: float  # within-episode sd
    n: int  # frames
    fired: bool  # evidence crossed a bound during the episode
    accepted: bool = False  # passed the Kalman gate (consistent with the intent model)
    trusted: bool = False  # counted as a deliberate attempt


class BOCPD:
    """Adams & MacKay (2007) with a Normal likelihood of known variance and a
    Normal prior on the segment mean. Run lengths are truncated at rmax."""

    def __init__(self, hazard: float, obs_sd: float, prior_mu: float = 0.5,
                 prior_sd: float = 0.5, rmax: int = 60):
        self.h = hazard
        self.r2 = obs_sd**2
        self.mu0, self.v0 = prior_mu, prior_sd**2
        self.rmax = rmax
        self.reset()

    def reset(self) -> None:
        self.R = np.array([1.0])
        self.mu = np.array([self.mu0])
        self.v = np.array([self.v0])
        self.n = 0

    def update(self, x: float) -> None:
        pv = self.v + self.r2
        pred = np.exp(-0.5 * (x - self.mu) ** 2 / pv) / np.sqrt(2 * np.pi * pv)
        growth = self.R * pred * (1 - self.h)
        cp = float((self.R * pred * self.h).sum())
        R = np.concatenate(([cp], growth))
        s = R.sum()
        R = R / s if s > 0 else np.full_like(R, 1.0 / len(R))
        v_post = 1.0 / (1.0 / self.v + 1.0 / self.r2)
        mu_post = v_post * (self.mu / self.v + x / self.r2)
        mu = np.concatenate(([self.mu0], mu_post))
        v = np.concatenate(([self.v0], v_post))
        if len(R) > self.rmax:
            R, mu, v = R[: self.rmax], mu[: self.rmax], v[: self.rmax]
            R = R / R.sum()
        self.R, self.mu, self.v = R, mu, v
        self.n += 1

    def p_recent(self, k: int = 3) -> float:
        """Posterior probability that a new segment began 2..k observations ago.
        Requiring at least two observations in the new regime means a single
        outlier never counts as a change."""
        return float(self.R[2: k + 1].sum())

    def segment_mean(self) -> float:
        """Posterior mean of the most likely *young* segment (the new regime)."""
        k = int(np.argmax(self.R[1:4])) + 1 if len(self.R) > 1 else 0
        return float(self.mu[k])


class ChannelTracker:
    def __init__(self, name: str, cfg: Config):
        self.name = name
        self.cfg = cfg
        # Resting noise
        self.mu_n = 0.0
        self.var_n = 0.04**2
        # Intent model
        self.s_i = 0.15
        self.D = 12.0  # typical episode length in frames
        self.x = np.array([0.7, 0.0])  # [level, trend per second]
        self.P = np.diag([0.1**2, cfg.trend_sd0**2])
        self.t_last: float | None = None
        self.bocpd = BOCPD(cfg.hazard, cfg.cp_obs_sd)
        # Observed reliability
        self.beta_a = 1.0
        self.beta_b = 1.0
        self.raw_recent: deque[bool] = deque(maxlen=cfg.baseline_window)
        # State
        self._ep: dict | None = None
        self.cp_prob = 0.0
        self.cp_until = -1.0
        self.calibrated = False
        self.last_episode: Episode | None = None
        self._cp_event: tuple[float, float] | None = None
        self.shape = default_shape()  # learned gesture profile (mean 1)
        self.vis_rate = 1.0
        self.strength_sd = 0.08
        # Incidental movement model (learned from idle channels)
        self.inc_mu = 0.2
        self.inc_var = 0.08**2
        self.weak_times: deque[float] = deque()

    # ------------------------------------------------------------------ noise
    @property
    def s_n(self) -> float:
        return float(np.sqrt(self.var_n))

    def onset(self) -> float:
        return max(self.mu_n + self.cfg.k_onset * self.s_n, self.mu_n + self.cfg.min_onset)

    def _update_noise(self, a: float) -> None:
        al = self.cfg.noise_alpha
        d = a - self.mu_n
        self.mu_n += al * d
        self.var_n = (1 - al) * (self.var_n + al * d * d)
        self.var_n = max(self.var_n, 0.01**2)

    # ----------------------------------------------------------------- kalman
    def _FQ(self, dt: float) -> tuple[np.ndarray, np.ndarray]:
        q = self.cfg.kalman_q
        F = np.array([[1.0, dt], [0.0, 1.0]])
        Q = q * np.array([[dt**3 / 3, dt**2 / 2], [dt**2 / 2, dt]])
        return F, Q

    def level_at(self, t: float) -> tuple[float, float]:
        """Predicted (level, variance) at time t, without changing state.
        The trend is extrapolated at most trend_horizon_s."""
        if self.t_last is None:
            return float(self.x[0]), float(self.P[0, 0])
        dt = min(max(0.0, t - self.t_last), self.cfg.trend_horizon_s)
        level = self.x[0] + self.x[1] * dt
        _, Q = self._FQ(dt)
        var = self.P[0, 0] + 2 * dt * self.P[0, 1] + dt * dt * self.P[1, 1] + Q[0, 0]
        return float(level), float(min(max(var, 1e-6), self.cfg.max_level_var))

    def _kalman_update(self, t: float, z: float) -> tuple[bool, float]:
        """Predict to t then update with z, if z passes the gate.
        Returns (accepted, normalized innovation)."""
        # Ability doesn't drift without bound while unobserved: both the trend
        # extrapolation and the uncertainty growth saturate after a horizon.
        dt = 0.0 if self.t_last is None else min(max(0.0, t - self.t_last), self.cfg.trend_horizon_s)
        F, Q = self._FQ(dt)
        x_pred = F @ self.x
        P_pred = F @ self.P @ F.T + Q
        _cap_level_var(P_pred, self.cfg.max_level_var)
        H = np.array([1.0, 0.0])
        S = float(H @ P_pred @ H + self.cfg.kalman_r)
        innov = z - float(x_pred[0])
        nis = innov / np.sqrt(S)
        if abs(nis) > self.cfg.gate_sigma:
            return False, nis
        K = P_pred @ H / S
        self.x = x_pred + K * innov
        mt = self.cfg.max_trend
        self.x[1] = float(np.clip(self.x[1], -mt, mt))
        IKH = np.eye(2) - np.outer(K, H)
        self.P = IKH @ P_pred @ IKH.T + np.outer(K, K) * self.cfg.kalman_r  # Joseph form
        self.t_last = t
        return True, nis

    def _reanchor(self, t: float, level: float) -> None:
        self.x = np.array([level, 0.0])
        self.P = np.diag([self.cfg.max_level_var, self.cfg.trend_sd0**2])
        self.t_last = t

    # ------------------------------------------------------------ calibration
    def calibrate(self, rest: list[float], gestures: list[list[float]], t: float = 0.0) -> None:
        """rest: amplitudes while still. gestures: one amplitude list per deliberate gesture."""
        r = np.asarray(rest, dtype=float)
        if len(r) >= 5:
            self.mu_n = float(np.mean(r))
            self.var_n = float(max(np.var(r), 0.01**2))
        on = self.onset()
        strengths, sds, lens = [], [], []
        for g in gestures:
            g = np.asarray(g, dtype=float)
            above = g[g > on]
            if len(above) >= 2:
                strengths.append(float(above.mean()))
                sds.append(float(above.std()))
                lens.append(len(above))
        shapes = []
        for g in gestures:
            g = np.asarray(g, dtype=float)
            above = g[g > on]
            if len(above) >= 3:
                shapes.append(_resample(above / above.mean()))
        if shapes:
            self.shape = np.mean(shapes, axis=0)
            self.shape = self.shape / self.shape.mean()
        if strengths:
            self.x = np.array([float(np.mean(strengths)), 0.0])
            spread = float(np.std(strengths)) if len(strengths) > 1 else 0.05
            self.P = np.diag([max(spread, 0.04) ** 2, self.cfg.trend_sd0**2])
            self.s_i = float(max(np.mean(sds), 0.03))
            self.D = float(np.mean(lens))
        self.t_last = t
        self.bocpd.reset()
        self.calibrated = True

    # ------------------------------------------------------------------ frames
    def observe(self, t: float, a: float, visible: bool, relevant: bool = True,
                prompted: bool = False) -> Episode | None:
        """Feed one frame. Returns an Episode when one closes and qualifies.

        relevant: the person is meant to use this channel right now (it is in
                  control, or being verified/probed).
        prompted: the person was just asked to do this gesture (verify/probe),
                  so any episode now is certainly deliberate.

        We only learn intent strength from evidence we trust:
          * fired episodes (detected, not cancelled)
          * prompted episodes
          * relevant non-fired episodes stronger than incidental movement
        Weaker relevant episodes are ambiguous (incidental, or a failed weak
        attempt?). We don't guess: we count them, and enough of them make the
        policy *ask* (a probe), whose answers are prompted and trusted.
        Incidental movement on idle channels trains the incidental model.
        """
        self.vis_rate = 0.995 * self.vis_rate + 0.005 * (1.0 if visible else 0.0)
        if not visible:
            if self._ep is not None:
                self._ep["gap"] += 1
                if self._ep["gap"] > 8:
                    return self._close(t)
            return None
        on = self.onset()
        if self._ep is None:
            if a > on:
                self._ep = {"t0": t, "amps": [a], "fired": False, "below": 0, "gap": 0,
                            "relevant": relevant, "prompted": prompted}
            elif self.cfg.adapt:
                self._update_noise(a)
            return None
        self._ep["relevant"] |= relevant
        self._ep["prompted"] |= prompted
        if a > on:
            self._ep["amps"].append(a)
            self._ep["below"] = 0
            return None
        self._ep["below"] += 1
        if self._ep["below"] >= 2:
            return self._close(t)
        return None

    def mark_fired(self) -> None:
        if self._ep is not None:
            self._ep["fired"] = True
        elif self.last_episode is not None:
            self.last_episode.fired = True

    @property
    def in_episode(self) -> bool:
        return self._ep is not None

    def incidental_ceiling(self) -> float:
        """Strength below which an unprompted, undetected episode could just be
        incidental movement (95th percentile of the incidental model), never
        more than 60% of the current intent level."""
        hi = self.inc_mu + 1.645 * np.sqrt(self.inc_var)
        return float(min(hi, 0.6 * max(self.x[0], 0.0)))

    def weak_count(self, t: float, window: float) -> int:
        while self.weak_times and self.weak_times[0] < t - window:
            self.weak_times.popleft()
        return len(self.weak_times)

    def _close(self, t: float) -> Episode | None:
        ep, self._ep = self._ep, None
        amps = np.asarray(ep["amps"], dtype=float)
        if len(amps) < self.cfg.min_episode_frames[self.name]:
            return None  # too short to be deliberate (e.g. a natural blink)
        e = Episode(self.name, ep["t0"], t, float(amps.mean()), float(amps.std()),
                    len(amps), ep["fired"])
        self.raw_recent.append(e.fired)
        if not ep["relevant"] and not e.fired:
            # Idle channel: this is incidental movement. Learn what it looks like.
            if self.cfg.adapt:
                d = e.strength - self.inc_mu
                self.inc_mu += 0.1 * d
                self.inc_var = 0.9 * (self.inc_var + 0.1 * d * d)
            self.last_episode = e
            return e
        trusted = e.fired or ep["prompted"] or e.strength >= self.incidental_ceiling()
        if not trusted:
            self.weak_times.append(t)
            self.last_episode = e
            return e
        e.trusted = True
        if self.cfg.adapt:
            accepted, _ = self._kalman_update(t, e.strength)
            e.accepted = accepted
            if accepted:
                self.s_i = 0.7 * self.s_i + 0.3 * max(e.sd, 0.03)
                self.D = 0.7 * self.D + 0.3 * e.n
                if e.n >= 3:
                    prof = _resample(amps / max(amps.mean(), 1e-6))
                    self.shape = 0.8 * self.shape + 0.2 * prof
                    self.shape = self.shape / self.shape.mean()
            if self.cfg.changepoint:
                self.bocpd.update(e.strength)
                if self.bocpd.n >= self.cfg.cp_min_obs:
                    self.cp_prob = self.bocpd.p_recent(3)
                    if self.cp_prob > self.cfg.cp_threshold:
                        new_level = self.bocpd.segment_mean()
                        self._reanchor(t, new_level)
                        self.cp_until = t + self.cfg.cp_flag_s
                        self.bocpd.reset()
                        e.accepted = True
                        self._cp_event = (t, new_level)
        # Observed reliability from trusted attempts only.
        lam = 0.85
        self.beta_a = 1 + lam * (self.beta_a - 1) + (1.0 if e.fired else 0.0)
        self.beta_b = 1 + lam * (self.beta_b - 1) + (0.0 if e.fired else 1.0)
        self.last_episode = e
        return e

    def failed_check(self, t: float, peak: float) -> float | None:
        """A prompted check failed. The strongest amplitude seen while asked is
        a (censored) observation of current strength. If it's below what we
        believe, re-anchor there; never raise our belief on a failure."""
        if not self.cfg.adapt:
            return None
        est = max(peak, 0.0) / float(np.max(self.shape))  # peak -> mean-above-onset strength
        level, _ = self.level_at(t)
        if est >= level:
            return None
        self._reanchor(t, est)
        self.bocpd.reset()
        self.beta_b += 1.0
        return est

    def pop_changepoint(self) -> tuple[float, float] | None:
        cp = getattr(self, "_cp_event", None)
        self._cp_event = None
        return cp

    # --------------------------------------------------------------- readouts
    @property
    def obs_rate(self) -> tuple[float, float]:
        """(mean, effective n) of the observed hit rate."""
        n = self.beta_a + self.beta_b - 2
        return self.beta_a / (self.beta_a + self.beta_b), n

    def baseline_rate(self) -> tuple[float, int]:
        n = len(self.raw_recent)
        return (sum(self.raw_recent) / n if n else 1.0), n

    def recent_changepoint(self, t: float) -> bool:
        return t < self.cp_until


def _resample(x: np.ndarray, n: int = 16) -> np.ndarray:
    return np.interp(np.linspace(0, 1, n), np.linspace(0, 1, len(x)), x)


def _cap_level_var(P: np.ndarray, cap: float) -> None:
    """Cap the level variance in place, shrinking the covariance to stay PSD."""
    if P[0, 0] > cap:
        r = np.sqrt(cap / P[0, 0])
        P[0, 0] = cap
        P[0, 1] *= r
        P[1, 0] *= r
