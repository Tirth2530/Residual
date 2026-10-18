"""Synthetic person + session runner.

A SimUser watches the engine like a real user would: it picks a phrase,
waits for it to light up on the board, then gestures on whatever channel(s)
the screen says are in control. During verification it does the requested
gesture twice. It also produces realistic background activity: natural
blinks, incidental eyebrow movement while talking, and sensor noise.

Degradation is applied to the raw amplitudes through degrade.Degradation,
the same code the live slider uses.

This is how we sanity-check the system without a webcam, and how the
evaluation compares Residual against baselines on identical sessions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .config import CHANNELS, Config
from .degrade import Degradation, from_level
from .engine import Engine
from .events import Frame

GESTURE_S = {"hand": 0.6, "brow": 0.6, "blink": 0.7}
NOISE_SD = 0.035


@dataclass
class Scenario:
    """How each channel degrades over a session.

    kind:      degradation kind (shrink | jitter | dropout | mixed)
    severity:  final slider level, 0..1
    channels:  which channels degrade
    shape:     "ramp" (gradual, 20%-80% of the session) or "step" (abrupt at 45%)
    """

    kind: str = "shrink"
    severity: float = 0.8
    channels: tuple[str, ...] = ("hand",)
    shape: str = "ramp"
    duration_s: float = 180.0

    def level(self, ch: str, t: float) -> float:
        if ch not in self.channels:
            return 0.0
        T = self.duration_s
        if self.shape == "step":
            return self.severity if t >= 0.45 * T else 0.0
        x = (t - 0.2 * T) / (0.6 * T)
        return self.severity * float(np.clip(x, 0.0, 1.0))

    def degradation(self, ch: str, t: float) -> Degradation:
        return from_level(self.level(ch, t), self.kind)

    def best_channel(self, t: float, current: str = "hand") -> str:
        """Ground truth for the truth_switch baseline: the least-degraded channel. It only
        leaves its current channel when another is clearly better (by 0.15 of slider level),
        so it doesn't switch for nothing."""
        best = min(CHANNELS, key=lambda c: (self.level(c, t), CHANNELS.index(c)))
        if self.level(current, t) - self.level(best, t) > 0.15:
            return best
        return current


class Gestures:
    """Superposition of gestures on one channel: synthetic half-sines, or real
    recorded traces from a library (trace-driven simulation)."""

    def __init__(self) -> None:
        self.items: list[tuple] = []  # (t0, dur, peak, trace|None, fps)

    def add(self, t0: float, dur: float, peak: float) -> None:
        self.items.append((t0, dur, peak, None, 0.0))

    def add_trace(self, t0: float, trace: np.ndarray, scale: float, fps: float) -> None:
        self.items.append((t0, len(trace) / fps, scale, trace, fps))

    def value(self, t: float) -> float:
        v = 0.0
        keep = []
        for it in self.items:
            t0, dur, pk, trace, fps = it
            if t < t0 + dur + 0.05:
                keep.append(it)
            if t0 <= t <= t0 + dur:
                if trace is None:
                    v += pk * np.sin(np.pi * (t - t0) / dur)
                else:
                    v += pk * float(trace[min(int((t - t0) * fps), len(trace) - 1)])
        self.items = keep
        return v


class Library:
    """Real normalized traces recorded with `python -m residual.perception --library`."""

    def __init__(self, path: str):
        with open(path) as f:
            d = json.load(f)
        self.fps = float(d.get("fps", 30.0))
        self.rest = {c: np.asarray(d["rest"].get(c, []), dtype=float) for c in CHANNELS}
        self.gestures = {c: [np.asarray(g, dtype=float) for g in d["gestures"].get(c, [])]
                         for c in CHANNELS}
        for c in CHANNELS:
            if len(self.gestures[c]) == 0 or len(self.rest[c]) < 30:
                raise ValueError(f"library {path} has too little data for {c}")


