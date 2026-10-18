# Residual

**Failover for the human body.** Residual lets your technology keep understanding you, even as
the movements you can make change.

People with progressive conditions like ALS lose movements over time. Assistive setups are
configured around one control method, so when that movement fades, the person loses their voice
until someone reconfigures it. Residual watches every movement a person can still make (hand
pinch, eyebrow raise, long blink) through a normal laptop webcam, tracks how reliable each one is
right now, and adapts, fuses or migrates control as abilities change, verifying first.

> Research prototype. Evaluation is in simulation; nothing has been tested with people with ALS.
> We make no clinical claims. The Residual app (`../app`) is the normal way to use this engine;
> it starts it for you with `--app`.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest -q                                  # ~2 minutes
python -m residual.server --source autopilot         # opens http://localhost:8000
```

Drag the **Hand** slider to 90% and watch: missed movements → a quick check → "Testing eyebrow
raise: do it twice" → control moves to the eyebrow.

No camera? `--source keyboard`, then hold **H** (pinch), **B** (eyebrow) or **K** (long blink).

With a camera (Python 3.11):

```bash
pip install -r requirements-vision.txt
python scripts/get_models.py
python -m residual.server --source webcam            # click Calibrate, follow the prompts
```

Optional: copy `.env.example` to `.env` for ElevenLabs speech, phone alerts (ntfy) and keystroke
output into other apps.

## Evaluation

```bash
python -m residual.evaluate --quick                  # a few minutes, sanity check
python -m residual.evaluate --seeds 30               # the reported run (~1.5 h on 2 cores) → eval_out/
```

Residual vs fixed controls, threshold switching, threshold + hysteresis and a truth-switch reference, on paired
closed-loop simulated sessions, with ablations and bootstrap CIs. Results, including where
Residual loses: [docs/EVALUATION.md](docs/EVALUATION.md).

## Docs

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): the seven layers
- [docs/MATH.md](docs/MATH.md): every formula
- [docs/CONTRACT.md](docs/CONTRACT.md): frames, events, websocket, recordings

## Built vs used

We use MediaPipe for face and hand landmarks and ElevenLabs for speech. Everything else is ours:
normalization, episode segmentation, incidental-movement model, Kalman ability tracking,
changepoint detection, reliability prediction, Markov-chain false-alarm model, sequential
decisions, fusion, the control policy, the simulator, the evaluation and the UI.
