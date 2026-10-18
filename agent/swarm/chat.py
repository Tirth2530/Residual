"""What each agent says on ASI:One. Anything that acts goes through one gate: the care team's addresses (ASI_AUTHORIZED).
Everyone else can ask what the agent does and is told, plainly, why nothing was done."""

from __future__ import annotations

import logging
import os
import re
from urllib.parse import urlparse

log = logging.getLogger("residual.hands")

URL = re.compile(r"(https?://[^\s<>\"]+|(?<![@\w])(?:[a-z0-9-]+\.)+(?:com|org|net|gov|edu|io|co|health|care)(?:/[^\s<>\"]*)?)", re.I)


def authorized_from_env() -> set[str]:
    return {x.strip() for x in os.environ.get("ASI_AUTHORIZED", "").split(",") if x.strip()}


def parse_errand(text: str) -> tuple[str, str | None]:
    """'Renew the prescription at https://x.test/refill' -> (goal, url). The url is None if the request names no site."""
    m = URL.search(text or "")
    if not m:
        return (text or "").strip(), None
    raw = m.group(1).rstrip(".,;)")
    url = raw if raw.lower().startswith("http") else "https://" + raw
    goal = (text[:m.start()] + text[m.end():]).strip()
    goal = re.sub(r"\s+(at|on|from|via|using|through|in)\s*$", "", goal, flags=re.I)
    goal = re.sub(r"\s+(at|on|from|via|using|through|in)\s+(please|thanks)", r" \2", goal, flags=re.I)
    goal = re.sub(r"\s+(at|on|from|via|using|through|in)\s+(?=and\b|then\b|,)", " ", goal, flags=re.I)
    goal = re.sub(r"\s+(at|on|from|via|using|through|in)(?=,)", "", goal, flags=re.I)
    goal = re.sub(r"\s+,", ",", goal)
    goal = re.sub(r"\s{2,}", " ", goal).strip()
    return goal, url


def hands_chat(hands, authorized: set[str] | None = None, relay=None):
    async def handle(text: str, sender: str) -> str:
        auth = authorized if authorized is not None else authorized_from_env()
        person = hands.person
        trusted = sender in auth
        if not trusted:
            return (f"I'm Residual's Hands: I use a real browser (Browser Use) to do things on websites for {person}, who can't use a mouse. "
                    f"I stop one click before anything final, ask {person} on their own screen, and ask a family member to lend their hands if a "
                    f"site needs a login. I only take errands from {person}'s care team, so I haven't done anything.")
        t = text.strip()
        log.info("Hands: chat from %s: %r", sender[:14], t[:140])
        if len(t) > 400:                                       # a request is a sentence or two; a paragraph is the model talking
            return ""
        if re.search(r"\b(status|what('?s| is) (happening|going on)|progress|how('?s| is) it going)\b", t, re.I):
            live = [e for e in hands.errands.values() if e.status not in ("done", "failed", "cancelled", "declined", "refused", "no_hands")] or \
                   sorted(hands.errands.values(), key=lambda e: -e.created)[:1]
            if not live:
                return "There are no errands yet."
            e = live[0]
            wait = {"awaiting_consent": f" Waiting for {person} to confirm on their screen: {e.final_action}.",
                    "needs_hands": f" Waiting for {e.helper} to lend a hand."}.get(e.status, "")
            return f"{e.goal} on {e.host}: {e.status.replace('_', ' ')}. {e.summary}{wait}"
        if re.search(r"\b(cancel|stop)\b", t, re.I):
            for e in sorted(hands.errands.values(), key=lambda e: -e.created):
                if e.status not in ("done", "failed", "cancelled", "declined", "refused", "no_hands"):
                    r = await hands.cancel(e.id, source="asi")
                    return r.get("error") or "Stopped. Nothing was submitted."
            return "There's nothing running."
        goal, url = parse_errand(t)
        if not url:
            return ("Tell me what to do and on which approved site, for example: \"Renew Dev's prescription at https://pharmacy.example.com\". "
                    "I need an https site address a caregiver has approved.")
        if relay is not None:
            again = relay.guard(sender, ("errand", (urlparse(url).hostname or "").lower(), re.sub(r"\W+", " ", goal.lower()).strip()[:80]),
                                "I already started that errand, so I haven't opened another browser.")
            if again is not None:
                log.warning("Hands: chat request ignored (%s)", again[:40] or "repeat")
                return again
        r = await hands.start(goal, url, source="asi", trusted=True, session=sender)
        log.info("Hands: errand %s %s on %s", r.get("id"), r.get("status"), r.get("host"))
        if r.get("status") == "refused":
            return r["error"]
        if relay is not None:
            relay.pushed(sender)                               # our reply is about to go into that chat: what comes straight back is the model answering it
        return (f"On it: {goal} on {r['host']}. I'll go as far as one click before anything final, then {person} confirms on their own screen. "
                f"If the site needs a login I'll ask a family member to lend their hands.")
    return handle


def concierge_chat(relay, authorized: set[str] | None = None):
    async def handle(text: str, sender: str) -> str:
        com = relay.orch.commerce
        auth = authorized if authorized is not None else authorized_from_env()
        person = relay.orch.policy.person if hasattr(relay.orch, "policy") else "Dev"
        if com is None:
            return "Ordering isn't set up on this Relay."
        if re.search(r"\b(order|buy|confirm|pay|place)\b", text, re.I):
            return await relay.handle_text(text, sender)              # the Relay's single gate decides, strangers included
        if re.search(r"\b(cost|price|quote|menu|how much|options|cheapest|fastest)\b", text, re.I):
            if sender not in auth:
                return f"I only share {person}'s food plans with their care team, so I haven't done anything."
            r = await com.quote_round()
            if r is None:
                return "Only one approved restaurant, so there's nothing to compare."
            lines = [f"{q['merchant_name']}: ${q['total_cents'] / 100:.2f}, about {q['eta_min']} min" + (" (the usual)" if q["exact"] else "") + (" ← best" if q["chosen"] else "")
                     for q in r["quotes"]]
            return "Quotes for the usual:\n" + "\n".join(lines) + f"\n{r['why']} Nothing is ordered until {person} confirms it on their own screen. (Sandbox: no real money.)"
        return (f"I'm Residual's Concierge. I ask every approved restaurant for a quote on {person}'s usual, pick the one that costs the fewest choices "
                f"(in budget, not too slow, and nobody has to be woken to approve it), and wait for {person} to confirm on their own screen. "
                f"Try \"order Dev's usual\" or \"what would dinner cost?\". Sandbox: no real money.")
    return handle
