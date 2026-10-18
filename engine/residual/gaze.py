"""Eyes as switches, not a cursor.

Webcam gaze cursors fail people with ALS exactly when they need them: drooping lids (ptosis),
slower and narrower eye movement, dry and tired eyes, and the "Midas touch" of selecting whatever
you look at. Look to Speak (Google) and GazeSpeak (Microsoft) showed that a few deliberate eye
gestures work far better. This module turns the face into a small set of reliable events and
watches how tired the eyes are.

Pipeline, per frame:
    478 face landmarks + 4x4 head transform (MediaPipe Face Landmarker)
      -> eye features with the head's rotation undone          (ported from AGENCY's landmarks.ts)
      -> One Euro filter + median-of-3 spike rejection           (Casiez et al. 2012)
      -> adaptive centre (slow drift tracking while the eyes rest) and per-person range
      -> gestures: LEFT / RIGHT / UP, each needing a dwell and a return to centre (hysteresis);
         rejected while the head is turning or the eyes are closing
      -> fatigue: lid opening vs this person's own baseline (ptosis), blink rate, PERCLOS,
         glance speed vs early in the session -> one 0..1 score with a reason
      -> help pattern: three eyes-held-shut closures (1.2 to 4 s) inside 15 s (pattern from AGENCY).
         Deliberately longer than a "long blink" select (about 0.3 to 0.9 s), so three selects in
         a row can never be read as a call for help.

Credit: feature extraction, the One Euro filter, the blink switch and the help pattern are
adapted (with the authors' permission) from AGENCY OS (UCSC AI Hackathon 2026). The gesture layer,
adaptive centring, fatigue model and quality score are new here.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np

# MediaPipe Face Landmarker indices (person's own left/right). Checked against MediaPipe's
# FACE_LANDMARKS_*_EYE / *_IRIS connection tables in AGENCY's tests.
RIGHT_EYE = dict(outer=33, inner=133, upper=159, lower=145, iris=(469, 470, 471, 472))
LEFT_EYE = dict(outer=263, inner=362, upper=386, lower=374, iris=(474, 475, 476, 477))
EYE_RING_R = (33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246)
EYE_RING_L = (263, 249, 390, 373, 374, 380, 381, 382, 362, 398, 384, 385, 386, 387, 388, 466)


# ------------------------------------------------------------------ filters
class OneEuro:
    """One Euro filter: heavy smoothing when still, little lag when moving."""

    def __init__(self, min_cutoff: float = 1.2, beta: float = 0.3, d_cutoff: float = 1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x = self.dx = self.t = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x: float, t: float) -> float:
        if self.t is None or t <= self.t:
            self.x, self.dx, self.t = x, 0.0, t
            return x
        dt = t - self.t
        self.t = t
        d = (x - self.x) / dt
        self.dx = self.dx + self._alpha(self.d_cutoff, dt) * (d - self.dx)
        cutoff = self.min_cutoff + self.beta * abs(self.dx)
        self.x = self.x + self._alpha(cutoff, dt) * (x - self.x)
        return self.x

    def reset(self) -> None:
        self.x = self.dx = self.t = None


class Median3:
    """Rejects single-frame spikes (a landmark jump) before smoothing."""

    def __init__(self):
        self.buf: deque[float] = deque(maxlen=3)

    def __call__(self, x: float) -> float:
        self.buf.append(x)
        return float(np.median(self.buf))


# ------------------------------------------------------------------ features
@dataclass
class EyeFeat:
    h: float          # iris along the eye's corner-to-corner axis, 0 = centred, + = person's left in image
    v: float          # iris perpendicular to that axis, + = down, in eye widths
    openness: float   # lid gap / eye width


@dataclass
class Features:
    left: EyeFeat
    right: EyeFeat
    yaw: float
    pitch: float
    roll: float
    normalized: bool  # True when the head rotation was undone

    @property
    def h(self) -> float:
        return (self.left.h + self.right.h) / 2

    @property
    def v(self) -> float:
        return (self.left.v + self.right.v) / 2

    @property
    def openness(self) -> float:
        return (self.left.openness + self.right.openness) / 2


def head_pose(m: np.ndarray) -> tuple[float, float, float]:
    """Yaw, pitch, roll in degrees from MediaPipe's 4x4 facial transformation matrix."""
    r = m[:3, :3]
    sy = math.hypot(r[0, 0], r[1, 0])
    pitch = math.degrees(math.atan2(r[2, 1], r[2, 2]))
    yaw = math.degrees(math.atan2(-r[2, 0], sy))
    roll = math.degrees(math.atan2(r[1, 0], r[0, 0]))
    return yaw, pitch, roll


