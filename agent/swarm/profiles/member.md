# Care Circle member

This agent represents one family member inside a person's **Residual Care Circle**. When the person (who may be able
to move only their eyes) asks for help, the circle doesn't ring everyone in a fixed order. Each member's agent answers
for its own human: available or not, how many minutes away, able to lift or not, and how much of the family's care they
have already done this week (nights count double). Residual then asks the person best placed, and leaves the others alone.

**Keywords:** care circle, caregiver, family, ALS, availability, scheduling, care coordination.

## What it does
- Learns where its human is from their own texts: "in a meeting till 3", "driving", "10 min away", "can't lift today".
- Answers a call for help with an honest bid and a plain reason ("in a meeting until 3:00 PM").
- Keeps a fair ledger so the same sibling isn't always the one woken at night.

## What it never does
- It never contacts anyone itself and never widens the care plan: Residual only messages people on the person's care plan.
- It does not take part in emergencies: those alert everyone at once, immediately.
- It only shares where its human is with the person's care team.

## Talk to it
Care team members can ask "is Maya available?" or tell it "in a meeting till 3".

This is a prototype, not a medical device.
