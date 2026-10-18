"""Natural language (ASI) -> structured Intent.

Deterministic first: contacts are matched against the allowlist in the order they're mentioned,
needs and emergencies by keywords. If ASI1_API_KEY is set, ASI:One can extract the intent
instead, but its output goes through exactly the same Intent model and policy: it can't add a
contact, change the emergency order, or cancel an emergency.
"""

from __future__ import annotations

import json
import os
import re

from models import Intent

NEED_WORDS = [
    ("repositioning", r"reposition|turn (him|her|them|me)|move (him|her|them|me)|shift|roll over|adjust"),
    ("suction", r"suction"),
    ("breathing", r"breath|breathing|choking|can'?t breathe"),
    ("pain", r"\bpain|hurt"),
    ("thirsty", r"thirst|water|drink"),
    ("bathroom", r"bathroom|toilet|restroom"),
]
EMERGENCY = r"emergency|urgent|can'?t breathe|not breathing|choking|fell|fallen|911|ambulance"


def mentioned(text: str, names: list[str]) -> list[str]:
    hits = []
    for n in names:
        m = re.search(rf"\b{re.escape(n)}\b", text, re.I)
        if m:
            hits.append((m.start(), n))
    return [n for _, n in sorted(hits)]


# Words that follow "ask / tell / contact…" without being a person's name.
NOT_NAMES = {"me", "my", "him", "her", "them", "us", "you", "i", "it", "everyone", "everybody", "someone", "somebody", "anyone", "anybody",
             "whoever", "all", "the", "a", "an", "each", "both", "family", "caregiver", "caregivers", "carer", "carers", "nurse", "doctor",
             "neighbor", "neighbour", "friend", "help", "urgent", "emergency", "please", "now", "again", "this", "that", "these", "those"}
_ADDRESSED = re.compile(r"\b(?i:ask|contact|call|tell|text|message|notify|alert|email|page)\s+([A-Z][a-z]+)")
_REQUEST_FIRST = re.compile(r"\s*(?:please\s+|can you\s+|could you\s+)?(ask|tell|text|message|send|contact|call|notify|alert)\b", re.I)
_STATUS = re.compile(r"\bstatus\b|\b(?:has|have|had|did|any)\b.{0,30}?\b(?:repl(?:y|ied|ies)|respon(?:d|ded|se|ses)|answer(?:ed|s)?|news|update|heard back)\b"
                     r"|\bheard back\b", re.I)
_CHANGE_CONTACTS = re.compile(r"\b(?:add|include|put|register|save|insert)\b[^.?!]{0,60}\b(?:contacts?|contact list|care team|caregivers?|list)\b", re.I)


def unknown_names(text: str, names: list[str], person: str | None = None) -> list[str]:
    """Capitalised words right after "ask / contact / tell…" that aren't on the allowlist (and aren't a pronoun or the person)."""
    known = {n.lower() for n in names} | ({person.lower()} if person else set())
    out = []
    for n in _ADDRESSED.findall(text):
        if n.lower() not in known and n.lower() not in NOT_NAMES and n not in out:
            out.append(n)
    return out


def parse(text: str, names: list[str], person: str | None = None) -> Intent | dict:
    """Returns an Intent, or {"error": ...} with a plain answer or a question to ask back."""
    t = text.strip()
    low = t.lower()
    who = mentioned(t, names)
    roster = ", ".join(names)
    # a status question in ordinary words ("Has anyone responded?"), unless it starts as an instruction ("Ask Maya if she has time to reply")
    if _STATUS.search(low) and (re.search(r"\bstatus\b", low) or not _REQUEST_FIRST.match(low)):
        return Intent(intent="get_status", source="asi")
    if re.search(r"\bcancel\b|\bnever ?mind\b|\bstop\b", low):
        return Intent(intent="cancel_task", source="asi")
    if _CHANGE_CONTACTS.search(t):
        return {"error": f"I can't add or change contacts: only the care team can change who is on the list. I can contact: {roster}."}
    msg = re.search(r"\b(?:tell|text|message|send)\s+(\w+)\s*(?:that|:|saying|to say)?\s*[\"“']?(.+?)[\"”']?$", t, re.I)
    need = next((k for k, pat in NEED_WORDS if re.search(pat, low)), None)
    if re.search(EMERGENCY, low):
        return Intent(intent="emergency", urgency="emergency", need=need or "general", source="asi")
    stranger = unknown_names(t, names, person)
    if stranger:                       # never quietly drop a name and do something else: say who can't be contacted
        who_not = ", ".join(stranger)
        return {"error": f"{who_not} isn't on the care team's list, so I won't contact them. I can contact: {roster}."}
    if need or re.search(r"\bhelp\b|\bcome\b|\bcheck on\b", low):
        if not who:
            return {"error": f"Who should I ask? I can contact: {', '.join(names)}."}
        return Intent(intent="request_caregiver_help", need=need or "general", preferred_contact=who[0],
                      contacts=who[1:], requires_confirmation=True, source="asi")
    if msg:
        to = next((n for n in names if n.lower() == msg.group(1).lower()), None)
        if to is None:
            return {"error": f"I can only message {', '.join(names)}."}
        return Intent(intent="send_message", contact=to, message=msg.group(2).strip(), source="asi")
    return {"error": "I can contact a carer for help, send a message, or check on a request. "
                     f"For example: \"Ask {names[0] if names else 'Maya'} to help reposition him.\""}


async def parse_with_asi1(text: str, names: list[str], person: str | None = None) -> Intent | dict:
    """Optional ASI:One extraction (OpenAI-compatible). Falls back to `parse` on any problem."""
    key = os.environ.get("ASI1_API_KEY", "").strip()
    if not key:
        return parse(text, names, person)
    try:
        import aiohttp

        system = ("Extract a JSON intent for a care-coordination agent. Fields: intent "
                  "(request_caregiver_help|emergency|send_message|get_status|cancel_task), need "
                  "(repositioning|pain|suction|breathing|thirsty|bathroom|general), preferred_contact, "
                  "contacts (ordered fallbacks), contact, message. Only use these contact names: "
                  + ", ".join(names) + ". Reply with JSON only.")
        body = {"model": "asi1-mini", "temperature": 0,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}]}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as s:
            async with s.post("https://api.asi1.ai/v1/chat/completions", json=body,
                              headers={"Authorization": f"Bearer {key}"}) as r:
                data = await r.json(content_type=None)
        raw = data["choices"][0]["message"]["content"]
        d = json.loads(re.search(r"\{[\s\S]*\}", raw).group(0))
        d["source"] = "asi"
        it = Intent(**{k: v for k, v in d.items() if k in Intent.model_fields})
        # the model may only name allowlisted contacts; anything else is rejected by policy later,
        # but we also fall back to deterministic parsing when it invented one
        for n in [it.preferred_contact, it.contact, *it.contacts]:
            if n and n not in names:
                return parse(text, names, person)
        return it
    except Exception:
        return parse(text, names)