def _eye(pts: np.ndarray, eye: dict, inv: np.ndarray | None) -> EyeFeat | None:
    """pts: (478, 3) in isotropic image units. With inv, measure in the head's frontal frame."""
    idx = [eye["outer"], eye["inner"], eye["upper"], eye["lower"], *eye["iris"]]
    p = pts[idx]
    if inv is not None:
        centre = (p[0] + p[1]) / 2
        p = (p - centre) @ inv.T  # rotate back to face the camera
    a, b, up, low, ring = p[0, :2], p[1, :2], p[2, :2], p[3, :2], p[4:, :2]
    left, right = (a, b) if a[0] <= b[0] else (b, a)
    axis = right - left
    width = float(np.linalg.norm(axis))
    if width < 1e-6:
        return None
    unit = axis / width
    normal = np.array([-unit[1], unit[0]])
    mid = (left + right) / 2
    iris = ring.mean(axis=0)
    return EyeFeat(h=float((iris - mid) @ unit / width), v=float((iris - mid) @ normal / width),
                   openness=float(np.linalg.norm(low - up) / width))


def extract(landmarks, matrix: np.ndarray | None, aspect: float) -> Features | None:
    """landmarks: sequence of 478 objects with .x .y .z (normalised). matrix: 4x4 or None."""
    if landmarks is None or len(landmarks) < 478:
        return None
    pts = np.array([[l.x * aspect, l.y, (getattr(l, "z", 0.0) or 0.0) * aspect] for l in landmarks])
    inv = None
    yaw = pitch = roll = 0.0
    if matrix is not None:
        m = np.asarray(matrix, dtype=float).reshape(4, 4)
        yaw, pitch, roll = head_pose(m)
        flip = np.diag([1.0, -1.0, -1.0])  # OpenGL (y up, z toward viewer) -> image frame
        rot = flip @ m[:3, :3] @ flip
        inv = rot.T
    le, re = _eye(pts, LEFT_EYE, inv), _eye(pts, RIGHT_EYE, inv)
    if le is None or re is None:
        return None
    return Features(left=le, right=re, yaw=yaw, pitch=pitch, roll=roll, normalized=inv is not None)


# ------------------------------------------------------------------ gestures
@dataclass
class GestureCfg:
    enter: float = 0.9          # normalised deviation that starts a gesture
    leave: float = 0.45         # must come back under this before the next gesture (hysteresis)
    dwell_s: float = 0.28       # held this long = deliberate (quick glances don't count)
    max_hold_s: float = 4.0     # held longer = looking away / resting, not a command
    init_range_h: float = 0.11  # starting guess for a full glance, in eye widths (adapts per person)
    init_range_v: float = 0.10
    max_yaw_speed: float = 40.0 # deg/s: faster head turn means "turning", not "looking"
    centre_tau_s: float = 6.0   # how fast the resting centre follows drift (pillow, posture)
    min_open_ratio: float = 0.55 # eyes must be at least this open vs baseline to read gaze


