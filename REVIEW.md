# Residual: review guide (version 3)

**Please attack every weak part.** Version 1 was reviewed and taken apart (thank you). This file
says what changed because of that review, how the whole thing fits together now, and what is still
weak. Section 5 is the list to start from.

---

## 0. Version 3: what changed

| Problem | Fix | Where | Test |
|---|---|---|---|
| A held gesture fired again and again (3 s hold: 4 fires) | Explicit arming: armed, gesture, fire, wait for release. Re-arms only after the smoothed signal stays below a neutral threshold for 0.25 s. Same for Select, Next, Back, verification and fused controls. Blink selects on reopening, and a blink held over 1 s never selects | `engine/residual/engine.py` | `tests/test_arming.py` (17) |
| Calibration said "You're all set" when the eyebrow failed | Success-driven calibration: 3 clear examples per movement, live count, timeout to FAILED. Verdict VALID / WEAK / FAILED / NOT_VISIBLE per movement. Failed movements are not eligible for Select, Next, Back, migration or fusion. Result sheet: "✓ learned / ✕ not clear enough", Retry, Skip | `calibration.py`, `server.py`, `app/ui/calib-view.js` | `test_calibration.py` (15), `test_server.py`, `app/tests/v3.test.js` |
| Dead camera looked healthy | Preflight: opens, frames arrive, face visible, face steady. Each failure named. Retry camera / Use keyboard for now. `/api/health` reports camera state and the app's supervisor polls it | `calibration.py`, `server.py`, `app/lib/engine-proc.js` | `test_calibration.py`, `test_server.py` |
| Eyebrow barely registered | max(inner, mean(outer)) blendshape plus a geometric brow-to-eye feature, each normalized per person; the Lab shows every part | `perception.py`, `normalize.py`, Lab | `test_calibration.py` |
| Greeting / briefing repeated | One priority speech queue (P0 emergency to P4 ambient; only P0/P1 interrupt; duplicates dropped). Spoken welcome once per launch (main process flag). Briefing, routines, Ask, sending and help are single-flight. "Say good morning" routine removed. Nothing auto-runs a briefing | `app/ui/speech.js`, `home.js`, `main.js` | `v3.test.js` |
| Gaze direction guessed | Gaze calibration center/left/center/right/center/up/center; direction and thresholds from the person's own range; swapLeftRight only as fallback | `gaze.py`, `calibration.py` | `test_calibration.py` |
| Home duplicated role logic (ENGINE_OK) | The engine owns roles (`runtime.roles`); Home renders them | `server.py roles()`, `home.js` | `test_server.py`, `test_arming.py` |
| Guessable Lifeline incident code | `crypto.randomUUID()` | `app/lib/lifeline.js` | `v3.test.js` |
| No path from intent to the real world | Residual Relay: one `handle_intent`, deterministic policy (allowlist, fixed emergency order, cancel rules), Photon tool with idempotency keys, inbound webhook, escalation, ACP chat protocol for ASI. Home sends structured intents | `agent/` | `agent/tests` (29) |

Not verified here, to check on the demo Mac: the webcam on macOS, Agentverse registration, a live
Photon send and webhook, and `python -m pytest -q` finishing cleanly under the Mac's Python.
On Linux (Python 3.11 here) the full engine suite printed `73 passed` and exited normally.

---

## 1. What it is

> When a movement stops working, Residual finds another one, checks it's deliberate, and moves
> control to it, so a person with ALS keeps using their computer without anyone reconfiguring
> anything.

| Folder | What |
|---|---|
| `engine/` | Python. Webcam perception (MediaPipe), per-movement reliability, adapt / check / verify / migrate / fuse, eye engine (`residual/gaze.py`), simulator, evaluation |
| `app/` | Electron. The person's screen (real apps, Say, News, Ask, Lifeline) and the Lab (every decision, live). Starts and watches the engine |
| `docs/` | `CLINICAL.md` for a clinician, `JUDGES.md` hard questions and the demo script |

Run: see `README.md`. Tests: `cd engine && python -m pytest -q` (73), `cd agent && python -m pytest -q` (29), `cd app && npm test` (29).

---

## 2. What changed because of the v1 review

