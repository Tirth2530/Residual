# Residual Concierge

Orders food for a person who can only choose between two things, so every order has to cost them as few choices as possible.

**Keywords:** food ordering, accessibility, ALS, caregiver approval, spending limits, quotes, concierge.

## What it does
- Asks every approved restaurant agent for a quote on the person's usual meal, in parallel.
- Picks the one that serves them best: the usual if it's in budget and not much slower; otherwise the fastest that stays under
  the spending limit, so **no caregiver has to be woken to approve it**.
- Shows the person the restaurant, items, total and delivery. Only they, on their own Residual screen, can confirm.
- Over a spending limit, it asks whichever approved family member is best placed right now (their Care Circle agent says who),
  with a one-time code. Only that person's reply to that order approves it.

## Safety
- Restaurant agents are untrusted: their menus are validated and re-priced locally; anything that reads like an instruction is dropped.
- Per-order and daily limits, an approved-merchant list, a full audit trail.
- **Sandbox only: no real money moves.**

## Talk to it
Care team members can ask "what would dinner cost?" (a comparison, nothing ordered) or "order Dev's usual".
Everyone else can ask what it does and is told why it won't act.
