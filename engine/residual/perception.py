"""Layer 0 (used, not built): webcam -> MediaPipe landmarks -> raw features.

Raw features per frame:
    hand      pinch closure: 1 - (thumb tip to index tip) / (wrist to middle knuckle)
    brow      max(browInnerUp, mean(browOuterUpLeft, browOuterUpRight)). The old plain mean of
              the three let one strongly-responding blendshape be averaged away.
    brow_geo  geometric: eyebrow-to-upper-eyelid distance / eye width, both eyes averaged.
              Calibration uses whichever of brow / brow_geo separates from rest for this person.
    blink     min(eyeBlinkLeft, eyeBlinkRight): intentionally bilateral (a wink isn't a blink)
    _brow     the raw parts, for the Lab

Normalization (normalize.py) maps these to ~0 at rest and ~1 at a calibrated
gesture. Everything above this file is ours.

Models: run `python scripts/get_models.py` once to download the two .task files.

CLI:
    python -m residual.perception --check          # print live raw features
    python -m residual.perception --library out.json  # record a cued trace library
"""

from __future__ import annotations

import argparse
import json
import math
import os
import threading
import time
from pathlib import Path

import numpy as np

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
FACE_MODEL = MODELS_DIR / "face_landmarker.task"
HAND_MODEL = MODELS_DIR / "hand_landmarker.task"


class FeatureExtractor:
    def __init__(self) -> None:
        try:
            import mediapipe as mp
            from mediapipe.tasks import python as mpt
            from mediapipe.tasks.python import vision
        except ImportError as e:  # pragma: no cover - depends on local install
            raise RuntimeError("mediapipe is not installed: pip install -r requirements-vision.txt") from e
        for p in (FACE_MODEL, HAND_MODEL):
            if not p.exists():
                raise RuntimeError(f"missing {p.name}: run python scripts/get_models.py")
        self.mp = mp
        self.face = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
            base_options=mpt.BaseOptions(model_asset_path=str(FACE_MODEL)),
            running_mode=vision.RunningMode.VIDEO,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
            num_faces=1,
        ))
        self.hands = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
            base_options=mpt.BaseOptions(model_asset_path=str(HAND_MODEL)),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=1,
        ))
        self.last_landmarks: dict = {}

    def process(self, rgb: np.ndarray, ts_ms: int) -> dict[str, float]:
        """Returns raw features for channels that were visible in this frame."""
        img = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)
        out: dict[str, float] = {}
        self.last_landmarks = {}
        fr = self.face.detect_for_video(img, ts_ms)
        if fr.face_blendshapes:
            bs = {c.category_name: c.score for c in fr.face_blendshapes[0]}
            inner, ol, orr = bs.get("browInnerUp", 0.0), bs.get("browOuterUpLeft", 0.0), bs.get("browOuterUpRight", 0.0)
            out["brow"] = brow_blendshape(inner, ol, orr)
            h_img0, w_img0 = rgb.shape[:2]
            geo = brow_geometry(fr.face_landmarks[0], w_img0 / h_img0)
            if geo is not None:
                out["brow_geo"] = geo
            out["blink"] = min(bs.get("eyeBlinkLeft", 0.0), bs.get("eyeBlinkRight", 0.0))
            out["_brow"] = {"inner": round(inner, 4), "outer_l": round(ol, 4), "outer_r": round(orr, 4),
                            "bs": round(out["brow"], 4), "geo": None if geo is None else round(geo, 4)}
            self.last_landmarks["face"] = [(p.x, p.y) for p in fr.face_landmarks[0][::12]]
            # Eyes: features with the head's rotation undone (residual/gaze.py)
            from .gaze import extract
            mats = getattr(fr, "facial_transformation_matrixes", None)
            h_img, w_img = rgb.shape[:2]
            self.last_face = fr.face_landmarks[0]
            self.last_gaze = extract(fr.face_landmarks[0], mats[0] if mats else None, w_img / h_img)
            out["_gaze"] = self.last_gaze
        else:
            self.last_face = self.last_gaze = None
        hr = self.hands.detect_for_video(img, ts_ms)
        if hr.hand_landmarks:
            lm = hr.hand_landmarks[0]
            palm = _dist(lm[0], lm[9])
            if palm > 1e-4:
                out["hand"] = max(0.0, 1.0 - _dist(lm[4], lm[8]) / palm)
            self.last_landmarks["hand"] = [(p.x, p.y) for p in lm]
        return out

    def close(self) -> None:
        self.face.close()
        self.hands.close()


