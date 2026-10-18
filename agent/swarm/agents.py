"""The uAgents around the pure logic: Agentverse identities, the Care Circle protocol, and the Agent Chat Protocol so each one
can also be found and talked to through ASI:One. Nothing here decides anything; swarm/circle.py does."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (ChatAcknowledgement, ChatMessage, EndSessionContent, StartSessionContent,
                                                 TextContent, chat_protocol_spec)

from .circle import CaregiverBrain
from .wire import (PROTOCOL_NAME, PROTOCOL_VERSION, CircleQuery, CircleState, HelpBid, HelpCall, Outcome, StatusReply,
                   StatusText)

log = logging.getLogger("residual.swarm")
DOCS = Path(__file__).resolve().parent / "profiles"


def text_msg(text: str, end: bool = False) -> ChatMessage:
    content = [TextContent(type="text", text=text)]
    if end:
        content.append(EndSessionContent(type="end-session"))
    return ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid.uuid4(), content=content)


def chat_protocol(handle_text, greeting: str) -> Protocol:
    """The Agent Chat Protocol around any `async handle_text(text, sender) -> str`."""
    chat = Protocol(spec=chat_protocol_spec)

    @chat.on_message(ChatMessage)
    async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
        await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id))
        text = " ".join(c.text for c in msg.content if isinstance(c, TextContent)).strip()
        text = re.sub(r"^(?:\s*@[\w.-]+)+\s*", "", text)         # ASI:One puts the @handle you mentioned in front of your words
        if not text:
            if any(isinstance(c, StartSessionContent) for c in msg.content):
                await ctx.send(sender, text_msg(greeting))
            return
        try:
            reply = await handle_text(text, sender)
        except Exception as e:                                     # an agent never goes silent on a chat
            log.exception("chat handler failed")
            reply = "Something went wrong on my side, so I haven't done anything."
        await ctx.send(sender, text_msg(reply))

    @chat.on_message(ChatAcknowledgement)
    async def on_ack(_ctx: Context, _sender: str, _msg: ChatAcknowledgement):
        pass

    return chat


# ----------------------------------------------------------------------------------- a family member's agent
def member_chat(brain: CaregiverBrain, person: str, authorized: set[str] | None = None):
    """What anyone on ASI:One can ask this member's agent. Where Maya is belongs to her family: only the care team's
    addresses (ASI_AUTHORIZED) get an answer; everyone else is told why not."""
    async def handle(text: str, sender: str) -> str:
        auth = authorized if authorized is not None else {x.strip() for x in os.environ.get("ASI_AUTHORIZED", "").split(",") if x.strip()}
        if sender not in auth:
            return (f"I'm {brain.name}'s care agent. I only share where {brain.name} is, or take updates, from {person}'s care team, "
                    f"so I haven't done anything. I help {person}'s family decide who is best placed when {person} needs someone.")
        ack = brain.hear(text)
        if ack:
            return ack
        s = brain.summary()
        if re.search(r"\b(status|available|where|free|busy|how|load|week)\b", text, re.I):
            mode = {"free": "available", "busy": s["label"] or "busy", "asleep": "asleep", "driving": "driving", "away": "away"}.get(s["mode"], s["mode"])
            eta = f", about {s['eta_min']} min away" if s["eta_min"] and s["mode"] == "free" else ""
            if s["until"] and s["mode"] != "free":
                eta += " until " + datetime.fromtimestamp(s["until"]).strftime("%I:%M %p").lstrip("0")
            return (f"{brain.name} is {mode}{eta}. {'Can lift.' if s['can_lift'] else 'Not lifting right now.'} "
                    f"Care load this week: {s['load']:g} (nights count double).")
        return f"Tell me where {brain.name} is (\"in a meeting till 3\", \"driving\", \"10 min away\") or ask \"is {brain.name} available?\"."
    return handle


def publish_manifests() -> bool:
    """Publishing a protocol's manifest tells Agentverse what the protocol is. Tests switch it off (RESIDUAL_PUBLISH_MANIFEST=0)."""
    return os.environ.get("RESIDUAL_PUBLISH_MANIFEST", "1") != "0"


def build_member_agent(brain: CaregiverBrain, *, person: str, seed: str, mailbox: bool = True, port: int | None = None,
                       endpoint: list[str] | None = None, handle: str | None = None, authorized: set[str] | None = None, loop=None) -> Agent:
    kw = {}
    if loop is not None:
        kw["loop"] = loop
    if port:
        kw["port"] = port
    if endpoint:
        kw["endpoint"] = endpoint
    readme = DOCS / "member.md"
    agent = Agent(name=f"{brain.name} · Residual Care Circle", seed=seed, mailbox=mailbox, publish_agent_details=True,
                  description=(f"{brain.name}'s care agent in {person}'s Residual Care Circle. It answers a call for help on behalf of "
                               f"{brain.name}: available or not, how far, who has helped most this week. It never contacts anyone itself."),
                  readme_path=str(readme) if readme.exists() else None, handle=handle,
                  metadata={"category": "care coordination", "keywords": "care circle, caregiver, family, ALS, availability, scheduling"},
                  **kw)
    circle = Protocol(name=PROTOCOL_NAME, version=PROTOCOL_VERSION)

    @circle.on_message(HelpCall, replies={HelpBid})
    async def on_call(ctx: Context, sender: str, msg: HelpCall):
        b = brain.bid(msg.model_dump())
        await ctx.send(sender, HelpBid(**b))

    @circle.on_message(StatusText, replies={StatusReply})
    async def on_status(ctx: Context, sender: str, msg: StatusText):
        await ctx.send(sender, StatusReply(member=brain.name, ack=brain.hear(msg.text) or ""))

    @circle.on_message(Outcome)
    async def on_outcome(ctx: Context, sender: str, msg: Outcome):
        brain.record(msg.status, msg.need)

    @circle.on_message(CircleQuery, replies={CircleState})
    async def on_query(ctx: Context, sender: str, msg: CircleQuery):
        s = brain.summary()
        await ctx.send(sender, CircleState(request_id=msg.request_id, member=brain.name, mode=s["mode"], label=s["label"] or "",
                                           until=s["until"] or 0.0, eta_min=s["eta_min"] or 0, can_lift=s["can_lift"], load=s["load"]))

    agent.include(circle, publish_manifest=publish_manifests())
    agent.include(chat_protocol(member_chat(brain, person, authorized),
                                f"Hi, I'm {brain.name}'s care agent in {person}'s Care Circle."), publish_manifest=publish_manifests())
    return agent


