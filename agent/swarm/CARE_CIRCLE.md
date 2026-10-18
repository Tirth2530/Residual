# Care Circle protocol, v1 (`residual-care-circle`)

A small, open protocol so the people (and agents) around someone who needs care can answer *who is best placed right now*
instead of being rung in a fixed order. Any agent that answers a `HelpCall` with a `HelpBid` can be part of a family's circle.
The messages are in `wire.py`; the rules members follow are in `circle.py`.

| Message | From → to | Meaning |
|---|---|---|
| `HelpCall{call_id, person, need, urgency, text}` | coordinator → member | Someone needs help (repositioning, pain, thirsty, bathroom, suction, breathing, general) |
| `HelpBid{call_id, member, can_help, eta_min, fit, load, interrupt_cost, reason}` | member → coordinator | An honest answer for *my* human, with a plain reason |
| `StatusText{member, text}` → `StatusReply{ack}` | coordinator → member | What the human said about where they are |
| `Outcome{call_id, member, status, need}` | coordinator → member | The member did (or declined) the help; feeds the fairness ledger |
| `CircleQuery` → `CircleState` | coordinator → member | Where is this person right now, and how much have they done this week |

Rules every implementation keeps:
- A member **advises**. It never contacts anyone; the coordinator contacts only people on the person's care plan.
- A member who says nothing (or is down) is simply *silent*: the care plan's order applies. The circle can only improve on the plan.
- **Emergencies do not use the circle**: everyone on the emergency list is alerted at once.
- Load counts only help that really happened; nights (22:00-06:00) count double. A bid is a statement about the human's availability, never a command.
