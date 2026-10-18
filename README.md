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
 webcam ─► MediaPipe face landmarks ─► gesture timing + safety (eyes/) ─► Residual app (Electron)
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

`eyes/tests/safety.test.ts` runs the Back detector and the help detector on the same frames, configured as
the app configures them, and checks every closure length from 0.1 s to 5 s does at most one thing. Help uses
the same per-person eye levels as the other gestures.

- **Setup you can do alone.** Camera check, Learn your face, Try it, Extras. Each screen moves on by itself
  with a spoken countdown when things work. When a choice is needed (Retry / Continue / Skip) the options light
  up in turn and closing your eyes for a moment picks the lit one (general eye levels until your own are
  learned). The camera is mirrored with your eyes and brows outlined as Residual sees them, with "2 / 3
  learned" counts, a sound for each gesture, and "Blink once more" after the first blink. Keyboard and mouse
  still work for a helper.
- **Several controls, all calibrated, none assumed.** Blink twice, eyebrows, Back closure, plus optional
  **glances** (centre, left, right, up: your own range and direction) and a **wink** (accepted only if the other
  eye stays open and your normal blinks never looked like winks). Settings, How I choose. With a wink in use,
  the help detector requires both eyes, so a held wink can never count toward help (tested).
- **Adapts when a signal gets harder to read, and always asks.** `ui/quality.js` tracks the right-side signal
  (eyebrows or the wink): each attempt's strength against your Setup level, near misses, attempts that fell
  short and were tried again, timing consistency, and whether the face was even visible. It never claims to
  detect ALS progression or fatigue. "Your eyebrow signal is getting harder to read. Switch to one-signal
  mode?" In one-signal mode the sides light up in turn and blinking twice picks the lit one; the eyebrows are
  still watched, and when they're reliable again it asks "Restore two-signal control?". The Lab shows the
  history and the exact reasons. "Simulate eyebrows weakening (demo)" puts a red SIMULATED banner on Home and Lab.
- **Test my controls** (Settings): 20 blink twice, 20 of the right-side signal, 10 Back, 10 of each learned
  fallback, then 2 minutes of normal talking and looking around. Per gesture: prompted, detected, missed, wrong
  command, median response, false activations; plus false commands per minute. Saved as JSON
  (`residual-control-tests.json` in the app's data folder). Presentation numbers come only from this test.
- **Record session** (Settings or Lab): off by default, asks first, numbers only (eye and eyebrow levels per
  frame, gaze numbers, gestures, thresholds, choices, mode changes). No video, sound or message text.
  Stop recording and Delete recordings are always there. JSON lines in the app's data folder, `recordings/`.
- **Websites use the whole window.** The two choices float over the bottom of the page, translucent; buttons
  hidden behind them aren't offered until scrolled into view. Scroll down / up, Previous page and More are
  always there; the choices are rebuilt after scrolling. Larger text zooms the site and the choices.
- **Choices don't move under you.** While you're inside a split the page's choices are frozen; small page
  changes are ignored; a real change rebuilds them only after a moment without gestures, and says
  "Choices refreshed".
- **Known apps use fast built-in actions** (Reels: Next is one pick; YouTube: Play / pause, Next video, Back to
  videos, videos by title; WhatsApp; Netflix). **Other websites: AI web mode**, Browser Use as a general web
  fallback: it reads the page once, Residual offers its few actions two at a time, the chosen one runs as one
  task. Never one call per gesture; timeouts return to the last safe choices; consequential actions (buying,
  posting, sending, deleting, signing in, security settings) are filtered out in code whatever the model says.
- **Say something:** Yes / No, Care, Conversation, Comfort, My phrases, and "Reply to Maya" with AI-suggested
  answers when someone has just written. Suggestions only: the person picks what is said. Optional ElevenLabs
  voice for the person's words (`ELEVENLABS_API_KEY` in `app/.env`), system voice otherwise.
- **Help:** three long closures sound the alarm and start the Relay: message, then a call if calls are set up,
  then the next person; "Your daughter answered. She's on her way."
- **Order my usual (sandbox):** merchant, items, total and delivery, then Confirm / Cancel. Spending caps,
  approved merchants, caregiver approval by reply ("YES 482913"), idempotent sandbox payments, an audit trail and
  a Care Report history. No real money. Details in `agent/README.md`.
- **Demo Mode** (Settings): a Demo menu that needs no logins: a bundled news page, the labelled weakening,
  a Relay request (live or clearly SIMULATED), Browser Use, Test my controls, a sandbox order.
- A helper can use Left / Right arrows, Backspace (back), Escape (Home), P (pause), or click a side.
- The old three-movement mode with the Python engine: `"input": { "mode": "engine" }` in `app/config.json`.

What the AI is: MediaPipe's face model (computer vision) turns camera frames into face landmarks and
expression scores. Residual's own calibrated timing logic turns those noisy scores into deliberate
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

The central path needs none of Ollama, news, Instagram, WhatsApp or Netflix: camera, two signals, structured
intent, the care circle, Photon, a human reply, the acknowledgement on screen. **Cmd/Ctrl+L** opens the Lab on a
second display if there is one.

**Local AI (recommended):** `brew install ollama`, `ollama serve`, then `ollama pull qwen3:4b`
(or `qwen2.5:3b` on an older laptop). The app picks the best installed model by itself. Without a
model, everything still works with rule-based replies and summaries. Other options in
`app/config.json → ai`: any OpenAI-compatible server (LM Studio locally, or xAI Grok, Groq,
OpenRouter with a key), or Gemini.

```jsonc
// app/config.json → "ai", examples
{ "provider": "ollama", "ollamaModel": "auto" }                                                   // local, default
{ "provider": "openai", "openaiBase": "http://localhost:1234/v1", "openaiModel": "qwen2.5-7b-instruct" } // LM Studio, local
{ "provider": "openai", "openaiBase": "https://api.x.ai/v1", "openaiKey": "…", "openaiModel": "grok-3-mini" }   // Grok
{ "provider": "openai", "openaiBase": "https://api.groq.com/openai/v1", "openaiKey": "…", "openaiModel": "qwen/qwen3-32b" } // Groq free tier
```

Cloud model names change often; check the provider's model list. With a cloud provider, chat
messages and article text leave the laptop, which is why local is the default.

**No webcam?** In the Lab, choose *Simulated person*: a model of a user drives the real engine.
Keys also stand in for movements: → next, space select, ← back, Esc home, H help.

## Where to look

| | |
|---|---|
| `REVIEW.md` | For a reviewer: architecture, what's tested, known weak spots. Start here to attack it |
| `docs/CLINICAL.md` | For a clinician: what it is and isn't, how it differs from existing AAC, safety, a validation plan |
| `docs/DEMO.md` | The 2-minute demo and pitch: beat sheet, setup checklist, fallbacks, Q&A |
| `docs/JUDGES.md` | The hard questions from a healthcare, a product and a technical reviewer, with straight answers |
| `agent/README.md` | Residual Relay: profile, tools, safety rules, Photon setup, ASI prompts, Home integration |
| `engine/docs/` | The engine: math, architecture, evaluation |
| `engine/eval_out/` | Evaluation results: 30 paired seeds per cell, tables and charts |

Built on Arjun's Residual engine; eye feature extraction, One Euro filter, blink switch and help
pattern adapted with permission from AGENCY OS (UCSC AI Hackathon 2026).
