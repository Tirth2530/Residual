"""The shared contract between perception, the core, the UI and the evaluation.

Perception produces Frames. The engine consumes Frames and emits Events.
Everything is plain data so a session can be logged, replayed and evaluated
through the exact same code as the live demo.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Frame:
    """One camera frame, already reduced to per-channel amplitudes.

    amps: normalized amplitude per channel. 0 ~ at rest, ~1 ~ a calibrated
          deliberate gesture at peak. Degradation is applied to these values,
          never to any score.
    visible: False when the channel couldn't be measured (face/hand lost,
             dropped frame). Invisible frames contribute no evidence.
    """

    t: float
    amps: dict[str, float]
    visible: dict[str, bool] = field(default_factory=dict)

    def is_visible(self, ch: str) -> bool:
        return self.visible.get(ch, True)


@dataclass
class Event:
    """Something the engine decided or observed. kinds:

    fire          evidence crossed a bound on an option
    select        a board option was chosen (data: index, label, cls)
    confirm       an irreversible option is waiting for a second gesture
    cancel        a confirm timed out (counted as a likely false trigger)
    speak         text to say aloud
    alert         caregiver alert
    attempt       a qualifying gesture episode closed (data: channel, strength, fired)
    changepoint   BOCPD flagged an abrupt change on a channel
    verify_start  asking the person to confirm a candidate channel
    verify_ok / verify_fail
    migrate       control moved (data: from, to)
    log           human-readable observation line for the event log
    """

    t: float
    kind: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self))


def frame_to_json(f: Frame) -> str:
    return json.dumps({"t": f.t, "amps": f.amps, "visible": f.visible})


def frame_from_json(s: str) -> Frame:
    d = json.loads(s)
    return Frame(t=d["t"], amps=d["amps"], visible=d.get("visible", {}))
