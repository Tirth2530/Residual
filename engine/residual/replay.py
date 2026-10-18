"""Replay a recorded live session through a fresh Engine.

    python -m residual.replay sessions/session_20261017_143000.jsonl

The recording holds the calibration and every (already degraded) frame, so
this reproduces the live run's decisions exactly: same code, same inputs.
Use it to debug a demo moment, or to try a config change on real data:

    python -m residual.replay FILE --set p_switch=0.6 --set verify=false
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

from .config import Config
from .engine import Engine
from .events import Frame


def load(path: str) -> tuple[dict, list[Frame]]:
    header, frames = {}, []
    with open(path) as f:
        for line in f:
            d = json.loads(line)
            if d.get("header"):
                header = d
            else:
                frames.append(Frame(d["t"], d["amps"], d.get("visible", {})))
    return header, frames


def apply_overrides(cfg: Config, sets: list[str]) -> Config:
    for s in sets:
        k, v = s.split("=", 1)
        cur = getattr(cfg, k)
        if isinstance(cur, bool):
            val = v.lower() in ("1", "true", "yes", "on")
        else:
            val = type(cur)(v)
        setattr(cfg, k, val)
    return cfg


def replay(path: str, cfg: Config | None = None, verbose: bool = True) -> list:
    header, frames = load(path)
    if cfg is None:
        cfg = Config()
        for k, v in (header.get("config") or {}).items():
            if hasattr(cfg, k):
                setattr(cfg, k, tuple(v) if isinstance(getattr(cfg, k), tuple) else v)
    eng = Engine(cfg)
    t0 = header.get("t_start", frames[0].t if frames else 0.0)
    eng.policy.note_activity(t0)
    cal = header.get("calibration") or {}
    if cal:
        eng.calibrate(cal["rest"], cal["gestures"], t0)
    out = []
    for f in frames:
        for e in eng.step(f):
            out.append(e)
            if verbose and e.kind in ("select", "migrate", "verify_start", "verify_ok",
                                      "verify_fail", "probe_ok", "probe_fail", "changepoint"):
                print(f"{e.t:8.2f}  {e.kind:13s} {e.data}")
    if verbose:
        c = Counter(e.kind for e in out)
        print(f"\n{len(frames)} frames, {frames[-1].t - t0:.0f} s: " if frames else "no frames",
              dict(c))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--set", action="append", default=[], help="override a Config field, k=v")
    a = ap.parse_args()
    cfg = None
    if a.set:
        header, _ = load(a.file)
        cfg = Config()
        for k, v in (header.get("config") or {}).items():
            if hasattr(cfg, k):
                setattr(cfg, k, tuple(v) if isinstance(getattr(cfg, k), tuple) else v)
        cfg = apply_overrides(cfg, a.set)
    replay(a.file, cfg)


if __name__ == "__main__":
    main()
