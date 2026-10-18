"""Degradation simulator.

HONESTY RULE: this corrupts the raw input amplitudes only. It never touches a
score, a threshold or a model. Reliability must fall on its own because real
gestures start getting missed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Degradation:
    """How one channel's raw signal is corrupted.

    shrink:  fraction of gesture size lost (0 = none, 0.9 = only 10% left)
    jitter:  sd of extra Gaussian noise added to every frame
    dropout: probability a frame is lost (tracking failure)
    """

    shrink: float = 0.0
    jitter: float = 0.0
    dropout: float = 0.0

    def apply(self, a: float, rng: np.random.Generator) -> tuple[float, bool]:
        if self.dropout > 0 and rng.random() < self.dropout:
            return 0.0, False
        a = a * (1.0 - self.shrink)
        if self.jitter > 0:
            a += rng.normal(0.0, self.jitter)
        return float(a), True

    @property
    def is_identity(self) -> bool:
        return self.shrink == 0 and self.jitter == 0 and self.dropout == 0


def from_level(level: float, kind: str = "mixed") -> Degradation:
    """Map one 0..1 slider to a degradation of a given kind.

    kinds: shrink | jitter | dropout | mixed
    The live demo slider uses "mixed". The evaluation sweeps every kind and
    reports some of them as held out (never used while tuning).
    """
    x = float(np.clip(level, 0.0, 1.0))
    if kind == "shrink":
        return Degradation(shrink=0.92 * x)
    if kind == "jitter":
        return Degradation(shrink=0.35 * x, jitter=0.22 * x)
    if kind == "dropout":
        return Degradation(shrink=0.35 * x, dropout=0.6 * x)
    if kind == "mixed":
        return Degradation(shrink=0.85 * x, jitter=0.06 * x, dropout=0.25 * x)
    raise ValueError(f"unknown degradation kind {kind!r}")
