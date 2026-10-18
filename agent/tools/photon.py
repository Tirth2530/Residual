"""Photon 2.0: the human communication layer (one channel: iMessage or SMS).

Outbound, per Photon 2.0's REST API (api.photon.codes/v1/projects/{project}):
    POST /users     {platform, handle}                   -> user for the contact's phone number
    POST /spaces    {platform, participantIds}           -> the conversation (agent + contact)
    POST /messages  {spaceId, senderId, content}         -> 202 Accepted (accepted, not delivered)
    headers: Authorization: Bearer <key>, idempotency-key: <key>
User and space ids are cached per contact (state/photon_map.json), so each contact gets one
conversation. Every send carries an idempotency key; the same key never sends twice, locally or
at Photon.

Inbound: Photon delivers replies by webhook. parse_inbound() accepts the Spectrum webhook shape
(event "messages", space.id, message.sender.id, message.content.text) and Photon 2.0 event
envelopes ({type, data: {...}}), finding the message wherever it sits. Field names for Photon 2.0
events should be confirmed against https://api.photon.codes/asyncapi.yaml on the day
(`python tools/photon_doctor.py` prints what your project actually sends).

SimulatedPhoton has the same interface, sends nothing, and marks every result simulated=True, so
rehearsals can never be mistaken for a real message.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

PHOTON_BASE = "https://api.photon.codes/v1"

# What each endpoint needs from the API key (Photon's OpenAPI x-required-permissions), so a 403 says what to fix.
_NEEDS = {"/platforms/imessage/assignments": "platforms:read", "/users": "project:write", "/spaces": "project:write",
          "/messages": "project:write"}


_OUTBOUND_STATUS = {"accepted", "queued", "sending", "dispatched", "delivered", "failed", "sent"}


def _digits(s) -> str:
    return "".join(c for c in str(s or "") if c.isdigit())[-10:]


def _rows(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("assignments", "items", "data", "results"):
            if isinstance(data.get(k), list):
                return data[k]
    return []


class PhotonError(Exception):
    pass


class _Store:
    def __init__(self, path: Path | None):
        self.path = path
        self.d = {"contacts": {}, "sent": {}}
        if path and path.exists():
            try:
                self.d = json.loads(path.read_text())
            except Exception:
                pass

    def save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.d, indent=1))
            tmp.replace(self.path)


class PhotonClient:
    simulated = False

    def __init__(self, project_id: str, api_key: str, agent_user_id: str = "", platform: str = "imessage",
                 base: str = PHOTON_BASE, state_path: Path | None = None,
                 post: Callable[[str, dict, dict], Awaitable[tuple[int, dict]]] | None = None,
                 get: Callable[[str, dict | None], Awaitable[tuple[int, dict]]] | None = None,
                 timeout_s: float = 15.0):
        """agent_user_id is optional. Photon's shared iMessage lines are pooled: each person is assigned a line, and the
        agent that person talks to is the user for THAT line's number, so by default it is found per contact (see
        ensure_space). Setting it forces one agent for everyone (a dedicated line)."""
        if not (project_id and api_key):
            raise PhotonError("PHOTON_PROJECT_ID and PHOTON_API_KEY are required")
        self.base = f"{base.rstrip('/')}/projects/{project_id}"
        self.key, self.agent_user, self.platform = api_key, agent_user_id or "", platform
        self.store = _Store(state_path)
        self._post = post or self._http_post
        self._get = get or self._http_get
        self.timeout_s = timeout_s

    async def _http_post(self, path: str, body: dict, headers: dict) -> tuple[int, dict]:
        import aiohttp

        h = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json", **headers}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout_s)) as s:
            async with s.post(self.base + path, json=body, headers=h) as r:
                try:
                    data = await r.json(content_type=None)
                except Exception:
                    data = {"raw": (await r.text())[:500]}
                return r.status, data or {}

    async def _http_get(self, path: str, params: dict | None = None) -> tuple[int, dict]:
        import aiohttp

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout_s)) as s:
            async with s.get(self.base + path, params=params, headers={"Authorization": f"Bearer {self.key}"}) as r:
                try:
                    data = await r.json(content_type=None)
                except Exception:
                    data = {"raw": (await r.text())[:500]}
                return r.status, data or {}

    def _fail(self, path: str, status: int, data: dict) -> PhotonError:
        detail = (data.get("detail") or data.get("title") or data.get("message") or data) if isinstance(data, dict) else data
        hint = ""
        if status == 401:
            hint = " The API key was rejected: check PHOTON_API_KEY."
        elif status == 403 and path in _NEEDS:
            hint = f" The API key lacks a permission: it needs {_NEEDS[path]} (create a key with project:read, project:write and platforms:read)."
        return PhotonError(f"Photon {path} -> HTTP {status}: {detail}.{hint}")

    async def _call(self, path: str, body: dict, idem: str) -> dict:
        status, data = await self._post(path, body, {"idempotency-key": idem})
        if status >= 400:
            raise self._fail(path, status, data)
        return data.get("data", data) if isinstance(data, dict) else {}

    async def _line_for(self, contact: str, phone: str) -> str:
        """The line (a number Photon allocated) this person was assigned to in the Photon dashboard."""
        path = "/platforms/imessage/assignments"
        status, data = await self._get(path, None)
        if status >= 400:
            raise self._fail(path, status, data)
        want = _digits(phone)
        for a in _rows(data):
            if isinstance(a, dict) and not a.get("releasedAt") and _digits(a.get("handle")) == want and a.get("assignedPhoneNumber"):
                return a["assignedPhoneNumber"]
        raise PhotonError(f"{contact} isn't assigned a shared iMessage line. In the Photon dashboard assign one to this phone number, "
                          f"then have {contact} text that line a message once (a shared line can only reply to someone who wrote first).")

    async def ensure_space(self, contact: str, phone: str) -> dict:
        m = self.store.d["contacts"].get(contact)
        if m and m.get("space_id") and m.get("handle") == phone:
            return m
        agent_id, line = self.agent_user, None
        if not agent_id:                       # pooled shared lines: the agent is the user for this contact's assigned number
            line = await self._line_for(contact, phone)
            agent = await self._call("/users", {"platform": self.platform, "handle": line}, f"user:{self.platform}:{line}")
            agent_id = agent.get("id")
            if not agent_id:
                raise PhotonError(f"Photon didn't return an agent user for the line assigned to {contact}")
        user = await self._call("/users", {"platform": self.platform, "handle": phone}, f"user:{self.platform}:{phone}")
        uid = user.get("id")
        if not uid:
            raise PhotonError(f"Photon didn't return a user id for {contact}")
        space = await self._call("/spaces", {"platform": self.platform, "participantIds": [agent_id, uid]},
                                 f"space:{self.platform}:{agent_id}:{uid}")
        sid = space.get("id")
        if not sid:
            raise PhotonError(f"Photon didn't return a space id for {contact}")
        m = {"handle": phone, "user_id": uid, "space_id": sid, "platform": self.platform, "agent_user_id": agent_id, "line": line}
        self.store.d["contacts"][contact] = m
        self.store.save()
        return m

    async def send_message(self, contact: str, phone: str, text: str, idempotency_key: str) -> dict:
        """Idempotent: a key that was already sent returns the stored result without sending."""
        prev = self.store.d["sent"].get(idempotency_key)
        if prev:
            return {**prev, "replayed": True}
        t0 = time.monotonic()
        sp = await self.ensure_space(contact, phone)
        msg = await self._call("/messages", {"spaceId": sp["space_id"], "senderId": sp.get("agent_user_id") or self.agent_user,
                                             "content": {"type": "text", "format": "plain", "text": text}},
                               idempotency_key)
        res = {"message_id": msg.get("id"), "status": msg.get("status", "accepted"), "space_id": sp["space_id"],
               "contact": contact, "ms": int((time.monotonic() - t0) * 1000), "simulated": False}
        self.store.d["sent"][idempotency_key] = res
        self.store.save()
        return res

    def contact_for(self, space_id: str | None, sender: str | None) -> str | None:
        for name, m in self.store.d["contacts"].items():
            if space_id and m.get("space_id") == space_id:
                return name
            if sender and sender in (m.get("user_id"), m.get("handle")):
                return name
        return None

    def health(self) -> dict:
        return {"mode": "photon", "platform": self.platform, "contacts_mapped": len(self.store.d["contacts"])}


class SimulatedPhoton:
    """Rehearsal stand-in. Sends nothing. Every result says simulated=True, and the UI and Lab say so."""
    simulated = True

    def __init__(self, platform: str = "imessage", state_path: Path | None = None, fail: bool = False):
        self.platform = platform
        self.store = _Store(state_path)
        self.fail = fail
        self.sent: list[dict] = []

    async def send_message(self, contact: str, phone: str, text: str, idempotency_key: str) -> dict:
        prev = self.store.d["sent"].get(idempotency_key)
        if prev:
            return {**prev, "replayed": True}
        if self.fail:
            raise PhotonError("Photon unavailable (simulated failure)")
        sid = f"sim-space-{contact.lower()}"
        self.store.d["contacts"][contact] = {"handle": phone, "user_id": f"sim-user-{contact.lower()}",
                                             "space_id": sid, "platform": self.platform}
        res = {"message_id": f"sim-msg-{len(self.sent) + 1}", "status": "accepted", "space_id": sid,
               "contact": contact, "ms": 0, "simulated": True}
        self.sent.append({"contact": contact, "text": text, "key": idempotency_key})
        self.store.d["sent"][idempotency_key] = res
        return res

    def contact_for(self, space_id: str | None, sender: str | None) -> str | None:
        for name, m in self.store.d["contacts"].items():
            if space_id and m.get("space_id") == space_id:
                return name
            if sender and sender in (m.get("user_id"), m.get("handle")):
                return name
        return None

    def health(self) -> dict:
        return {"mode": "simulated", "platform": self.platform, "contacts_mapped": len(self.store.d["contacts"])}


# ------------------------------------------------------------------ inbound
def _find(d: Any, pred, depth: int = 0):
    if depth > 6:
        return None
    if isinstance(d, dict):
        if pred(d):
            return d
        for v in d.values():
            r = _find(v, pred, depth + 1)
            if r is not None:
                return r
    elif isinstance(d, list):
        for v in d:
            r = _find(v, pred, depth + 1)
            if r is not None:
                return r
    return None


def parse_inbound(payload: dict) -> dict | None:
    """-> {message_id, space_id, sender, text, platform} for an inbound text message, else None
    (outbound echoes, reactions, delivery receipts, unknown events are ignored)."""
    if not isinstance(payload, dict):
        return None
    # Photon 2.0 envelope: {type: "message.received" | "message.failed" | "message.delivered" | "space.updated" ...}. Only a
    # received message is a reply. The others describe OUR messages (a failed send carries our own text): never a reply.
    kind = payload.get("type")
    if isinstance(kind, str) and kind != "message.received" and kind.startswith(("message.", "space.")):
        return None
    msg = _find(payload, lambda d: isinstance(d.get("content"), dict) and "text" in d["content"])
    if msg is None:
        return None
    if str(msg.get("status", "")).lower() in _OUTBOUND_STATUS:
        return None
    direction = str(msg.get("direction", "inbound")).lower()
    if direction in ("outbound", "sent"):
        return None
    content = msg["content"]
    if content.get("type", "text") != "text":
        return None
    space = msg.get("space") if isinstance(msg.get("space"), dict) else (payload.get("space") or {})
    space_id = (space.get("id") if isinstance(space, dict) else None) or msg.get("spaceId") or \
        (payload.get("data") or {}).get("spaceId") or (msg.get("space") if isinstance(msg.get("space"), str) else None)
    sender = msg.get("sender") or msg.get("sentBy") or msg.get("from") or {}
    if isinstance(sender, dict):
        sender_id = sender.get("handle") or sender.get("id") or sender.get("phone")
    else:
        sender_id = str(sender)
    return {"message_id": msg.get("id") or payload.get("id"), "space_id": space_id, "sender": sender_id,
            "text": str(content.get("text", ""))[:2000], "platform": msg.get("platform") or (space or {}).get("platform")}


def verify_signature(secret: str, headers: dict, body: bytes, max_age_s: int = 300) -> bool:
    """Standard Webhooks (webhook-id / webhook-timestamp / webhook-signature: 'v1,<base64>') and
    Spectrum (X-Spectrum-Timestamp / X-Spectrum-Signature: 'v0=<hex>'). True if either verifies."""
    h = {k.lower(): v for k, v in headers.items()}
    now = time.time()
    if "webhook-signature" in h:
        wid, ts = h.get("webhook-id", ""), h.get("webhook-timestamp", "0")
        if abs(now - int(ts)) > max_age_s:
            return False
        key = secret.split("_", 1)[1] if secret.startswith("whsec_") else secret
        try:
            key_b = base64.b64decode(key)
        except Exception:
            key_b = key.encode()
        mac = base64.b64encode(hmac.new(key_b, f"{wid}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
        return any(hmac.compare_digest(mac, part.split(",", 1)[-1]) for part in h["webhook-signature"].split())
    if "x-spectrum-signature" in h:
        ts = h.get("x-spectrum-timestamp", "0")
        if abs(now - int(ts)) > max_age_s:
            return False
        mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        return hmac.compare_digest("v0=" + mac, h["x-spectrum-signature"])
    return False
