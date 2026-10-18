"""Layer 6: the scanning board the person uses.

Options highlight one at a time. One gesture (a "fire") selects whichever
option is lit. Irreversible options need a second gesture to confirm.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import ACTION_CLASSES, Config


@dataclass(frozen=True)
class BoardOption:
    label: str
    cls: str = "say"  # say | help | send
    text: str = ""  # what gets spoken or sent; defaults to label

    @property
    def speech(self) -> str:
        return self.text or self.label


def default_board() -> list[BoardOption]:
    return [
        BoardOption("I'm thirsty"),
        BoardOption("I'm in pain"),
        BoardOption("Yes"),
        BoardOption("No"),
        BoardOption("Thank you"),
        BoardOption("Get help", "help", "I need help"),
    ]


class ScanBoard:
    def __init__(self, options: list[BoardOption], cfg: Config):
        self.options = options
        self.cfg = cfg
        self.highlight = 0
        self.period = cfg.scan_period_s
        self.t_next: float | None = None
        self.paused = False
        self.confirm: dict | None = None  # {"index", "until"}
        self.cycle = 0  # increments every time the highlight moves

    def current(self) -> BoardOption:
        return self.options[self.highlight]

    def current_class(self) -> str:
        if self.confirm is not None:
            return self.options[self.confirm["index"]].cls
        return self.current().cls

    def update(self, t: float, latency_s: float | None = None) -> list[tuple[str, int]]:
        """Advance the highlight. Returns [("cancel", idx)] if a confirm timed out."""
        out: list[tuple[str, int]] = []
        if self.cfg.adaptive_scan and latency_s is not None and latency_s < 5:
            # Leave room for reaction (~0.35 s) plus detection latency.
            self.period = max(self.cfg.scan_period_s, 1.5 * latency_s + 0.6)
        if self.t_next is None:
            self.t_next = t + self.period
        if self.confirm is not None:
            if t > self.confirm["until"]:
                out.append(("cancel", self.confirm["index"]))
                self.confirm = None
                self.t_next = t + self.period
            return out
        if self.paused:
            self.t_next = t + self.period
            return out
        if t >= self.t_next:
            self.highlight = (self.highlight + 1) % len(self.options)
            self.t_next = t + self.period
            self.cycle += 1
        return out

    def on_fire(self, t: float) -> tuple[str, int]:
        """Returns ("select", idx) or ("confirm", idx)."""
        if self.confirm is not None:
            idx = self.confirm["index"]
            self.confirm = None
            self._restart(t)
            return "select", idx
        idx = self.highlight
        if ACTION_CLASSES[self.options[idx].cls].confirm:
            self.confirm = {"index": idx, "until": t + self.cfg.confirm_window_s}
            return "confirm", idx
        self._restart(t)
        return "select", idx

    def _restart(self, t: float) -> None:
        self.highlight = 0
        self.t_next = t + self.period
        self.cycle += 1
