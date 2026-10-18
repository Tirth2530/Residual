"""Layer 5: the control policy, i.e. failover for the body.

Residual mode escalates through three responses as the active option weakens:
  1. Adapt   (automatic: the tracker re-estimates strength, so the LLR midpoint follows it)
  2. Migrate to a verified single channel, if one is reliable enough
  3. Fuse two weak channels, if no single channel is reliable enough

Objective: keep the person's selections working, i.e. maximize the conservative
(lower-bound) hit probability subject to a false-activation budget. Switching
has a cost (the person must change what they do), which is why we require
a dwell, a margin, verification, and cooldowns. Together these are hysteresis.

Baseline modes reproduce what simpler systems do, for the evaluation.
"""

from __future__ import annotations

from itertools import combinations
from typing import Callable

from .config import Config
from .events import Event
from .reliability import OptionStats

Option = tuple[str, ...]


class Policy:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.control: Option = tuple(cfg.initial_control)
        self.state = "ACTIVE"  # ACTIVE | VERIFY
        self.candidate: Option | None = None
        self.verify_t0 = 0.0
        self.verify_count = 0
        self.verify_misses = 0
        self.bad_since: float | None = None
        self.cooldown: dict[Option, float] = {}
        self.last_switch_t = -1e9
        self.abandoned: dict[str, float] = {}
        self.switches = 0
        self._last_note = -1e9
        self.truth: Callable[[float, str], str] | None = None
        self.reason = ""
        self.probing = False  # VERIFY on the active option itself
        self.probe_cooldown_until = -1e9
        self.force_degraded_until = -1e9
        self.probes = 0
        # Heartbeat: if nothing has been selected for a while, check the active
        # channel. A person who can no longer move enough to trigger anything
        # produces no evidence at all, so silence itself must be tested.
        self.last_activity_t = 0.0
        self.idle_check_s = cfg.idle_check_s
        # Explainability: `diag` is the live reasoning (every update), `decisions` keeps the
        # evidence behind each check / verification / migration, so the UI can show *why*.
        self.diag: dict = {}
        self.decisions: list[dict] = []
        self.eligible: set[str] = set(cfg.channel_order)   # calibration-valid channels

    # ---------------------------------------------------------------- options
    def options(self) -> list[Option]:
        chans = [c for c in self.cfg.channel_order if c in self.eligible]
        opts: list[Option] = [(c,) for c in chans]
        if self.cfg.fusion and self.cfg.policy_mode == "residual":
            opts += [tuple(p) for p in combinations(chans, 2)]
        return opts

    @property
    def prompt(self) -> str | None:
        if self.state == "VERIFY" and self.candidate:
            names = " + ".join(_nice(c) for c in self.candidate)
            if self.probing:
                return f"Checking {names}: do it twice"
            return f"Testing {names}: do it twice"
        return None

    # ---------------------------------------------------------------- update
    def update(self, t: float, stats: dict[Option, OptionStats], trackers: dict) -> list[Event]:
        mode = self.cfg.policy_mode
        if mode == "fixed":
            return []
        if mode in ("threshold", "hysteresis"):
            return self._baseline(t, trackers, hysteresis=(mode == "hysteresis"))
        if mode == "truth_switch":
            return self._truth_switch(t)
        return self._residual(t, stats, trackers)

    def note_activity(self, t: float) -> None:
        self.last_activity_t = t
        self.idle_check_s = self.cfg.idle_check_s

    def on_prompted_miss(self, t: float) -> list[Event]:
        """A prompted gesture was seen but not detected. Two of those end the
        check early instead of waiting out the window."""
        self.verify_misses += 1
        if self.verify_misses >= self.cfg.verify_hits:
            self.verify_t0 = t - self.cfg.verify_window_s - 1.0  # expire now
        return []

    def on_candidate_fire(self, t: float) -> list[Event]:
        self.verify_count += 1
        ev = [Event(t, "log", {"text": f"Verify: {'+'.join(self.candidate or ())} detected "
                                       f"({self.verify_count}/{self.cfg.verify_hits})"})]
        if self.verify_count >= self.cfg.verify_hits:
            if self.probing:
                self._resolve(t, "still works")
                ev.append(Event(t, "probe_ok", {"option": list(self.control)}))
                ev.append(Event(t, "log", {"text": f"Check passed: {'+'.join(self.control)} still works"}))
                self.state, self.candidate, self.probing = "ACTIVE", None, False
                self.probe_cooldown_until = t + self.cfg.probe_cooldown_s
                return ev
            self._resolve(t, "verified, moved")
            ev.append(Event(t, "verify_ok", {"option": list(self.candidate or ())}))
            ev += self._migrate(t, self.candidate, "verified")  # type: ignore[arg-type]
        return ev

    # ------------------------------------------------------------- residual
    def _residual(self, t: float, stats: dict[Option, OptionStats], trackers: dict) -> list[Event]:
        cfg = self.cfg
        ev: list[Event] = []
        if self.state == "VERIFY":
            if self.probing and t - self.verify_t0 > cfg.verify_window_s:
                self._resolve(t, "check failed")
                ev.append(Event(t, "probe_fail", {"option": list(self.control)}))
                ev.append(Event(t, "log", {"text": f"Check failed: {'+'.join(self.control)} isn't "
                                                   f"reliable any more"}))
                self.state, self.candidate, self.probing = "ACTIVE", None, False
                self.probe_cooldown_until = t + cfg.probe_cooldown_s
                self.force_degraded_until = t + cfg.probe_cooldown_s
                self.bad_since = t - cfg.dwell_s
                return ev
            if t - self.verify_t0 > cfg.verify_window_s:
                self._resolve(t, "not verified, kept")
                ev.append(Event(t, "verify_fail", {"option": list(self.candidate or ())}))
                ev.append(Event(t, "log", {"text": f"Verify failed for {'+'.join(self.candidate or ())}; keeping {'+'.join(self.control)}"}))
                self.cooldown[self.candidate] = t + cfg.cooldown_s  # type: ignore[index]
                self.state, self.candidate = "ACTIVE", None
            return ev

        # Ambiguous evidence (several weak, undetected movements on the active
        # channel) -> ask: a short check whose answers are trusted.
        weak = sum(trackers[c].weak_count(t, cfg.weak_window_s) for c in self.control)
        idle = t - self.last_activity_t >= self.idle_check_s
        if (weak >= cfg.weak_probe_count or idle) and t >= self.probe_cooldown_until and cfg.verify:
            why = f"{weak} weak {'+'.join(self.control)} movements" if not idle else \
                f"nothing selected for {t - self.last_activity_t:.0f}s"
            if idle:  # back off so a resting person isn't nagged
                self.last_activity_t = t
                self.idle_check_s = min(self.idle_check_s * 2, cfg.idle_check_max_s)
            for c in self.control:
                trackers[c].weak_times.clear()
            self.state, self.candidate, self.probing = "VERIFY", self.control, True
            self.verify_t0, self.verify_count, self.verify_misses = t, 0, 0
            self.probes += 1
            self._decide(t, "probe", self.control, why, stats)
            ev.append(Event(t, "verify_start", {"option": list(self.control), "probe": True,
                                                "reason": why}))
            ev.append(Event(t, "log", {"text": f"{why}; checking {'+'.join(self.control)}"}))
            return ev

        cur = stats[self.control]
        cp_recent = any(trackers[c].recent_changepoint(t) for c in self.control)
        rate, n = min((trackers[c].obs_rate for c in self.control), key=lambda r: r[0])
        # Degradation needs evidence: the point estimate (which follows observed
        # strength and its trend), a changepoint, or a falling observed hit rate.
        # Uncertainty alone (no recent gestures) is not a reason to switch.
        clauses = {
            "p_hit_below_switch": cur.p_hat < cfg.p_switch,
            "changepoint_and_below_accept": bool(cp_recent and cur.p_hat < cfg.p_accept),
            "observed_hit_rate_low": bool(n >= cfg.obs_rate_min_n and rate < cfg.obs_rate_floor),
            "false_alarms_over_budget": cur.fa_per_min > cfg.fa_max_per_min,
            "recent_check_failed": t < self.force_degraded_until,
            "better_channel_available": False,
        }
        degrading = any(clauses.values())
        # Proactive: a clearly better single channel exists while we're below the
        # acceptance bar. Maximizing the hit rate means moving, not just adapting.
        # Idle channels' models are stale, so judge them pessimistically (p_lo).
        if not degrading and cur.p_hat < cfg.p_accept:
            alt = self._best(t, stats, singles_only=True)
            if alt is not None and stats[alt].p_lo >= cur.p_hat + cfg.proactive_margin:
                degrading = True
                clauses["better_channel_available"] = True
        self.diag = {"t": round(t, 2), "control": list(self.control), "clauses": clauses,
                     "degrading": degrading, "p_hat": round(cur.p_hat, 3), "p_lo": round(cur.p_lo, 3),
                     "obs_rate": round(rate, 3), "obs_n": round(n, 1), "cp_recent": bool(cp_recent),
                     "fa_per_min": round(cur.fa_per_min, 3), "weak": weak,
                     "dwell": round(t - self.bad_since, 2) if self.bad_since is not None else 0.0,
                     "dwell_needed": cfg.dwell_s, "idle_s": round(t - self.last_activity_t, 1),
                     "thresholds": {"p_switch": cfg.p_switch, "p_accept": cfg.p_accept,
                                    "obs_rate_floor": cfg.obs_rate_floor,
                                    "fa_max_per_min": cfg.fa_max_per_min}}
        if degrading:
            if self.bad_since is None:
                self.bad_since = t
        else:
            self.bad_since = None

        cand: Option | None = None
        why = ""
        if self.bad_since is not None and t - self.bad_since >= cfg.dwell_s:
            cand = self._best(t, stats, singles_only=True)
            why = "active option degrading"
            if cand is None and cfg.fusion:
                cand = self._best(t, stats, singles_only=False)
                why = "no single channel reliable; fusing"
            if cand is None and t - self._last_note > 10:
                self._last_note = t
                ev.append(Event(t, "log", {"text": f"{'+'.join(self.control)} degrading "
                                                   f"(p_hit {cur.p_hat:.0%}); no reliable alternative yet"}))
        elif len(self.control) > 1:
            # Recovered enough to leave fusion for a single channel?
            best = self._best(t, stats, singles_only=True)
            if best is not None and stats[best].p_hat >= cfg.p_accept + cfg.upgrade_margin:
                cand, why = best, "single channel recovered"

        if cand is not None:
            self.reason = why
            self._decide(t, "verify", cand, why, stats)
            if cfg.verify:
                self.state, self.candidate = "VERIFY", cand
                self.verify_t0, self.verify_count, self.verify_misses = t, 0, 0
                ev.append(Event(t, "verify_start", {"option": list(cand), "reason": why}))
                ev.append(Event(t, "log", {"text": f"{'+'.join(self.control)} {why}. Testing "
                                                   f"{'+'.join(cand)} (p_hit {stats[cand].p_hat:.0%})"}))
            else:
                ev += self._migrate(t, cand, why)
        return ev

    def _decide(self, t: float, kind: str, option: Option, why: str, stats: dict) -> None:
        cur = stats.get(self.control)
        cand = stats.get(tuple(option))
        self.decisions.append({
            "t": round(t, 2), "kind": kind, "from": list(self.control), "to": list(option),
            "why": why, "clauses": {k: v for k, v in (self.diag.get("clauses") or {}).items() if v},
            "from_p": [round(cur.p_lo, 3), round(cur.p_hat, 3), round(cur.p_hi, 3)] if cur else None,
            "to_p": [round(cand.p_lo, 3), round(cand.p_hat, 3), round(cand.p_hi, 3)] if cand else None,
            "alternatives": {"+".join(o): round(s.p_hat, 3) for o, s in stats.items()
                             if o != self.control},
            "result": "pending", "hits": 0, "needed": self.cfg.verify_hits,
        })
        self.decisions = self.decisions[-20:]

    def _resolve(self, t: float, result: str) -> None:
        for d in reversed(self.decisions):
            if d["result"] == "pending":
                d["result"], d["t_end"], d["hits"] = result, round(t, 2), self.verify_count
                return

    def _best(self, t: float, stats: dict[Option, OptionStats], singles_only: bool) -> Option | None:
        cfg = self.cfg
        best, best_p = None, -1.0
        for o, s in stats.items():
            if o == self.control or (singles_only and len(o) > 1) or (not singles_only and len(o) == 1):
                continue
            if self.cooldown.get(o, -1) > t:
                continue
            if any(self.abandoned.get(c, -1) > t for c in o):
                continue
            if any(c not in self.eligible for c in o):
                continue
            if s.p_hat < cfg.p_accept or s.fa_per_min > cfg.fa_max_per_min:
                continue
            if s.p_hat > best_p:
                best, best_p = o, s.p_hat
        return best

    def _migrate(self, t: float, to: Option, why: str) -> list[Event]:
        old = self.control
        self.control = tuple(to)
        self.state, self.candidate, self.probing = "ACTIVE", None, False
        self.bad_since = None
        self.force_degraded_until = -1e9
        self.last_switch_t = t
        self.switches += 1
        for c in old:
            if c not in self.control:
                self.abandoned[c] = t + self.cfg.cooldown_s
        return [
            Event(t, "migrate", {"from": list(old), "to": list(self.control), "reason": why}),
            Event(t, "log", {"text": f"Control: {'+'.join(old)} -> {'+'.join(self.control)} ({why})"}),
        ]

    # ------------------------------------------------------------- baselines
    def _baseline(self, t: float, trackers: dict, hysteresis: bool) -> list[Event]:
        cfg = self.cfg
        ch = self.control[0]
        rate, n = trackers[ch].baseline_rate()
        if n < cfg.baseline_min_attempts or rate >= 0.5:
            return []
        if hysteresis and t - self.last_switch_t < cfg.baseline_dwell_s:
            return []
        order = list(cfg.channel_order)
        i = order.index(ch)
        for k in range(1, len(order)):
            nxt = order[(i + k) % len(order)]
            if hysteresis and self.abandoned.get(nxt, -1) > t:
                continue
            trackers[nxt].raw_recent.clear()
            ev = self._migrate(t, (nxt,), "observed hit rate < 50%")
            if hysteresis:
                self.abandoned[ch] = t + cfg.baseline_return_block_s
            return ev
        return []

    def _truth_switch(self, t: float) -> list[Event]:
        if self.truth is None:
            return []
        best = self.truth(t, self.control[0])
        if (best,) != self.control:
            return self._migrate(t, (best,), "truth switch")
        return []


def _nice(c: str) -> str:
    return {"hand": "hand", "brow": "eyebrow raise", "blink": "long blink"}.get(c, c)