| v1 criticism | What we did | Where |
|---|---|---|
| The invention is invisible; the dashboard is a feature pile | Home is now intent-first (Talk, Say, Ask, News, Watch, Listen, Routines, Help) with three "Right now" suggestions. The migration is a full card: "Your hand pinch is getting harder to read. Let's try eyebrow raise", two dots, then "Eyebrow raise selects now", spoken. The control strip shows which movement does Back / Select / Next | `app/ui/home.js` |
| Top bar was telemetry; "Eyes 79%" has no meaning | The person's screen shows no percentages. Plain status ("You're in control", "Checking a movement", "Eyes resting"). All numbers moved to the Lab, labelled for what they are (command reliability, not "ability") | `home.js` `pill()`, `ui/lab.*` |
| Show the reasoning, not the jargon | The Lab draws the engine's per-frame trace: amplitude, onset threshold, Kalman level, CUSUM vs the bound for the current action class, changepoints, misses; the degradation clauses that are true right now; every check and verification with both reliability intervals; the eye engine's internals | `engine.py` trace, `policy.py` `diag`/`decisions`, `ui/lab.js` |
| Next/Back used `strength ≥ 0.6`, bypassing the model | Each non-control channel has its own CUSUM against the same bound as Select (`aux_fire`). Separate accumulators, decisions unchanged | `engine.py`, `tests/test_aux.py` |
| Oracle scored below Residual | Renamed `truth_switch` and explained: it's a reference, not an upper bound | `config.py`, `evaluate.py`, `docs/EVALUATION.md` |
| 2 seeds; CIs are theatre | 30 paired seeds per cell; per-seed paired differences with bootstrap CIs and win/loss rates; ablations on 15 | `evaluate.py`, `eval_out/` |
| Bits/min means nothing to most judges | Headline is successful selections per minute and share of undegraded performance kept; bits/min kept for the literature | `evaluate.py` |
| Lifeline cancelled when the app closed (P0) | The window refuses to close and the app refuses to quit while a call is unanswered | `app/main.js` |
| Anyone with the topic could post "on my way" | Each alert has a one-time code; acks without it are ignored and logged. Tested with a forged ack | `lib/lifeline.js`, `tests/lib.test.js` |
| Blink select vs three-long-blinks help could collide | Help needs eyes held shut 1.2 to 4 s, three times in 15 s. A long-blink select is about 0.3 to 0.9 s. Tested: four 0.75 s selects don't trigger it | `gaze.py`, `tests/test_gaze.py` |
| Fatigue baseline learned while already tired | Baselines and glance ranges saved per person and reloaded | `gaze.py` profiles, `server.py` |
| Centre tracking assumed 30 fps | Uses the real frame interval | `gaze.py` |
| Engine's own board spoke and alerted in parallel | `--app` mode: the engine's board never speaks, types or alerts | `server.py` |
| Two processes, either can die silently | The app starts the engine, restarts it with backoff, shows status; camera health is reported and a dead camera is explained on screen | `lib/engine-proc.js`, `perception.py` |
| `pageCall` ran arbitrary strings in pages | Whitelisted helper methods with JSON arguments only | `main.js` |
| Real-site selectors break silently | The Lab shows each site adapter's health live; YouTube scanning now stops on videos, not every chip | `inject.js` `health()` |
| Fonts from Google (flaky Wi-Fi) | Bundled: Atkinson Hyperlegible (low-vision font), Fraunces, IBM Plex | `app/ui/fonts/` |
| AI is a generic add-on | Every AI feature cuts selections or brings the world in: briefing, news read aloud, Ask (a conversation driven by picking follow-ups), smart replies, search ideas, next words. Local Qwen by default, any OpenAI-compatible server or Gemini optional. Untrusted text is fenced. Every call logged to the Lab | `lib/ai.js` |
| No clinical framing | `docs/CLINICAL.md`: what it is and isn't, comparison with existing AAC, safety design, validation plan, references. A care report (daily reliability, migrations, tired episodes, help calls, export) and a clinician lock | `docs/`, Lab "Care report", `lock` command |

---

## 3. Architecture

```
Webcam
  │  engine/residual/perception.py: MediaPipe Face (478 pts, blendshapes, head transform) + Hands
  ├─► hand pinch, eyebrow, blink ─► normalize (calibration) ─► degrade (demo: raw signal only)
  │       └─► engine.py per frame: episodes → Kalman level/trend → BOCPD changepoints →
  │           reliability p_hit ± CI → clipped LLR → CUSUM vs ln((1-β)/α) per action class
  │           → fire (Select) / aux_fire (Next, Back) ; policy.py: adapt → check → verify →
  │           migrate / fuse, with diag + decisions recorded
  └─► eyes (gaze.py): head rotation undone → median-3 → One Euro → adaptive centre and range →
      look left / right / up with dwell + hysteresis; fatigue; help pattern
  │
  ▼  server.py websocket: state 12 Hz, events, per-frame trace; commands (degrade, source, lock…)
app/ (Electron)
  main.js        windows, real sites as WebContentsViews, IPC, AI, news, weather, intents,
                 Lifeline, engine supervisor, telemetry bus to the Lab
  ui/home.js     the person's screen: screens + group scanning, roles (Select = engine control;
                 Next/Back = eyes while fresh, else channels at p_hit ≥ 0.85 via aux_fire; else
                 auto-scan), migration card, sheets, voice, Lifeline alarm
  ui/lab.js      the Lab: canvases from the trace, panels from state, telemetry, evaluation, care
  inject.js      runs inside each real site: remote panel, scanner, chat reading, typing, guard
  lib/           ai, news (RSS), weather (Open-Meteo), intents, lifeline, store, engine-proc
```