class GestureReader:
    def __init__(self, cfg: GestureCfg | None = None):
        self.cfg = cfg or GestureCfg()
        # responsive during a glance (beta), very still at rest (min_cutoff)
        self.fh, self.fv = OneEuro(1.5, 4.0), OneEuro(1.5, 4.0)
        self.mh, self.mv = Median3(), Median3()
        self.ch = self.cv = None
        self.range = {"left": self.cfg.init_range_h, "right": self.cfg.init_range_h, "up": self.cfg.init_range_v}
        self.peaks = {k: deque(maxlen=12) for k in self.range}
        self.active: str | None = None
        self.since = 0.0
        self.fired = False
        self.peak = 0.0
        self.armed = True
        self.prev_yaw = self.prev_t = None
        self.last = {"dx": 0.0, "dy": 0.0, "dir": None}
        self.speeds = deque(maxlen=40)  # glance peak speeds (fatigue signal)
        self._prev = None
        self._t_last: float | None = None
        self.calibrated = False
        self.sign_h = 1          # +1: looking right moves h positive on this camera (set by calibration)
        self.raw = (0.0, 0.0)
        self.filt = (0.0, 0.0)
        self.yaw_speed = 0.0

    def calibrate(self, result: dict) -> bool:
        """Apply an explicit gaze calibration (calibration.py): centre, this person's comfortable
        range per direction, and which way is right on this camera. Thresholds become fractions
        of the measured range, so a narrow range still works and a wrong guess can't block it."""
        if not result or not result.get("calibrated"):
            self.calibrated = False
            return False
        self.ch, self.cv = result["center"]
        self.sign_h = int(result.get("sign_h", 1))
        rg = result["range"]
        # aim for ~70% of a comfortable full glance; never below a noise floor
        self.range["left"] = max(0.02, 0.7 * rg["left"])
        self.range["right"] = max(0.02, 0.7 * rg["right"])
        if rg.get("up"):
            self.range["up"] = max(0.015, 0.7 * rg["up"])
        self.cfg.enter, self.cfg.leave = 0.85, 0.4
        self.calibrated = True
        self.armed = True
        return True

    def update(self, t: float, f: Features, open_ratio: float) -> dict | None:
        cfg = self.cfg
        h, v = self.fh(self.mh(f.h), t), self.fv(self.mv(f.v), t)
        self.raw, self.filt = (f.h, f.v), (h, v)
        frame_dt = 1 / 30 if self._t_last is None else min(max(t - self._t_last, 1e-3), 0.25)
        self._t_last = t
        if self.ch is None:
            self.ch, self.cv = h, v
        yaw_speed = 0.0
        if self.prev_t is not None and t > self.prev_t:
            yaw_speed = abs(f.yaw - self.prev_yaw) / (t - self.prev_t)
        self.prev_yaw, self.prev_t = f.yaw, t
        self.yaw_speed = yaw_speed
        # + h is image-right. The image is mirrored (selfie view), so image-right is the person's
        # right as they see the screen: looking at the right side of the screen moves h positive.
        dx_raw, dy_raw = self.sign_h * (h - self.ch), v - self.cv
        dx = dx_raw / (self.range["right"] if dx_raw > 0 else self.range["left"])
        dy = -dy_raw / self.range["up"]  # up is negative v
        speed = 0.0
        if self._prev is not None and t > self._prev[0]:
            speed = math.hypot(h - self._prev[1], v - self._prev[2]) / (t - self._prev[0])
        self._prev = (t, h, v)

        eyes_ok = open_ratio >= cfg.min_open_ratio
        head_ok = yaw_speed < cfg.max_yaw_speed
        mag = max(abs(dx), dy)
        cand = None
        if eyes_ok and head_ok:
            if dy >= cfg.enter and dy >= abs(dx):
                cand = "up"
            elif abs(dx) >= cfg.enter:
                cand = "right" if dx > 0 else "left"

        # resting near centre: let the centre follow slow drift
        if mag < cfg.leave and eyes_ok:
            # real frame interval (webcams run anywhere from 10 to 60 fps), not an assumed 1/30 s
            k = min(1.0, frame_dt / cfg.centre_tau_s)
            self.ch += k * (h - self.ch)
            self.cv += k * (v - self.cv)
            self.armed = True

        event = None
        if cand and self.armed:
            if self.active != cand:
                self.active, self.since, self.fired, self.peak = cand, t, False, 0.0
            self.peak = max(self.peak, speed)
            held = t - self.since
            if not self.fired and held >= cfg.dwell_s:
                dev = abs(dy_raw) if cand == "up" else abs(dx_raw)
                self.peaks[cand].append(dev)
                event = {"type": "gesture", "dir": cand, "strength": round(min(mag / 2, 1.0), 3),
                         "held_s": round(held, 2), "calibrated": self.calibrated}
                self.fired, self.armed = True, False
                self.speeds.append(self.peak)
                self._adapt(cand)
        elif not cand:
            self.active = None
        if self.active and t - self.since > cfg.max_hold_s:
            self.active = None  # looking away for a while: not a command
        self.last = {"dx": round(dx, 3), "dy": round(dy, 3), "dir": self.active if self.fired else None,
                     "eyes_ok": eyes_ok, "head_ok": head_ok, "cand": cand,
                     "held": round(t - self.since, 2) if self.active else 0.0, "armed": self.armed}
        return event

    def _adapt(self, d: str) -> None:
        """Learn this person's comfortable glance size: the range follows the median of their
        recent deliberate glances, so a narrowing range of motion (common in ALS) still works."""
        if len(self.peaks[d]) >= 3:
            med = float(np.median(self.peaks[d]))
            target = max(0.04, med / 1.25)
            self.range[d] += 0.3 * (target - self.range[d])


