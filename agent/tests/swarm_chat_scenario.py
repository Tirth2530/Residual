"""Run by test_swarm_agents.py in its own process. The real Relay agent is asked for help through the Agent Chat Protocol, the way
ASI:One asks, and has to get the Care Circle's bids back while that very message is still being handled. uAgents handles an agent's
messages one at a time, so a handler that waits for the bids blocks the bids' own delivery. Prints one JSON line."""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

os.environ["RESIDUAL_PUBLISH_MANIFEST"] = "0"
os.environ["AGENT_SEED"] = "test seed relay chat " + "x" * 20
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from uagents import Agent, Bureau, Protocol  # noqa: E402
from uagents_core.contrib.protocols.chat import ChatAcknowledgement, ChatMessage, TextContent, chat_protocol_spec  # noqa: E402

from residual_agent import Relay, build_agent  # noqa: E402
from swarm.agents import Link, RemoteMember, build_member_agent  # noqa: E402
from swarm.circle import CaregiverBrain  # noqa: E402
from swarm.runtime import Circle  # noqa: E402
from test_security import make_orch  # noqa: E402


def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    out = {}
    maya, raj = CaregiverBrain("Maya"), CaregiverBrain("Raj")
    maya.hear("in a meeting till 11:59 pm")
    raj.hear("home, 6 min away")
    am = build_member_agent(maya, person="Dev", seed="test seed maya c " + "x" * 20, mailbox=False, loop=loop)
    ar = build_member_agent(raj, person="Dev", seed="test seed raj c " + "x" * 20, mailbox=False, loop=loop)
    user = Agent(name="asi-user", seed="test seed user c " + "x" * 20, loop=loop)
    orch = make_orch()
    relay = Relay(orch, asi_authorized={user.address}, asi_mode="closed")
    link = Link()
    relay_agent = build_agent(relay, loop, link=link, mailbox=False, port=0)
    orch.circle = Circle({"Maya": RemoteMember("Maya", am.address, link, 3.0), "Raj": RemoteMember("Raj", ar.address, link, 3.0)}, timeout_s=3.5)

    chat = Protocol(spec=chat_protocol_spec)

    @chat.on_message(ChatMessage)
    async def got(ctx, sender, msg):
        out.setdefault("replies", []).append(" ".join(c.text for c in msg.content if isinstance(c, TextContent)))
        await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id))
        if len(out["replies"]) == 1:
            await asyncio.sleep(0.5)
            t = next(iter(orch.tasks.values()), None)
            out["swarm"] = (t.swarm or {}).get("mode") if t else None
            out["bids"] = sorted(b["member"] for b in (t.swarm or {}).get("bids", [])) if t else None
            out["order"] = list(t.order) if t else None
            print("RESULT " + json.dumps(out, default=str), flush=True)
            os._exit(0)

    @chat.on_message(ChatAcknowledgement)
    async def ack(ctx, sender, msg):
        pass

    user.include(chat)

    @user.on_event("startup")
    async def ask(ctx):
        await asyncio.sleep(2.0)
        await ctx.send(relay_agent.address, ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(),
                                                       content=[TextContent(type="text", text="Ask for help, please come Maya or Raj")]))

    async def watchdog():
        await asyncio.sleep(40)
        out["error"] = "no reply"
        print("RESULT " + json.dumps(out, default=str), flush=True)
        os._exit(0)
    loop.create_task(watchdog())
    bureau = Bureau(port=int(sys.argv[1]) if len(sys.argv) > 1 else 18766, loop=loop)
    for a in (relay_agent, am, ar, user):
        bureau.add(a)
    bureau.run()


main()
