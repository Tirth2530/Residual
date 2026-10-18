# Residual Relay

Residual Relay turns low-bandwidth user intent into real-world communication for people with
severe motor impairment. It can contact caregivers through Photon, track replies, escalate
unanswered requests, and report the outcome.

**Keywords:** accessibility, ALS, assistive technology, AAC, limited mobility, caregiver
communication, care coordination, emergency escalation, messaging agent, communication agent.

**This is a prototype, not a medical device.** "Dev", "Maya" and "Raj" are demo people. Never
rely on it for a real emergency: call your local emergency number.

## The team

Residual Relay works with three other agents on Agentverse, each findable on its own:
**Maya's and Raj's Care Circle agents** (each speaks for one family member: free or not, how far, who has helped most this week) and the
**Residual Concierge** (food quotes within the person's spending limits). The Relay asks the circle who is best placed, then contacts
that person first and leaves the others undisturbed. Emergencies never wait for the circle: they go to the care plan's contacts in a fixed order, a message first, then a call, moving on every minute until a human answers.

Web errands (a refill, a form) don't go through ASI:One at all: Residual's screen sends them straight to Browser Use, stops one click
before anything final, and asks the person on their own screen.

## What it does (real tool calls, not chat)

- Contacts a caregiver over Photon (iMessage or SMS) with a fixed, clear message.
- Waits for a reply. "On my way" stops the request; "can't" or silence moves to the next person.
- For an emergency, alerts the care plan's contacts in a fixed order, then everyone again, until
  a human answers.
- Prepares a food order from the person's saved usual (a sandbox test: **no real money moves**),
  and asks their caregiver to approve anything over the spending limit.
- Reports every step and the outcome back to you.

## Who can make it act

Residual Relay is for conversations between a person and their family, so it is **closed to the
public**. Only senders the person's care team has authorised can make it contact anyone. If you
aren't one of them you can still ask what it does and who it can contact, and it will tell you
plainly when it won't act: *"I only act on requests from people Dev's care team has authorised, so
I haven't sent anything."* If a message looks like an emergency it tells you to call your local
emergency number. It never messages anyone, prepares an order, or alarms anyone for a public chat.

## Try it

- "Did Maya reply?" (or just "status"): answers for anyone.
- "Ask Priya to bring my medication": refused, because Priya isn't on the care team's list.
- "Add my neighbor Tom to the contact list": refused, because only the care team can change the list.
- "Ask Maya to help reposition him": goes through for authorised care-team senders; everyone else is
  told it hasn't been sent.

In a demo, Photon may run in **simulation mode**; the agent says so in its replies whenever it does.

## Safety

- Only people on the care team's allowlist can be contacted. Anyone else is refused.
- Messages to caregivers use fixed wording. Emergency order is fixed and can only be cancelled
  from the person's own Residual screen.
- Closed to the public: requests from anyone not authorised are refused and nothing is sent, prepared
  or ordered. Authorised messages are labelled as coming through ASI:One, never as the person's own words.
- Spending has per-order and daily limits, an approved-merchant list, and caregiver approval
  with a one-time code. Sandbox only.
- A language model can help read your request; it can't add contacts or change these rules.

The same agent is used from Residual, an assistive interface that turns whatever movement a
person still has (a pinch, an eyebrow, a blink, a glance) into these same requests.
