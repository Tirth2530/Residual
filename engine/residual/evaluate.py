"""Evaluation: Residual vs baselines, plus ablations.

Every system runs on identical simulated sessions (same seeds, same degradation schedule, same
simulated person) through the same Engine code the live app uses. Only the Config differs.

Run:
    python -m residual.evaluate --quick            # sanity check (minutes)
    python -m residual.evaluate --seeds 30         # the reported run (about 2 hours on 2 cores)
Outputs go to eval_out/: results.csv, summary.md, results.json (read by the Lab screen), charts.

Honesty rules:
  * "shrink" is the tuning set; "jitter", "dropout", step "fatigue" and "multi" are held out.
    Both are reported.
  * Every metric is reported, including where Residual loses.
  * Comparisons are paired (common random numbers), so we report per-seed differences and
    win rates, not just overlapping means.
  * truth_switch is a baseline that knows the true degradation schedule. It is NOT an upper
    bound (see docs/EVALUATION.md), so it is never called an oracle.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

import numpy as np

from .config import preset
from .mathx import wolpaw_bits
from .sim import Library, Scenario, run_session

SYSTEMS = ["fixed", "threshold", "hysteresis", "truth_switch", "residual"]
BASELINES = ["fixed", "threshold", "hysteresis", "truth_switch"]
ABLATIONS = ["residual", "no_adapt", "no_fusion", "no_changepoint", "no_verify"]
LABELS = {
    "fixed": "Calibrated once",
    "threshold": "Switch when hits < 50%",
    "hysteresis": "Switch + dwell + no-return",
    "truth_switch": "Knows true degradation, switches instantly",
    "residual": "Residual",
}


@dataclass(frozen=True)
class ScenarioSpec:
    name: str
    kind: str
    channels: tuple[str, ...]
    shape: str
    held_out: bool
    about: str


SCENARIOS = [
    ScenarioSpec("shrink", "shrink", ("hand",), "ramp", False,
                 "hand movement gradually gets smaller"),
    ScenarioSpec("jitter", "jitter", ("hand",), "ramp", True,
                 "hand movement gradually gets shakier"),
    ScenarioSpec("dropout", "dropout", ("hand",), "ramp", True,
                 "hand tracking gradually drops frames"),
    ScenarioSpec("fatigue", "mixed", ("hand",), "step", True,
                 "hand suddenly gets weaker, smaller and shakier at once"),
    ScenarioSpec("multi", "jitter", ("hand", "brow", "blink"), "ramp", True,
                 "every movement degrades together"),
]


_LIB_CACHE: dict = {}


def _run(args: tuple) -> dict:
    system, spec_name, severity, seed, duration, lib_path = args
    spec = next(s for s in SCENARIOS if s.name == spec_name)
    sc = Scenario(spec.kind, severity, spec.channels, spec.shape, duration)
    lib = None
    if lib_path:
        if lib_path not in _LIB_CACHE:
            _LIB_CACHE[lib_path] = Library(lib_path)
        lib = _LIB_CACHE[lib_path]
    r = run_session(preset(system), sc, seed, library=lib)
    minutes = duration / 60.0
    bits = wolpaw_bits(6, r.accuracy)
    return {
        "system": system,
        "scenario": spec_name,
        "held_out": spec.held_out,
        "severity": severity,
        "seed": seed,
        "correct": r.correct,
        "wrong": r.wrong,
        "misses": r.misses,
        "switches": r.switches,
        "accuracy": round(r.accuracy, 4),
        "correct_per_min": round(r.correct / minutes, 3),
        "false_per_min": round(r.wrong / minutes, 3),
        "bits_per_min": round(bits * r.selections / minutes, 3),
        "calib": [(a["pred"], a["hit"]) for a in r.attempts
                  if a["pred"] is not None and a["hit"] is not None],
    }


def bootstrap_ci(x: list[float], n: int = 4000, seed: int = 0) -> tuple[float, float, float]:
    a = np.asarray(x, dtype=float)
    if len(a) == 0:
        return math.nan, math.nan, math.nan
    if len(a) == 1:
        return float(a[0]), float(a[0]), float(a[0])
    rng = np.random.default_rng(seed)
    means = rng.choice(a, (n, len(a))).mean(axis=1)
    return float(a.mean()), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def run_grid(systems, scenarios, severities, seeds, duration, workers, library=None) -> list[dict]:
    jobs = [(s, sc, sev, seed, duration, library) for sc in scenarios for sev in severities
            for seed in seeds for s in systems]
    t0 = time.time()
    out: list[dict] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for i, row in enumerate(ex.map(_run, jobs, chunksize=2)):
            out.append(row)
            if (i + 1) % 50 == 0 or i + 1 == len(jobs):
                print(f"  {i + 1}/{len(jobs)} sessions ({time.time() - t0:.0f}s)", flush=True)
    return out


def summarize(rows: list[dict], metric: str) -> dict:
    groups: dict[tuple, list[float]] = {}
    for r in rows:
        groups.setdefault((r["scenario"], r["severity"], r["system"]), []).append(r[metric])
    return {k: bootstrap_ci(v) for k, v in groups.items()}


def paired(rows: list[dict], metric: str, a: str = "residual") -> dict:
    """Per (scenario, severity, baseline): mean of (a - baseline) over seeds with a bootstrap CI,
    and the share of seeds where a did strictly better / worse."""
    by = {(r["scenario"], r["severity"], r["system"], r["seed"]): r[metric] for r in rows}
    out = {}
    keys = {(r["scenario"], r["severity"]) for r in rows}
    seeds = sorted({r["seed"] for r in rows})
    for sc, sev in keys:
        for b in BASELINES:
            d = [by[(sc, sev, a, s)] - by[(sc, sev, b, s)] for s in seeds
                 if (sc, sev, a, s) in by and (sc, sev, b, s) in by]
            if not d:
                continue
            m, lo, hi = bootstrap_ci(d)
            out[(sc, sev, b)] = {"diff": m, "lo": lo, "hi": hi, "n": len(d),
                                 "win": float(np.mean([x > 1e-9 for x in d])),
                                 "loss": float(np.mean([x < -1e-9 for x in d]))}
    return out


def retained(rows: list[dict]) -> dict:
    """Share of a system's own undegraded correct selections/min it keeps at each severity."""
    s = summarize(rows, "correct_per_min")
    out = {}
    for (sc, sev, sy), (m, _, _) in s.items():
        base = s.get((sc, 0.0, sy))
        if base and base[0] > 0:
            out[(sc, sev, sy)] = m / base[0]
    return out


