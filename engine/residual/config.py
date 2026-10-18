"""All tunable numbers live here. Nothing in the core hard-codes a magic constant.

Every value has a one-line reason. If you change one, say why in the commit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CHANNELS: tuple[str, ...] = ("hand", "brow", "blink")


@dataclass(frozen=True)
class ActionClass:
    """Per-action error budgets. Wald's SPRT bound is A = ln((1 - beta) / alpha).

    alpha: tolerated false-trigger probability per decision (cost of acting wrongly)
    beta:  tolerated miss probability per attempt (cost of not acting)
    """

    alpha: float
    beta: float
    confirm: bool = False


ACTION_CLASSES: dict[str, ActionClass] = {
    # Everyday phrase: balanced.
    "say": ActionClass(alpha=0.01, beta=0.10),
    # Safety request: missing it is the worst outcome, so a lower bar.
    "help": ActionClass(alpha=0.03, beta=0.02),
    # Irreversible (sends a message): false triggers are costly, and it needs a confirm.
    "send": ActionClass(alpha=0.002, beta=0.15, confirm=True),
}


@dataclass
class Config:
    fps: float = 30.0

    # --- Perception / episode segmentation -------------------------------
    k_onset: float = 3.0  # episode starts when amplitude > mu_noise + k * sd_noise
    min_onset: float = 0.06  # floor so a perfectly still channel isn't hair-trigger
    min_episode_frames: dict[str, int] = field(
        # Blink needs a long, deliberate blink (~0.33 s); natural blinks are ~0.1-0.15 s.
        default_factory=lambda: {"hand": 4, "brow": 4, "blink": 10}
    )
    noise_alpha: float = 0.005  # EW rate for resting-noise stats

    # --- Decisions (CUSUM / SPRT) -----------------------------------------
    # Per-frame LLR is clipped so no single frame can trigger an action.
    # A low blink clip forces sustained evidence, which filters natural blinks.
    llr_clip: dict[str, float] = field(
        default_factory=lambda: {"hand": 1.0, "brow": 1.0, "blink": 0.65}
    )
    refractory_s: float = 0.7  # after a fire, ignore evidence briefly
    # One physical gesture = at most one action. After a fire, the channel is DISARMED until it
    # has actually returned to neutral (out of its episode, below the onset threshold) for this
    # long. A refractory timer alone is not enough: a held eyebrow would fire again after it.
    release_s: float = 0.25
    # Some channels act on release: a long blink selects when the eyes reopen, and only if the
    # closure was shorter than max_hold_s. Eyes held shut longer (resting, or the three-closure
    # help pattern) never select.
    fire_on_release: dict[str, bool] = field(
        default_factory=lambda: {"hand": False, "brow": False, "blink": True}
    )
    max_hold_s: dict[str, float] = field(default_factory=lambda: {"hand": 99.0, "brow": 99.0, "blink": 1.0})
    # A channel the engine offers for Next / Back must be this reliable (and calibrated VALID/WEAK).
    aux_min_p: float = 0.85

    # --- Ability tracking (Kalman, local linear trend) --------------------
    kalman_q: float = 5e-6  # process noise spectral density on the trend
    trend_sd0: float = 0.002  # prior sd of the trend (strength units per second)
    max_trend: float = 0.02  # physical cap: strength can't fall faster than ~1.2 per minute
    kalman_r: float = 0.08**2  # observation noise on an episode's strength
    gate_sigma: float = 3.5  # reject observations this many sd from prediction
    trend_horizon_s: float = 8.0  # never extrapolate a trend (or grow uncertainty) further than this
    max_level_var: float = 0.08**2  # cap on strength uncertainty while a channel is unobserved

    # --- Changepoints (BOCPD, Adams & MacKay 2007) ------------------------
    hazard: float = 1 / 25  # prior: an abrupt change every ~25 attempts
    cp_obs_sd: float = 0.08
    cp_threshold: float = 0.6  # P(run length < 3) above this = changepoint
    cp_min_obs: int = 5  # don't flag until the segment has some history
    cp_flag_s: float = 6.0  # how long a changepoint counts as "recent"

    # --- Control policy (adapt -> fuse -> migrate) ------------------------
    p_switch: float = 0.70  # active option's predicted p_hit below this = degrading
    p_accept: float = 0.85  # a candidate needs at least this p_hit
    dwell_s: float = 1.5  # degrading must persist this long before acting
    fa_max_per_min: float = 1.0  # false-activation budget an option must meet
    verify_window_s: float = 6.0
    verify_hits: int = 2
    cooldown_s: float = 30.0  # a failed candidate / abandoned channel rests this long
    proactive_margin: float = 0.10  # below p_accept, move if an alternative beats us by this
    upgrade_margin: float = 0.07  # leave fusion only for a single channel with p_hit >= p_accept + this
    weak_window_s: float = 20.0  # ambiguous weak movements counted over this window
    weak_probe_count: int = 2  # this many -> run a check (probe) on the active channel
    probe_cooldown_s: float = 20.0
    idle_check_s: float = 15.0  # heartbeat: check the active channel after this long with no selection
    idle_check_max_s: float = 120.0  # backoff ceiling while a person is simply resting
    obs_rate_floor: float = 0.5  # observed hit rate below this also counts as degrading
    obs_rate_min_n: float = 3.0

    # Baseline knobs (threshold / hysteresis policies used in evaluation)
    baseline_window: int = 5
    baseline_min_attempts: int = 4
    baseline_dwell_s: float = 30.0
    baseline_return_block_s: float = 60.0

    # --- Scanning board ---------------------------------------------------
    scan_period_s: float = 1.2
    adaptive_scan: bool = False  # live demo may turn this on; eval keeps it fixed
    confirm_window_s: float = 3.0

    # --- Feature flags (ablations + baselines) ----------------------------
    adapt: bool = True  # Kalman/noise tracking updates the model
    fusion: bool = True
    changepoint: bool = True
    verify: bool = True
    policy_mode: str = "residual"  # residual | fixed | threshold | hysteresis | truth_switch

    channel_order: tuple[str, ...] = CHANNELS
    initial_control: tuple[str, ...] = ("hand",)

    def bound(self, action_class: str) -> float:
        import math

        ac = ACTION_CLASSES[action_class]
        return math.log((1 - ac.beta) / ac.alpha)


def preset(name: str) -> Config:
    """Named configurations used by the evaluation and the ablation table."""
    c = Config()
    if name == "residual":
        return c
    if name == "fixed":
        c.adapt, c.policy_mode = False, "fixed"
    elif name == "threshold":
        c.adapt, c.policy_mode = False, "threshold"
    elif name == "hysteresis":
        c.adapt, c.policy_mode = False, "hysteresis"
    elif name == "truth_switch":
        # Knows the true degradation schedule and moves at once to the least-degraded channel,
        # with no verification. NOT an upper bound: see docs/EVALUATION.md.
        c.policy_mode, c.fusion = "truth_switch", False
    elif name == "no_adapt":
        c.adapt = False
    elif name == "no_fusion":
        c.fusion = False
    elif name == "no_changepoint":
        c.changepoint = False
    elif name == "no_verify":
        c.verify = False
    else:
        raise ValueError(f"unknown preset {name!r}")
    return c