# ----------------------------------------------------------------------------------- the Relay's side of the conversation
class Link:
    """Asks other agents a question from code that isn't a message handler: queue the message, wait for the matching reply.
    The Relay's agent pumps the queue (pump) and hands replies back (resolve)."""

    def __init__(self):
        self.outbox: asyncio.Queue = asyncio.Queue()
        self.waiting: dict[str, asyncio.Future] = {}

    async def request(self, address: str, model, key: str, timeout: float):
        fut = asyncio.get_running_loop().create_future()
        self.waiting[key] = fut
        self.outbox.put_nowait((address, model))
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.waiting.pop(key, None)

    def tell(self, address: str, model) -> None:
        self.outbox.put_nowait((address, model))

    def resolve(self, key: str, response) -> bool:
        fut = self.waiting.get(key)
        if fut is not None and not fut.done():
            fut.set_result(response)
            return True
        return False

    async def pump(self, ctx: Context) -> None:
        while not self.outbox.empty():
            address, model = self.outbox.get_nowait()
            try:
                await ctx.send(address, model)
            except Exception as e:
                log.warning("couldn't send to %s: %s", address[:14], e)


class RemoteMember:
    """A family member's agent reached over Agentverse (or the local dispatcher when it runs in the same process)."""
    transport = "agentverse"

    def __init__(self, name: str, address: str, link: Link, timeout_s: float = 3.0):
        self.name, self.address, self.link, self.timeout_s = name, address, link, timeout_s

    async def bid(self, call: dict):
        r = await self.link.request(self.address, HelpCall(call_id=call["call_id"], person=call.get("person", ""), need=call.get("need", "general"),
                                                           urgency=call.get("urgency", "normal"), text=call.get("text", "")),
                                    f"bid:{call['call_id']}:{self.name}", self.timeout_s)
        return r.model_dump() if r is not None else None

    async def hear(self, text: str):
        rid = uuid.uuid4().hex[:8]
        r = await self.link.request(self.address, StatusText(member=self.name, text=text), f"status:{self.name}:{rid}", self.timeout_s)
        return (r.ack or None) if r is not None else None

    async def record(self, status: str, need: str):
        self.link.tell(self.address, Outcome(call_id="", member=self.name, status=status, need=need))

    async def summary(self):
        rid = uuid.uuid4().hex[:8]
        r = await self.link.request(self.address, CircleQuery(request_id=rid), f"state:{rid}", self.timeout_s)
        if r is None:
            return None
        return {"member": r.member, "mode": r.mode, "label": r.label, "until": r.until or None, "eta_min": r.eta_min or None,
                "can_lift": r.can_lift, "load": r.load}


def attach_relay_side(agent: Agent, link: Link) -> None:
    """Teach the Relay's agent to deliver the circle's replies to whoever is waiting for them, and to send what is queued."""
    @agent.on_message(HelpBid)
    async def got_bid(ctx: Context, sender: str, msg: HelpBid):
        link.resolve(f"bid:{msg.call_id}:{msg.member}", msg)

    @agent.on_message(StatusReply)
    async def got_status(ctx: Context, sender: str, msg: StatusReply):
        # statuses are matched by member: resolve whichever request from this member is waiting
        for k in [k for k in link.waiting if k.startswith(f"status:{msg.member}:")]:
            link.resolve(k, msg)

    @agent.on_message(CircleState)
    async def got_state(ctx: Context, sender: str, msg: CircleState):
        link.resolve(f"state:{msg.request_id}", msg)

    @agent.on_interval(period=0.2)
    async def pump(ctx: Context):
        await link.pump(ctx)


# ----------------------------------------------------------------------------------- the Concierge and the Hands
def build_chat_agent(*, name: str, description: str, readme: str, keywords: str, category: str, handle_text, greeting: str, seed: str,
                     mailbox: bool = True, loop=None, handle: str | None = None, outbox: asyncio.Queue | None = None) -> Agent:
    """An agent whose job is done through the Agent Chat Protocol (so ASI:One can find and use it). `outbox` holds
    (session, text, end) updates to send back into a conversation as an errand progresses."""
    kw = {"loop": loop} if loop is not None else {}
    path = DOCS / readme
    agent = Agent(name=name, seed=seed, mailbox=mailbox, publish_agent_details=True, description=description,
                  readme_path=str(path) if path.exists() else None, handle=handle,
                  metadata={"category": category, "keywords": keywords}, **kw)
    agent.include(chat_protocol(handle_text, greeting), publish_manifest=publish_manifests())
    if outbox is not None:
        @agent.on_interval(period=1.0)
        async def push(ctx: Context):
            while not outbox.empty():
                session, text, end = outbox.get_nowait()
                try:
                    st = await ctx.send(session, text_msg(text, end=end))
                    log.info("%s update to %s: %r -> %s", name, str(session)[:14], text[:90], getattr(st, "status", st))
                except Exception as e:
                    log.warning("couldn't update %s: %s", str(session)[:14], e)
    return agent