def calibration_bins(rows: list[dict], system: str = "residual", nbins: int = 8):
    pairs = [p for r in rows if r["system"] == system for p in r["calib"]]
    if not pairs:
        return []
    pred = np.array([p for p, _ in pairs])
    hit = np.array([1.0 if h else 0.0 for _, h in pairs])
    edges = np.linspace(0, 1, nbins + 1)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (pred >= lo) & (pred < hi if hi < 1 else pred <= hi)
        if m.sum() >= 5:
            out.append((float(pred[m].mean()), float(hit[m].mean()), int(m.sum())))
    return out


def _fmt(m, lo, hi, p=1):
    return f"{m:.{p}f} [{lo:.{p}f}, {hi:.{p}f}]"


def write_outputs(rows: list[dict], abl_rows: list[dict], out_dir: str, severities, seeds,
                  duration) -> str:
    os.makedirs(out_dir, exist_ok=True)
    fields = [k for k in rows[0] if k != "calib"]
    with open(os.path.join(out_dir, "results.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows + abl_rows:
            w.writerow({k: r[k] for k in fields})

    scen = [s for s in SCENARIOS if any(r["scenario"] == s.name for r in rows)]
    cpm = summarize(rows, "correct_per_min")
    bpm = summarize(rows, "bits_per_min")
    fpm = summarize(rows, "false_per_min")
    sw = summarize(rows, "switches")
    pr = paired(rows, "correct_per_min")
    ret = retained(rows)

    md = ["# Residual evaluation", ""]
    md.append(f"Closed-loop simulated sessions: {len(seeds)} seeds x {duration:.0f} s per cell, "
              "paired (every system sees the same simulated person, targets and noise). "
              "Degradation corrupts raw input only. `shrink` is the tuning set; the others are "
              "held out. Cells: mean [95% bootstrap CI].")
    md.append("")
    md.append("**Headline metric: successful selections per minute** (the person picked what they "
              "meant). Bits per minute (Wolpaw ITR, 6 options) is given for comparison with the "
              "BCI / AAC literature.")
    md.append("")
    md.append("## Residual vs each baseline, paired by seed (successful selections / min)")
    md.append("")
    md.append("Positive = Residual better. `win` = share of seeds where Residual did strictly "
              "better, `loss` = strictly worse (ties are neither).")
    md.append("")
    md.append("| scenario | severity | " + " | ".join(BASELINES) + " |")
    md.append("|---|---|" + "---|" * len(BASELINES))
    for spec in scen:
        for sev in severities:
            if sev == 0.0:
                continue
            cells = []
            for b in BASELINES:
                d = pr.get((spec.name, sev, b))
                cells.append("" if d is None else
                             f"{d['diff']:+.2f} [{d['lo']:+.2f}, {d['hi']:+.2f}] "
                             f"win {d['win']:.0%} loss {d['loss']:.0%}")
            tag = "" if spec.held_out else " (tuning)"
            md.append(f"| {spec.name}{tag} | {sev} | " + " | ".join(cells) + " |")
    md.append("")

    for metric, title, summ, p in [("correct_per_min", "Successful selections per minute", cpm, 1),
                                   ("bits_per_min", "Bits per minute (Wolpaw ITR, 6 options)", bpm, 1),
                                   ("false_per_min", "Wrong selections per minute", fpm, 2),
                                   ("switches", "Control switches per session", sw, 1)]:
        md.append(f"## {title}")
        md.append("")
        for spec in scen:
            tag = " (held out)" if spec.held_out else " (tuning)"
            md.append(f"### {spec.name}{tag}: {spec.about}")
            md.append("")
            md.append("| severity | " + " | ".join(SYSTEMS) + " |")
            md.append("|---|" + "---|" * len(SYSTEMS))
            for sev in severities:
                cells = [_fmt(*summ.get((spec.name, sev, s), (math.nan,) * 3), p=p) for s in SYSTEMS]
                md.append(f"| {sev} | " + " | ".join(cells) + " |")
            md.append("")

    md.append("## Share of undegraded performance kept (successful selections / min)")
    md.append("")
    md.append("| scenario | severity | " + " | ".join(SYSTEMS) + " |")
    md.append("|---|---|" + "---|" * len(SYSTEMS))
    for spec in scen:
        for sev in severities:
            if sev == 0.0:
                continue
            md.append(f"| {spec.name} | {sev} | " +
                      " | ".join(f"{ret.get((spec.name, sev, s), math.nan):.0%}" for s in SYSTEMS) + " |")
    md.append("")

    if abl_rows:
        md.append("## Ablations (successful selections per minute)")
        md.append("")
        summ = summarize(abl_rows, "correct_per_min")
        ascen = sorted({r["scenario"] for r in abl_rows})
        md.append("| scenario | severity | " + " | ".join(ABLATIONS) + " |")
        md.append("|---|---|" + "---|" * len(ABLATIONS))
        for sc in ascen:
            for sev in severities:
                cells = [f"{summ.get((sc, sev, s), (math.nan,))[0]:.1f}" for s in ABLATIONS]
                md.append(f"| {sc} | {sev} | " + " | ".join(cells) + " |")
        md.append("")

    cal = calibration_bins(rows)
    if cal:
        md.append("## Calibration (Residual): predicted p_hit vs observed hit rate")
        md.append("")
        md.append("| predicted | observed | n |")
        md.append("|---|---|---|")
        for p, o, n in cal:
            md.append(f"| {p:.2f} | {o:.2f} | {n} |")
        md.append("")

    text = "\n".join(md)
    with open(os.path.join(out_dir, "summary.md"), "w") as f:
        f.write(text)

    # machine-readable for the Lab screen
    j = {"generated": time.strftime("%Y-%m-%d %H:%M"), "seeds": len(seeds), "duration_s": duration,
         "severities": severities, "systems": SYSTEMS, "labels": LABELS,
         "scenarios": [{"name": s.name, "held_out": s.held_out, "about": s.about} for s in scen],
         "cells": [], "paired": [], "calibration": cal}
    for (sc, sev, sy), (m, lo, hi) in cpm.items():
        b = bpm[(sc, sev, sy)]
        j["cells"].append({"scenario": sc, "severity": sev, "system": sy,
                           "cpm": [round(m, 3), round(lo, 3), round(hi, 3)],
                           "bpm": [round(b[0], 3), round(b[1], 3), round(b[2], 3)],
                           "fpm": round(fpm[(sc, sev, sy)][0], 3),
                           "switches": round(sw[(sc, sev, sy)][0], 2),
                           "retained": round(ret.get((sc, sev, sy), math.nan), 3)
                           if (sc, sev, sy) in ret else None})
    for (sc, sev, b), d in pr.items():
        j["paired"].append({"scenario": sc, "severity": sev, "baseline": b,
                            **{k: round(v, 3) if isinstance(v, float) else v for k, v in d.items()}})
    if abl_rows:
        asum = summarize(abl_rows, "correct_per_min")
        j["ablations"] = {"variants": ABLATIONS, "seeds": len({r["seed"] for r in abl_rows}),
                          "cells": [{"scenario": sc, "severity": sev, "variant": v,
                                     "cpm": [round(x, 3) for x in asum[(sc, sev, v)]]}
                                    for (sc, sev, v) in sorted(asum)]}
    with open(os.path.join(out_dir, "results.json"), "w") as f:
        json.dump(j, f, indent=1)
    _charts(rows, out_dir, severities, cal)
    return text


def _charts(rows, out_dir, severities, cal) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed; skipping charts")
        return
    summ = summarize(rows, "correct_per_min")
    colors = {"fixed": "#9aa0a6", "threshold": "#c58af9", "hysteresis": "#f6ae2d",
              "residual": "#1a73e8", "truth_switch": "#34a853"}
    for spec in SCENARIOS:
        if not any(r["scenario"] == spec.name for r in rows):
            continue
        fig, ax = plt.subplots(figsize=(6.4, 4))
        for s in SYSTEMS:
            ms = [summ.get((spec.name, v, s), (math.nan,) * 3) for v in severities]
            ls = {"truth_switch": "--", "threshold": ":"}.get(s, "-")
            lw = 2.5 if s == "residual" else 1.5
            ax.plot(severities, [x[0] for x in ms], ls, color=colors[s], lw=lw, label=s, marker="o", ms=3)
            ax.fill_between(severities, [x[1] for x in ms], [x[2] for x in ms], color=colors[s], alpha=0.12)
        tag = "held out" if spec.held_out else "tuning set"
        ax.set_title(f"{spec.name} ({tag}): {spec.about}", fontsize=9)
        ax.set_xlabel("Final degradation level")
        ax.set_ylabel("Successful selections / min")
        ax.set_ylim(bottom=0)
        ax.grid(alpha=0.25)
        ax.legend(fontsize=8, frameon=False)
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, f"cpm_{spec.name}.png"), dpi=160)
        plt.close(fig)
    if cal:
        fig, ax = plt.subplots(figsize=(4, 4))
        ax.plot([0, 1], [0, 1], ":", color="#999")
        ax.plot([p for p, _, _ in cal], [o for _, o, _ in cal], "o-", color="#1a73e8")
        ax.set_xlabel("Predicted p_hit")
        ax.set_ylabel("Observed hit rate")
        ax.set_title("Reliability calibration", fontsize=10)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, "calibration.png"), dpi=160)
        plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true", help="small sweep for a sanity check")
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--duration", type=float, default=150.0)
    ap.add_argument("--severities", default="0,0.5,0.8,0.95")
    ap.add_argument("--scenarios", default=",".join(s.name for s in SCENARIOS))
    ap.add_argument("--ablation-seeds", type=int, default=15)
    ap.add_argument("--workers", type=int, default=max(1, os.cpu_count() or 2))
    ap.add_argument("--no-ablations", action="store_true")
    ap.add_argument("--out", default="eval_out")
    ap.add_argument("--library", help="real gesture traces from `python -m residual.perception "
                                      "--library`; replaces synthetic gestures and noise")
    a = ap.parse_args()

    severities = [float(x) for x in a.severities.split(",")]
    scen = a.scenarios.split(",")
    seeds = list(range(a.seeds))
    duration = a.duration
    abl_seeds = list(range(min(a.ablation_seeds, a.seeds)))
    if a.quick:
        seeds, abl_seeds, duration = list(range(3)), list(range(3)), 90.0

    print(f"Main grid: {len(SYSTEMS)} systems x {len(scen)} scenarios x {len(severities)} levels x "
          f"{len(seeds)} seeds", flush=True)
    rows = run_grid(SYSTEMS, scen, severities, seeds, duration, a.workers, a.library)
    abl: list[dict] = []
    if not a.no_ablations:
        abl_scen = [s for s in ("shrink", "multi") if s in scen]
        print(f"Ablations: {len(ABLATIONS)} variants x {abl_scen} x {len(abl_seeds)} seeds", flush=True)
        abl = run_grid(ABLATIONS, abl_scen, severities, abl_seeds, duration, a.workers, a.library)
    text = write_outputs(rows, abl, a.out, severities, seeds, duration)
    print(text)
    print(f"\nWrote {a.out}/results.csv, summary.md, results.json and charts")


if __name__ == "__main__":
    main()