def draw_eyes(img, ex) -> None:
    """Eye outlines, irises and a gaze arrow on the preview, so people can see what is tracked."""
    import cv2
    from .gaze import EYE_RING_L, EYE_RING_R, LEFT_EYE, RIGHT_EYE
    face = getattr(ex, "last_face", None)
    if face is None:
        return
    H, W = img.shape[:2]
    P = lambda i: (int(face[i].x * W), int(face[i].y * H))
    for ring, eye in ((EYE_RING_R, RIGHT_EYE), (EYE_RING_L, LEFT_EYE)):
        pts = [P(i) for i in ring]
        for a, b in zip(pts, pts[1:] + pts[:1]):
            cv2.line(img, a, b, (107, 197, 255), 1, cv2.LINE_AA)
        cx = sum(face[i].x for i in eye["iris"]) / 4 * W
        cy = sum(face[i].y for i in eye["iris"]) / 4 * H
        r = max(2, int(abs(face[eye["iris"][0]].x - face[eye["iris"][2]].x) * W / 2))
        cv2.circle(img, (int(cx), int(cy)), r, (168, 211, 115), 1, cv2.LINE_AA)
        cv2.circle(img, (int(cx), int(cy)), 1, (168, 211, 115), -1)
    g = getattr(ex, "last_gaze", None)
    if g is not None:
        nose = P(1)
        tip = (int(nose[0] + g.h * 6 * W / 4), int(nose[1] + g.v * 6 * H / 4))
        cv2.arrowedLine(img, nose, tip, (107, 197, 255), 2, cv2.LINE_AA, tipLength=0.3)


# MediaPipe face mesh: upper eyebrow arcs and upper eyelids (person's right, then left)
BROW_R, BROW_L = (70, 63, 105, 66, 107), (300, 293, 334, 296, 336)
LID_R, LID_L = 159, 386
CORNERS_R, CORNERS_L = (33, 133), (263, 362)


def brow_blendshape(inner: float, outer_l: float, outer_r: float) -> float:
    """Robust to faces where only the inner or only the outer brow blendshape responds."""
    return max(inner, (outer_l + outer_r) / 2.0)


def brow_geometry(lm, aspect: float) -> float | None:
    """Eyebrow-to-upper-eyelid distance divided by eye width, averaged over both eyes.
    Scale-free, so distance from the camera doesn't matter; per-person calibration sets the
    rest and raised levels."""
    try:
        out = []
        for brow, lid, (a, b) in ((BROW_R, LID_R, CORNERS_R), (BROW_L, LID_L, CORNERS_L)):
            by = sum(lm[i].y for i in brow) / len(brow)
            width = math.hypot((lm[a].x - lm[b].x) * aspect, lm[a].y - lm[b].y)
            if width < 1e-4:
                return None
            out.append((lm[lid].y - by) / width)
        return float(sum(out) / 2.0)
    except (IndexError, AttributeError):
        return None


def _dist(a, b) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


