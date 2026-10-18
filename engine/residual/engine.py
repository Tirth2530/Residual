"""The Residual engine: Frames in, Events out.

This single class is what the live server, the replay tool and the evaluation
all run. There is no separate "demo code path".

Per frame:
  1. Each channel tracker segments episodes and updates its ability model.
  2. Each channel's per-frame LLR goes into its CUSUM; the active option's
     accumulator (summed LLRs if fused) is compared with the bound for the
     highlighted option's action class.
  3. A crossing "fires": selects/confirms on the board, or counts toward
     verifying a candidate channel.
  4. Option stats (p_hit with an interval, false activations, latency) are
     recomputed and the policy decides whether to adapt, fuse or migrate.
"""

from __future__ import annotations

from collections import deque

from .board import BoardOption, ScanBoard, default_board
from .config import ACTION_CLASSES, CHANNELS, Config
from .events import Event, Frame
from .policy import Option, Policy
from .reliability import ChannelModel, OptionStats, option_stats
from .tracking import ChannelTracker


def okey(o: Option) -> str:
    return "+".join(o)


class Engine:
    def __init__(self, cfg: Config | None = None, options: list[BoardOption] | None = None):
        self.cfg = cfg or Config()
        self.trackers = {c: ChannelTracker(c, self.cfg) for c in CHANNELS}
        self.policy = Policy(self.cfg)
        self.board = ScanBoard(options or default_board(), self.cfg)
        self.S = {c: 0.0 for c in CHANNELS}
        self.S_f = 0.0
        self.score = 0.0
        self.refractory_until = -1.0
        self.t = 0.0
        self.last_amp = {c: 0.0 for c in CHANNELS}
        self.last_visible = {c: True for c in CHANNELS}
        self.stats: dict[Option, OptionStats] = {}
        self.log: deque[dict] = deque(maxlen=200)
        self.history: dict[str, deque] = {k: deque(maxlen=240) for k in
                                          [*CHANNELS, *("+".join(p) for p in _pairs())]}
        self._hist_t = -1e9
        self._frame_i = 0
        self._check_peak = {c: 0.0 for c in CHANNELS}  # max amplitude during a check
        self.stats_every = 6  # frames (5 Hz at 30 fps)
        # Secondary commands (the shell's Next / Back) from channels that are NOT in control go
        # through the same sequential test as Select: a separate CUSUM per channel against the
        # "say" bound. Separate accumulators, so they never change the policy's decisions.
        self.S_aux = {c: 0.0 for c in CHANNELS}
        self.aux_refractory = {c: -1.0 for c in CHANNELS}
        self.last_llr = {c: 0.0 for c in CHANNELS}
        self.watch: Option = self.policy.control
        self.keep_trace = False  # live server turns this on for the Lab screen
        self.trace: deque[dict] = deque(maxlen=1800)
        self.trace_seq = 0
        # Arming: one physical gesture episode -> at most one logical action.
        #   armed        ready; evidence accumulates
        #   pending      evidence crossed the bound on a fire-on-release channel (blink):
        #                acts when the eyes reopen, unless held longer than max_hold_s
        #   wait_release fired (or held too long); no evidence until the channel has been
        #                neutral for release_s
        self.arm = {c: "armed" for c in CHANNELS}
        self._neutral_since: dict[str, float | None] = {c: None for c in CHANNELS}
        self._smooth = {c: 0.0 for c in CHANNELS}   # ~0.1 s smoothed amplitude, for "released"
        self.pending: dict | None = None            # primary / verify fire waiting for release
        self.pending_aux: dict[str, dict] = {}       # aux fires waiting for release
        # Channels calibration said can't be used (FAILED / NOT_VISIBLE / skipped)
        self.eligible = {c: True for c in CHANNELS}

    # ------------------------------------------------------------ calibration
    def calibrate(self, rest: dict[str, list[float]], gestures: dict[str, list[list[float]]],
                  t: float = 0.0, eligible: dict[str, bool] | None = None) -> None:
        for c, tr in self.trackers.items():
            tr.calibrate(rest.get(c, []), gestures.get(c, []), t)
        if eligible is not None:
            self.set_eligible(eligible, t)
        self.stats = self._compute_stats(t)

    def set_eligible(self, eligible: dict[str, bool], t: float = 0.0) -> None:
        """A channel that failed calibration can't select, navigate, be verified, migrated to or
        fused. If the starting control channel failed, start on the first one that didn't."""
        self.eligible = {c: bool(eligible.get(c, False)) for c in CHANNELS}
        self.policy.eligible = {c for c, ok in self.eligible.items() if ok}
        if self.policy.eligible and not all(c in self.policy.eligible for c in self.policy.control):
            first = next(c for c in self.cfg.channel_order if c in self.policy.eligible)
            old = self.policy.control
            self.policy.control = (first,)
            self._log(t, f"Calibration: {'+'.join(old)} isn't usable, starting on {first}")

    # ------------------------------------------------------------- arming
    def _disarm(self, chans) -> None:
        for c in chans:
            self.arm[c] = "wait_release"
            self._neutral_since[c] = None
            self.S[c] = 0.0
            self.S_aux[c] = 0.0

    def neutral_threshold(self, c: str) -> float:
        """Below this (smoothed) the channel counts as back at neutral: under the onset threshold,
        or under halfway between rest and this person's current gesture level, whichever is
        higher. The halfway rule keeps a shaky or noisy signal from blocking re-arming forever."""
        tr = self.trackers[c]
        level = float(tr.x[0])
        return max(tr.onset(), tr.mu_n + 0.5 * max(level - tr.mu_n, 0.0))

    def released(self, c: str) -> bool:
        return self._smooth[c] < self.neutral_threshold(c)

    def _update_arming(self, c: str, t: float, a: float, vis: bool) -> None:
        if vis:
            self._smooth[c] += 0.4 * (a - self._smooth[c])
        if self.arm[c] != "wait_release":
            return
        neutral = vis and self.released(c)
        if not neutral:
            self._neutral_since[c] = None
            return
        if self._neutral_since[c] is None:
            self._neutral_since[c] = t
        if t - self._neutral_since[c] >= self.cfg.release_s:
            self.arm[c] = "armed"
            self._neutral_since[c] = None

    def _release_mode(self, option) -> bool:
        return len(option) == 1 and self.cfg.fire_on_release.get(option[0], False)

    def _episode_t0(self, c: str, t: float) -> float:
        ep = self.trackers[c]._ep
        return ep["t0"] if ep else t

    def arm_state(self, c: str) -> str:
        if self.arm[c] == "wait_release":
            return "wait_release"
        if (self.pending and c in self.pending["option"]) or c in self.pending_aux:
            return "pending"
        if self.trackers[c].in_episode or self.S[c] > 0.5 or self.S_aux[c] > 0.5:
            return "gesture"
        return "armed"

    # ------------------------------------------------------------------ model
    def model(self, c: str, t: float) -> ChannelModel:
        tr = self.trackers[c]
        level, var = tr.level_at(t)
        return ChannelModel(c, level, var, tr.mu_n, tr.s_i, tr.s_n, tr.D, self.cfg.llr_clip[c],
                            strength_sd=tr.strength_sd, vis_rate=tr.vis_rate, shape=tr.shape)

    def _compute_stats(self, t: float) -> dict[Option, OptionStats]:
        models = {c: self.model(c, t) for c in CHANNELS}
        bound = self.cfg.bound("say")
        out = {}
        for o in self.policy.options():
            out[o] = option_stats([models[c] for c in o], bound, self.cfg.fps)
        if self.policy.control not in out:  # e.g. fusion disabled mid-run
            out[self.policy.control] = option_stats([models[c] for c in self.policy.control],
                                                    bound, self.cfg.fps)
        return out

    # ------------------------------------------------------------------- step
    def step(self, f: Frame) -> list[Event]:
        t = self.t = f.t
        cfg = self.cfg
        ev: list[Event] = []
        llr: dict[str, float] = {}

        # 1. trackers + per-channel evidence
        cand = self.policy.candidate if self.policy.state == "VERIFY" else None
        for c, tr in self.trackers.items():
            vis = f.is_visible(c)
            relevant = c in self.policy.control or bool(cand and c in cand)
            a = float(f.amps.get(c, 0.0))
            self.last_amp[c], self.last_visible[c] = a, vis
            ep = tr.observe(t, a, vis, relevant, prompted=bool(cand and c in cand))
            if ep is not None and cand and c in cand and not ep.fired:
                ev += self.policy.on_prompted_miss(t)
            if ep is not None:
                ev.append(Event(t, "attempt", {"channel": c, "strength": round(ep.strength, 3),
                                               "fired": ep.fired, "accepted": ep.accepted}))
                if c in self.policy.control or (self.policy.candidate and c in self.policy.candidate):
                    word = "detected" if ep.fired else "missed"
                    self._log(t, f"{_nice(c)} strength {ep.strength:.2f} - {word}")
            cp = tr.pop_changepoint()
            if cp is not None:
                ev.append(Event(t, "changepoint", {"channel": c, "level": round(cp[1], 3)}))
                self._log(t, f"Changepoint on {_nice(c)}: strength now ~{cp[1]:.2f}")
            if cand and c in cand and vis:
                self._check_peak[c] = max(self._check_peak[c], a)
            m = self.model(c, t)
            llr[c] = m.llr(a) if vis else 0.0
            self._update_arming(c, t, a, vis)
            if self.arm[c] == "wait_release":
                llr[c] = min(llr[c], 0.0)   # a held gesture adds no evidence until released
            self.S[c] = max(0.0, self.S[c] + llr[c])
            self.S_aux[c] = max(0.0, self.S_aux[c] + llr[c])
        self.last_llr = llr

        # 2. accumulator for the option we're listening to
        verifying = self.policy.state == "VERIFY" and self.policy.candidate
        watch: Option = self.policy.candidate if verifying else self.policy.control  # type: ignore[assignment]
        if len(watch) > 1:
            self.S_f = max(0.0, self.S_f + sum(llr[c] for c in watch))
            self.score = self.S_f
        else:
            self.score = self.S[watch[0]]
        cls = "say" if verifying else self.board.current_class()
        bound = cfg.bound(cls)
        self.watch = watch

        # 3. fire: at most once per physical gesture (arming), acting on release for blink
        if self.pending is not None and tuple(self.pending["option"]) != tuple(watch):
            self.pending = None   # control changed under a pending fire: drop it
        armed = all(self.arm[c] == "armed" for c in watch)
        do_fire = False
        if self.pending is not None:
            c0 = watch[0]
            held = t - self.pending["t0"]
            if held > cfg.max_hold_s.get(c0, 99.0):
                self.pending = None
                self._disarm(watch)              # held too long: resting or the help pattern
                self._log(t, f"{_nice(c0)} held {held:.1f}s: not a select")
            elif not self.trackers[c0].in_episode or self.released(c0):
                do_fire = True                   # released in time: act now
                self.pending = None
        elif armed and t >= self.refractory_until and self.score > bound:
            if self._release_mode(watch):
                # The movement was detected now (the reliability model counts it as a hit);
                # the action waits for the release.
                self.trackers[watch[0]].mark_fired()
                self.pending = {"option": list(watch), "t0": self._episode_t0(watch[0], t),
                                "score": self.score}
            else:
                do_fire = True
        if do_fire:
            for c in watch:
                self.trackers[c].mark_fired()
            ev.append(Event(t, "fire", {"option": list(watch), "score": round(self.score, 2),
                                        "bound": round(bound, 2)}))
            self.S = {c: 0.0 for c in CHANNELS}
            self.S_f = 0.0
            self._disarm(watch)
            self.refractory_until = t + cfg.refractory_s
            if verifying:
                ev += self.policy.on_candidate_fire(t)
                if self.policy.state == "ACTIVE":
                    self.S_f = 0.0
            else:
                self.policy.note_activity(t)
                kind, idx = self.board.on_fire(t)
                opt = self.board.options[idx]
                if kind == "confirm":
                    ev.append(Event(t, "confirm", {"index": idx, "label": opt.label}))
                    self._log(t, f"Confirm '{opt.label}'? Gesture again")
                else:
                    ev.append(Event(t, "select", {"index": idx, "label": opt.label, "cls": opt.cls}))
                    ev.append(Event(t, "speak", {"text": opt.speech}))
                    if opt.cls == "help":
                        ev.append(Event(t, "alert", {"text": opt.speech}))
                    self._log(t, f"Selected '{opt.label}'")

        # 3b. secondary commands from channels that aren't being listened to (same arming)
        aux_bound = cfg.bound("say")
        for c in CHANNELS:
            if c in watch or c in self.policy.control or not self.eligible[c]:
                self.S_aux[c] = 0.0
                self.pending_aux.pop(c, None)
                continue
            fire_aux = False
            pa = self.pending_aux.get(c)
            if pa is not None:
                if t - pa["t0"] > cfg.max_hold_s.get(c, 99.0):
                    self.pending_aux.pop(c)
                    self._disarm([c])
                elif not self.trackers[c].in_episode or self.released(c):
                    self.pending_aux.pop(c)
                    fire_aux = True
            elif self.arm[c] == "armed" and t >= self.aux_refractory[c] and self.S_aux[c] > aux_bound:
                if self._release_mode((c,)):
                    self.pending_aux[c] = {"t0": self._episode_t0(c, t), "score": self.S_aux[c]}
                else:
                    fire_aux = True
            if fire_aux:
                ev.append(Event(t, "aux_fire", {"channel": c, "score": round(max(self.S_aux[c], (pa or {}).get("score", 0)), 2),
                                                "bound": round(aux_bound, 2)}))
                self.S_aux[c] = 0.0
                self._disarm([c])
                self.aux_refractory[c] = t + cfg.refractory_s

        # 4. stats + policy
        self._frame_i += 1
        if self._frame_i % self.stats_every == 0 or not self.stats:
            self.stats = self._compute_stats(t)
            before = self.policy.control
            pev = self.policy.update(t, self.stats, self.trackers)
            if self.policy.control != before:
                self.S_f = 0.0
            for e in pev:
                if e.kind == "verify_start":
                    self._check_peak = {c: 0.0 for c in CHANNELS}
                elif e.kind in ("verify_fail", "probe_fail"):
                    # A failed check is evidence too: what the person managed when
                    # asked is the best estimate of their current strength.
                    for c in e.data.get("option", []):
                        lvl = self.trackers[c].failed_check(t, self._check_peak[c])
                        if lvl is not None:
                            self._log(t, f"{_nice(c)} check: strongest movement {self._check_peak[c]:.2f}; "
                                         f"strength now ~{lvl:.2f}")
            ev += pev
        self.board.paused = self.policy.state == "VERIFY"
        lat = self.stats.get(self.policy.control)
        for kind, idx in self.board.update(t, lat.latency_s if lat else None):
            ev.append(Event(t, "cancel", {"index": idx}))
            self._log(t, f"'{self.board.options[idx].label}' cancelled (no confirm)")

        for e in ev:
            if e.kind == "log":
                self._log(t, e.data["text"])
        self._record_history(t)
        if self.keep_trace:
            self.trace_seq += 1
            self.trace.append({"i": self.trace_seq, "t": round(t, 3),
                               "a": [round(self.last_amp[c], 3) for c in CHANNELS],
                               "v": [1 if self.last_visible[c] else 0 for c in CHANNELS],
                               "l": [round(llr[c], 3) for c in CHANNELS],
                               "s": [round(self.S[c], 3) for c in CHANNELS],
                               "x": [round(self.S_aux[c], 3) for c in CHANNELS],
                               "sc": round(self.score, 3), "b": round(bound, 3),
                               "w": list(watch), "r": [self.arm_state(c)[0] for c in CHANNELS],
                               "f": [e.kind for e in ev if e.kind in
                                                       ("fire", "aux_fire", "attempt", "migrate",
                                                        "verify_start", "changepoint")]})
        return ev

    # --------------------------------------------------------------- readouts
    def _log(self, t: float, text: str) -> None:
        self.log.append({"t": round(t, 2), "text": text})

    def _record_history(self, t: float) -> None:
        if t - self._hist_t < 0.5 or not self.stats:
            return
        self._hist_t = t
        for o, s in self.stats.items():
            k = okey(o)
            if k in self.history:
                self.history[k].append((round(t, 2), round(s.p_hat, 3), round(s.p_lo, 3),
                                        round(s.p_hi, 3)))

    def snapshot(self, history: bool = True) -> dict:
        t = self.t
        chans = {}
        for c, tr in self.trackers.items():
            level, var = tr.level_at(t)
            s = self.stats.get((c,))
            rate, n = tr.obs_rate
            chans[c] = {
                "amp": round(self.last_amp[c], 3),
                "visible": self.last_visible[c],
                "level": round(level, 3),
                "level_sd": round(var**0.5, 3),
                "trend": round(float(tr.x[1]), 4),
                "noise_mu": round(tr.mu_n, 3),
                "noise_sd": round(tr.s_n, 3),
                "onset": round(tr.onset(), 3),
                "cusum": round(self.S[c], 2),
                "cp_prob": round(tr.cp_prob, 3),
                "cp_recent": tr.recent_changepoint(t),
                "obs_rate": round(rate, 3),
                "obs_n": round(n, 1),
                "in_episode": tr.in_episode,
                "stats": s.as_dict() if s else None,
                "arm": self.arm_state(c),
                "eligible": self.eligible[c],
                "smooth": round(float(self._smooth[c]), 3),          # re-arms below release_below
                "release_below": round(float(self.neutral_threshold(c)), 3),
            }
        ctl = self.policy.control
        verifying = self.policy.state == "VERIFY"
        cls = "say" if verifying else self.board.current_class()
        snap = {
            "t": round(t, 2),
            "control": list(ctl),
            "policy": {
                "state": self.policy.state,
                "candidate": list(self.policy.candidate) if self.policy.candidate else None,
                "prompt": self.policy.prompt,
                "reason": self.policy.reason,
                "switches": self.policy.switches,
                "verify_count": self.policy.verify_count,
                "verify_needed": self.cfg.verify_hits,
                "probing": self.policy.probing,
                "mode": self.cfg.policy_mode,
                "diag": self.policy.diag,
                "decisions": self.policy.decisions[-6:],
            },
            "channels": chans,
            "options": {okey(o): s.as_dict() for o, s in self.stats.items()},
            "evidence": {"score": round(self.score, 2), "bound": round(self.cfg.bound(cls), 2),
                         "cls": cls, "watch": list(self.watch),
                         "aux": {c: round(self.S_aux[c], 2) for c in CHANNELS},
                         "aux_bound": round(self.cfg.bound("say"), 2),
                         "classes": {k: {"alpha": v.alpha, "beta": v.beta,
                                         "bound": round(self.cfg.bound(k), 3)}
                                     for k, v in ACTION_CLASSES.items()}},
            "board": {
                "options": [{"label": o.label, "cls": o.cls} for o in self.board.options],
                "highlight": self.board.highlight,
                "paused": self.board.paused,
                "confirm": self.board.confirm["index"] if self.board.confirm else None,
                "period": round(self.board.period, 2),
            },
            "log": list(self.log)[-40:],
        }
        if history:
            snap["history"] = {k: list(v) for k, v in self.history.items()}
        return snap


def _pairs() -> list[tuple[str, str]]:
    from itertools import combinations

    return list(combinations(CHANNELS, 2))


def _nice(c: str) -> str:
    return {"hand": "Hand", "brow": "Eyebrow", "blink": "Blink"}.get(c, c)