---

## 4. What is tested

| | |
|---|---|
| Engine unit + server (Arjun) | 21 |
| Eye engine with synthetic faces: glances, quick glances ignored, head turns rejected, drift, range adaptation, fatigue, help pattern, help vs blink selects, profile round trip | 13 |
| Next/Back evidence (aux_fire), determinism, decisions recorded | 5 |
| App: rule fallbacks, JSON parsing, model picking (Qwen first), every AI call logged, Ask, briefing and summary fallbacks, RSS/Atom parsing, article extraction, news de-duplication, intent model and urgent messages, weather, care report, Lifeline escalation + forged ack + cancel | 19 |
| Real Electron app booted headless against the real engine: Home, Lab, telemetry, close refused during Lifeline | manual, scripted with Playwright |
| Full migration on the real engine (simulated person): check, verify, migrate, the card and the control strip update | scripted with Playwright |
| Closed-loop evaluation, 30 paired seeds | `engine/eval_out/` |

**Not tested:** the eye gestures on a real webcam with a real person (sign of left/right, real
noise, glasses, dim light, lying on a pillow); the real sites (Instagram, WhatsApp, YouTube,
Netflix, Uber Eats) since this machine had no internet for them; the local model's actual answer
quality (prompts are written for it and every output has a fallback, but no Qwen was run here);
anyone with ALS.

---

## 5. Still weak (start here)

**Evidence**
1. All performance numbers are simulated with a cooperative simulated person. No real traces yet,
   no people.
2. Residual loses on held-out jitter (−0.3 to −0.6 selections/min) and when every channel
   degrades at once (−0.25 to −1.1), and only ties threshold switching on abrupt fatigue. See
   `engine/docs/EVALUATION.md`.
2b. In the ablations, removing verification scores *higher* (+0.6/min) because the simulated
   person never suffers from a wrong switch. Changepoints and fusion show no measurable effect.
   Attack this: is verification worth it, and how would you measure its benefit?
3. The fatigue weights, gaze thresholds and dwell are hand-set. The intent model's weights are
   hand-set too.
4. "Successful selections per minute" in a scanning-board simulation is not the same as getting
   through a real day.

**Eye engine**
5. Never run on a real face by us. Left/right sign has a config switch for a reason.
6. Glasses, dim rooms and a head lying sideways are untested. Ptosis interacts with "look up".
7. Eye "quality" is face-seen time times eyes-open-enough. It isn't a calibrated reliability like
   the other channels have.

**Integration**
8. When the engine fuses two channels, the app simply drops to auto-scan.
9. `aux_fire` uses the "say" bound for Next/Back. Whether that's the right error budget for
   navigation is a guess.
10. The engine still runs its own scanning board internally (silently in `--app` mode). Its board
    timing affects which action-class bound Select uses at a given moment.

**Real apps**
11. Site adapters rely on DOM selectors. They will break; the Lab now shows when.
12. Google News "For you" links redirect through JavaScript, so those stories get a short summary
    from the feed text rather than the article.
13. Automating these sites may break their terms of service. Netflix needs a DRM Electron build.

**Lifeline**
14. Internet push only. No SMS or phone call fallback, no local device.
15. The ack code stops forged acks from strangers, not someone who saw the alert.

**AI**
16. A 3 to 4B local model will sometimes be wrong about current events; the prompt tells it to say
    so, and it gets today's headlines as context, but it's still a small model.
17. The companion's tone ("warm, curious, never patronising") is a prompt, not a tested result.

**Patient experience**
18. Scanning keyboard is row/column with frequency ordering, not probability-adaptive.
19. Countdown and scan speeds are fixed or coarse (scan slows by 30% when the eyes are tiring).
20. Low-vision contrast checked by eye, not with a tool.

**Privacy**
21. History and the care log are plain JSON in the app data folder.

---

## 6. Questions for the reviewer

1. Is the migration card the right moment to build the demo around?
2. Does the Lab convince you nothing is faked? What would you need to see?
3. Which of the "still weak" items would you fix before showing a clinician?
4. What would a person with ALS, or their carer, ask for on day one that isn't here?