class Webcam:
    """Background capture + feature extraction. read() returns the latest
    (t, raw_features, jpeg_preview_or_None)."""

    def __init__(self, index: int = 0, width: int = 640, height: int = 480, preview: bool = True):
        import cv2  # noqa: F401  (fail early if missing)

        self.index, self.width, self.height = index, width, height
        self.preview = preview
        self.extractor = FeatureExtractor()
        self._lock = threading.Lock()
        self._latest: tuple[float, dict, bytes | None] | None = None
        self._stop = False
        self.fps = 0.0
        self.opened: bool | None = None       # did the camera open at all (permission, no device)
        self.last_frame_t: float | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        import cv2

        cap = cv2.VideoCapture(self.index)
        self.opened = bool(cap.isOpened())
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        t0 = time.monotonic()
        n, tick = 0, t0
        while not self._stop:
            ok, bgr = cap.read()
            if not ok:
                time.sleep(0.01)
                continue
            self.last_frame_t = time.monotonic()
            bgr = cv2.flip(bgr, 1)  # mirror, like a selfie view
            t = time.monotonic() - t0
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            raw = self.extractor.process(rgb, int(t * 1000))
            jpg = None
            if self.preview and n % 3 == 0:
                small = cv2.resize(bgr, (320, 240))
                for kind, pts in self.extractor.last_landmarks.items():
                    col = (232, 115, 26) if kind == "hand" else (180, 180, 180)
                    for x, y in pts:
                        cv2.circle(small, (int(x * 320), int(y * 240)), 2, col, -1)
                draw_eyes(small, self.extractor)
                jpg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 60])[1].tobytes()
            with self._lock:
                self._latest = (t, raw, jpg)
            n += 1
            if t - (tick - t0) >= 1.0:
                self.fps = n / max(t, 1e-6)
                tick = time.monotonic()
        cap.release()
        self.extractor.close()

    def read(self) -> tuple[float, dict, bytes | None] | None:
        with self._lock:
            v, self._latest = self._latest, None
            return v

    def stop(self, timeout: float = 2.0) -> None:
        self._stop = True
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout)


def _check() -> None:  # pragma: no cover - needs a camera
    cam = Webcam(preview=False)
    print("Showing raw features. Ctrl+C to stop.")
    try:
        while True:
            v = cam.read()
            if v:
                t, raw, _ = v
                print(f"t={t:6.2f} fps={cam.fps:4.1f} " +
                      " ".join(f"{c}={raw.get(c, float('nan')):.3f}" for c in ("hand", "brow", "blink")))
            time.sleep(0.05)
    except KeyboardInterrupt:
        cam.stop()


def _record_library(path: str, reps: int = 8) -> None:  # pragma: no cover - needs a camera
    """Cued protocol: rest, then `reps` prompted gestures per channel. Saves
    normalized traces for trace-driven evaluation (sim.py uses them instead of
    synthetic half-sines when --library is given)."""
    from .normalize import fit, split_gestures

    cam = Webcam(preview=False)
    rec: dict[str, list[dict]] = {}

    def phase(name: str, secs: float, prompt: str) -> None:
        print(f"\n>>> {prompt} ({secs:.0f} s)")
        rec[name] = []
        end = time.monotonic() + secs
        while time.monotonic() < end:
            v = cam.read()
            if v:
                rec[name].append(v[1])
            time.sleep(0.005)

    time.sleep(2.0)
    phase("rest", 20, "Relax. Talk, look around, blink normally")
    phase("hand", 2.5 * reps, f"Pinch {reps} times, one every ~2 seconds")
    phase("brow", 2.5 * reps, f"Raise your eyebrows {reps} times")
    phase("blink", 2.5 * reps, f"Do {reps} long blinks")
    cam.stop()
    norm, rest, gestures = fit(rec)
    lib = {"fps": cam.fps or 30.0, "normalizer": norm.to_dict(), "rest": rest,
           "gestures": {c: gestures[c] for c in gestures}}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(lib, f)
    print(f"Saved {path}: " + ", ".join(f"{c}={len(g)} gestures" for c, g in gestures.items()))
    _ = split_gestures


def main() -> None:  # pragma: no cover
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--library", metavar="OUT_JSON")
    a = ap.parse_args()
    if a.library:
        _record_library(a.library)
    else:
        _check()


if __name__ == "__main__":  # pragma: no cover
    os.environ.setdefault("GLOG_minloglevel", "2")
    main()
