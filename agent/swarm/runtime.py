"""How the Relay talks to the circle. A member is anything that can bid on a call, hear its human's text and be told how
a call ended: an agent reached over Agentverse (swarm/agents.py) or, when it is running inside this process, its brain
directly. Either way a slow, broken or missing member only ever means "that one didn't answer"."""

from __future__ import annotations

import asyncio
import logging
import time

from .circle import CaregiverBrain, choose

log = logging.getLogger("residual.circle")


class LocalMember:
    """A member whose brain runs in this process (tests, and a Relay started without the rest of the team)."""
    transport = "local"

    def __init__(self, brain: CaregiverBrain):
        self.brain = brain

    async def bid(self, call: dict):
        return self.brain.bid(call)

    async def hear(self, text: str):
        return self.brain.hear(text)

    async def record(self, status: str, need: str):
        self.brain.record(status, need)

    async def summary(self):
        return self.brain.summary()


class Circle:
    def __init__(self, members: dict, *, timeout_s: float = 3.0, clock=time.time):
        self.members = members
        self.timeout_s = timeout_s
        self.clock = clock

    async def _ask(self, name: str, coro_fn, *args):
        try:
            return await asyncio.wait_for(coro_fn(*args), timeout=self.timeout_s)
        except asyncio.TimeoutError:
            log.info("%s's agent didn't answer in %.1fs", name, self.timeout_s)
        except Exception as e:                                   # a broken member is a silent member
            log.warning("%s's agent failed: %s", name, e)
        return None

    async def plan(self, call: dict, plan_order: list[str], explicit: bool) -> dict:
        """Ask every member in the care plan (in parallel) and order them. `explicit` = the request named people, so the circle
        only advises and the person's choice stands."""
        t0 = time.monotonic()
        names = [n for n in plan_order if n in self.members]
        got = await asyncio.gather(*[self._ask(n, self.members[n].bid, call) for n in names])
        bids = {n: b for n, b in zip(names, got) if isinstance(b, dict)}
        r = choose(bids, call.get("urgency", "normal"), plan_order)
        mode = "fallback" if r["fallback"] else ("advice" if explicit else "circle")
        return {"mode": mode, "order": list(plan_order) if explicit or r["fallback"] else r["order"], "winner": r["winner"] if mode == "circle" else None,
                "suggested": r["winner"], "why": r["why"], "bids": list(bids.values()), "call_id": call.get("call_id"),
                "transport": {n: getattr(self.members[n], "transport", "local") for n in names},
                "ms": int((time.monotonic() - t0) * 1000)}

    async def hear(self, name: str, text: str):
        m = self.members.get(name)
        return await self._ask(name, m.hear, text) if m else None

    async def record(self, name: str, status: str, need: str) -> None:
        m = self.members.get(name)
        if m:
            await self._ask(name, m.record, status, need)

    async def snapshot(self) -> list[dict]:
        got = await asyncio.gather(*[self._ask(n, m.summary) for n, m in self.members.items()])
        return [{**(s or {"member": n, "mode": "unknown"}), "transport": getattr(self.members[n], "transport", "local")}
                for (n, _), s in zip(self.members.items(), got)]