# ------------------------------------------------------------------ fatigue
class Fatigue:
    """How tired are the eyes right now, compared with this person earlier today.

    Signals (each 0..1, then combined):
      ptosis   lids lower than their own baseline (fatigue and ALS both lower the lids)
      perclos  share of the last 60 s with eyes >= 80% closed (driver-fatigue research)
      blinks   blink rate well above or below baseline
      speed    deliberate glances getting slower than early in the session
    """

    # Hand-set, not fitted: there is no labelled ALS eye-fatigue data behind these. Ptosis gets
    # the most weight because lid droop is the most visible, least noisy signal on a webcam.
    WEIGHTS = {"ptosis": 0.4, "perclos": 0.3, "blinks": 0.1, "slowing": 0.2}
    LEVELS = {"tiring": 0.3, "tired": 0.55}

    def __init__(self, fps: float = 30.0):
        self.fps = fps
        self.prior_open: float | None = None  # from a saved profile: yesterday's "normal"
        self.base_open: float | None = None
        self.base_n = 0
        self.open_ema: float | None = None
        self.closed_hist: deque[bool] = deque(maxlen=int(60 * fps))
        self.blink_times: deque[float] = deque(maxlen=200)
        self.in_blink_since: float | None = None
        self.base_blink_rate: float | None = None
        self.base_speed: float | None = None
        self.t0: float | None = None
        self.state = {"score": 0.0, "level": "fresh", "reason": "", "ptosis": 0.0, "perclos": 0.0,
                      "blink_rate": 0.0, "slowing": 0.0, "open_ratio": 1.0}

    def update(self, t: float, openness: float, speeds: deque) -> dict:
        if self.t0 is None:
            self.t0 = t
        # baseline lid opening: the 80th percentile style running max over the first minute,
        # then a very slow follower (so a whole day's drift doesn't redefine "normal")
        if self.base_open is None:
            # Start from the saved baseline if there is one, so a session that starts when the
            # person is already tired doesn't learn "tired" as their normal.
            self.base_open = self.prior_open if self.prior_open else openness
        elif t - self.t0 < 60:
            self.base_open = max(self.base_open * 0.999, openness) if openness < self.base_open * 1.3 else self.base_open
        self.open_ema = openness if self.open_ema is None else self.open_ema + 0.05 * (openness - self.open_ema)
        ratio = openness / self.base_open if self.base_open > 1e-6 else 1.0
        closed = ratio < 0.2
        self.closed_hist.append(closed)
        # blinks: closures of 60..400 ms
        if ratio < 0.5:
            if self.in_blink_since is None:
                self.in_blink_since = t
        elif self.in_blink_since is not None:
            dur = t - self.in_blink_since
            if 0.06 <= dur <= 0.4:
                self.blink_times.append(t)
            self.in_blink_since = None
        window = 60.0
        recent = [b for b in self.blink_times if t - b <= window]
        span = min(window, max(1e-6, t - self.t0))
        rate = len(recent) * 60.0 / span if span >= 10 else 0.0
        if self.base_blink_rate is None and t - self.t0 >= 60:
            self.base_blink_rate = max(rate, 6.0)
        if self.base_speed is None and len(speeds) >= 5:
            self.base_speed = float(np.median(list(speeds)[:5]))

        ema_ratio = self.open_ema / self.base_open if self.base_open > 1e-6 else 1.0
        ptosis = float(np.clip((0.85 - ema_ratio) / 0.35, 0, 1))         # 15% lower lids starts counting
        perclos = float(np.mean(self.closed_hist)) if self.closed_hist else 0.0
        perclos_s = float(np.clip((perclos - 0.05) / 0.2, 0, 1))
        blink_s = 0.0
        if self.base_blink_rate:
            blink_s = float(np.clip(abs(rate - self.base_blink_rate) / (self.base_blink_rate * 1.5), 0, 1))
        slowing = 0.0
        if self.base_speed and len(speeds) >= 8:
            cur = float(np.median(list(speeds)[-5:]))
            slowing = float(np.clip((1 - cur / self.base_speed) / 0.5, 0, 1))
        parts = {"lids lower than usual": ptosis, "eyes closing often": perclos_s,
                 "blinking changed": blink_s, "glances slower": slowing}
        w = self.WEIGHTS
        score = float(np.clip(w["ptosis"] * ptosis + w["perclos"] * perclos_s + w["blinks"] * blink_s
                              + w["slowing"] * slowing, 0, 1))
        reason = max(parts, key=parts.get) if score >= 0.25 else ""
        level = "tired" if score >= self.LEVELS["tired"] else "tiring" if score >= self.LEVELS["tiring"] else "fresh"
        self.state = {"score": round(score, 3), "level": level, "reason": reason, "ptosis": round(ptosis, 3),
                      "perclos": round(perclos, 3), "blink_rate": round(rate, 1), "slowing": round(slowing, 3),
                      "open_ratio": round(ratio, 3), "perclos_score": round(perclos_s, 3),
                      "blink_score": round(blink_s, 3), "base_open": round(self.base_open, 4),
                      "base_blink_rate": self.base_blink_rate, "from_profile": bool(self.prior_open)}
        return self.state


