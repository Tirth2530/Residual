"""Download the two MediaPipe models into models/ (run once; allowed before the event)."""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

MODELS = {
    "face_landmarker.task": "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
    "hand_landmarker.task": "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
}


def main() -> int:
    out = Path(__file__).resolve().parent.parent / "models"
    out.mkdir(exist_ok=True)
    for name, url in MODELS.items():
        dest = out / name
        if dest.exists() and dest.stat().st_size > 100_000:
            print(f"ok   {name} (already present)")
            continue
        print(f"get  {name} ...", flush=True)
        try:
            urllib.request.urlretrieve(url, dest)
        except Exception as e:
            print(f"FAILED {name}: {e}\nDownload it manually from {url} into {out}")
            return 1
        print(f"ok   {name} ({dest.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
