"""Live app: a local Python engine + a browser UI on the same laptop.

    python -m residual.server                    # opens http://localhost:8000
    python -m residual.server --source webcam    # real camera (needs requirements-vision.txt)
    python -m residual.server --source keyboard  # hold H / B / K to "gesture" (no camera needed)
    python -m residual.server --source autopilot # a simulated person uses it (demo mode fallback)

Sources produce raw features; calibration fits a normalizer; the degradation
slider corrupts normalized amplitudes (never scores); the Engine does the rest.
Recorded sessions (sessions/*.jsonl) replay through the identical Engine:
    python -m residual.replay sessions/<file>.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import dataclasses
import json
import logging
import math
import threading
import time
import webbrowser
from pathlib import Path

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response

from .config import CHANNELS, Config
from .degrade import Degradation, from_level
from .engine import Engine
from .gaze import Eyes
from .events import Frame
from .calibration import Calibration, ChannelResult, fit_calibration
from .normalize import Normalizer
from .outputs import Alerts, Keystrokes, Speech
from .sim import Scenario, SimUser, simulated_calibration

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
SESSIONS = ROOT / "sessions"
PROFILES = ROOT / "profiles"
log = logging.getLogger("residual.server")


class KeyboardSource:
    """Hold a key to 'gesture'. Amplitude rises and falls smoothly like a real
    movement, with sensor-like noise, so the full pipeline is exercised."""

    def __init__(self, rng: np.random.Generator):
        self.rng = rng
        self.down = {c: False for c in CHANNELS}
        self.a = {c: 0.0 for c in CHANNELS}
        self.t_prev = None

    def frame(self, t: float) -> tuple[dict, dict]:
        dt = 1 / 30 if self.t_prev is None else max(t - self.t_prev, 1e-3)
        self.t_prev = t
        k = 1 - math.exp(-dt / 0.09)
        raw = {}
        for c in CHANNELS:
            target = 1.0 if self.down[c] else 0.0
            self.a[c] += (target - self.a[c]) * k
            raw[c] = self.a[c] + float(self.rng.normal(0, 0.03))
        return raw, {c: True for c in CHANNELS}


class Runtime:
    def __init__(self, source: str, cfg: Config | None = None, seed: int = 0,
                 person: str = "default", board_outputs: bool = True):
        self.cfg_template = cfg or Config()
        self.cfg_template.adaptive_scan = True
        self.rng = np.random.default_rng(seed)
        self.lock = threading.RLock()
        self.source_name = source
        self.levels = {c: 0.0 for c in CHANNELS}
        self.deg_kind = "mixed"
        self.normalizer = Normalizer()
        self.events: list[dict] = []
        self.preview: bytes | None = None
        self.eyes = Eyes()
        self.person = "".join(ch for ch in person if ch.isalnum() or ch in "-_") or "default"
        self.profile_path = PROFILES / f"{self.person}.json"
        self._load_profile()
        self._profile_saved_t = 0.0
        # When the Residual app drives the engine, the engine's own scanning board must not
        # speak, type or send alerts in parallel with the app (the app owns those).
        self.board_outputs = board_outputs
        self.gaze_sample: list | None = None
        self.cal: Calibration | None = None      # a calibration in progress
        self.cal_fit = None                      # (normalizer, rest, gestures, results) of the last one
        self.cal_rest_stats: dict | None = None
        self.cal_results: dict[str, dict] = {}   # channel -> verdict, for the UI
        self.cal_seq = 0
        self.gaze_cal: dict | None = None
        self._last_gaze = None                   # this frame's eye features (None = no face)
        self.brow_parts: dict | None = None
        self.camera_error: str | None = None
        self._roles: dict = {"select": ["hand"], "next": None, "back": None}
        self.calib_data: dict | None = None
        self.recording = None
        self.speech, self.alerts, self.keys = Speech(), Alerts(), Keystrokes()
        self.keyboard = KeyboardSource(self.rng)
        self.webcam = None
        self.autopilot: SimUser | None = None
        self.fps_measured = 0.0
        self.t0 = time.monotonic()
        self.t = 0.0
        self._stop = False
        self._new_engine()
        self.set_source(source)
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    # --------------------------------------------------------------- control
    def _new_engine(self) -> None:
        import copy

        self.engine = Engine(copy.deepcopy(self.cfg_template))
        self.engine.keep_trace = True
        self.engine.policy.note_activity(self.t)
        self.speech.prewarm([o.speech for o in self.engine.board.options])

    def _open_webcam(self) -> None:
        from .perception import Webcam

        try:
            self.webcam = Webcam()
            self.camera_error = None
        except Exception as e:   # missing models, OpenCV, MediaPipe: say so, don't die
            self.webcam = None
            self.camera_error = str(e)
            self._push("log", {"text": f"Camera could not start: {e}"})

    def set_source(self, name: str) -> None:
        with self.lock:
            self.source_name = name
            if name == "webcam" and self.webcam is None:
                self._open_webcam()
            if name == "autopilot":
                self.autopilot = SimUser(Scenario(severity=0.0), self.rng)
                self.normalizer = Normalizer()
                self._new_engine()
                rest, g = simulated_calibration(self.rng, self.engine.cfg.fps)
                self.engine.calibrate(rest, g, self.t)
                self.calib_data = {"normalizer": self.normalizer.to_dict(), "rest": rest,
                                   "gestures": g}
                self.cal = None
                self.cal_results = {c: {**ChannelResult(status="VALID", count=4).as_dict(),
                                        "reason": "simulated person"} for c in CHANNELS}
                self.cal_seq += 1
                self._push("log", {"text": "Autopilot: simulated person calibrated"})
            else:
                self.start_calibration()

    def start_calibration(self, channels: list[str] | None = None, gaze: bool | None = None) -> None:
        """Full calibration, or a retry of some movements (channels=["brow"]) / gaze only
        (channels=[], gaze=True). Retries keep everything else from the last calibration."""
        with self.lock:
            if self.source_name == "autopilot":
                self.set_source("autopilot")
                return
            webcam = self.source_name == "webcam"
            partial = channels is not None and self.cal_fit is not None
            self.cal = Calibration(
                self.t, preflight=webcam and not partial,
                channels=list(channels) if channels is not None else None,
                gaze=webcam if gaze is None else (gaze and webcam),
                prev_rest=self.cal_rest_stats if partial else None,
                rest_s=2.0 if partial else 4.0)
            self._partial = partial
            self._push("calibration", {"phase": self.cal.name, "prompt": "Calibration started",
                                       "channels": self.cal.channels})

    def skip_calibration_step(self) -> None:
        with self.lock:
            if self.cal is not None:
                self.cal.skip(self.t)

    def cancel_calibration(self) -> None:
        """'Use keyboard for now': stop waiting on a camera that isn't working. Whatever was
        learned before stays; nothing new is claimed. Keys in the app keep working."""
        with self.lock:
            if self.cal is not None:
                self.cal = None
                self._push("calibration", {"phase": "cancelled"})
                self._push("log", {"text": "Calibration cancelled: using keys for now"})

    def retry_camera(self) -> None:
        with self.lock:
            if self.webcam is not None:
                self.webcam.stop()
                self.webcam = None
            if self.source_name == "webcam":
                self._open_webcam()
                self.start_calibration()

    def camera_info(self) -> dict | None:
        if self.source_name != "webcam":
            return None
        if self.webcam is None:
            return {"open": False, "age": None, "error": self.camera_error}
        lf = getattr(self.webcam, "last_frame_t", None)
        return {"open": getattr(self.webcam, "opened", None),
                "age": None if lf is None else round(time.monotonic() - lf, 1),
                "error": self.camera_error}

    def set_degradation(self, channel: str, level: float) -> None:
        with self.lock:
            self.levels[channel] = float(np.clip(level, 0, 1))

    def toggle_recording(self, on: bool) -> None:
        with self.lock:
            if on and self.recording is None:
                if self.calib_data is None:
                    self._push("log", {"text": "Calibrate before recording"})
                    return
                # Start from a fresh engine with the same calibration, so a replay
                # of this file reproduces every decision exactly.
                fps = self.engine.cfg.fps
                self._new_engine()
                self.engine.cfg.fps = fps
                cd = self.calib_data
                self.engine.calibrate(cd["rest"], cd["gestures"], self.t)
                SESSIONS.mkdir(exist_ok=True)
                path = SESSIONS / time.strftime("session_%Y%m%d_%H%M%S.jsonl")
                self.recording = open(path, "w")
                self.recording.write(json.dumps({"header": True, "source": self.source_name,
                                                 "config": dataclasses.asdict(self.engine.cfg),
                                                 "calibration": cd, "t_start": self.t},
                                                default=_json_default) + "\n")
                self._push("log", {"text": f"Recording to {path.name}"})
            elif not on and self.recording is not None:
                self.recording.close()
                self.recording = None
                self._push("log", {"text": "Recording stopped"})

    # ------------------------------------------------------------------ loop
    def _raw_frame(self) -> tuple[dict, dict] | None:
        if self.source_name == "keyboard":
            return self.keyboard.frame(self.t)
        if self.source_name == "autopilot" and self.autopilot is not None:
            if self.cal is None:
                self.autopilot.act(self.t, self.engine)
            f = self.autopilot.frame(self.t)
            return f.amps, f.visible
        if self.source_name == "webcam" and self.webcam is not None:
            v = self.webcam.read()
            if v is None:
                return None
            _, raw, jpg = v
            g = raw.pop("_gaze", None)
            self._last_gaze = g
            self.brow_parts = raw.pop("_brow", None)
            for e in self.eyes.update(self.t, g, raw.get("blink")):
                self._push("gaze" if e["type"] == "gesture" else "help", e)
            gl = self.eyes.reader.last
            self.gaze_sample = None if g is None else [
                gl.get("dx", 0.0), gl.get("dy", 0.0), self.eyes.fatigue.state.get("open_ratio", 1.0),
                round(self.eyes.help.closed_for, 2), gl.get("cand") or ""]
            if jpg:
                self.preview = jpg
            return raw, self.normalizer.visible(raw)
        return None

    def _loop(self) -> None:
        period = 1.0 / self.cfg_template.fps
        nxt = time.monotonic()
        n, tick = 0, time.monotonic()
        while not self._stop:
            now = time.monotonic()
            if now < nxt:
                time.sleep(min(nxt - now, 0.005))
                continue
            nxt += period
            if now - nxt > 0.5:  # fell behind (e.g. laptop slept): don't burst
                nxt = now + period
            with self.lock:
                self.t = now - self.t0
                got = self._raw_frame()
                if got is None:
                    # no new camera frame: the preflight still has to notice that
                    if self.cal is not None and self.source_name == "webcam":
                        self._last_gaze = None
                        self._calibration_step({}, {})
                    continue
                raw, vis = got
                if self.cal is not None:
                    self._calibration_step(raw, vis)
                else:
                    self._engine_step(raw, vis)
            n += 1
            if now - tick >= 1.0:
                self.fps_measured = n / (now - tick)
                n, tick = 0, now
            if self.t - self._profile_saved_t > 60 and self.source_name == "webcam":
                self._profile_saved_t = self.t
                self._save_profile()

    def _engine_step(self, raw: dict, vis: dict) -> None:
        amps = self.normalizer(raw)
        out_a, out_v = {}, {}
        for c in CHANNELS:
            d: Degradation = from_level(self.levels[c], self.deg_kind)
            if vis.get(c, True):
                out_a[c], out_v[c] = d.apply(amps[c], self.rng)
            else:
                out_a[c], out_v[c] = 0.0, False
        frame = Frame(self.t, out_a, out_v)
        evs = self.engine.step(frame)
        if self.engine.trace and self.gaze_sample is not None and self.source_name == "webcam":
            self.engine.trace[-1]["g"] = self.gaze_sample
        if self.recording is not None:
            self.recording.write(json.dumps({"t": frame.t, "amps": frame.amps,
                                             "visible": frame.visible}) + "\n")
        for e in evs:
            if e.kind == "fire":
                if self.board_outputs:
                    self.keys.on_fire()
            elif e.kind == "select":
                opt = self.engine.board.options[e.data["index"]]
                if self.board_outputs:
                    self.keys.on_select(opt.speech)
                if self.autopilot is not None and self.source_name == "autopilot":
                    self.autopilot.on_select(self.t, e.data["index"])
            elif e.kind == "alert":
                if self.board_outputs:
                    self.alerts.send(e.data["text"])
            if e.kind in ("fire", "aux_fire", "attempt", "speak", "alert", "select", "migrate",
                          "verify_start", "verify_ok", "verify_fail", "probe_ok", "probe_fail",
                          "changepoint", "confirm", "cancel"):
                self._push(e.kind, e.data)

    def _calibration_step(self, raw: dict, vis: dict) -> None:
        cal = self.cal
        g = self._last_gaze
        was_failed = cal.failed
        cal.step(self.t, raw, face=g is not None or (self.source_name != "webcam" and bool(raw)),
                 yaw=g.yaw if g is not None else None,
                 gaze_hv=(g.h, g.v) if g is not None else None,
                 camera=self.camera_info())
        if cal.failed and not was_failed:
            self._push("calibration", {"phase": "preflight", "failed": cal.failed, "reason": cal.pre["reason"]})
            self._push("log", {"text": f"Camera check failed: {cal.pre['reason']}"})
        if cal.done:
            self._finish_calibration()

    def _finish_calibration(self) -> None:
        cal, self.cal = self.cal, None
        if cal.rest_stats:
            self.cal_rest_stats = dict(cal.rest_stats)
        if cal.channels or self.cal_fit is None:
            prev = self.cal_fit if getattr(self, "_partial", False) else None
            norm, rest, gestures, results = fit_calibration(cal, prev)
            self.cal_fit = (norm, rest, gestures, results)
            self.normalizer = norm
            self._new_engine()
            if self.fps_measured > 5:
                self.engine.cfg.fps = round(self.fps_measured, 1)
            eligible = {c: results[c].usable for c in CHANNELS}
            self.engine.calibrate(rest, gestures, self.t, eligible=eligible)
            self.calib_data = {"normalizer": norm.to_dict(), "rest": rest, "gestures": gestures,
                               "validity": {c: results[c].status for c in CHANNELS}}
            self.cal_results = {c: results[c].as_dict() for c in CHANNELS}
        if cal.gaze_result is not None:
            self.gaze_cal = cal.gaze_result
            self.eyes.reader.calibrate(cal.gaze_result)
            if self.source_name == "webcam":
                self._save_profile()
        self.cal_seq += 1
        ok = all(r["status"] == "VALID" for r in self.cal_results.values())
        self._push("calibration", {"phase": "done", "results": self.cal_results, "gaze": self.gaze_cal,
                                   "all_ok": ok})
        self._push("log", {"text": "Calibration: " + ", ".join(
            f"{c} {r['status']} ({r['count']}/3, sep {r['separation']})" for c, r in self.cal_results.items())})

    # ---------------------------------------------------------------- roles
    def roles(self) -> dict:
        """The engine decides who does what; the app only renders it.

        Select: the policy's control option. Next / Back: the eyes while gaze is calibrated,
        seen and not tired; otherwise other channels that passed calibration and are reliable
        (p_hit >= aux_min_p), kept sticky so roles don't flicker; otherwise none (auto-scan)."""
        eng = self.engine
        ctl = list(eng.policy.control)
        fused = len(ctl) > 1
        av = self.eyes.availability() if self.source_name == "webcam" else {"gaze_usable": False}
        eligible_aux = []
        for c in CHANNELS:
            st = eng.stats.get((c,))
            if c in ctl or not eng.eligible[c] or st is None:
                continue
            if st.p_hat >= eng.cfg.aux_min_p:
                eligible_aux.append(c)
        eligible_aux.sort(key=lambda c: -eng.stats[(c,)].p_hat)
        prev = self._roles
        if av.get("gaze_usable") and not fused:
            nxt, back, why = "gaze_r", "gaze_l", "eyes are calibrated, seen and not tired"
        elif fused:
            nxt, back, why = None, None, "two movements are combined for Select: the screen scans"
        else:
            keep = [r for r in (prev.get("next"), prev.get("back")) if r in eligible_aux]
            order = keep + [c for c in eligible_aux if c not in keep]
            nxt = order[0] if order else None
            back = order[1] if len(order) > 1 else None
            why = "reliable calibrated movements" if nxt else "no other reliable movement: the screen scans"
        mode = 3 if nxt and back else 2 if nxt else 1
        self._roles = {"select": ctl, "next": nxt, "back": back, "eligible_aux": eligible_aux,
                       "mode": mode, "why": why, "gaze": av}
        return self._roles

    # -------------------------------------------------------------- profile
    def _load_profile(self) -> None:
        try:
            d = json.loads(self.profile_path.read_text())
            self.eyes.load_profile(d.get("eyes"))
            log.info("Loaded profile %s", self.profile_path.name)
        except FileNotFoundError:
            pass
        except Exception as e:  # a broken profile must never stop the engine
            log.warning("Ignoring profile %s: %s", self.profile_path, e)

    def _save_profile(self) -> None:
        try:
            PROFILES.mkdir(exist_ok=True)
            tmp = self.profile_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"person": self.person, "saved": time.time(),
                                       "eyes": self.eyes.to_profile()}, default=_json_default))
            tmp.replace(self.profile_path)
        except Exception as e:
            log.warning("Could not save profile: %s", e)

    def trace_since(self, i: int) -> list[dict]:
        with self.lock:
            return [r for r in self.engine.trace if r["i"] > i]

    # -------------------------------------------------------------- readouts
    def _push(self, kind: str, data: dict) -> None:
        self.events.append({"seq": len(self.events), "t": round(self.t, 2), "kind": kind,
                            "data": data})
        if len(self.events) > 5000:
            self.events = self.events[-2000:]

    def state(self) -> dict:
        with self.lock:
            snap = self.engine.snapshot(history=True)
            cal = self.cal.status(self.t) if self.cal is not None else None
            snap["runtime"] = {
                "source": self.source_name,
                "levels": self.levels,
                "fps": round(self.fps_measured, 1),
                "calibration": cal,
                "calibration_result": {"seq": self.cal_seq, "results": self.cal_results,
                                       "gaze": self.gaze_cal,
                                       "all_ok": bool(self.cal_results) and all(
                                           r["status"] == "VALID" for r in self.cal_results.values())},
                "roles": self.roles(),
                "simulated_weakening": any(v > 0 for v in self.levels.values()),
                "brow": self._brow_debug(),
                "recording": self.recording is not None,
                "tts": self.speech.enabled,
                "alerts": self.alerts.enabled,
                "keystrokes": self.keys.mode,
                "adaptive_scan": self.engine.cfg.adaptive_scan,
                "person": self.person,
                "board_outputs": self.board_outputs,
                "config": {k: getattr(self.engine.cfg, k) for k in
                           ("fps", "k_onset", "dwell_s", "p_switch", "p_accept", "verify_hits",
                            "verify_window_s", "cooldown_s", "refractory_s", "hazard", "cp_threshold")},
            }
            snap["eyes"] = self.eyes.snapshot()
            cam = self.camera_info()
            if cam is not None:
                snap["runtime"]["camera"] = cam
            if self.preview is not None and self.source_name == "webcam":
                snap["runtime"]["preview"] = base64.b64encode(self.preview).decode()
            return snap

    def _brow_debug(self) -> dict | None:
        n = self.normalizer
        parts = self.brow_parts
        return {"parts": parts, "sources": n.sources.get("brow"),
                "rest": {s: round(n.rest.get(s, 0.0), 4) for s in ("brow", "brow_geo")},
                "peak": {s: round(n.peak.get(s, 0.0), 4) for s in ("brow", "brow_geo")},
                "normalized": {s: round(n.source(s, parts[k]), 3) for s, k in (("brow", "bs"), ("brow_geo", "geo"))
                               if parts and parts.get(k) is not None} if parts else None,
                "combined": round(self.engine.last_amp["brow"], 3)}

    def command(self, msg: dict) -> None:
        typ = msg.get("type")
        if typ == "key" and msg.get("channel") in CHANNELS:
            self.keyboard.down[msg["channel"]] = bool(msg.get("down"))
        elif typ == "degrade" and msg.get("channel") in CHANNELS:
            self.set_degradation(msg["channel"], float(msg.get("level", 0)))
        elif typ == "source" and msg.get("value") in ("keyboard", "autopilot", "webcam"):
            try:
                self.set_source(msg["value"])
            except Exception as e:
                self._push("log", {"text": f"Could not start {msg['value']}: {e}"})
        elif typ == "calibrate":
            ch = msg.get("channel")
            if ch == "gaze":
                self.start_calibration(channels=[], gaze=True)
            elif ch in CHANNELS:
                self.start_calibration(channels=[ch], gaze=False)
            else:
                self.start_calibration()
        elif typ == "calibration_skip":
            self.skip_calibration_step()
        elif typ == "calibration_cancel":
            self.cancel_calibration()
        elif typ == "camera_retry":
            self.retry_camera()
        elif typ == "reset":
            with self.lock:
                self.levels = {c: 0.0 for c in CHANNELS}
                self.set_source(self.source_name)
        elif typ == "record":
            self.toggle_recording(bool(msg.get("value")))
        elif typ == "lock":
            # Clinician lock: keep measuring and adapting thresholds, but never move control.
            with self.lock:
                mode = "fixed" if msg.get("value") else "residual"
                self.cfg_template.policy_mode = mode
                self.engine.cfg.policy_mode = mode
                self._push("log", {"text": "Control locked by the care team" if mode == "fixed" else "Control unlocked"})
        elif typ == "autoscan":
            with self.lock:
                self.engine.cfg.adaptive_scan = bool(msg.get("value"))
                self.cfg_template.adaptive_scan = self.engine.cfg.adaptive_scan

    def stop(self) -> None:
        self._stop = True
        if self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=2.0)
        if self.source_name == "webcam":
            self._save_profile()
        if self.webcam:
            self.webcam.stop()
        self.toggle_recording(False)


