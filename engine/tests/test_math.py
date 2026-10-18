import numpy as np

from residual.mathx import (censored_moments, censored_moments_np, cusum_arl_markov,
                            wolpaw_bits)


def test_censored_moments_match_monte_carlo():
    rng = np.random.default_rng(0)
    for mu, sd, clip in [(0.0, 1.0, 0.5), (2.0, 0.5, 1.0), (-1.0, 2.0, 0.3)]:
        y = np.minimum(rng.normal(mu, sd, 400_000), clip)
        m, v = censored_moments(mu, sd, clip)
        assert abs(m - y.mean()) < 0.01
        assert abs(v - y.var()) < 0.02


def test_vectorized_matches_scalar():
    mus = np.linspace(-3, 3, 13)
    m_np, v_np = censored_moments_np(mus, 0.7, 0.8)
    for mu, m, v in zip(mus, m_np, v_np):
        ms, vs = censored_moments(mu, 0.7, 0.8)
        assert abs(m - ms) < 1e-5 and abs(v - vs) < 1e-5


def _mc_arl(mu, sd, clip, bound, n=300_000, seed=1):
    rng = np.random.default_rng(seed)
    y = np.minimum(rng.normal(mu, sd, n), clip)
    S, t, runs = 0.0, 0, []
    for v in y:
        t += 1
        S = max(0.0, S + v)
        if S > bound:
            runs.append(t)
            S, t = 0.0, 0
    return np.mean(runs)


def test_markov_arl_matches_simulation():
    # detection regime (positive drift) and a false-alarm regime (negative drift)
    for mu, sd, clip, bound in [(0.5, 0.5, 1.0, 4.5), (-0.5, 1.0, 1.0, 3.0)]:
        est = cusum_arl_markov(mu, sd, clip, bound)
        mc = _mc_arl(mu, sd, clip, bound)
        assert abs(est - mc) / mc < 0.2, (est, mc)


def test_wolpaw_bits():
    assert wolpaw_bits(6, 1.0) == np.log2(6)
    assert wolpaw_bits(6, 1 / 6) == 0.0
    assert 0 < wolpaw_bits(6, 0.8) < np.log2(6)
