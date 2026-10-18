"""Small, dependency-free statistics helpers used by the core."""

from __future__ import annotations

import math

_SQRT2 = math.sqrt(2.0)
_SQRT2PI = math.sqrt(2.0 * math.pi)


def phi(x: float) -> float:
    """Standard normal pdf."""
    return math.exp(-0.5 * x * x) / _SQRT2PI


def Phi(x: float) -> float:
    """Standard normal cdf."""
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def censored_moments(mu: float, sigma: float, upper: float) -> tuple[float, float]:
    """Mean and variance of min(Y, upper) for Y ~ N(mu, sigma^2).

    Used because per-frame log-likelihood ratios are clipped from above.
    """
    if sigma <= 1e-12:
        x = min(mu, upper)
        return x, 1e-12
    b = (upper - mu) / sigma
    Pb, pb = Phi(b), phi(b)
    m = mu * Pb - sigma * pb + upper * (1.0 - Pb)
    m2 = (mu * mu + sigma * sigma) * Pb - sigma * (mu + upper) * pb + upper * upper * (1.0 - Pb)
    return m, max(m2 - m * m, 1e-12)


def _erf_np(x):
    """Vectorized erf (Abramowitz & Stegun 7.1.26, |error| < 1.5e-7)."""
    import numpy as np

    x = np.asarray(x, dtype=float)
    sgn = np.sign(x)
    a = np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * a)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t
               + 0.254829592) * t * np.exp(-a * a)
    return sgn * y


def censored_moments_np(mu, sigma, upper):
    """Vectorized censored_moments over arrays of mu (sigma, upper scalars or arrays)."""
    import numpy as np

    mu = np.asarray(mu, dtype=float)
    sigma = np.maximum(np.asarray(sigma, dtype=float), 1e-12)
    b = (upper - mu) / sigma
    Pb = 0.5 * (1.0 + _erf_np(b / _SQRT2))
    pb = np.exp(-0.5 * b * b) / _SQRT2PI
    m = mu * Pb - sigma * pb + upper * (1.0 - Pb)
    m2 = (mu * mu + sigma * sigma) * Pb - sigma * (mu + upper) * pb + upper * upper * (1.0 - Pb)
    return m, np.maximum(m2 - m * m, 1e-12)


def Phi_np(x):
    import numpy as np

    return 0.5 * (1.0 + _erf_np(np.asarray(x, dtype=float) / _SQRT2))


def cusum_arl(mean: float, var: float, bound: float) -> float:
    """Siegmund's approximation to the average run length (in steps) of the
    CUSUM S_t = max(0, S_{t-1} + x_t) before it crosses `bound`, when the
    increments x_t have the given mean and variance.

    mean > 0 (a real gesture): ARL ~ bound / mean, the detection delay.
    mean < 0 (resting noise):  ARL is large, its inverse is the false-alarm rate.
    """
    sd = math.sqrt(max(var, 1e-12))
    b = bound / sd + 1.166
    delta = mean / sd
    if abs(delta) < 1e-6:
        return b * b
    x = -2.0 * delta * b
    if x > 700:
        return float("inf")
    return (math.exp(x) + 2.0 * delta * b - 1.0) / (2.0 * delta * delta)


def cusum_arl_markov(mu: float, sd: float, clip: float, bound: float, nbins: int = 64) -> float:
    """Average run length (frames) of S_t = max(0, S_{t-1} + min(Y_t, clip)),
    Y ~ N(mu, sd^2), until S crosses `bound`. Exact up to discretization:
    the CUSUM state is binned on [0, bound] and the expected absorption time
    of the resulting Markov chain is solved directly.

    Siegmund's approximation (cusum_arl) badly overstates false alarms when
    increments are clipped: reaching the bound then needs a run of several
    near-maximal frames, which the clipped Markov chain captures exactly.
    """
    import numpy as np

    sd = max(sd, 1e-9)
    edges = np.linspace(0.0, bound, nbins + 1)
    centers = 0.5 * (edges[:-1] + edges[1:])
    centers[0] = 0.0  # the reset state
    # cdf of s + X at every bin edge, X = min(Y, clip): below s + clip only the
    # continuous part counts; at or above s + clip everything (incl. the atom) is in.
    z = (edges[None, :] - centers[:, None] - mu) / sd
    F = np.where(edges[None, :] >= centers[:, None] + clip, 1.0, Phi_np(z))
    Q = np.diff(F, axis=1)  # mass landing in each bin; mass above the bound is absorbed
    Q[:, 0] += F[:, 0]  # at or below 0: reset to state 0
    A = np.eye(nbins) - Q
    try:
        tvec = np.linalg.solve(A, np.ones(nbins))
    except np.linalg.LinAlgError:
        return float("inf")
    arl = float(tvec[0])
    return arl if arl > 0 else float("inf")


def wolpaw_bits(n_options: int, accuracy: float) -> float:
    """Bits per selection (Wolpaw et al. 2002), the standard BCI/switch ITR."""
    n = n_options
    p = min(max(accuracy, 0.0), 1.0)
    if p <= 1.0 / n:
        return 0.0
    bits = math.log2(n) + p * math.log2(p)
    if p < 1.0:
        bits += (1.0 - p) * math.log2((1.0 - p) / (n - 1))
    return bits