# ------------------------------------------------------------------ help pattern
class HelpPattern:
    """Three deliberate eyes-held-shut closures (1.2 to 4 s each) started within 15 s = call for help.
    Ordinary blinks are ignored; eyes closed longer (resting, sleep) reset it; a lost face is never
    read as closed eyes."""

    def __init__(self, min_s=1.2, max_s=4.0, count=3, window_s=15.0, refractory_s=6.0):
        self.min_s, self.max_s, self.count, self.window_s, self.refractory_s = min_s, max_s, count, window_s, refractory_s
        self.starts: list[float] = []
        self.closed_since: float | None = None
        self.quiet_until = -1e9
        self.closed_for = 0.0

    def update(self, t: float, closed: bool | None) -> dict:
        if t < self.quiet_until:
            return {"counted": 0, "fired": False}
        if closed is None:  # face lost
            self.closed_since = None
            return {"counted": len(self.starts), "fired": False}
        self.closed_for = (t - self.closed_since) if (closed and self.closed_since is not None) else 0.0
        if closed and self.closed_since is None:
            self.closed_since = t
        elif not closed and self.closed_since is not None:
            dur = t - self.closed_since
            if self.min_s <= dur <= self.max_s:
                self.starts = [s for s in self.starts if self.closed_since - s <= self.window_s] + [self.closed_since]
            elif dur > self.max_s:
                self.starts = []
            self.closed_since = None
            if len(self.starts) >= self.count:
                self.starts, self.quiet_until = [], t + self.refractory_s
                return {"counted": self.count, "fired": True}
        return {"counted": len(self.starts), "fired": False}


# ------------------------------------------------------------------ the module the runtime uses
@dataclass
class EyeState:
    available: bool = False
    quality: float = 0.0
    events: list = field(default_factory=list)


