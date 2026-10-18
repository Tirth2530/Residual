"""Layer 3: reliability is computed, not counted.

Detector (what the decision layer runs):
    per-frame LLR between  intent ~ N(mu_i, s_i^2)  and  rest ~ N(mu_n, s_n^2),
    with a pooled variance, clipped above, accumulated in a CUSUM.

Predictor (what this module computes): given the tracked gesture strength,
the learned gesture shape, resting noise and how often frames are visible,
the exact distribution of the CUSUM over one gesture. From it:
  * p_hit: probability a gesture of the current strength crosses the bound
  * an interval on p_hit from the Kalman uncertainty in strength
  * fa_per_min: false activations at rest (Siegmund's CUSUM run length)
  * latency: expected time from gesture start to detection

Fused options sum per-channel LLRs, so means and variances add.
The predicted p_hit is checked against observed hit rates (calibration plot).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .mathx import Phi_np, censored_moments_np, cusum_arl_markov

# 5-point Gauss-Hermite (probabilists') nodes/weights for E[f(Z)], Z ~ N(0,1)
_GH_X = np.array([-2.856970, -1.355626, 0.0, 1.355626, 2.856970])
_GH_W = np.array([0.011257, 0.222076, 0.533333, 0.222076, 0.011257])


def default_shape(n: int = 16) -> np.ndarray:
    """Above-onset part of a half-sine bump, normalized to mean 1."""
    x = np.linspace(0.15, 0.85, n)
    s = np.sin(np.pi * x)
    return s / s.mean()


@dataclass
class ChannelModel:
    name: str
    mu_i: float  # tracked deliberate-gesture strength (mean amplitude above onset)
    var_mu: float  # uncertainty in mu_i (Kalman variance)
    mu_n: float
    s_i: float
    s_n: float
    D: float  # frames per gesture (above onset)
    clip: float
    strength_sd: float = 0.08  # gesture-to-gesture variability
    vis_rate: float = 1.0
    shape: np.ndarray = field(default_factory=default_shape)

    @property
    def s2(self) -> float:
        return 0.5 * (self.s_i**2 + self.s_n**2)

    @property
    def mu_i_eff(self) -> float:
        return max(self.mu_i, self.mu_n + 1e-3)

    @property
    def w(self) -> float:
        return (self.mu_i_eff - self.mu_n) / self.s2

    @property
    def mid(self) -> float:
        return 0.5 * (self.mu_i_eff + self.mu_n)

    @property
    def d_prime(self) -> float:
        return (self.mu_i_eff - self.mu_n) / math.sqrt(self.s2)

    def llr(self, a: float) -> float:
        """Per-frame evidence for 'deliberate gesture' over 'rest', clipped above."""
        return min(self.w * (a - self.mid), self.clip)

    def frames(self) -> np.ndarray:
        n = max(2, int(round(self.D)))
        xs = np.linspace(0, 1, len(self.shape))
        return np.interp(np.linspace(0, 1, n), xs, self.shape)

    def gesture_moments(self, strengths: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Sum over one gesture of clipped LLR increments, for each strength in
        `strengths`: (means, vars). Invisible frames contribute nothing, so
        both scale by the visibility rate."""
        g = self.frames()
        s = np.maximum(np.asarray(strengths, dtype=float), 0.0)[:, None]
        m, v = censored_moments_np(self.w * (s * g[None, :] - self.mid), abs(self.w) * self.s_n,
                                   self.clip)
        # The CUSUM floors at 0, so frames whose expected evidence is negative
        # (the gesture's rise and fall) don't subtract: count only the core.
        core = m > 0
        return (m * core).sum(axis=1) * self.vis_rate, (v * core).sum(axis=1) * self.vis_rate



@dataclass
class OptionStats:
    channels: tuple[str, ...]
    p_hat: float
    p_lo: float
    p_hi: float
    fa_per_min: float
    latency_s: float
    d_prime: float

    def as_dict(self) -> dict:
        return {
            "channels": list(self.channels),
            "p_hat": round(self.p_hat, 4),
            "p_lo": round(self.p_lo, 4),
            "p_hi": round(self.p_hi, 4),
            "fa_per_min": round(self.fa_per_min, 4) if math.isfinite(self.fa_per_min) else None,
            "latency_s": round(self.latency_s, 3) if math.isfinite(self.latency_s) else None,
            "d_prime": round(self.d_prime, 3),
        }


def _p_hit(models: list[ChannelModel], bound: float, z: float) -> float:
    """P(CUSUM over one gesture > bound) when each channel's true strength is
    mu_i + z * sd(mu_i), averaged over gesture-to-gesture variability."""
    M = np.zeros(len(_GH_X))
    V = np.zeros(len(_GH_X))
    for md in models:
        s = md.mu_i + z * math.sqrt(md.var_mu) + _GH_X * md.strength_sd
        m, v = md.gesture_moments(s)
        M += m
        V += v
    p = float(np.sum(_GH_W * (1.0 - Phi_np((bound - M) / np.sqrt(V + 1e-9)))))
    return min(max(p, 0.0), 1.0)


def option_stats(models: list[ChannelModel], bound: float, fps: float) -> OptionStats:
    p_hat = _p_hit(models, bound, 0.0)
    p_lo = _p_hit(models, bound, -1.645)
    p_hi = _p_hit(models, bound, +1.645)

    # False activations: exact run length of the (clipped) CUSUM at rest.
    # Fused options sum per-channel evidence; we approximate the sum of clipped
    # increments by a clipped sum (summed means, variances and clips).
    mu0 = sum(md.w * (md.mu_n - md.mid) for md in models)
    sd0 = math.sqrt(sum((md.w * md.s_n) ** 2 for md in models))
    arl = cusum_arl_markov(mu0, sd0, sum(md.clip for md in models), bound)
    fa = 0.0 if math.isinf(arl) else 60.0 * fps / max(arl, 1.0)

    # Latency: first frame where the expected cumulative evidence crosses the bound.
    n = max(len(md.frames()) for md in models)
    cum = np.zeros(n)
    for md in models:
        g = md.frames()
        inc = censored_moments_np(md.w * (md.mu_i * g - md.mid), abs(md.w) * md.s_n,
                                  md.clip)[0] * md.vis_rate
        cum[: len(inc)] += np.maximum(inc, 0.0)
    cs = np.cumsum(cum)
    hit = np.nonzero(cs > bound)[0]
    latency = float(hit[0] + 1) / fps if len(hit) else float("inf")

    dp = math.sqrt(sum(md.d_prime**2 for md in models))
    return OptionStats(tuple(md.name for md in models), p_hat, p_lo, p_hi, fa, latency, dp)
