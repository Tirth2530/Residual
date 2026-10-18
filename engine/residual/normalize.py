"""Calibration: turn raw per-channel features into normalized amplitudes and
fit the engine's per-channel models.

Raw features (pinch closure, eyebrow blendshape, blink blendshape) have
arbitrary scales per person and camera. Calibration (calibration.py, success-driven) fixes that. The older fixed-time version
below (`PHASES`, `fit`) is kept for recorded sessions and the trace-library tool:

    rest   5 s   relax, look at the screen
    hand   7 s   pinch 3 times
    brow   7 s   raise your eyebrows 3 times
    blink  8 s   3 long blinks

Normalized amplitude = (raw - rest_level) / (gesture_peak - rest_level),
so ~0 at rest and ~1 at a calibrated deliberate gesture. All downstream
code (engine, degradation, evaluation) works in these units.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import CHANNELS

PHASES: list[tuple[str, str | None, float, str]] = [
    ("rest", None, 5.0, "Relax and look at the screen"),
    ("hand", "hand", 7.0, "Pinch your fingers together 3 times"),
    ("brow", "brow", 7.0, "Raise your eyebrows 3 times"),
    ("blink", "blink", 8.0, "Do 3 long blinks (hold each about half a second)"),
]


@dataclass
class Normalizer:
    """rest / peak are per raw feature; `sources` says which features make up each channel.
    A channel's amplitude is the max over its sources (each normalized to this person), so an
    eyebrow raise that shows up strongly in only one feature still counts."""
    rest: dict[str, float] = field(default_factory=lambda: {c: 0.0 for c in CHANNELS})
    peak: dict[str, float] = field(default_factory=lambda: {c: 1.0 for c in CHANNELS})
    sources: dict[str, list[str]] = field(default_factory=lambda: {c: [c] for c in CHANNELS})

    def source(self, s: str, v: float) -> float:
        r, p = self.rest.get(s, 0.0), self.peak.get(s, 1.0)
        return float(np.clip((v - r) / max(p - r, 1e-3), -0.5, 1.8))

    def __call__(self, raw: dict[str, float]) -> dict[str, float]:
        out = {}
        for c in CHANNELS:
            vals = [self.source(s, raw[s]) for s in self.sources.get(c, [c]) if s in raw]
            out[c] = max(vals) if vals else 0.0
        return out

    def visible(self, raw: dict) -> dict[str, bool]:
        return {c: any(s in raw for s in self.sources.get(c, [c])) for c in CHANNELS}

    def to_dict(self) -> dict:
        return {"rest": self.rest, "peak": self.peak, "sources": self.sources}

    @classmethod
    def from_dict(cls, d: dict) -> "Normalizer":
        return cls(rest=dict(d["rest"]), peak=dict(d["peak"]),
                   sources={k: list(v) for k, v in d.get("sources", {c: [c] for c in CHANNELS}).items()})


def fit(recordings: dict[str, list[dict[str, float]]]) -> tuple[Normalizer, dict, dict]:
    """recordings: phase name -> list of raw frames (dict channel -> value; frames
    where a channel wasn't visible should omit it).

    Returns (normalizer, rest_amps, gesture_amps) where the last two are in
    normalized units, ready for Engine.calibrate().
    """
    norm = Normalizer()
    for c in CHANNELS:
        rest_vals = [f[c] for f in recordings.get("rest", []) if c in f]
        g_vals = [f[c] for f in recordings.get(c, []) if c in f]
        if len(rest_vals) >= 5:
            norm.rest[c] = float(np.median(rest_vals))
        if len(g_vals) >= 5:
            norm.peak[c] = float(np.percentile(g_vals, 95))
        if norm.peak[c] - norm.rest[c] < 1e-3:
            norm.peak[c] = norm.rest[c] + 1.0  # no usable gesture: leave unscaled

    rest_amps = {c: [norm({c: f[c]})[c] for f in recordings.get("rest", []) if c in f]
                 for c in CHANNELS}
    gestures: dict[str, list[list[float]]] = {}
    for c in CHANNELS:
        series = np.array([norm({c: f[c]})[c] for f in recordings.get(c, []) if c in f])
        gestures[c] = split_gestures(series)
    return norm, rest_amps, gestures


def split_gestures(series: np.ndarray, thresh: float = 0.3, pad: int = 3) -> list[list[float]]:
    """Cut a calibration phase into individual gestures: runs above `thresh`,
    padded so the rise and fall are included."""
    above = series > thresh
    out: list[list[float]] = []
    i, n = 0, len(series)
    while i < n:
        if above[i]:
            j = i
            while j < n and above[j]:
                j += 1
            if j - i >= 3:
                out.append(list(series[max(0, i - pad): min(n, j + pad)]))
            i = j
        else:
            i += 1
    return out