class Eyes:
    """One object per runtime. update() per frame; snapshot() for the UI."""

    def __init__(self, fps: float = 30.0):
        self.reader = GestureReader()
        self.fatigue = Fatigue(fps)
        self.help = HelpPattern()
        self.seen: deque[bool] = deque(maxlen=int(10 * fps))
        self.features: Features | None = None
        self.counts = {"left": 0, "right": 0, "up": 0}
        self.help_progress = 0

    def update(self, t: float, f: Features | None, blink_score: float | None = None) -> list[dict]:
        events: list[dict] = []
        self.seen.append(f is not None)
        self.features = f
        if f is None:
            self.help.update(t, None)
            return events
        fat = self.fatigue.update(t, f.openness, self.reader.speeds)
        ratio = fat["open_ratio"]
        closed = (blink_score is not None and blink_score > 0.55) or ratio < 0.25
        h = self.help.update(t, closed)
        self.help_progress = h["counted"]
        if h["fired"]:
            events.append({"type": "help", "why": "three long blinks"})
        g = self.reader.update(t, f, ratio)
        if g:
            self.counts[g["dir"]] += 1
            events.append(g)
        return events

    # ---- per-person profile, saved between sessions
    def to_profile(self) -> dict:
        fat = self.fatigue
        return {"version": 2, "range": dict(self.reader.range), "sign_h": self.reader.sign_h,
                "gaze_calibrated": self.reader.calibrated,
                "base_open": fat.base_open, "base_blink_rate": fat.base_blink_rate,
                "base_speed": fat.base_speed}

    def load_profile(self, d: dict | None) -> None:
        if not d:
            return
        for k, v in (d.get("range") or {}).items():
            if k in self.reader.range and v:
                self.reader.range[k] = max(0.04, float(v))
        self.reader.sign_h = int(d.get("sign_h", 1) or 1)
        if d.get("gaze_calibrated"):
            # ranges and direction carry over; the centre re-learns from the first frames
            self.reader.calibrated = True
            self.reader.cfg.enter, self.reader.cfg.leave = 0.85, 0.4
        self.fatigue.prior_open = d.get("base_open") or None
        self.fatigue.base_blink_rate = d.get("base_blink_rate") or None
        self.fatigue.base_speed = d.get("base_speed") or None

    def availability(self) -> dict:
        """Four separate questions, never collapsed into "eyes OK": is there a face, can the eyes
        be read (open enough), has this person's gaze been calibrated, and is gaze usable for
        navigation right now (calibrated, seen most of the last 10 s, not tired)."""
        face = self.features is not None
        eyes = face and self.fatigue.state.get("open_ratio", 0) >= self.reader.cfg.min_open_ratio
        cal = self.reader.calibrated
        usable = bool(cal and self.quality() >= 0.7 and self.fatigue.state.get("level") != "tired")
        return {"face": face, "eyes": bool(eyes), "gaze_calibrated": cal, "gaze_usable": usable}

    def state_word(self) -> str:
        """What the person's screen says. Never a percentage: 'Eyes 79%' has no honest meaning."""
        if self.features is None or self.quality() < 0.5:
            return "not seen"
        lvl = self.fatigue.state["level"]
        return {"fresh": "available", "tiring": "getting tired", "tired": "resting"}[lvl]

    def quality(self) -> float:
        """Share of the last 10 s with a usable face and open-enough eyes."""
        if not self.seen:
            return 0.0
        seen = float(np.mean(self.seen))
        eyes = 1.0 if self.fatigue.state["open_ratio"] >= self.reader.cfg.min_open_ratio else 0.5
        return round(seen * eyes, 3)

    def snapshot(self) -> dict:
        f = self.features
        return {
            "available": f is not None,
            "quality": self.quality(),
            "pose": {"yaw": round(f.yaw, 1), "pitch": round(f.pitch, 1), "roll": round(f.roll, 1)} if f else None,
            "iris": {"h": round(f.h, 4), "v": round(f.v, 4)} if f else None,
            "openness": {"left": round(f.left.openness, 3), "right": round(f.right.openness, 3)} if f else None,
            "gesture": self.reader.last,
            "range": {k: round(v, 4) for k, v in self.reader.range.items()},
            "fatigue": self.fatigue.state,
            "help_progress": self.help_progress,
            "help": {"closed_for": round(self.help.closed_for, 2), "min_s": self.help.min_s,
                     "max_s": self.help.max_s, "count": self.help.count, "window_s": self.help.window_s},
            "counts": self.counts,
            "word": self.state_word(),
            "availability": self.availability(),
            "sign_h": self.reader.sign_h,
            "raw": [round(x, 4) for x in self.reader.raw],
            "filt": [round(x, 4) for x in self.reader.filt],
            "centre": [round(self.reader.ch, 4), round(self.reader.cv, 4)] if self.reader.ch is not None else None,
            "yaw_speed": round(self.reader.yaw_speed, 1),
            "cfg": {"enter": self.reader.cfg.enter, "leave": self.reader.cfg.leave,
                    "dwell_s": self.reader.cfg.dwell_s, "max_yaw_speed": self.reader.cfg.max_yaw_speed,
                    "min_open_ratio": self.reader.cfg.min_open_ratio},
            "weights": Fatigue.WEIGHTS, "levels": Fatigue.LEVELS,
        }
