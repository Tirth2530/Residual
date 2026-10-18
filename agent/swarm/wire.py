"""The Care Circle protocol (residual-care-circle v1): the typed messages the Relay and the family members' agents exchange.

Any agent that answers a HelpCall with a HelpBid can be part of a family's circle, so a home-care agency or a neighbour's
agent can speak it too. A member only ever *advises*: the Relay still contacts only people on the care plan.
"""

from uagents import Model


class HelpCall(Model):
    call_id: str
    person: str               # who needs help
    need: str                 # repositioning | pain | thirsty | bathroom | suction | breathing | general
    urgency: str              # normal | urgent
    text: str = ""


class HelpBid(Model):
    call_id: str
    member: str
    can_help: bool
    eta_min: int
    fit: float = 1.0
    load: float = 0.0         # how much of the family's care this person has done this week (nights count double)
    interrupt_cost: float = 0.0
    reason: str = ""


class StatusText(Model):
    """What a family member told Residual about where they are ("in a meeting till 3")."""
    member: str
    text: str


class StatusReply(Model):
    member: str
    ack: str = ""             # empty: the text wasn't a status


class Outcome(Model):
    call_id: str
    member: str
    status: str               # acknowledged | declined
    need: str = "general"


class CircleQuery(Model):
    request_id: str


class CircleState(Model):
    request_id: str
    member: str
    mode: str
    label: str = ""
    until: float = 0.0
    eta_min: int = 0
    can_lift: bool = True
    load: float = 0.0


PROTOCOL_NAME = "residual-care-circle"
PROTOCOL_VERSION = "1.0.0"
