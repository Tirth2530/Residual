# Residual

**Residual lets a person who can only blink and raise their eyebrows run their day and reach their
family, and a small team of agents decides who to wake instead of waking everyone.**

ALS takes movements away one at a time. Residual turns the two that are left into everything else:
**blink twice = left, raise your eyebrows = right, close your eyes a moment = back.** Every screen is
two big choices. Asking for help works like this: Dev selects "Move me", the family's agents decide who is
best placed, only that person's phone gets "Dev needs help repositioning. Can you come?", she replies
"On my way", and Dev's screen says so. Nobody who didn't need to be woken was.

## What it does

- **Eyes-only control.** A webcam and MediaPipe's face landmarks, with timing and safety logic written for
  this (`eyes/`, TypeScript, 49 tests). Calibrated to the person's own eyes. A held gesture fires once.
  Back and the call for help can't be confused (tested for every closure length from 0.1 to 5 s).
- **A care circle of agents, on Agentverse.** Each family member has their own agent (Maya's, Raj's) that
  knows whether they're free, how far away they are and who has helped most this week (nights count
  double). When Dev asks for help, the Relay asks them and they bid; only the best-placed person is
  contacted, with the next one after 2 minutes of silence. Availability stays with each person's own agent,
  updated by a text ("in a meeting till 3pm"). A request that names someone is honoured: the circle only advises.
  Emergencies skip the circle and follow the care plan's fixed order (a message, a call, then the next person).
- **Real messages, real replies.** Photon sends iMessage/SMS; "On my way" stops the escalation, "can't" or
  silence moves on. Only people on the care team's allowlist can be contacted, and ASI:One is closed to
  strangers by default.
- **Errands on the web, one click short.** Browser Use drives a real cloud browser on approved sites only,
  stops one click before anything final, and asks Dev on their own screen. It never pays.
- **Food orders (sandbox).** A Concierge agent asks every approved restaurant for a quote, prefers the one
  that needs nobody woken to approve it, and waits for Dev to confirm. No real money.
- **The person's day.** WhatsApp, YouTube, Reels, Netflix, music, a needs board that speaks, news, and
  suggestions to say. The person always picks what is said.

```
 webcam ─► MediaPipe face landmarks ─► Deepseek Model ─► gesture timing + safety (eyes/) ─► Residual app (Electron)
                                       blink twice · eyebrows · long blink    two big choices, real apps, Errands, Care circle
                                                                                   │ structured intent (local HTTP + token)
                                                                                   ▼
  ASI:One chat ◄──► Residual Relay (uAgents) ──bids──► Maya's agent · Raj's agent     ("who is best placed?")
                          │                    └─────► Residual Concierge             (food quotes within limits)
                          ├─► Photon iMessage ─► caregiver: "On my way" ─► webhook ─► Dev's screen
                          └─► Browser Use ─► stops one click short ─► Dev confirms on their own screen
```

Four agents are published on Agentverse (the Relay, Maya's, Raj's and the Concierge); each can be found and
talked to in ASI:One. Browser errands go straight from the app to Browser Use through the local Relay, not
through ASI:One (set `HANDS_ON_ASI=1` in `agent/.env` to also publish them as a chat agent).

## What is real, and what is simulated

- **Real:** the two-signal control on a webcam, messages through Photon (iMessage/SMS) with replies, the agents on
  Agentverse (the Relay, Maya's, Raj's and the Concierge), and Browser Use running a real cloud browser.
- **Simulated, and labelled on screen:** payments (a sandbox, no real money), the "eyebrows weakening" demo, and any
  Photon run without credentials.
- **Prototype:** not a medical device. The next step is working with ALS clinics and speech therapists
  (`docs/CLINICAL.md`).
- **Also in the repo:** `engine/`, an earlier input layer in Python (Kalman strength, changepoints, sequential tests,
  automatic migration between movements, 73 tests and a simulated evaluation). It is used with
  `"input": { "mode": "engine" }` in `app/config.json`. Residual's default mode uses the lighter adaptation in
  `app/ui/quality.js`, which only ever suggests a change and asks.

## Two signals. Full agency.

Everything is two big choices. **Blink twice = left. Raise your eyebrows = right. Close your eyes for a
moment = back.** More options are split in two again, most likely first: 8 options take 3 picks, 32 take 5.

**Eye closures, by length** (one closure, eyes reopened). Back and the call for help can't be confused:

| Closure | Means |
|---|---|
| under 0.5 s | a blink (two quick ones = left) |
| 0.6 to 1.0 s | Back |
| 1.0 to 1.5 s | nothing (a dead zone on purpose) |
| 1.5 to 4 s | counts toward help: three within 15 s calls for help |
| over 4 s | resting the eyes: nothing, and the help count starts again |


- **Adapts when a signal gets harder to read, and always asks.** `ui/quality.js` tracks the right-side signal
  (eyebrows or the wink): each attempt's strength against your Setup level, near misses, attempts that fell
  short and were tried again, timing consistency, and whether the face was even visible. It never claims to
  detect ALS progression or fatigue. "Your eyebrow signal is getting harder to read. Switch to one-signal
  mode?" In one-signal mode the sides light up in turn and blinking twice picks the lit one; the eyebrows are
  still watched, and when they're reliable again it asks "Restore two-signal control?". The Lab shows the
  history and the exact reasons. "Simulate eyebrows weakening (demo)" puts a red SIMULATED banner on Home and Lab.

- **Order my usual (sandbox):** merchant, items, total and delivery, then Confirm / Cancel. Spending caps,
  approved merchants, caregiver approval by reply ("YES 482913"), idempotent sandbox payments, an audit trail and
  a Care Report history. No real money. Details in `agent/README.md`.

What the AI is: MediaPipe's face model (computer vision) turns camera frames into face landmarks and
expression scores which are shared with a local DeepSeek model. Deepseek handles Residual's calibrated timing logic turns those noisy scores into deliberate
commands (interpretable on purpose, and safety-critical decisions stay deterministic). The agents
(Residual Relay on Agentverse with Photon; Browser Use for the web) turn choices into real actions within
fixed limits. It is not facial recognition: it never identifies who someone is. It is a prototype, not a
clinically validated device.

## Run it

You need Python 3.11 and Node 18+.

```bash
# 1. The agent team (Relay, Maya's and Raj's agents, Concierge): see agent/README.md for Photon and Agentverse
cd agent
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && cp contacts.example.json contacts.json && cp care_profile.example.json care_profile.json
python -m pytest -q                          # 216 tests
python residual_agent.py                     # SIMULATED Photon until credentials are set
python tools/browser_use_doctor.py           # checks BROWSER_USE_API_KEY (add --run for one small real task)

# 2. The eye tracker (once): installs MediaPipe's runtime and builds the bundle the app loads
cd ../eyes
npm install && npm run build:app

# 3. The app (start the agent first)
cd ../app
npm install
cp .env.example .env                         # add BROWSER_USE_API_KEY for AI web mode and Errands
npm test                                     # 118 tests
npm start
npm run preflight                            # PASS / WARN / FAIL for everything the demo needs

# 4. Optional: the legacy Python engine (only for "input": { "mode": "engine" })
cd ../engine
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-vision.txt && python scripts/get_models.py
python -m pytest -q                          # 73 tests
```

`cd eyes && npm test` runs the gesture and safety tests (49). Keys live only in the git-ignored `.env` files.
