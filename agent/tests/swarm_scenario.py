"""Run by test_swarm_agents.py in its own process: a Bureau owns the event loop it runs on and cancels everything on it at
shutdown, so it can't share pytest's. Prints one JSON line with what happened."""
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

os.environ["RESIDUAL_PUBLISH_MANIFEST"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from uagents import Agent, Bureau  # noqa: E402

from swarm.agents import Link, RemoteMember, attach_relay_side, build_member_agent  # noqa: E402
from swarm.circle import CaregiverBrain  # noqa: E402
from swarm.runtime import Circle  # noqa: E402

TS = datetime(2026, 10, 18, 13, 0).timestamp()


async def scenario(circle, maya, raj, out):
    await asyncio.sleep(2.0)
    try:
        plan = await circle.plan({"call_id": "call-1", "person": "Dev", "need": "repositioning", "urgency": "normal"}, ["Maya", "Raj"], explicit=False)
        out["plan"] = {k: plan[k] for k in ("mode", "order", "why", "transport")}
        out["bids"] = {b["member"]: b for b in plan["bids"]}
        out["ack"] = await circle.hear("Maya", "driving")
        out["maya_mode"] = maya.presence.mode
        await circle.record("Raj", "acknowledged", "repositioning")
        await asyncio.sleep(1.0)
        out["raj_load"] = raj.ledger.load(TS)
        out["snap"] = await circle.snapshot()
    except Exception as e:  # report, don't hang
        out["error"] = repr(e)
    print("RESULT " + json.dumps(out, default=str), flush=True)
    os._exit(0)


def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    maya, raj = CaregiverBrain("Maya", clock=lambda: TS), CaregiverBrain("Raj", clock=lambda: TS)
    maya.hear("in a meeting till 3")
    raj.hear("home, 6 min away")
    am = build_member_agent(maya, person="Dev", seed="test seed maya " + "x" * 20, mailbox=False)
    ar = build_member_agent(raj, person="Dev", seed="test seed raj " + "x" * 20, mailbox=False)
    relay = Agent(name="relay-under-test", seed="test seed relay " + "x" * 20)
    link = Link()
    attach_relay_side(relay, link)
    circle = Circle({"Maya": RemoteMember("Maya", am.address, link, 5.0), "Raj": RemoteMember("Raj", ar.address, link, 5.0)}, timeout_s=6.0)
    bureau = Bureau(port=int(sys.argv[1]) if len(sys.argv) > 1 else 18765, loop=loop)
    for a in (relay, am, ar):
        bureau.add(a)
    loop.create_task(scenario(circle, maya, raj, {}))
    bureau.run()


main()