@dataclass
class SimUser:
    scenario: Scenario
    rng: np.random.Generator
    n_options: int = 6
    reaction_s: float = 0.30
    target: int | None = None
    next_target_t: float = 1.0
    acted_cycle: int = -1
    verify_done_for: float = -1.0
    g: dict[str, Gestures] = field(default_factory=lambda: {c: Gestures() for c in CHANNELS})
    next_blink: float = 0.0
    next_brow: float = 0.0
    # bookkeeping for metrics
    attempts: list[dict] = field(default_factory=list)
    misses: int = 0
    _pending: dict | None = None
    library: Library | None = None
    _rest_i: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Common random numbers: separate streams for the task (targets,
        # reaction times), motor variability and background activity, so
        # different systems see the same person doing the same things even
        # though their decisions differ. Comparisons are then paired.
        s = self.rng.integers(2**32, size=3)
        self.r_task = np.random.default_rng(int(s[0]))
        self.r_motor = np.random.default_rng(int(s[1]))
        self.r_bg = np.random.default_rng(int(s[2]))
        self.next_blink = self.r_bg.exponential(4.0)
        self.next_brow = self.r_bg.exponential(12.0)
        if self.library:
            self._rest_i = {c: int(self.rng.integers(len(self.library.rest[c]))) for c in CHANNELS}

    def gesture(self, ch: str, t0: float) -> None:
        if self.library:
            traces = self.library.gestures[ch]
            tr = traces[int(self.r_motor.integers(len(traces)))]
            self.g[ch].add_trace(t0, tr, self.r_motor.uniform(0.9, 1.1), self.library.fps)
            return
        dur = GESTURE_S[ch] * self.r_motor.uniform(0.85, 1.2)
        self.g[ch].add(t0, dur, self.r_motor.uniform(0.85, 1.1))

    def act(self, t: float, eng: Engine) -> None:
        pol, board = eng.policy, eng.board
        # Background activity (a recorded library already contains real
        # natural blinks and incidental movement in its rest traces)
        if self.library is None and t >= self.next_blink:
            self.g["blink"].add(t, self.r_bg.uniform(0.08, 0.15), 1.0)
            self.next_blink = t + self.r_bg.exponential(4.0)
        if self.library is None and t >= self.next_brow:
            self.g["brow"].add(t, self.r_bg.uniform(0.3, 0.5), self.r_bg.uniform(0.15, 0.3))
            self.next_brow = t + self.r_bg.exponential(12.0)

        # Verification prompt: do the candidate gesture twice
        if pol.state == "VERIFY" and pol.candidate and self.verify_done_for != pol.verify_t0:
            self.verify_done_for = pol.verify_t0
            for k, dt in enumerate((0.8, 2.4)):
                for c in pol.candidate:
                    self.gesture(c, t + dt)
            return
        if pol.state == "VERIFY" or board.confirm is not None:
            return

        # Pick a phrase
        if self.target is None and t >= self.next_target_t:
            self.target = int(self.r_task.integers(self.n_options))
        if self.target is None:
            return
        # Gesture when the target lights up (once per highlight)
        if board.highlight == self.target and self.acted_cycle != board.cycle:
            self.acted_cycle = board.cycle
            # A real user doesn't start a gesture they can't finish in time;
            # they wait for the option to come round again.
            if board.t_next is not None and board.t_next - t < 0.75:
                return
            t0 = t + self.reaction_s + self.r_task.uniform(0, 0.1)
            for c in pol.control:
                self.gesture(c, t0)
            if self._pending is not None:  # previous attempt never landed
                self.misses += 1
                self._pending["hit"] = False
                self.attempts.append(self._pending)
            st = eng.stats.get(pol.control)
            self._pending = {"t": t0, "control": list(pol.control),
                             "pred": st.p_hat if st else None, "hit": None}

    def on_select(self, t: float, index: int) -> bool:
        """Returns True if the selection was what the user wanted."""
        ok = index == self.target
        if self._pending is not None:
            self._pending["hit"] = ok
            self.attempts.append(self._pending)
            self._pending = None
        if ok:
            self.target = None
            self.next_target_t = t + self.r_task.uniform(0.6, 1.2)
        return ok

    def frame(self, t: float) -> Frame:
        amps, vis = {}, {}
        for c in CHANNELS:
            if self.library:
                r = self.library.rest[c]
                self._rest_i[c] = (self._rest_i[c] + 1) % len(r)
                noise = float(r[self._rest_i[c]])
            else:
                noise = self.rng.normal(0.0, NOISE_SD)
            raw = self.g[c].value(t) + noise
            a, v = self.scenario.degradation(c, t).apply(raw, self.rng)
            amps[c], vis[c] = a, v
        return Frame(t, amps, vis)


def simulated_calibration(rng: np.random.Generator, fps: float = 30.0,
                          library: Library | None = None) -> tuple[dict, dict]:
    """10 s of rest, then 4 deliberate gestures per channel (undegraded)."""
    if library is not None:
        rest = {c: list(library.rest[c][: int(10 * fps)]) for c in CHANNELS}
        gestures = {}
        for c in CHANNELS:
            idx = rng.choice(len(library.gestures[c]), size=min(4, len(library.gestures[c])),
                             replace=False)
            gestures[c] = [list(library.gestures[c][i]) for i in idx]
        return rest, gestures
    rest = {c: list(rng.normal(0.0, NOISE_SD, int(10 * fps))) for c in CHANNELS}
    gestures: dict[str, list[list[float]]] = {}
    for c in CHANNELS:
        reps = []
        for _ in range(4):
            dur = GESTURE_S[c] * rng.uniform(0.85, 1.2)
            n = int(dur * fps)
            ts = np.arange(n) / fps
            pk = rng.uniform(0.85, 1.1)
            reps.append(list(pk * np.sin(np.pi * ts / dur) + rng.normal(0, NOISE_SD, n)))
        gestures[c] = reps
    return rest, gestures


@dataclass
class SessionResult:
    correct: int = 0
    wrong: int = 0
    misses: int = 0
    switches: int = 0
    duration_s: float = 0.0
    attempts: list[dict] = field(default_factory=list)
    events: list = field(default_factory=list)

    @property
    def selections(self) -> int:
        return self.correct + self.wrong

    @property
    def accuracy(self) -> float:
        return self.correct / self.selections if self.selections else 0.0


def run_session(cfg: Config, scenario: Scenario, seed: int,
                on_step: Callable[[Engine, list], None] | None = None,
                keep_events: bool = False, library: Library | None = None) -> SessionResult:
    rng = np.random.default_rng(seed)
    eng = Engine(cfg)
    rest, gestures = simulated_calibration(rng, cfg.fps, library)
    eng.calibrate(rest, gestures, t=0.0)
    if cfg.policy_mode == "truth_switch":
        eng.policy.truth = scenario.best_channel
    user = SimUser(scenario, rng, n_options=len(eng.board.options), library=library)
    res = SessionResult(duration_s=scenario.duration_s)
    n = int(scenario.duration_s * cfg.fps)
    for i in range(n):
        t = i / cfg.fps
        user.act(t, eng)
        evs = eng.step(user.frame(t))
        for e in evs:
            if e.kind == "select":
                if user.on_select(t, e.data["index"]):
                    res.correct += 1
                else:
                    res.wrong += 1
        if keep_events:
            res.events.extend(evs)
        if on_step:
            on_step(eng, evs)
    res.misses = user.misses
    res.switches = eng.policy.switches
    res.attempts = user.attempts
    return res
