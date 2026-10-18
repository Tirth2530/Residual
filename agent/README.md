# Residual Relay

An Agentverse agent that turns low-bandwidth intent into a real-world outcome: a caregiver's
phone gets a message through Photon, the agent waits for the reply, escalates if nobody answers,
and reports what happened.

Two ways in, one orchestration:

```
ASI:One chat   --Agent Chat Protocol (Agentverse mailbox)-->  parse -> Intent --+
                                                                                  +--> Orchestrator.handle_intent(intent, context)
Residual Home  --POST 127.0.0.1:8100/intent (structured JSON, X-Residual-Token)--> Intent --+     |
                                                                                   Photon.send_message (tools/photon.py)
Photon webhook --POST :8101/photon/webhook (signed, its own server)--> Orchestrator.on_inbound()  <-- the caregiver's reply
```

Residual Home never sends prompt text. When Dev selects "Move me" with an eyebrow raise, Home sends

```json
{"intent": "request_caregiver_help", "need": "repositioning", "urgency": "normal",
 "requires_confirmation": false, "source": "residual_ui"}
```

ASI requests are parsed into the same `Intent`. `tests/test_agent.py::test_asi_and_residual_home_call_the_same_orchestrator` checks both routes reach the same function and the same Photon client.

## Profile

| | |
|---|---|
| Name | Residual Relay |
| Address | printed at startup (`agent1q...`). It comes from `AGENT_SEED`, so keep that fixed. |
| Protocol | AgentChatProtocol 0.3.0 (`uagents_core.contrib.protocols.chat`), manifest published |
| Profile text | `AGENT_PROFILE.md` (published as the agent's readme) |
| Keywords | accessibility, ALS, assistive technology, AAC, limited mobility, caregiver communication, care coordination, emergency escalation, messaging agent, communication agent |

Description (as registered): "Residual Relay turns low-bandwidth user intent into real-world communication for people with severe motor impairment. It can contact caregivers through Photon, track replies, escalate unanswered requests, and report the outcome."

## The team (`swarm/`): five agents, each on Agentverse

Residual Relay is no longer one agent ringing people in a fixed order. `python residual_agent.py` starts a small team in one
process; they talk to each other as uAgents (typed messages), and each has its own Agentverse mailbox, so each can be found and
used through ASI:One on its own.

| Agent | Job | Talks to |
|---|---|---|
| **Residual Relay** | Receives the request (the person's screen, ASI:One, a Photon reply), keeps the care plan, sends the messages | everyone |
| **Maya / Raj Care Circle agents** (one per caregiver on the care plan) | Speak for one family member: *available or not, how far, can they lift, how much of the family's care have they done this week (nights count double)*. Learn it from the human's own texts ("in a meeting till 3", "driving", "10 min away"). | the Relay (Care Circle protocol) |
| **Residual Concierge** | Asks every approved restaurant for a quote on the usual and picks the one that costs the person the fewest choices: in budget, not much slower, and **under the spending limit so nobody has to be woken to approve**. Over the limit, it asks whichever approved family member is best placed right now. | restaurant agents, the circle |
| **Residual Hands** | Browser errands, straight to Browser Use (not an ASI:One agent unless you set `HANDS_ON_ASI=1`). Does everything up to *one click before anything irreversible*; if a site needs a login/CAPTCHA, a family member is sent a link to the **same live browser** and lends their hands from their phone; the person confirms the final click on their own screen; then exactly that action runs. Never pays. | Browser Use, the circle |

**Care Circle protocol** (`swarm/wire.py`, published as `residual-care-circle` v1): `HelpCall` -> `HelpBid`, `StatusText` -> `StatusReply`,
`Outcome`, `CircleQuery` -> `CircleState`. A member only ever *advises*; the Relay still messages only people on the care plan
(`contacts.json`). A call that names people keeps the person's choice. **Emergencies never wait for the circle**: they follow the care plan's fixed order (a message, then a call after half the interval, then the next person, up to 5 rounds), exactly as before. The circle only reorders ordinary help requests, so it can't slow an emergency down.

How it shows up: the help screen lists the circle's answer ("Raj · asked first · 6 min away" / "Maya · left alone · in a meeting until 3:00 PM"
and whether each is an agent on Agentverse); the Care circle menu shows who is free; Errands starts the Hands.

Setup, once:

1. `AGENT_SEED` in `.env` (keep it private; the other four identities are derived from it, so nothing else to store). Add each caregiver's `base_eta_min` / `can_lift`
   under `"circle"` in `contacts.json` if you want (optional). In `care_profile.json`: `spending.allowed_merchants`, `spending.approvers`,
   and `hands.allowed_hosts` (the only sites the Hands may visit).
2. `BROWSER_USE_API_KEY` in `.env` for a real browser (without it the Hands run a SIMULATED browser and say so everywhere).
3. Connect the mailboxes, once. The Agentverse Inspector can't talk to a Bureau ("agent bureaus are not supported"), so stop the team and run
   `python connect_mailboxes.py`: it starts Maya, Raj, the Concierge and the Hands each alone on its own port (same identities as in the team;
   `--all` adds the Relay) and prints one link per agent. Open each link, click **Connect**, choose **Mailbox**, then Ctrl-C. The mailbox belongs to the
   agent's address, so afterwards `python residual_agent.py` just uses them. (`RESIDUAL_TEAM=0` runs the Relay alone; `RESIDUAL_MAILBOX=0` skips mailboxes.)
4. In Agentverse, for each agent: add a handle, check its README renders, and run the Agentverse chat test. Add `ASI_AUTHORIZED=<your ASI:One address>` (printed
   once as "ASI sender ...") so the team accepts your requests.
5. Family members text their Photon line things like "in a meeting till 3" or "10 min away"; their agent answers and remembers.

Without the team running, the same logic runs inside the Relay and the screen labels it "local agent", so nothing breaks.

Local API additions (token required): `GET /circle`, `POST /circle/say` (simulated, needs simulated replies on), `GET/POST /errands`,
`POST /errands/{id}/consent` (the person's screen only), `POST /errands/{id}/cancel`.

## Tools

| Tool | What it does |
|---|---|
| `send_message` | Photon `POST /messages` to the contact's space. Idempotency key `task:contact:round`, also stored locally so a retry never double-sends. |
| `get_conversation_status` | Task status, attempts, replies, outcome (`GET /tasks/{id}`, or "Did Maya reply?" in ASI). |
| `escalate_to_contact` | Automatic: a decline, or no reply within `escalate_after_s`, moves to the next allowed contact. |
| `cancel_task` | Allowed for help requests. An emergency can only be cancelled from Residual Home with explicit confirmation. |

**Emergency: message, then call, then the next person.** An emergency message that isn't answered in half the
interval becomes a call to the same person, then the next person gets the same. Photon places calls over SIP
through a voice provider (ElevenLabs, Retell, Twilio, LiveKit) configured in its dashboard, so the Relay starts a
call through one webhook, `VOICE_CALL_WEBHOOK` (POST `{to, name, text, idempotency_key}`). Without it, live
emergencies are messages only and `/health` says `calls: off`; with simulated Photon, calls are SIMULATED and
labelled. A call never counts as an answer: only a reply does. Help requests never call.

When someone answers, the outcome carries their relation and pronoun from `contacts.json` (`"pronoun": "she"`),
and Residual says "Your daughter answered. She's on her way."

Photon 2.0 accepted messages return 202, which means accepted, not delivered; the agent reports "sent", and only a reply counts as acknowledged.

## Sandbox ordering (`commerce.py`)

"Order my usual" from Residual (two picks) or "Order Dev's usual lunch" from ASI:One:

1. Residual Relay discovers a food agent by capability, only among approved merchants. The bundled one is a
   mock restaurant (Luna Kitchen) with a real-looking menu and a structured API.
2. The menu it returns is **data**: validated with pydantic, unknown fields dropped (anything instruction-like
   is never read), items over $200 rejected, the merchant checked, and the total computed here from validated
   prices.
3. The person sees merchant, items, total and delivery, and picks **Confirm order** or **Cancel**. From ASI:One
   an order is only *prepared*; it waits for the person on Residual's screen. ASI can confirm only if its sender
   address is in the care profile's `authorized_asi` and `asi_may_confirm` is true (both off by default).
4. Spending policy (`care_profile.json`, see `care_profile.example.json`): per-order cap, daily cap, approved
   merchants. Over a cap, the approver gets "Dev wants to order … for $18.47. Reply YES 482913 to approve" through
   Photon. Only the approver's reply naming that order's code, within 30 minutes, approves it.
5. **Sandbox payment only**, idempotent on the order id: a repeated confirm or a retried approval can't pay twice.
6. Every step (proposed, confirmed, approval requested, approved / declined / expired, executed, rejected) goes to
   `state/audit.jsonl` without message text, and to the Care Report in the Lab.

Caps and the care profile change only with the caregiver token: `python commerce.py set-token` once, then
`POST /care/caps` or `PUT /care/profile` with header `X-Care-Token`. The usual order, dietary notes and delivery
address are whatever the caregiver wrote there; nothing is inferred from conversation.

| Endpoint | |
|---|---|
| `POST /orders` `{which:"usual"}` | prepare (Residual Home) |
| `POST /orders/{id}/confirm` `{confirmed}` | the person's Confirm / Cancel |
| `GET /orders`, `GET /audit` | history |
| `GET /care/profile`, `PUT /care/profile`, `POST /care/caps` | profile and limits (writes need the token) |


## Safety rules (deterministic, in `policy.py`)

- Only names in `contacts.json` can be contacted. Anything else is refused with a message, from both ASI and Home.
- Messages to caregivers come from fixed templates ("Dev needs help repositioning. Can you come?"). The person's own words are sent only for `send_message`, capped at 500 characters.
- Emergency order is fixed by the care plan; ASI can't reorder it, add people, or cancel it.
- An optional ASI:One parse (`ASI1_API_KEY`) can only produce an `Intent`, which goes through the same allowlist checks. Without a key, a deterministic parser is used.
- If every Photon send fails, the task ends as `failed` with `fallback: local`, and Residual Home starts its own Lifeline alert and the alarm in the room. Nothing reports success it didn't have.
- A restart marks unfinished tasks `failed` instead of pretending they carried on.

## Who can reach what (checked by `tests/test_security.py`)

- **The local API is for Residual's screen only.** It listens on 127.0.0.1 (it refuses to start on anything else), every
  route but a bare `/health` needs the `X-Residual-Token` header, and the `Host` must be a local name. The token is made
  once, kept in `state/local_token` (owner-readable only), and read by the app from the same file (or `RESIDUAL_LOCAL_TOKEN`
  in both). A web page in the person's browser can't use the API, and the care profile and audit trail aren't public.
- **The Photon webhook is a separate server** (`WEBHOOK_PORT`, default 8101) with only `POST /photon/webhook`. It is the one
  port to point a tunnel at. With Photon live, the agent **refuses to start** unless `PHOTON_WEBHOOK_SECRET` is set, and
  unsigned or wrongly signed posts are rejected.
- **Closed to the public (`ASI_MODE=closed`, the default).** This agent is for the person and their family. Only a sender
  listed in `ASI_AUTHORIZED` (comma-separated agent addresses, and/or `authorized_asi` in the care profile) can make it
  message, alarm or order anything. Anyone else still gets answers to harmless questions (status, who it can contact) and a
  plain refusal ("I only act on requests from people Dev's care team has authorised, so I haven't sent anything"; for an
  emergency it also says to call the local emergency number). Nothing is ever shown on the person's screen for a
  stranger's request. The agent logs each sender's address once (`ASI sender agent1q… (authorised / not authorised)`, never
  what they typed): that is how you find your own address to list. `ASI_MODE=approve` is an operator tool: strangers' requests
  wait for `POST /asi/pending/{id}/decide` on the local API (10 minutes, 5 at a time, rate limited). A message that came
  through ASI:One is sent as "Via ASI:One (not written by Dev): …", never as "From Dev".
- **Approval codes** for over-limit orders are six random digits (`secrets`); five wrong guesses in ten minutes lock that
  person out for fifteen, even if they then send the right code.
- **The agent's identity is deliberate.** It won't start without `AGENT_SEED` (20+ characters): the seed is its address,
  so a missing one would give a different agent on every restart. `ALLOW_EPHEMERAL_AGENT=1` is for a local experiment.

## Run it

```bash
cd agent
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # AGENT_SEED is required: python -c "import secrets; print(secrets.token_hex(32))"
cp contacts.example.json contacts.json   # real names and phone numbers (E.164)
python residual_agent.py
pytest -q                       # 190+ tests, no network
```

Without Photon credentials it runs in SIMULATED mode: nothing reaches a phone, every attempt says `simulated`, Home shows "SIMULATED Photon", and the Lab can post a simulated reply ("Maya replies (SIMULATED)"). Set `PHOTON_MODE=live` to refuse to start without credentials.

`DEMO_TIMING=1` escalates after 25 s instead of 120 s (help) / 60 s (emergency). Home and the Lab say "demo timing" whenever it's on.

## Photon setup (one channel: iMessage or SMS)

1. In the Photon Beta dashboard (app.beta.photon.codes), create a project, copy its Project ID (`pho_prj_…`), and create an API key with `project:read`, `project:write` and `platforms:read`. Put them in `.env` as `PHOTON_PROJECT_ID` and `PHOTON_API_KEY`, with `PHOTON_PLATFORM=imessage`. Then **assign a shared iMessage line to each caregiver's phone** (E.164 number) in the dashboard, and have each caregiver text their assigned line once: a shared line can only reply to someone who wrote first, and only in a direct chat. `PHOTON_AGENT_USER_ID` is optional: Photon's lines are pooled, so the Relay finds each contact's agent from the line they were assigned. Shared iMessage lines cannot place voice calls, so emergency escalation is messages only.
2. Check the API before relying on it: `python tools/photon_doctor.py` prints the live REST paths and inbound event names (Photon 2.0 is in beta; field names can move). The inbound parser accepts both the Spectrum `{"event":"messages", space, message}` shape and the 2.0 envelope.
3. Send one real test: `python tools/photon_doctor.py --send Maya`.
4. Expose the webhook, and only the webhook. It has its own port (8101); the local API on 8100 must never be tunnelled:
   `cloudflared tunnel --url http://127.0.0.1:8101` (or `ngrok http 8101`), then register `https://<tunnel>/photon/webhook` in the dashboard and put its secret in `PHOTON_WEBHOOK_SECRET`. Live mode won't start without it, and unsigned or wrongly signed posts are rejected.
5. Reply "On my way" from Maya's phone. The Lab's Agent panel should show the reply and `acknowledged`.

The contact to Photon user to space mapping is kept in `state/photon_map.json`; tasks in `state/tasks.json`.

## Agentverse and ASI

1. `AGENT_SEED` fixed, `AGENT_HANDLE` optional. Start the agent; it prints its address and an Agentverse inspector link.
2. Open the inspector link, connect the mailbox. The agent publishes its manifest, description, keywords and `AGENT_PROFILE.md`.
3. In ASI:One, search "Residual Relay" or "ALS caregiver" and chat:
   - "My father has ALS and cannot type. Ask Maya to help reposition him. If she cannot come, try Raj."
     Expected: "I'll contact Maya first." / "Message sent to Maya." / after her reply, "Maya replied that she's on her way. I won't contact Raj."
   - "Tell Raj that Dev says thank you for today."
   - "Did Maya reply?"
   - "Text Bob" (refused: not on the care team)

Not verified from the build machine: Agentverse registration (the sandbox couldn't reach Agentverse) and a live Photon send. Both need to be checked on the demo Mac with real credentials.

## Residual Home integration

- Home's Electron main process talks to `http://127.0.0.1:8100` (`app/lib/agent-client.js`, `agent.url` in `app/config.json`) and polls each task until it ends.
- Get help: "Move me", "I'm in pain", "Suction" become `request_caregiver_help`; "Help now", "Trouble breathing" and the three-closure eye pattern become `emergency`, with the alarm in the room. If the agent isn't running or Photon fails, an emergency falls back to the local Lifeline.
- Phrases on a person's screen become `send_message` to that contact, after a 3 s cancel countdown, when the agent knows the contact; otherwise WhatsApp as before.
- "Maya is on the way." is shown and spoken once, through the app's priority speech queue.
- The Lab's Agent panel shows INTENT / PLAN / TOOL EXECUTION / RESPONSE / OUTCOME from the real task record, with timings, errors and SIMULATED labels.

Local API (token required): `POST /intent`, `GET /tasks`, `GET /tasks/{id}`, `POST /tasks/{id}/cancel`, `GET /asi/pending`, `POST /asi/pending/{id}/decide`, orders and care endpoints, `POST /simulate/reply` (403 unless simulated replies are allowed), `GET /health` (bare `{ok:true}` without the token).
Webhook server: `POST /photon/webhook` only.
