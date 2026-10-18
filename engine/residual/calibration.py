"""Calibration that tells the truth.

The old calibration recorded a fixed number of seconds per movement and accepted whatever it got,
so a movement it never actually saw could be "calibrated" and then used. This one is
success-driven and reports a verdict per movement:

    preflight   (webcam only) camera opens, frames arrive, a face is visible and steady for ~1 s
    rest        a few seconds still: rest level and noise for every feature
    hand / brow / blink
                "do it 3 times": each deliberate gesture is detected live and counted (1/3, 2/3,
                3/3); the step ends early at 3, or times out
    gaze        look centre, left, centre, right, centre, up, centre: this person's comfortable
                range and the left/right direction of their camera

Verdict per movement (needs 3 gestures for VALID):

    VALID        >= 3 deliberate gestures and clear separation from rest (>= 3 noise widths)
    WEAK         2 gestures, or separation 2 to 3: usable, flagged
    FAILED       fewer, too little separation, or skipped
    NOT_VISIBLE  the feature was missing for most of the step (no hand in frame, face lost)

Only VALID and WEAK movements can select, navigate, be verified, migrated to or fused.

Brow uses two candidate features, each calibrated to this person: the blendshape combination
max(browInnerUp, mean(browOuterUpLeft, browOuterUpRight)) and a geometric brow-to-eyelid distance.
The engine uses whichever separates from rest (or the max of both if both do).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import CHANNELS

# features each movement can be read from (raw keys produced by perception)
SOURCES = {"hand": ["hand"], "brow": ["brow", "brow_geo"], "blink": ["blink"]}
# smallest raw change that can count as a gesture, per feature (raw units)
MIN_DELTA = {"hand": 0.08, "brow": 0.05, "brow_geo": 0.012, "blink": 0.25}
MIN_FRAMES = {"hand": 3, "brow": 3, "blink": 7}     # ~0.1 s, ~0.1 s, ~0.23 s (a deliberate blink)
MAX_GESTURE_S = {"hand": 6.0, "brow": 6.0, "blink": 2.5}
NEEDED = 3
PROMPTS = {
    "rest": "Relax and look at the screen",
    "hand": "Pinch your thumb and finger together, 3 times",
    "brow": "Raise your eyebrows, 3 times",
    "blink": "Close both eyes for about half a second, 3 times",
}
GAZE_STEPS = [("center", 1.5, "Look at the middle of the screen"),
              ("left", 2.0, "Look at the left edge of the screen"),
              ("center", 1.2, "Back to the middle"),
              ("right", 2.0, "Look at the right edge of the screen"),
              ("center", 1.2, "Back to the middle"),
              ("up", 2.0, "Look at the top of the screen"),
              ("center", 1.2, "Back to the middle")]


@dataclass
class ChannelResult:
    status: str = "FAILED"            # VALID | WEAK | FAILED | NOT_VISIBLE
    count: int = 0
    rest: float = 0.0                 # raw rest level of the chosen feature
    level: float = 0.0                # raw gesture level (median peak)
    separation: float = 0.0           # (level - rest) / rest noise
    visible: float = 0.0              # share of frames the feature was seen during its step
    frames: int = 0
    sources: list[str] = field(default_factory=list)
    skipped: bool = False
    reason: str = ""

    def as_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.__dict__.items()}

    @property
    def usable(self) -> bool:
        return self.status in ("VALID", "WEAK")


def _robust(vals) -> tuple[float, float]:
    a = np.asarray(vals, dtype=float)
    med = float(np.median(a))
    mad = float(np.median(np.abs(a - med))) * 1.4826
    return med, max(mad, float(np.std(a)) * 0.5, 1e-4)


class GestureCounter:
    """Counts deliberate gestures live on one movement, from rest statistics."""

    def __init__(self, channel: str, rest_stats: dict[str, tuple[float, float]]):
        self.c = channel
        self.rest = rest_stats            # source -> (median, noise)
        self.count = 0
        self.frames = 0
        self.seen = 0
        self.max_score = 0.0
        self.score = 0.0
        self._run: list[dict] | None = None
        self._run_t0 = 0.0
        self.gestures: list[list[dict]] = []
        self.peaks: dict[str, list[float]] = {s: [] for s in SOURCES[channel]}
        self.recent: list[dict] = []

    def scale(self, s: str) -> float:
        med, sd = self.rest.get(s, (0.0, 0.0))
        return max(MIN_DELTA[s], 4.0 * sd)

    def _score(self, raw: dict) -> float | None:
        vals = [(raw[s] - self.rest.get(s, (0.0, 0.0))[0]) / self.scale(s) for s in SOURCES[self.c] if s in raw]
        return max(vals) if vals else None

    def step(self, t: float, raw: dict) -> bool:
        """Returns True when a gesture was just counted."""
        self.frames += 1
        sc = self._score(raw)
        self.recent.append(raw)
        self.recent = self.recent[-4:]
        if sc is None:
            self.score = 0.0
            if self._run is not None and t - self._run_t0 > 0.3:
                self._run = None               # lost the feature mid-gesture: don't count it
            return False
        self.seen += 1
        self.score = sc
        self.max_score = max(self.max_score, sc)
        if self._run is None:
            if sc > 1.0:
                self._run, self._run_t0 = list(self.recent[:-1]) + [raw], t
            return False
        self._run.append(raw)
        if sc > 0.5:
            if t - self._run_t0 > MAX_GESTURE_S[self.c]:
                self._run = None               # held for ages: not a gesture
            return False
        run, self._run = self._run, None
        n_above = sum(1 for r in run if (self._score(r) or 0) > 1.0)
        if n_above < MIN_FRAMES[self.c]:
            return False                       # too short (e.g. a natural blink)
        self.count += 1
        self.gestures.append(run + list(self.recent[-1:]))
        for s in SOURCES[self.c]:
            v = [r[s] for r in run if s in r]
            if v:
                self.peaks[s].append(float(np.percentile(v, 90)))
        return True

    @property
    def visible_share(self) -> float:
        return self.seen / self.frames if self.frames else 0.0


class Calibration:
    """Drives one calibration. `step()` per frame; `status()` for the UI; `result()` at the end."""

    def __init__(self, t: float, *, preflight: bool, channels: list[str] | None = None,
                 gaze: bool = True, prev_rest: dict | None = None, rest_s: float = 4.0,
                 timeout_s: float = 15.0):
        self.t0 = t
        self.channels = list(channels or CHANNELS)
        self.timeout_s = timeout_s
        self.steps: list[tuple[str, float]] = []
        if preflight:
            self.steps.append(("preflight", 25.0))
        if prev_rest is None or rest_s > 0:
            self.steps.append(("rest", rest_s if prev_rest is None else min(rest_s, 2.0)))
        self.steps += [(c, timeout_s) for c in self.channels]
        if gaze:
            self.steps += [("gaze", sum(s[1] for s in GAZE_STEPS))]
        self.i = 0
        self.step_t0 = t
        self.rest_frames: list[dict] = []
        self.rest_stats: dict[str, tuple[float, float]] = dict(prev_rest or {})
        self.counter: GestureCounter | None = None
        self.results: dict[str, ChannelResult] = {}
        self.counters: dict[str, GestureCounter] = {}
        self.pre = {"state": "waiting_camera", "face_since": None, "yaws": [], "reason": ""}
        self.gaze_samples: dict[str, list[tuple[float, float]]] = {k: [] for k in ("center", "left", "right", "up")}
        self.gaze_result: dict | None = None
        self.failed: str | None = None         # preflight failure reason (then calibration stops)
        self.done = False
        self.hint = ""
        self.log: list[str] = []

    # -------------------------------------------------------------- per frame
    @property
    def name(self) -> str:
        return self.steps[self.i][0] if self.i < len(self.steps) else "done"

    def _next(self, t: float) -> None:
        self.i += 1
        self.step_t0 = t
        self.hint = ""
        if self.name in CHANNELS:
            self.counter = GestureCounter(self.name, self.rest_stats)
            self.counters[self.name] = self.counter
        if self.i >= len(self.steps):
            self.done = True

    def skip(self, t: float) -> None:
        """The person (or carer) chose to skip the current movement."""
        if self.name in CHANNELS:
            self.results[self.name] = ChannelResult(status="FAILED", skipped=True, reason="skipped",
                                                    count=self.counter.count if self.counter else 0)
            self._next(t)
        elif self.name == "gaze":
            self.gaze_result = {"calibrated": False, "reason": "skipped"}
            self._next(t)

    def step(self, t: float, raw: dict, face: bool, yaw: float | None = None,
             gaze_hv: tuple[float, float] | None = None, camera: dict | None = None) -> None:
        if self.done or self.failed:
            return
        el = t - self.step_t0
        name, limit = self.steps[self.i]
        if name == "preflight":
            self._preflight(t, el, face, yaw, camera or {})
            return
        if name == "rest":
            self.rest_frames.append(raw)
            if el >= limit:
                for s in [s for c in CHANNELS for s in SOURCES[c]]:
                    vals = [f[s] for f in self.rest_frames if s in f]
                    if len(vals) >= 10:
                        self.rest_stats[s] = _robust(vals)
                self._next(t)
            return
        if name in CHANNELS:
            cnt = self.counter
            got = cnt.step(t, raw)
            if got:
                self.hint = f"Got it, {cnt.count} of {NEEDED}"
            elif cnt.seen == 0 and el > 2.0:
                self.hint = ("I can't see your hand. Hold it where the camera can see it."
                             if name == "hand" else "I can't see your face. Look at the screen.")
            elif el > 4.0 and cnt.count == 0 and cnt.max_score < 0.6:
                self.hint = {"hand": "I'm not seeing a pinch yet. Try touching thumb and finger.",
                             "brow": "I'm not seeing your eyebrows move yet. Try a bigger raise.",
                             "blink": "I'm not seeing a long blink yet. Close both eyes a bit longer."}[name]
            if cnt.count >= NEEDED or el >= limit:
                self.results[name] = self._judge(name, cnt)
                self._next(t)
            return
        if name == "gaze":
            k, tt = 0, el
            for k, (_, secs, _) in enumerate(GAZE_STEPS):
                if tt < secs:
                    break
                tt -= secs
            else:
                k = len(GAZE_STEPS)
            if k >= len(GAZE_STEPS):
                self.gaze_result = self._judge_gaze()
                self._next(t)
                return
            target, secs, _ = GAZE_STEPS[k]
            if gaze_hv is not None and tt > 0.5:     # skip the saccade at the start of each step
                self.gaze_samples[target].append(gaze_hv)

    def _preflight(self, t: float, el: float, face: bool, yaw: float | None, cam: dict) -> None:
        P = self.pre
        if cam.get("open") is False:
            P["state"], P["reason"] = "camera_not_open", "The camera didn't open. It may be in use, missing, or blocked in privacy settings."
        elif cam.get("age") is None or cam.get("age", 99) > 1.0:
            P["state"], P["reason"] = "no_frames", "The camera opened but isn't sending pictures."
        elif not face:
            P["state"], P["reason"] = "no_face", "I can't see a face. Sit in front of the camera."
            P["face_since"], P["yaws"] = None, []
        else:
            if P["face_since"] is None:
                P["face_since"], P["yaws"] = t, []
            if yaw is not None:
                P["yaws"].append(yaw)
            steady = len(P["yaws"]) < 5 or float(np.std(P["yaws"][-30:])) < 4.0
            if not steady:
                P["state"], P["reason"] = "unstable", "Your face is moving a lot. Try to stay still for a moment."
                P["face_since"], P["yaws"] = t, P["yaws"][-3:]
            else:
                P["state"], P["reason"] = "face_ok", ""
                if t - P["face_since"] >= 1.0:
                    self.log.append("preflight ok")
                    self._next(t)
                    return
        if el >= self.steps[self.i][1]:
            self.failed = P["state"]

    # -------------------------------------------------------------- verdicts
    def _judge(self, c: str, cnt: GestureCounter) -> ChannelResult:
        r = ChannelResult(count=cnt.count, visible=round(cnt.visible_share, 3), frames=cnt.frames)
        if cnt.visible_share < 0.5:
            r.status, r.reason = "NOT_VISIBLE", "not visible for most of the step"
            return r
        seps = {}
        for s in SOURCES[c]:
            if s in self.rest_stats and cnt.peaks[s]:
                med, sd = self.rest_stats[s]
                lvl = float(np.median(cnt.peaks[s]))
                seps[s] = ((lvl - med) / max(sd, MIN_DELTA[s] / 4.0), med, lvl)
        if not seps:
            r.status, r.reason = "FAILED", "no deliberate movement seen"
            return r
        good = [s for s, v in seps.items() if v[0] >= 3.0]
        best = max(seps, key=lambda s: seps[s][0])
        r.sources = good or [best]
        r.separation, r.rest, r.level = round(seps[best][0], 2), seps[best][1], seps[best][2]
        if cnt.count >= NEEDED and r.separation >= 3.0:
            r.status = "VALID"
        elif cnt.count >= 2 and r.separation >= 2.0:
            r.status, r.reason = "WEAK", "only just clear enough"
        else:
            r.status = "FAILED"
            r.reason = f"saw {cnt.count} of {NEEDED}" if cnt.count < 2 else "too close to rest"
        return r

    def _judge_gaze(self) -> dict:
        S = self.gaze_samples
        if len(S["center"]) < 10 or min(len(S[k]) for k in ("left", "right")) < 5:
            return {"calibrated": False, "reason": "eyes not seen during gaze steps"}
        ch, cv = (float(np.median([p[i] for p in S["center"]])) for i in (0, 1))
        dl = float(np.median([p[0] for p in S["left"]])) - ch
        dr = float(np.median([p[0] for p in S["right"]])) - ch
        du = float(np.median([p[1] for p in S["up"]])) - cv if len(S["up"]) >= 5 else 0.0
        if dl * dr >= 0 or min(abs(dl), abs(dr)) < 0.015:
            return {"calibrated": False, "reason": "left and right glances weren't clearly different",
                    "dl": round(dl, 4), "dr": round(dr, 4)}
        sign = 1 if dr > 0 else -1      # +1: looking right moves the feature positive
        up_ok = du < -0.012             # up is negative v
        return {"calibrated": True, "center": [round(ch, 4), round(cv, 4)], "sign_h": sign,
                "range": {"left": round(abs(dl), 4), "right": round(abs(dr), 4),
                          "up": round(abs(du), 4) if up_ok else None},
                "up": up_ok, "swapped": sign < 0}

    # -------------------------------------------------------------- outputs
    def status(self, t: float) -> dict:
        name = self.name
        st = {"step": name, "index": self.i, "total": len(self.steps),
              "elapsed": round(t - self.step_t0, 1),
              "results": {c: r.as_dict() for c, r in self.results.items()},
              "hint": self.hint, "failed": self.failed}
        if name == "preflight":
            st.update(prompt="Checking the camera", detail=self.pre["state"], reason=self.pre["reason"])
        elif name == "rest":
            st.update(prompt=PROMPTS["rest"], remaining=round(max(0.0, self.steps[self.i][1] - (t - self.step_t0)), 1))
        elif name in CHANNELS:
            cnt = self.counter
            st.update(prompt=PROMPTS[name], channel=name, count=cnt.count, needed=NEEDED,
                      remaining=round(max(0.0, self.timeout_s - (t - self.step_t0)), 1),
                      live=round(min(cnt.score, 3.0), 2), seen=cnt.seen > 0)
        elif name == "gaze":
            el, k = t - self.step_t0, 0
            for k, (_, secs, _) in enumerate(GAZE_STEPS):
                if el < secs:
                    break
                el -= secs
            st.update(prompt=GAZE_STEPS[min(k, len(GAZE_STEPS) - 1)][2], gaze_step=k, gaze_total=len(GAZE_STEPS),
                      target=GAZE_STEPS[min(k, len(GAZE_STEPS) - 1)][0])
        # the old UI fields
        st["phase"] = name
        st["total"] = len(self.steps)
        return st


def fit_calibration(cal: Calibration, prev=None):
    """-> (Normalizer, rest_amps, gesture_amps, results) for Engine.calibrate().

    `prev` is an earlier (normalizer, rest_amps, gestures, results) to keep for channels this
    calibration didn't redo (retrying one movement)."""
    from .normalize import Normalizer

    norm = Normalizer() if prev is None else Normalizer.from_dict(prev[0].to_dict())
    rest_amps = {} if prev is None else dict(prev[1])
    gestures = {} if prev is None else dict(prev[2])
    results = {} if prev is None else dict(prev[3])
    for s, (med, _) in cal.rest_stats.items():
        norm.rest[s] = med
    for c, r in cal.results.items():
        cnt = cal.counters.get(c)
        results[c] = r
        if cnt is None:
            continue
        srcs = r.sources or SOURCES[c]
        for s in SOURCES[c]:
            if cnt.peaks[s]:
                norm.peak[s] = float(np.median(cnt.peaks[s]))
            elif s not in norm.peak or norm.peak[s] <= norm.rest.get(s, 0.0):
                norm.peak[s] = norm.rest.get(s, 0.0) + MIN_DELTA[s] * 4
        norm.sources[c] = list(srcs)
        gestures[c] = [[norm({k: v for k, v in f.items() if k in srcs})[c] for f in g] for g in cnt.gestures]
    for c in CHANNELS:
        if c not in rest_amps or cal.rest_frames:
            rest_amps[c] = [norm(f)[c] for f in cal.rest_frames if any(s in f for s in norm.sources[c])]
        gestures.setdefault(c, [])
        results.setdefault(c, ChannelResult(status="FAILED", reason="not calibrated"))
    return norm, rest_amps, gestures, results