def create_app(runtime: Runtime):
    app = FastAPI(title="Residual")

    @app.get("/")
    def index():
        return FileResponse(UI / "index.html")

    @app.get("/api/tts")
    def tts(text: str):
        audio = runtime.speech.audio(text[:200])
        if audio is None:
            return Response(status_code=204)
        return Response(audio, media_type="audio/mpeg")

    @app.get("/api/state")
    def state():
        return runtime.state()

    @app.get("/api/health")
    def health():
        """For the app's supervisor: the process answering is not the same as the camera working."""
        cam = runtime.camera_info()
        ok = True
        why = ""
        if cam is not None:
            if cam.get("open") is False:
                ok, why = False, "camera_not_open"
            elif cam.get("age") is None or cam["age"] > 3.0:
                ok, why = False, "no_frames"
        if runtime.cal is not None and runtime.cal.failed:
            ok, why = False, runtime.cal.failed
        return {"ok": ok, "why": why, "source": runtime.source_name, "camera": cam,
                "calibrating": runtime.cal is not None, "fps": round(runtime.fps_measured, 1)}

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        seq = len(runtime.events)

        trace_i = runtime.engine.trace_seq

        async def sender():
            nonlocal seq, trace_i
            i = 0
            while True:
                tr = runtime.trace_since(trace_i)
                if tr:
                    trace_i = tr[-1]["i"]
                    await sock.send_text(json.dumps({"type": "trace", "frames": tr}))
                elif runtime.engine.trace_seq < trace_i:  # engine was rebuilt (calibration)
                    trace_i = 0
                evs = runtime.events[seq:] if seq <= len(runtime.events) else []
                if evs:
                    seq = evs[-1]["seq"] + 1
                    await sock.send_text(json.dumps({"type": "events", "events": evs}))
                if i % 2 == 0:  # ~12 Hz state, ~25 Hz events
                    await sock.send_text(json.dumps({"type": "state", "state": runtime.state()},
                                                    default=_json_default))
                i += 1
                await asyncio.sleep(0.04)

        task = asyncio.create_task(sender())
        try:
            while True:
                msg = json.loads(await sock.receive_text())
                runtime.command(msg)
        except WebSocketDisconnect:
            pass
        finally:
            task.cancel()

    return app


def _json_default(o):
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, float) and (math.isinf(o) or math.isnan(o)):
        return None
    raise TypeError(type(o))


def main() -> None:  # pragma: no cover - interactive
    ap = argparse.ArgumentParser(description="Residual live app")
    ap.add_argument("--source", default="keyboard", choices=["keyboard", "autopilot", "webcam"])
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--person", default="default", help="profile name: eye ranges and baselines are "
                                                       "saved to profiles/<person>.json")
    ap.add_argument("--app", action="store_true", help="driven by the Residual app: no browser, and "
                                                      "the engine's own board doesn't speak, type or alert")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    import uvicorn

    rt = Runtime(a.source, person=a.person, board_outputs=not a.app)
    app = create_app(rt)
    if not a.no_browser and not a.app:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{a.port}")).start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")
    finally:
        rt.stop()


if __name__ == "__main__":  # pragma: no cover
    main()
